"""第 3 步离线审核测试：mock 视觉/DeepSeek，验证严格契约与报告生成。

覆盖：
1. 成功路径：mock 视觉+DeepSeek，返回每条含页码/证据/「待工程确认」
2. fail-closed：越界页码、空页面、非契约 JSON、空问题、空证据 → 一律明确失败
3. 《图纸 AI 审核报告》PDF 生成并含页数与结论
4. HTTP 全流程（TestClient）：上传 → 审核 → 下载报告，失败时旧结果被清空语义由前端保证
"""

from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "samples" / "课堂图纸样张.pdf"

from backend.engineering_review import ReviewError, run_review  # noqa: E402
from backend.intake import ingest_pdf  # noqa: E402
from backend.models import CONCLUSION_PENDING, VisionExtraction  # noqa: E402
from backend.pdf_report import build_report_pdf  # noqa: E402
from backend.providers import ProviderError  # noqa: E402
import backend.engineering_review as eng  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}  {detail}")


def build_extraction(pages: list[dict]) -> VisionExtraction:
    return VisionExtraction.model_validate({"pages": pages})


def valid_page(page: int) -> dict:
    """一页完整、不含任何规则问题的图纸事实（标题栏齐全+尺寸+技术要求+内容）。"""
    return {
        "page": page,
        "text": ["零件加工图", "技术要求见下"],
        "dimensions": ["Ø80 ±0.05", "100"],
        "title_block": {"图号": "KQ-001", "名称": "支架", "材料": "Q235", "比例": "1:2"},
        "technical_notes": ["未注圆角 R3", "去毛刺"],
    }


def flawed_page(page: int) -> dict:
    """一页含有规则问题的图纸事实：标题栏缺材料、无尺寸标注，规则必产出 findings。"""
    return {
        "page": page,
        "text": ["零件加工图"],
        "dimensions": [],
        "title_block": {"图号": "KQ-002", "名称": "底座", "材料": "", "比例": "1:1"},
        "technical_notes": ["倒角 1×45°"],
    }


def sample_doc() -> str:
    """真正摄入课堂样张，返回可用的 document_id（供 render_all_pages）。"""
    raw = SAMPLE.read_bytes()
    meta = ingest_pdf(raw, SAMPLE.name)
    return meta["document_id"]


def marked_page(page: int, marker: str) -> dict:
    """内容完整但带唯一标记的页，用于验证证据-页码对应关系。"""
    return {
        "page": page,
        "text": [marker],
        "dimensions": ["Ø80 ±0.05"],
        "title_block": {"图号": "KQ", "名称": "支架", "材料": "Q235", "比例": "1:2"},
        "technical_notes": [f"技术要求 {marker}"],
    }


_ORIG_DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY")
_TEST_KEY = "test-dummy-key-for-offline-tests"


def with_mock_vision(payload):
    """monkeypatch 视觉提取：模拟真实 providers.vision_extract 的严格校验语义。"""

    def fake(provider, images):
        try:
            return payload if isinstance(payload, VisionExtraction) else VisionExtraction.model_validate(payload)
        except Exception as exc:
            # 与真实 _parse_model_json 一致：契约失败抛 ProviderError
            from backend.providers import ProviderError as PE
            raise PE(f"模型输出不符合契约（{exc}），已按严格契约拒绝，不当作有效结果。") from exc

    eng.vision_extract = fake


def with_mock_deepseek(findings):
    """monkeypatch DeepSeek 复核；同时设 dummy 密钥让可选复核分支真实执行。

    与真实 providers.deepseek_review 一致：返回 TextFinding 实例。
    """
    os.environ["DEEPSEEK_API_KEY"] = _TEST_KEY

    def fake(pages_text):
        from backend.models import TextFinding
        return [TextFinding.model_validate(f) for f in findings]

    eng.deepseek_review = fake


def restore():
    # 恢复原始实现与密钥环境，避免测试间互相污染
    import backend.providers as prov
    eng.vision_extract = prov.vision_extract
    eng.deepseek_review = prov.deepseek_review
    if _ORIG_DEEPSEEK_KEY is None:
        os.environ.pop("DEEPSEEK_API_KEY", None)
    else:
        os.environ["DEEPSEEK_API_KEY"] = _ORIG_DEEPSEEK_KEY


def test_success() -> None:
    with_mock_vision({"pages": [valid_page(1), valid_page(2), valid_page(3)]})
    # 证据必须真实摘录自该页内容（与 DeepSeek 提示词要求一致）
    with_mock_deepseek(
        [{"page": 1, "rule": "technical", "title": "技术要求未见明确粗糙度", "evidence": "未注圆角 R3"}]
    )
    result = run_review(sample_doc(), "课堂图纸样张.pdf", "gemini")
    check("成功：3 页有效结果", result.page_count == 3, f"page_count={result.page_count}")
    check("成功：findings 非空", len(result.findings) >= 1, f"n={len(result.findings)}")
    for f in result.findings:
        check("每条页码在 1..3", 1 <= f.page <= 3, f"page={f.page}")
        check("每条证据非空", bool(f.evidence.strip()), f"evidence={f.evidence!r}")
        check("结论=待工程确认", f.conclusion == CONCLUSION_PENDING, f"conclusion={f.conclusion!r}")
        check("provider 透传", result.provider == "gemini", result.provider)
    check("DeepSeek 新增条目已并入", any(f.rule == "technical" for f in result.findings))
    restore()


def test_fail_out_of_range_page() -> None:
    with_mock_vision({"pages": [valid_page(1), {"page": 9, "text": [], "dimensions": [], "title_block": {}, "technical_notes": []}]})
    try:
        run_review(sample_doc(), "x.pdf", "gemini")
        check("越界页码被拒", False, "未抛错")
    except ReviewError as exc:
        check("越界页码被拒", "越界" in exc.message or "页码" in exc.message, exc.message)
    finally:
        restore()


def test_fail_empty_pages() -> None:
    with_mock_vision({"pages": []})
    try:
        run_review(sample_doc(), "x.pdf", "gemini")
        check("空页面被拒", False, "未抛错")
    except ReviewError as exc:
        check("空页面被拒", "任何页面内容" in exc.message, exc.message)
    finally:
        restore()


def test_fail_strict_schema() -> None:
    # 额外字段 extra="forbid" 会触发契约拒绝
    with_mock_vision({"pages": [valid_page(1), valid_page(2)], "unexpected_extra": True})
    try:
        run_review(sample_doc(), "x.pdf", "gemini")
        check("非契约 JSON 被拒", False, "未抛错")
    except ReviewError as exc:
        check("非契约 JSON 被拒", "契约" in exc.message, exc.message)
    finally:
        restore()


def test_empty_findings_is_valid() -> None:
    # 页面全部完整 → 规则不产出问题：这是合法结果（图纸内容完整），照常出报告
    with_mock_vision({"pages": [valid_page(1), valid_page(2), valid_page(3)]})
    with_mock_deepseek([])
    result = run_review(sample_doc(), "x.pdf", "gemini")
    check("空 findings 是合法结果", result.findings == [], f"n={len(result.findings)}")
    pdf = build_report_pdf(result)
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    doc.close()
    check("空 findings 报告可生成", "共 0 条待确认问题" in text, text[:100])
    restore()


def test_fail_empty_evidence_from_deepseek() -> None:
    # DeepSeek 返回空证据条目 → 合并后校验拒绝
    with_mock_vision({"pages": [valid_page(1)]})
    with_mock_deepseek([{"page": 1, "rule": "technical", "title": "可疑条目", "evidence": "  "}])
    try:
        run_review(sample_doc(), "x.pdf", "gemini")
        check("空证据被拒", False, "未抛错")
    except ReviewError as exc:
        check("空证据被拒", "缺少证据" in exc.message, exc.message)
    finally:
        restore()


def test_deepseek_missing_key_is_skipped() -> None:
    # 未配置 DEEPSEEK_API_KEY 时，可选复核被跳过，主结果仍完整
    import os
    old = os.environ.get("DEEPSEEK_API_KEY")
    os.environ.pop("DEEPSEEK_API_KEY", None)
    with_mock_vision({"pages": [flawed_page(1), flawed_page(2)]})
    # 即便 mock 会抛错，也不该被调用；这里让 mock 抛错来验证"未被调用"
    def should_not_run(pages_text):
        raise AssertionError("deepseek_review 不应在无密钥时被调用")
    eng.deepseek_review = should_not_run
    result = run_review(sample_doc(), "x.pdf", "k3")
    check("无密钥时跳过 DeepSeek 复核", len(result.findings) >= 1, f"n={len(result.findings)}")
    if old is not None:
        os.environ["DEEPSEEK_API_KEY"] = old
    restore()


def test_default_provider_uses_configured_key() -> None:
    # Codex #1：只配置 KIMI 时，默认组合应为 K3，Gemini 组合标为不可用
    from fastapi.testclient import TestClient
    from backend.app import create_application

    os.environ.pop("GEMINI_API_KEY", None)
    os.environ["KIMI_API_KEY"] = "test-kimi"
    app = create_application(Path(ROOT) / "static")
    client = TestClient(app)
    data = client.get("/api/v1/settings/providers").json()
    check("默认组合=已配置的 K3", data.get("default") == "k3-deepseek", str(data))
    by_id = {p["id"]: p for p in data.get("providers", [])}
    check("K3 组合可用", by_id.get("k3-deepseek", {}).get("enabled") is True)
    check("Gemini 组合不可用", by_id.get("gemini-deepseek", {}).get("enabled") is False)
    # 响应不含密钥
    txt = client.get("/api/v1/settings/providers").content.decode("utf-8", "ignore")
    check("设置响应不含密钥", "sk-" not in txt and "vision_key" not in txt, txt[:200])
    os.environ.pop("KIMI_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)


def test_fail_deepseek_evidence_wrong_page() -> None:
    # Codex #2：DeepSeek 把第 3 页证据标成第 1 页问题 → 整体拒绝
    with_mock_vision(
        {
            "pages": [
                marked_page(1, "第一页专属内容"),
                marked_page(2, "第二页专属内容"),
                marked_page(3, "第三页专属内容"),
            ]
        }
    )
    with_mock_deepseek([{"page": 1, "rule": "technical", "title": "错配条目", "evidence": "第三页专属内容"}])
    try:
        run_review(sample_doc(), "x.pdf", "gemini")
        check("证据页码错配被拒", False, "未抛错")
    except ReviewError as exc:
        check("证据页码错配被拒", "页码" in exc.message, exc.message)
    finally:
        restore()


def test_deepseek_evidence_matches_page() -> None:
    # 正例：DeepSeek 证据属于本页时通过
    with_mock_vision({"pages": [marked_page(1, "第一页专属内容"), marked_page(2, "第二页专属内容")]})
    with_mock_deepseek([{"page": 2, "rule": "technical", "title": "正确条目", "evidence": "第二页专属内容"}])
    result = run_review(sample_doc(), "x.pdf", "gemini")
    check("证据属于本页时通过", any(f.title == "正确条目" for f in result.findings))
    restore()


def test_fail_deepseek_call_failure() -> None:
    # Codex #3：配置了 DeepSeek 但调用失败 → 整体失败，不再静默吞掉
    from backend.providers import ProviderError as PE

    with_mock_vision({"pages": [flawed_page(1)]})
    os.environ["DEEPSEEK_API_KEY"] = _TEST_KEY

    def boom(pages_text):
        raise PE("模拟 DeepSeek 服务不可用")

    eng.deepseek_review = boom
    try:
        run_review(sample_doc(), "x.pdf", "gemini")
        check("DeepSeek 失败不静默", False, "未抛错")
    except ReviewError as exc:
        check("DeepSeek 失败不静默", "DeepSeek 复核失败" in exc.message, exc.message)
    finally:
        restore()


def test_fail_after_success_report_unavailable() -> None:
    # Codex #4：同一文档先成功审核，再失败审核 → 旧报告不可下载
    from fastapi.testclient import TestClient
    from backend.app import create_application
    from backend.providers import ProviderError as PE

    app = create_application(Path(ROOT) / "static")
    client = TestClient(app)
    with open(SAMPLE, "rb") as fh:
        resp = client.post("/api/v1/documents", files={"file": ("x.pdf", fh, "application/pdf")})
    doc_id = resp.json()["document"]["document_id"]

    with_mock_vision({"pages": [flawed_page(1), flawed_page(2), flawed_page(3)]})
    with_mock_deepseek([])
    r1 = client.post(f"/api/v1/documents/{doc_id}/review", json={"provider": "k3-deepseek"})
    check("第一次审核成功", r1.status_code == 200, f"status={r1.status_code}")
    r_ok = client.get(f"/api/v1/documents/{doc_id}/report")
    check("成功后报告可下载", r_ok.status_code == 200, f"status={r_ok.status_code}")

    def boom(provider, images):
        raise PE("模拟视觉模型故障")

    eng.vision_extract = boom
    r2 = client.post(f"/api/v1/documents/{doc_id}/review", json={"provider": "k3-deepseek"})
    check("第二次审核明确失败", r2.status_code == 500, f"status={r2.status_code}")
    r_fail = client.get(f"/api/v1/documents/{doc_id}/report")
    check("失败后旧报告不可下载", r_fail.status_code == 400, f"status={r_fail.status_code}, body={r_fail.text[:120]}")
    restore()


def test_report_pdf() -> None:
    with_mock_vision({"pages": [flawed_page(1), flawed_page(2)]})
    with_mock_deepseek([])
    result = run_review(sample_doc(), "课堂图纸样张.pdf", "gemini")
    pdf = build_report_pdf(result)
    check("报告以 %PDF 开头", pdf[:5] == b"%PDF-", str(pdf[:5]))
    check("报告非空", len(pdf) > 3000, f"bytes={len(pdf)}")
    # reportlab 用 CID 字体编码中文，需用 PyMuPDF 抽取文本校验内容
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    doc.close()
    check("报告标题含中文", "图纸 AI 审核报告" in text, text[:80])
    check("结论含待工程确认", "待工程确认" in text, text[:120])
    check("报告含文件与页数", "课堂图纸样张" in text and "2" in text, text[:120])
    check("报告含免责声明", "不构成正式工程批准" in text, text[-200:])
    restore()


def test_http_flow() -> None:
    # 用 TestClient 走完整 HTTP 流程
    from fastapi.testclient import TestClient
    from backend.app import create_application

    # 用含规则问题的页面，确保审核产出 findings（不依赖 DeepSeek）
    with_mock_vision({"pages": [flawed_page(1), flawed_page(2), flawed_page(3)]})
    with_mock_deepseek([])

    app = create_application(Path(ROOT) / "static")
    client = TestClient(app)

    # 上传
    with open(SAMPLE, "rb") as fh:
        resp = client.post("/api/v1/documents", files={"file": (SAMPLE.name, fh, "application/pdf")})
    check("HTTP 上传 200", resp.status_code == 200, f"status={resp.status_code}")
    doc_id = resp.json()["document"]["document_id"]

    # 未知组合
    resp = client.post(f"/api/v1/documents/{doc_id}/review", json={"provider": "nope"})
    check("未知组合 400", resp.status_code == 400, f"status={resp.status_code}")

    # 审核（mock）
    resp = client.post(f"/api/v1/documents/{doc_id}/review", json={"provider": "gemini-deepseek"})
    data = resp.json()
    check("HTTP 审核 200", resp.status_code == 200, f"status={resp.status_code}, body={str(data)[:200]}")
    result = data.get("result", {})
    check("HTTP 结果含 findings", isinstance(result.get("findings"), list) and len(result["findings"]) >= 1,
          f"n={len(result.get('findings', []))}")
    for f in result.get("findings", []):
        check("HTTP 每条有页码", f.get("page", 0) >= 1, str(f))
        check("HTTP 每条有证据", bool((f.get("evidence") or "").strip()), str(f))
        check("HTTP 每条结论待工程确认", f.get("conclusion") == CONCLUSION_PENDING, str(f))

    # 未审核先下载报告（新文档）应 400
    with open(SAMPLE, "rb") as fh:
        resp = client.post("/api/v1/documents", files={"file": ("other.pdf", fh, "application/pdf")})
    doc2 = resp.json()["document"]["document_id"]
    resp = client.get(f"/api/v1/documents/{doc2}/report")
    check("未审核下载报告 400", resp.status_code == 400, f"status={resp.status_code}")

    # 审核后下载报告
    resp = client.get(f"/api/v1/documents/{doc_id}/report")
    check("报告下载 200 且为 PDF", resp.status_code == 200 and resp.headers.get("content-type") == "application/pdf",
          f"status={resp.status_code}, ct={resp.headers.get('content-type')}")
    check("报告下载内容为 PDF", resp.content[:5] == b"%PDF-", str(resp.content[:5]))
    disp = resp.headers.get("content-disposition", "")
    check("报告下载带附件文件名", "attachment" in disp and "filename*=UTF-8''" in disp and "%E5%9B%BE%E7%BA%B8" in disp, disp)

    # 隐私：上传/审核/报告响应都不含本机路径
    for path in (f"/api/v1/documents/{doc_id}", f"/api/v1/documents/{doc_id}/review"):
        r = client.get(path) if path.endswith("documents/%s" % doc_id) else client.post(path, json={"provider": "gemini-deepseek"})
        txt = r.content.decode("utf-8", "ignore")
        check(f"响应无本机路径 {path}", "D:\\" not in txt and "\\0815\\" not in txt, txt[:120])
    restore()


def test_review_state_refresh() -> None:
    # 第 4 步：刷新恢复——GET /documents/{id}/review 返回元信息 + 最近审核结果（null 或结果）
    from fastapi.testclient import TestClient
    from backend.app import create_application

    with_mock_vision({"pages": [flawed_page(1), flawed_page(2), flawed_page(3)]})
    with_mock_deepseek([])

    app = create_application(Path(ROOT) / "static")
    client = TestClient(app)

    with open(SAMPLE, "rb") as fh:
        resp = client.post("/api/v1/documents", files={"file": (SAMPLE.name, fh, "application/pdf")})
    doc_id = resp.json()["document"]["document_id"]

    # 上传后未审核：review 为 null，document 元信息可恢复
    r0 = client.get(f"/api/v1/documents/{doc_id}/review")
    j0 = r0.json()
    check("未审核时 review=null", r0.status_code == 200 and j0.get("review") is None,
          f"status={r0.status_code}, body={r0.text[:120]}")
    check("恢复响应含元信息", j0.get("document", {}).get("document_id") == doc_id, r0.text[:120])

    # 审核后：review 返回结果
    r1 = client.post(f"/api/v1/documents/{doc_id}/review", json={"provider": "k3-deepseek"})
    check("刷新用例：审核成功", r1.status_code == 200, f"status={r1.status_code}")
    r2 = client.get(f"/api/v1/documents/{doc_id}/review")
    body = r2.json()
    check("审核后 review 有结果", r2.status_code == 200 and body.get("review") is not None,
          f"status={r2.status_code}, body={r2.text[:120]}")
    check("恢复结果含 findings", isinstance(body.get("review", {}).get("findings"), list)
          and len(body["review"]["findings"]) >= 1, str(body.get("review", {}))[:150])

    # 失效 id → 404（前端据此清除本地会话回到首页）
    r3 = client.get("/api/v1/documents/000000000000/review")
    check("失效文档 404", r3.status_code == 404, f"status={r3.status_code}")

    # 隐私：恢复响应不含本机路径与密钥
    txt = r2.content.decode("utf-8", "ignore")
    check("恢复响应无路径无密钥", "D:\\" not in txt and "0815" not in txt and "sk-" not in txt, txt[:150])
    restore()


def main() -> None:
    if not SAMPLE.is_file():
        print(f"SKIP 缺少样张 {SAMPLE}")
        raise SystemExit(0)
    for name, fn in [
        ("成功路径", test_success),
        ("越界页码 fail-closed", test_fail_out_of_range_page),
        ("空页面 fail-closed", test_fail_empty_pages),
        ("非契约 JSON fail-closed", test_fail_strict_schema),
        ("空 findings 为合法结果", test_empty_findings_is_valid),
        ("空证据 fail-closed", test_fail_empty_evidence_from_deepseek),
        ("无密钥跳过 DeepSeek", test_deepseek_missing_key_is_skipped),
        ("默认组合按已配置密钥", test_default_provider_uses_configured_key),
        ("证据页码错配被拒", test_fail_deepseek_evidence_wrong_page),
        ("证据属于本页时通过", test_deepseek_evidence_matches_page),
        ("DeepSeek 失败不静默", test_fail_deepseek_call_failure),
        ("失败后旧报告不可下载", test_fail_after_success_report_unavailable),
        ("报告 PDF 生成", test_report_pdf),
        ("HTTP 全流程", test_http_flow),
        ("刷新后恢复审核状态", test_review_state_refresh),
    ]:
        try:
            fn()
        except Exception as exc:
            check(f"{name} 无异常", False, f"{exc}\n{traceback.format_exc()}")

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
