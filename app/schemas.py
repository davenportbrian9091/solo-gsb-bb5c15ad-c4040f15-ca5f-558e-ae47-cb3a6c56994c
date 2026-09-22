"""请求 / 响应数据模型。"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class WordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = Field(default=None, description="词框自定义 ID，缺省使用提交序号")
    text: str = Field(..., description="OCR 识别文本")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                        description="置信度 [0,1]")
    # 四角点，顺序为左上、右上、右下、左下（顺序不强制，服务端自动归一化）
    quad: List[Point] = Field(..., min_length=4, max_length=4)

    @field_validator("quad")
    @classmethod
    def _four_points(cls, v: List[Point]) -> List[Point]:
        if len(v) != 4:
            raise ValueError("quad 必须包含 4 个角点")
        return v


class SortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_width: float = Field(..., gt=0, description="页面宽度（像素/点）")
    page_height: float = Field(..., gt=0, description="页面高度（像素/点）")
    words: List[WordIn] = Field(default_factory=list, description="OCR 词框列表")


class TaskCreated(BaseModel):
    task_id: str
    status: str


class UnclassifiedWord(BaseModel):
    id: object
    text: str
    confidence: Optional[float]
    quad: list
    reason: str


class TaskStatus(BaseModel):
    task_id: str
    status: str  # pending | processing | done | failed
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    result: Optional[dict] = None


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word_id: object = Field(..., description="异常词框的原始 id")
    line_id: object = Field(..., description="词框所属文本行 id（排序结果中的行号）")
    kind: str = Field(..., description="异常类别：low_confidence / size_outlier / "
                                       "overlap / gap_anomaly")
    severity: str = Field(..., description="严重程度：major / minor")
    reason: str = Field(..., description="确定性规则给出的判定依据（含测量值与阈值）")
    suggestion: str = Field(..., description="建议的人工处理方式")


class QualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_issues: int
    words_with_issues: int
    scanned_words: int
    scanned_lines: int
    unclassified_words: int
    by_kind: Dict[str, int]
    by_severity: Dict[str, int]


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    rules_version: str
    issues: List[QualityIssue]
    summary: QualitySummary
