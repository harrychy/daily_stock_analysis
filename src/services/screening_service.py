# -*- coding: utf-8 -*-
"""
===================================
两阶段筛选 - 编排服务
===================================

职责：
1. start_screening: 把第一阶段（技术面预筛）包成 task_queue 的父 task，跑批
2. promote_to_llm: 取 Top N + 过滤当日已分析 → 提交到现有 LLM 异步链路

设计要点（参见 docs/two-stage-screening.md 与 .claude/plans/zippy-hugging-thunder.md）：
- 复用 task_queue 的 submit_background_task 拿父 task；不为每只股票起独立 task
- on_score 回调通过 task_queue._broadcast_event 推自定义 SSE 事件
- 父 task 主键用 f"screening:{screening_id}" 避开 _analyzing_stocks dedupe
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config, get_config
from src.core.screening import ScreeningResult, ScreeningRunSummary
from src.repositories.screening_repo import ScreeningRepository

logger = logging.getLogger(__name__)

# 单次筛选最大股票数（防止一次性把数据源打爆；参考 analyze API 的 MAX_BATCH_SIZE=50 适度放宽）
MAX_SCREENING_BATCH_SIZE = 200


class ScreeningService:
    """两阶段筛选编排服务（无状态，可单例使用）。"""

    def __init__(
        self,
        config: Optional[Config] = None,
        repo: Optional[ScreeningRepository] = None,
    ):
        self.config = config or get_config()
        self.repo = repo or ScreeningRepository()

        # 进程内 in-memory 缓存（用于 GET /screening/{id} 即时返回，无需查 DB）
        # key: screening_id, value: ScreeningRunSummary
        self._runs: Dict[str, ScreeningRunSummary] = {}
        self._runs_lock = threading.Lock()

    # ------------------------------------------------------------
    # 第一阶段：启动技术面预筛
    # ------------------------------------------------------------

    def start_screening(self, stock_codes: List[str]) -> Tuple[str, str]:
        """
        启动一次技术面预筛。

        Returns:
            (screening_id, parent_task_id)
        """
        # 输入校验
        if not stock_codes:
            raise ValueError("stock_codes is empty")
        if len(stock_codes) > MAX_SCREENING_BATCH_SIZE:
            raise ValueError(
                f"stock_codes too large: {len(stock_codes)} > {MAX_SCREENING_BATCH_SIZE}"
            )

        screening_id = uuid.uuid4().hex[:12]

        # 初始化进程内 summary
        summary = ScreeningRunSummary(
            screening_id=screening_id,
            status="pending",
            total_count=len(stock_codes),
            started_at=datetime.now(),
        )
        with self._runs_lock:
            self._runs[screening_id] = summary

        # 通过 task_queue 调度父 task
        from src.services.task_queue import get_task_queue

        task_queue = get_task_queue()

        def _run() -> Dict[str, Any]:
            return self._execute_screening(screening_id, stock_codes, task_queue)

        parent_task = task_queue.submit_background_task(
            run_task=_run,
            # R1: 用 screening:<id> 唯一化，避开 _analyzing_stocks 的 stock_code 主键去重
            stock_code=f"screening:{screening_id}",
            stock_name=f"两阶段筛选 ({len(stock_codes)} 只)",
            report_type="screening",
            message=f"已加入队列，准备扫描 {len(stock_codes)} 只股票",
        )

        logger.info(
            "[Screening] 启动技术面预筛 screening_id=%s task_id=%s 数量=%d",
            screening_id,
            parent_task.task_id,
            len(stock_codes),
        )

        # 推一条 screening_started 事件
        task_queue._broadcast_event(
            "screening_started",
            {
                "screening_id": screening_id,
                "task_id": parent_task.task_id,
                "total_count": len(stock_codes),
                "started_at": summary.started_at.isoformat(),
            },
        )

        return screening_id, parent_task.task_id

    def _execute_screening(
        self,
        screening_id: str,
        stock_codes: List[str],
        task_queue: Any,
    ) -> Dict[str, Any]:
        """实际跑技术面预筛的工作函数（在 task_queue worker 线程里执行）。"""
        from src.core.pipeline import StockAnalysisPipeline

        # 标记 running
        with self._runs_lock:
            summary = self._runs[screening_id]
            summary.status = "running"

        pipeline = StockAnalysisPipeline()

        def _on_score(result: ScreeningResult) -> None:
            """单只股票完成时：累加 in-memory summary + 推 SSE 事件。"""
            with self._runs_lock:
                summary.results.append(result)
                if result.is_success():
                    summary.success_count += 1
                else:
                    summary.failed_count += 1
                progress = int(
                    100 * (summary.success_count + summary.failed_count) / max(1, summary.total_count)
                )
            task_queue._broadcast_event(
                "screening_score",
                {
                    "screening_id": screening_id,
                    "code": result.code,
                    "name": result.name,
                    "signal_score": result.signal_score,
                    "trend_strength": result.trend_strength,
                    "trend_status": result.trend_status,
                    "buy_signal": result.buy_signal,
                    "ma_alignment": result.ma_alignment,
                    "current_price": result.current_price,
                    "volume_ratio": result.volume_ratio,
                    "error_msg": result.error_msg,
                    "progress": progress,
                },
            )

        try:
            results = pipeline.run_technical_screening(stock_codes, on_score=_on_score)

            # 落库
            saved_count = self.repo.save_batch(screening_id, results)
            logger.info(
                "[Screening] 落库 screening_id=%s 写入=%d/%d",
                screening_id,
                saved_count,
                len(results),
            )

            with self._runs_lock:
                summary.status = "completed"
                summary.completed_at = datetime.now()

            task_queue._broadcast_event(
                "screening_completed",
                {
                    "screening_id": screening_id,
                    "total_count": summary.total_count,
                    "success_count": summary.success_count,
                    "failed_count": summary.failed_count,
                    "saved_count": saved_count,
                    "completed_at": summary.completed_at.isoformat(),
                },
            )
            return {
                "screening_id": screening_id,
                "success_count": summary.success_count,
                "failed_count": summary.failed_count,
                "saved_count": saved_count,
            }
        except Exception as exc:
            logger.error("[Screening] 预筛失败 screening_id=%s error=%s", screening_id, exc, exc_info=True)
            with self._runs_lock:
                summary.status = "failed"
                summary.completed_at = datetime.now()
            task_queue._broadcast_event(
                "screening_failed",
                {"screening_id": screening_id, "error": str(exc)},
            )
            raise

    # ------------------------------------------------------------
    # GET 接口：返回筛选状态/结果（轮询兜底）
    # ------------------------------------------------------------

    def get_summary(self, screening_id: str) -> Optional[ScreeningRunSummary]:
        """优先从内存取；不存在时尝试从 DB 读历史。"""
        with self._runs_lock:
            cached = self._runs.get(screening_id)
        if cached is not None:
            return cached

        # DB 兜底（进程重启或老的 screening_id）
        rows = self.repo.get_by_screening_id(screening_id)
        if not rows:
            return None
        results = [
            ScreeningResult(
                code=r.code,
                name=r.name or "",
                signal_score=r.signal_score or 0,
                trend_strength=r.trend_strength or 0.0,
                trend_status=r.trend_status or "",
                buy_signal=r.buy_signal or "",
                ma_alignment=r.ma_alignment or "",
                error_msg=r.error_msg or "",
                current_price=r.current_price,
                volume_ratio=r.volume_ratio,
            )
            for r in rows
        ]
        success = sum(1 for r in results if r.is_success())
        # 用最后一条的 created_at 当 completed_at 近似值
        completed_at = max((r.created_at for r in rows), default=None)
        return ScreeningRunSummary(
            screening_id=screening_id,
            status="completed",
            total_count=len(results),
            success_count=success,
            failed_count=len(results) - success,
            started_at=min((r.created_at for r in rows), default=None),
            completed_at=completed_at,
            results=results,
        )

    # ------------------------------------------------------------
    # 第二阶段：promote 到 LLM 链路
    # ------------------------------------------------------------

    def promote_to_llm(
        self,
        screening_id: str,
        top_n: int,
        *,
        report_type: str = "detailed",
    ) -> Dict[str, Any]:
        """
        从技术面筛选结果按 signal_score desc 取 Top N，过滤当日已分析的股票，
        提交到现有 LLM 异步分析队列。

        Returns: 含 promoted/skipped/details 的 dict（语义参考 BatchTaskAcceptedResponse）
        """
        if top_n <= 0:
            raise ValueError(f"top_n must be > 0, got {top_n}")

        summary = self.get_summary(screening_id)
        if summary is None:
            raise KeyError(f"screening_id not found: {screening_id}")

        # 仅成功的、按分数倒序排列
        success_results = sorted(
            [r for r in summary.results if r.is_success()],
            key=lambda r: r.signal_score,
            reverse=True,
        )

        candidates = success_results[:top_n]
        candidate_codes = [r.code for r in candidates]

        if not candidate_codes:
            return {
                "screening_id": screening_id,
                "requested_top_n": top_n,
                "promoted_count": 0,
                "skipped_already_analyzed": [],
                "promoted_codes": [],
                "accepted_tasks": [],
            }

        # 过滤当日已跑过 LLM 的股票
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        already_analyzed = self.repo.codes_analyzed_since(candidate_codes, today_start)

        to_promote = [c for c in candidate_codes if c not in already_analyzed]
        skipped = [c for c in candidate_codes if c in already_analyzed]

        if not to_promote:
            return {
                "screening_id": screening_id,
                "requested_top_n": top_n,
                "promoted_count": 0,
                "skipped_already_analyzed": skipped,
                "promoted_codes": [],
                "accepted_tasks": [],
            }

        # 调现有 LLM 异步队列
        from src.services.task_queue import get_task_queue

        task_queue = get_task_queue()
        accepted, duplicates = task_queue.submit_tasks_batch(
            stock_codes=to_promote,
            report_type=report_type,
            force_refresh=False,
            notify=True,
            selection_source="screening",
            original_query=screening_id,
        )

        accepted_payload = [
            {"task_id": t.task_id, "stock_code": t.stock_code, "status": t.status.value if hasattr(t.status, "value") else str(t.status)}
            for t in accepted
        ]
        duplicate_payload = [
            {"stock_code": d.stock_code, "task_id": getattr(d, "existing_task_id", None)}
            for d in duplicates
        ]

        logger.info(
            "[Screening] promote screening_id=%s top_n=%d 候选=%d 已分析跳过=%d 提交=%d 重复=%d",
            screening_id,
            top_n,
            len(candidate_codes),
            len(skipped),
            len(accepted),
            len(duplicates),
        )

        return {
            "screening_id": screening_id,
            "requested_top_n": top_n,
            "promoted_count": len(accepted),
            "skipped_already_analyzed": skipped,
            "promoted_codes": [t.stock_code for t in accepted],
            "accepted_tasks": accepted_payload,
            "duplicate_tasks": duplicate_payload,
        }


# ------------------------------------------------------------
# 单例（与 task_queue 等服务一致的工厂模式）
# ------------------------------------------------------------

_screening_service: Optional[ScreeningService] = None
_screening_lock = threading.Lock()


def get_screening_service() -> ScreeningService:
    """获取全局 ScreeningService 单例。"""
    global _screening_service
    with _screening_lock:
        if _screening_service is None:
            _screening_service = ScreeningService()
        return _screening_service
