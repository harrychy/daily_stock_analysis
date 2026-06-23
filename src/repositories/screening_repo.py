# -*- coding: utf-8 -*-
"""
===================================
两阶段筛选历史数据访问层
===================================

封装 TechnicalScreeningHistory 表的 CRUD。
"""

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import desc

from src.core.screening import ScreeningResult, ScreeningRunSummary
from src.storage import DatabaseManager, TechnicalScreeningHistory

logger = logging.getLogger(__name__)


class ScreeningRepository:
    """筛选历史数据访问层。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_batch(self, screening_id: str, results: List[ScreeningResult]) -> int:
        """
        把一批 ScreeningResult 落库。

        Returns: 实际写入的行数。
        """
        if not results:
            return 0

        rows = [
            TechnicalScreeningHistory(
                screening_id=screening_id,
                code=r.code,
                name=r.name,
                signal_score=int(r.signal_score or 0),
                trend_strength=float(r.trend_strength or 0.0),
                trend_status=r.trend_status,
                buy_signal=r.buy_signal,
                ma_alignment=r.ma_alignment,
                current_price=r.current_price,
                volume_ratio=r.volume_ratio,
                error_msg=r.error_msg or None,
                created_at=datetime.now(),
            )
            for r in results
        ]

        try:
            with self.db.get_session() as session:
                session.add_all(rows)
                session.commit()
            return len(rows)
        except Exception as exc:
            logger.error(
                "[ScreeningRepo] 保存筛选结果失败 screening_id=%s error=%s",
                screening_id,
                exc,
            )
            return 0

    def get_by_screening_id(
        self, screening_id: str, *, order_by_score_desc: bool = True
    ) -> List[TechnicalScreeningHistory]:
        """读取一次筛选的全部条目。"""
        try:
            with self.db.get_session() as session:
                query = session.query(TechnicalScreeningHistory).filter(
                    TechnicalScreeningHistory.screening_id == screening_id
                )
                if order_by_score_desc:
                    query = query.order_by(desc(TechnicalScreeningHistory.signal_score))
                rows = query.all()
                # 在 session 关闭前 expunge 防止后续访问触发 detached error
                for row in rows:
                    session.expunge(row)
                return rows
        except Exception as exc:
            logger.error(
                "[ScreeningRepo] 读取筛选结果失败 screening_id=%s error=%s",
                screening_id,
                exc,
            )
            return []

    def list_recent(self, limit: int = 20) -> List[str]:
        """列出最近 N 个 screening_id（按时间倒序）。"""
        try:
            with self.db.get_session() as session:
                rows = (
                    session.query(TechnicalScreeningHistory.screening_id)
                    .distinct()
                    .order_by(desc(TechnicalScreeningHistory.created_at))
                    .limit(limit)
                    .all()
                )
                return [r[0] for r in rows]
        except Exception as exc:
            logger.error("[ScreeningRepo] 列出最近筛选失败 error=%s", exc)
            return []

    def codes_analyzed_since(self, codes: List[str], since: datetime) -> set[str]:
        """
        过滤辅助：找出在 codes 里、AnalysisHistory 中 created_at >= since 的 code 集合。

        用于 promote 阶段跳过"今日已跑过 LLM 分析"的股票。
        """
        from src.storage import AnalysisHistory  # 避免循环引用，按需导入

        if not codes:
            return set()

        try:
            with self.db.get_session() as session:
                rows = (
                    session.query(AnalysisHistory.code)
                    .filter(
                        AnalysisHistory.code.in_(codes),
                        AnalysisHistory.created_at >= since,
                    )
                    .distinct()
                    .all()
                )
                return {r[0] for r in rows}
        except Exception as exc:
            logger.error("[ScreeningRepo] 查询已分析股票失败 error=%s", exc)
            return set()
