"""严格结构化契约：模型输出视为不可信输入，一律 fail-closed。

视觉模型只允许输出本模块定义的字段（extra="forbid"）；
缺少证据、页码越界或内容不完整的条目会被拒绝，不当作有效结果。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 统一结论：课堂审核草稿，需工程确认，不冒充正式批准
CONCLUSION_PENDING = "待工程确认"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageFacts(StrictModel):
    """视觉模型从某一页图纸提取出的事实。"""

    page: int = Field(gt=0, description="页码，从 1 开始")
    text: list[str] = Field(default_factory=list, description="页面上识别到的文字行")
    dimensions: list[str] = Field(default_factory=list, description="尺寸标注")
    title_block: dict[str, str] = Field(default_factory=dict, description="标题栏字段")
    technical_notes: list[str] = Field(default_factory=list, description="技术要求/备注")


class VisionExtraction(StrictModel):
    """视觉模型对整份图纸的提取结果。"""

    pages: list[PageFacts]


class TextFinding(StrictModel):
    """DeepSeek 文本复核产出的问题（只针对文字类要求）。"""

    page: int = Field(gt=0)
    rule: str = Field(min_length=1)
    title: str = Field(min_length=1)
    evidence: str = Field(min_length=1, description="必须引用真实文字摘录")


class DeepSeekReviewOutput(StrictModel):
    findings: list[TextFinding]


class ReviewFinding(StrictModel):
    """最终呈现给用户的一条审核问题：必须有页码与证据，结论统一为「待工程确认」。"""

    rule: str = Field(min_length=1)
    title: str = Field(min_length=1)
    page: int = Field(gt=0)
    evidence: str = Field(min_length=1, description="证据摘录，非空")
    conclusion: str = CONCLUSION_PENDING


class ReviewResult(StrictModel):
    document_id: str
    filename: str
    page_count: int
    provider: str
    findings: list[ReviewFinding]
    reviewed_at: str

    @field_validator("reviewed_at")
    @classmethod
    def _default_now(cls, v: str) -> str:
        if not v:
            return datetime.now(timezone.utc).isoformat()
        return v


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
