"""AI Provider 边界。

- 视觉模型（Gemini 3.7 Flash / K3 high）读取本页图纸图片，提取结构化事实；
- DeepSeek V4 Pro 只复核必要文字，不接收图片、PDF、密钥或本机路径；
- 模型输出先解析再经 pydantic 严格校验（extra="forbid"），失败即抛 ProviderError。

默认模型可用 .env.local 覆盖（见 .env.example）。
"""

from __future__ import annotations

import base64
import json
import os
import re

from openai import OpenAI

from .models import DeepSeekReviewOutput, TextFinding, VisionExtraction

# ---- 默认配置（与课堂一致；可被 .env.local 覆盖）----
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")

KIMI_BASE = os.environ.get("KIMI_API_BASE", "https://api.kimi.com/coding/v1")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "k3")

DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")


class ProviderError(Exception):
    """Provider 调用或契约校验失败，携带面向用户的中文提示。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


VISION_PROMPT = (
    "你是图纸审核助手。请阅读给出的每一页图纸图片，逐页提取事实，"
    "只输出一个 JSON 对象，不要输出任何其他文字。\n"
    "JSON 结构（字段名固定，不要增加、删除或改名字段）：\n"
    '{"pages":[{"page":1,"text":["识别到的文字行"],"dimensions":["尺寸标注，如 Ø80 ±0.05"],'
    '"title_block":{"图号":"...","名称":"...","材料":"...","比例":"...","设计":"...","审核":"..."},'
    '"technical_notes":["技术要求原文"]}]}\n'
    "要求：\n"
    "1. page 必须是从 1 开始的页码；\n"
    "2. 每一页都必须给出；找不到的内容填空数组或空字符串，不要编造；\n"
    "3. 只输出 JSON，不要用代码块包裹，不要加说明文字。"
)

DEEPSEEK_PROMPT = (
    "你是图纸审核助手（文本复核）。下面是一份图纸每页提取出的文字与标题栏内容。"
    "请只复核文字类要求（技术要求是否明确、材料是否标注、标题栏是否完整），"
    "不要输出图片相关内容。只输出一个 JSON 对象：\n"
    '{"findings":[{"page":1,"rule":"rule-id","title":"问题简述","evidence":"引用原文作证据"}]}\n'
    "要求：\n"
    "1. 每条必须引用该页真实文字摘录作为证据，禁止编造；\n"
    "2. evidence 必须是给定页面文字里的原文摘录；不要引用 JSON 字段名或结构"
    "（如 \"设计\": \"\"、\"technical_notes\"、\"title_block\" 这类），只引用图纸上的真实文字；\n"
    "3. 没有问题时 findings 为空数组；\n"
    "4. 只输出 JSON，不要用代码块包裹，不要加说明文字。"
)


def _client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key, timeout=180, max_retries=1)


def _require(env_key: str) -> str:
    value = os.environ.get(env_key, "").strip()
    if not value:
        raise ProviderError(f"缺少密钥配置 {env_key}。请在项目根目录 .env.local 中填写后重启服务。")
    return value


def _extract_json(text: str) -> str:
    """从模型输出中抽取 JSON 对象文本（容忍代码块与前后杂散文字）。"""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ProviderError("模型输出不是有效的 JSON，已按严格契约拒绝。")
    return text[start : end + 1]


def _parse_model_json(text: str, model_type: type):
    try:
        payload = json.loads(_extract_json(text))
    except json.JSONDecodeError as exc:
        raise ProviderError("模型输出无法解析为 JSON，已按严格契约拒绝。") from exc
    try:
        return model_type.model_validate(payload)
    except Exception as exc:
        raise ProviderError(f"模型输出不符合契约（{exc}），已按严格契约拒绝，不当作有效结果。") from exc


def _image_content(images: list[bytes]) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": VISION_PROMPT}]
    for img in images:
        b64 = base64.b64encode(img).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        )
    return content


def vision_extract(provider: str, images: list[bytes]) -> VisionExtraction:
    """让视觉模型读取全部页图片，返回严格校验后的结构化事实。"""
    if provider == "gemini":
        client = _client(GEMINI_BASE, _require("GEMINI_API_KEY"))
        model = GEMINI_MODEL
        temperature = 0
    elif provider == "k3":
        client = _client(KIMI_BASE, _require("KIMI_API_KEY"))
        model = KIMI_MODEL
        # K3 接口只接受 temperature=1
        temperature = 1
    else:
        raise ProviderError(f"未知的视觉 Provider：{provider}")

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _image_content(images)}],
            temperature=temperature,
        )
        text = resp.choices[0].message.content or ""
    except Exception as exc:
        raise ProviderError(f"视觉模型调用失败：{exc}") from exc

    return _parse_model_json(text, VisionExtraction)


def deepseek_review(pages_text: list[dict]) -> list[TextFinding]:
    """DeepSeek 只复核文本；pages_text 仅含页码与文字，不含图片/路径/密钥。"""
    client = _client(DEEPSEEK_BASE, _require("DEEPSEEK_API_KEY"))
    user_content = DEEPSEEK_PROMPT + "\n\n图纸文字内容：\n" + json.dumps(pages_text, ensure_ascii=False)
    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是严格、只依据给定文字作答的文本复核助手。"},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
        )
        text = resp.choices[0].message.content or ""
    except Exception as exc:
        raise ProviderError(f"DeepSeek 复核失败：{exc}") from exc

    output = _parse_model_json(text, DeepSeekReviewOutput)
    return output.findings
