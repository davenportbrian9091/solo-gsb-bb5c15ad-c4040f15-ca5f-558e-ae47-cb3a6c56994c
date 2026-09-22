"""请求 / 响应数据模型。"""
from __future__ import annotations

from typing import List, Optional

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
