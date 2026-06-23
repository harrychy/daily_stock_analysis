# -*- coding: utf-8 -*-
"""
===================================
两阶段筛选 API 端点
===================================

POST   /api/v1/screening/technical      启动第一阶段（异步），返回 screening_id
GET    /api/v1/screening/{id}           查筛选结果（轮询兜底，SSE 失败时使用）
POST   /api/v1/screening/{id}/promote   触发第二阶段（按 Top N 提交到 LLM 队列）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.services.screening_service import (
    MAX_SCREENING_BATCH_SIZE,
    get_screening_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------


class StartScreeningRequest(BaseModel):
    stock_codes: List[str] = Field(
        ...,
        description="股票代码列表（A股6位/港股hk前缀/美股代号），同一次最多 200 只",
        min_length=1,
        max_length=MAX_SCREENING_BATCH_SIZE,
    )


class StartScreeningResponse(BaseModel):
    screening_id: str
    parent_task_id: str
    total_count: int


class ScreeningResultItem(BaseModel):
    code: str
    name: str = ""
    signal_score: int = 0
    trend_strength: float = 0.0
    trend_status: str = ""
    buy_signal: str = ""
    ma_alignment: str = ""
    current_price: Optional[float] = None
    volume_ratio: Optional[float] = None
    error_msg: str = ""


class ScreeningStatusResponse(BaseModel):
    screening_id: str
    status: str
    total_count: int
    success_count: int
    failed_count: int
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    results: List[ScreeningResultItem] = Field(default_factory=list)


class PromoteRequest(BaseModel):
    top_n: int = Field(
        ..., ge=1, le=MAX_SCREENING_BATCH_SIZE,
        description="按 signal_score desc 取前 N 只送 LLM",
    )
    report_type: str = Field("detailed", pattern="^(simple|detailed|full|brief)$")


class PromoteAcceptedTask(BaseModel):
    task_id: str
    stock_code: str
    status: str


class PromoteResponse(BaseModel):
    screening_id: str
    requested_top_n: int
    promoted_count: int
    promoted_codes: List[str] = Field(default_factory=list)
    skipped_already_analyzed: List[str] = Field(default_factory=list)
    accepted_tasks: List[PromoteAcceptedTask] = Field(default_factory=list)
    duplicate_tasks: List[Dict[str, Any]] = Field(default_factory=list)


# ------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------


@router.post(
    "/technical",
    response_model=StartScreeningResponse,
    status_code=202,
    summary="启动第一阶段技术面预筛",
    description=(
        "对一批股票仅跑技术面分析（不调 LLM），异步执行。"
        "进度通过现有 SSE 端点 GET /api/v1/analysis/tasks/stream 推送，"
        "事件类型以 `screening_` 开头。"
    ),
)
def start_screening(request: StartScreeningRequest) -> StartScreeningResponse:
    service = get_screening_service()
    try:
        screening_id, task_id = service.start_screening(request.stock_codes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[Screening API] start_screening failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"start_screening failed: {exc}")

    return StartScreeningResponse(
        screening_id=screening_id,
        parent_task_id=task_id,
        total_count=len(request.stock_codes),
    )


@router.get(
    "/{screening_id}",
    response_model=ScreeningStatusResponse,
    summary="查询筛选状态/结果",
    description="返回该批次的最新状态与每只股票的技术面分数（轮询兜底，SSE 不可用时使用）。",
)
def get_screening(screening_id: str) -> ScreeningStatusResponse:
    service = get_screening_service()
    summary = service.get_summary(screening_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"screening_id not found: {screening_id}")

    return ScreeningStatusResponse(
        screening_id=summary.screening_id,
        status=summary.status,
        total_count=summary.total_count,
        success_count=summary.success_count,
        failed_count=summary.failed_count,
        started_at=summary.started_at.isoformat() if summary.started_at else None,
        completed_at=summary.completed_at.isoformat() if summary.completed_at else None,
        results=[ScreeningResultItem(**r.to_dict()) for r in summary.results],
    )


@router.post(
    "/{screening_id}/promote",
    response_model=PromoteResponse,
    summary="提升 Top N 到 LLM 综合分析",
    description=(
        "按 signal_score desc 取前 N 只，过滤当日已跑过 LLM 的股票后，"
        "提交到现有异步分析队列。返回的 task_id 可在 /api/v1/analysis/tasks/stream 跟踪。"
    ),
)
def promote_screening(screening_id: str, request: PromoteRequest) -> PromoteResponse:
    service = get_screening_service()
    try:
        result = service.promote_to_llm(
            screening_id, request.top_n, report_type=request.report_type
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[Screening API] promote failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"promote failed: {exc}")

    return PromoteResponse(**result)
