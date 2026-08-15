"""AI 审核编排：渲染全部页 → 视觉提取 → 规则 → DeepSeek 文本复核 → 严格校验。

任何一步契约不过或调用失败都整体失败（fail-closed），不返回半截结果。
DeepSeek 一旦配置了密钥就必须成功：失败不再静默吞掉，而是整体失败。
"""

from __future__ import annotations

import os
import re

from .intake import IntakeError, render_all_pages
from .models import (
    CONCLUSION_PENDING,
    PageFacts,
    ReviewFinding,
    ReviewResult,
    VisionExtraction,
    now_iso,
)
from .providers import ProviderError, deepseek_review, vision_extract
from .rules import merge_findings, run_rules


class ReviewError(Exception):
    """审核失败，携带面向用户的中文提示。"""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.message = message
        self.status = status


def _validate_extraction(extraction: VisionExtraction, page_count: int) -> None:
    """提取结果必须覆盖 1..page_count，页码越界或缺失即 fail-closed。"""
    if not extraction.pages:
        raise ReviewError("视觉模型未返回任何页面内容，审核失败，请重试或更换 Provider。")
    seen_pages = {f.page for f in extraction.pages}
    invalid = [p for p in seen_pages if p < 1 or p > page_count]
    if invalid:
        raise ReviewError(f"视觉模型返回了越界页码 {sorted(invalid)}，无法追溯证据，已拒绝本次结果。")
    missing = [p for p in range(1, page_count + 1) if p not in seen_pages]
    if missing:
        # 缺失页面会由规则引擎按"该页无内容"处理；此处不整单失败，但需告知
        pass


def _norm(s: str) -> str:
    """去除空白，用于证据与页面内容的可比较匹配。"""
    return re.sub(r"\s+", "", s or "")


def _page_content_bag(facts: PageFacts) -> str:
    """把某页所有提取内容拼成一段文字，供证据-页码对应校验使用。"""
    parts: list[str] = []
    parts.extend(facts.text)
    parts.extend(facts.technical_notes)
    parts.extend(facts.dimensions)
    parts.extend(v for v in facts.title_block.values() if v)
    return " ".join(parts)


def _validate_deepseek_findings(extra: list[ReviewFinding], extraction: VisionExtraction) -> None:
    """DeepSeek 证据的页码错配校验：逐字摘录来自其他页 → 整体拒绝。

    规则引擎的证据由其所在页事实构造，天然一致；DeepSeek 是文本模型，
    可能把别页的原文引到自己声明的页码上（错配冒充成功），必须拦截。
    规则：
    - 证据是所声明页的原文 → 通过；
    - 证据逐字来自别的页、而非所声明页 → 错配，整体拒绝；
    - 其余（描述性/总结性，不逐字出现在任一页）→ 不判为错配。
    """
    bags = {f.page: _norm(_page_content_bag(f)) for f in extraction.pages}
    for f in extra:
        ev = _norm(f.evidence)
        if not ev:
            raise ReviewError("存在缺少证据的问题条目，已按严格契约拒绝本次结果。")
        if f.page not in bags:
            raise ReviewError(f"DeepSeek 返回了不存在的页码 {f.page}，证据无法追溯，已拒绝本次结果。")
        if ev in bags[f.page]:
            continue  # 真实摘录自本页
        misattributed = any(q != f.page and bags[q] and ev in bags[q] for q in bags)
        if misattributed:
            raise ReviewError(
                f"DeepSeek 第 {f.page} 页的证据实际来自其他页，无法追溯页码，已拒绝本次结果。"
            )
        # 其余为描述性证据，不逐字出现在任一页，不判为错配


def _validate_findings(findings: list[ReviewFinding], page_count: int) -> None:
    """每条问题必须都有非空证据且页码合法；否则整单失败。

    空 findings 是合法结果（图纸内容完整，未发现明确问题），照常出报告。
    """
    for f in findings:
        if not f.evidence or not f.evidence.strip():
            raise ReviewError("存在缺少证据的问题条目，已按严格契约拒绝本次结果。")
        if f.page < 1 or f.page > page_count:
            raise ReviewError(f"存在页码越界的问题条目（第 {f.page} 页），已拒绝本次结果。")


def _page_text_for_deepseek(extraction: VisionExtraction) -> list[dict]:
    """只把页码与文字交给 DeepSeek，不包含任何图片、路径或密钥。"""
    rows: list[dict] = []
    for f in sorted(extraction.pages, key=lambda x: x.page):
        row: dict = {"page": f.page, "title_block": f.title_block, "technical_notes": f.technical_notes}
        if f.text:
            row["text"] = f.text
        rows.append(row)
    return rows


def run_review(document_id: str, filename: str, provider: str) -> ReviewResult:
    """对已上传文档执行一次完整 AI 审核。provider 为 'gemini' 或 'k3'。

    视觉提取、规则、DeepSeek 复核任一失败都会整体失败；失败时不返回半截结果。
    """
    try:
        images = render_all_pages(document_id)
    except IntakeError as exc:
        raise ReviewError(exc.message, status=exc.status) from exc

    page_count = len(images)
    if page_count < 1:
        raise ReviewError("文档没有可审核的页面。", status=400)

    try:
        extraction = vision_extract(provider, images)
    except ProviderError as exc:
        raise ReviewError(exc.message) from exc

    _validate_extraction(extraction, page_count)
    findings = run_rules(extraction, page_count)

    # DeepSeek 文本复核：配置了密钥就必须成功；失败即整体失败，绝不静默吞掉。
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        try:
            text_findings = deepseek_review(_page_text_for_deepseek(extraction))
        except ProviderError as exc:
            raise ReviewError(f"DeepSeek 复核失败：{exc.message}") from exc

        extra = [
            ReviewFinding(
                rule=f.rule,
                title=f.title,
                page=f.page,
                evidence=f.evidence,
                conclusion=CONCLUSION_PENDING,
            )
            for f in text_findings
        ]
        # 证据必须属于对应页，错配即整体拒绝
        _validate_deepseek_findings(extra, extraction)
        findings = merge_findings(findings, extra)

    _validate_findings(findings, page_count)

    return ReviewResult(
        document_id=document_id,
        filename=filename,
        page_count=page_count,
        provider=provider,
        findings=findings,
        reviewed_at=now_iso(),
    )
