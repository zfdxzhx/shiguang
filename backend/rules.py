"""审核规则引擎：基于视觉提取的事实，产出需工程确认的问题清单。

每条问题必须携带页码与非空证据（取自真实提取结果，不编造）。
"""

from __future__ import annotations

from .models import CONCLUSION_PENDING, ReviewFinding, VisionExtraction

REQUIRED_TITLE_FIELDS = ("图号", "名称", "材料", "比例")


def _finding(rule: str, title: str, page: int, evidence: str) -> ReviewFinding:
    return ReviewFinding(
        rule=rule,
        title=title,
        page=page,
        evidence=evidence,
        conclusion=CONCLUSION_PENDING,
    )


def run_rules(extraction: VisionExtraction, page_count: int) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    by_page = {f.page: f for f in extraction.pages}

    for page_no in range(1, page_count + 1):
        facts = by_page.get(page_no)
        if facts is None:
            # 该页没有任何提取事实：不猜测，标记为需人工查看
            findings.append(
                _finding(
                    "content",
                    "该页未能提取到任何内容，需人工查看",
                    page_no,
                    f"（第 {page_no} 页未提取到文字或尺寸，需人工查看）",
                )
            )
            continue

        # 规则 1：标题栏完整性
        missing = [f for f in REQUIRED_TITLE_FIELDS if not facts.title_block.get(f, "").strip()]
        if missing:
            title_text = "；".join(f"{k}:{v}" for k, v in facts.title_block.items()) or "（标题栏为空）"
            findings.append(
                _finding(
                    "title_block",
                    f"标题栏缺少字段：{'、'.join(missing)}",
                    page_no,
                    title_text[:200],
                )
            )

        # 规则 2：尺寸标注
        if not facts.dimensions:
            findings.append(
                _finding(
                    "dimensions",
                    "未提取到尺寸标注，需确认是否遗漏",
                    page_no,
                    "（该页未提取到任何尺寸标注文字）",
                )
            )

        # 规则 3：技术要求
        if not facts.technical_notes:
            findings.append(
                _finding(
                    "technical_notes",
                    "未提取到技术要求，需确认是否遗漏",
                    page_no,
                    "（该页未提取到技术要求文字）",
                )
            )

        # 规则 4：视图/内容表达
        if not facts.text and not facts.dimensions and not facts.technical_notes:
            findings.append(
                _finding(
                    "content",
                    "该页没有可识别的图纸内容，需人工查看",
                    page_no,
                    "（该页未提取到文字、尺寸或技术要求）",
                )
            )

        # 规则 5：材料标注
        material = (facts.title_block.get("材料") or "").strip()
        if material in {"", "未标注", "无"}:
            findings.append(
                _finding(
                    "material",
                    "未标注材料，需确认",
                    page_no,
                    (facts.title_block.get("材料") or "").strip() or "（标题栏未给出材料）",
                )
            )

    return findings


def merge_findings(primary: list[ReviewFinding], extra: list[ReviewFinding]) -> list[ReviewFinding]:
    """合并规则发现与 DeepSeek 文本复核发现，按 (规则, 页码, 标题) 去重。"""
    seen: set[tuple[str, int, str]] = set()
    merged: list[ReviewFinding] = []
    for item in [*primary, *extra]:
        key = (item.rule, item.page, item.title)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged
