"""FastAPI 入口：任务创建 / 结果查询。"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import BackgroundTasks, FastAPI, HTTPException

from . import db
from .quality import build_quality_report
from .schemas import QualityReport, SortRequest, TaskCreated, TaskStatus
from .worker import process_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动即建库建表，避免首个请求延迟
    db.get_engine()
    yield


app = FastAPI(
    title="OCR 版面排序服务",
    description=(
        "提交页面宽高与带四角坐标的 OCR 词框，服务按几何位置自动聚类文本行，"
        "在倾斜、行距不均、双栏版面下输出稳定的阅读顺序。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/tasks", response_model=TaskCreated, status_code=201)
def create_task(req: SortRequest, background: BackgroundTasks) -> TaskCreated:
    """创建排序任务，立即返回任务 ID，排序在线程池后台执行。"""
    task_id = uuid.uuid4().hex
    db.create_task(task_id, req.model_dump_json())
    background.add_task(process_task, task_id)
    return TaskCreated(task_id=task_id, status="pending")


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    result = None
    if row.get("result_json"):
        result = json.loads(row["result_json"])

    def iso(v):
        return v.isoformat() if v is not None else None

    return {
        "task_id": row["task_id"],
        "status": row["status"],
        "created_at": iso(row.get("created_at")),
        "started_at": iso(row.get("started_at")),
        "finished_at": iso(row.get("finished_at")),
        "error": row.get("error"),
        "result": result,
    }


@app.get("/api/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: str) -> Dict[str, Any]:
    """查询任务状态；done 时 result 内含行分组、有序文本与无法归类词框。"""
    row = db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _serialize(row)


@app.post("/api/tasks/{task_id}/quality-report",
          response_model=QualityReport)
def quality_report(task_id: str) -> Dict[str, Any]:
    """对已完成任务生成词框质量报告。

    纯只读操作：重新测量已完成结果中的原始词框与行分组，按确定性规则
    （置信度、行内字号中位数/MAD、重叠、行内间距）识别异常词框，
    不修改、不重算任务结果。任务不存在返回 404，未完成返回 409。
    """
    row = db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    if row["status"] != "done" or not row.get("result_json"):
        raise HTTPException(
            status_code=409,
            detail=f"task not ready (status={row['status']})",
        )
    return build_quality_report(task_id, json.loads(row["result_json"]))
