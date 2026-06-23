# -*- coding: utf-8 -*-
"""
两阶段筛选 - 后端单元测试

覆盖：
- ScreeningResult dataclass 的基本契约
- ScreeningRepository 的 CRUD
- ScreeningService.promote_to_llm 的 Top N 切片 + 当日已分析过滤
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from typing import List
from unittest.mock import MagicMock, patch

# Stub litellm 避免 import 失败（按仓库现有惯例）
from tests.litellm_stub import ensure_litellm_stub  # noqa: E402

ensure_litellm_stub()


class ScreeningDataclassTests(unittest.TestCase):
    """ScreeningResult 与 ScreeningRunSummary 的基础行为。"""

    def test_screening_result_to_dict_round_trip(self) -> None:
        from src.core.screening import ScreeningResult

        r = ScreeningResult(
            code="600519",
            name="贵州茅台",
            signal_score=75,
            trend_strength=80.5,
            trend_status="多头",
            buy_signal="买入",
            ma_alignment="多头排列",
        )
        d = r.to_dict()
        self.assertEqual(d["code"], "600519")
        self.assertEqual(d["signal_score"], 75)
        self.assertTrue(r.is_success())

    def test_screening_result_failure_path(self) -> None:
        from src.core.screening import ScreeningResult

        r = ScreeningResult(code="XX", error_msg="fetch_failed")
        self.assertFalse(r.is_success())
        self.assertEqual(r.signal_score, 0)


class ScreeningRepositoryTests(unittest.TestCase):
    """Repository 行为（用临时 SQLite，测试隔离）。"""

    def test_save_and_read_batch(self) -> None:
        """构造一个轻量 mock DatabaseManager，验证 Repository 的入库 + 排序读出。

        不依赖真实 DatabaseManager（避免影响全局单例）。
        """
        from src.core.screening import ScreeningResult
        from src.repositories.screening_repo import ScreeningRepository

        # 用 in-memory 的临时 SQLAlchemy session
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from src.storage import Base, TechnicalScreeningHistory  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)

        # 构造一个最小 mock DatabaseManager 替身
        class _StubDB:
            def get_session(self):
                return SessionLocal()

        repo = ScreeningRepository(db_manager=_StubDB())  # type: ignore[arg-type]
        results: List[ScreeningResult] = [
            ScreeningResult(code="600519", name="A", signal_score=80, trend_strength=85.0, trend_status="多头", buy_signal="买入"),
            ScreeningResult(code="000001", name="B", signal_score=45, trend_strength=30.0, trend_status="震荡", buy_signal="观望"),
            ScreeningResult(code="ZZZ", error_msg="fetch_failed"),
        ]
        n = repo.save_batch("scrn-test-1", results)
        self.assertEqual(n, 3)

        rows = repo.get_by_screening_id("scrn-test-1", order_by_score_desc=True)
        self.assertEqual(len(rows), 3)
        # 应按 signal_score desc：80, 45, 0
        self.assertEqual([r.signal_score for r in rows], [80, 45, 0])
        self.assertEqual(rows[2].error_msg, "fetch_failed")


class PromoteToLLMTests(unittest.TestCase):
    """ScreeningService.promote_to_llm 的核心行为：Top N 切片 + 跳过当日已分析。"""

    def _make_summary(self) -> "ScreeningRunSummary":  # noqa: F821 - import 局部
        from src.core.screening import ScreeningResult, ScreeningRunSummary

        results = [
            ScreeningResult(code="A", name="A", signal_score=85, trend_strength=90, trend_status="多头", buy_signal="强烈买入"),
            ScreeningResult(code="B", name="B", signal_score=72, trend_strength=70, trend_status="多头", buy_signal="买入"),
            ScreeningResult(code="C", name="C", signal_score=60, trend_strength=55, trend_status="弱势多头", buy_signal="持有"),
            ScreeningResult(code="D", name="D", signal_score=45, trend_strength=30, trend_status="震荡", buy_signal="观望"),
            # 失败条目不应进 promote
            ScreeningResult(code="E", error_msg="boom"),
        ]
        return ScreeningRunSummary(
            screening_id="sid-promote-1",
            status="completed",
            total_count=5,
            success_count=4,
            failed_count=1,
            results=results,
        )

    def test_promote_top_n_returns_highest_scores(self) -> None:
        from src.services import screening_service as svc_mod

        service = svc_mod.ScreeningService(repo=MagicMock())
        # 直接塞 in-memory summary，绕开 start_screening
        with service._runs_lock:
            service._runs["sid-promote-1"] = self._make_summary()
        service.repo.codes_analyzed_since.return_value = set()  # 没有今日已分析

        # mock task_queue 避免真去跑 LLM
        accepted = [MagicMock(stock_code="A", task_id="t1", status=MagicMock(value="pending"))]
        accepted[0].stock_code = "A"
        accepted[0].task_id = "t1"

        with patch("src.services.screening_service.get_screening_service", return_value=service):
            with patch("src.services.task_queue.get_task_queue") as mock_get_q:
                mock_q = MagicMock()
                mock_q.submit_tasks_batch.return_value = (accepted, [])
                mock_get_q.return_value = mock_q

                result = service.promote_to_llm("sid-promote-1", top_n=2)

        # Top 2 by signal_score = A(85), B(72)
        self.assertEqual(result["requested_top_n"], 2)
        # 检查 submit_tasks_batch 收到的是 ['A', 'B']（不是 C/D/E）
        call_args = mock_q.submit_tasks_batch.call_args
        self.assertEqual(call_args.kwargs["stock_codes"], ["A", "B"])
        self.assertEqual(call_args.kwargs["selection_source"], "screening")

    def test_promote_skips_codes_already_analyzed_today(self) -> None:
        from src.services import screening_service as svc_mod

        service = svc_mod.ScreeningService(repo=MagicMock())
        with service._runs_lock:
            service._runs["sid-promote-1"] = self._make_summary()
        # B 今日已分析，应被跳过
        service.repo.codes_analyzed_since.return_value = {"B"}

        with patch("src.services.task_queue.get_task_queue") as mock_get_q:
            mock_q = MagicMock()
            mock_q.submit_tasks_batch.return_value = ([], [])
            mock_get_q.return_value = mock_q

            result = service.promote_to_llm("sid-promote-1", top_n=2)

        self.assertEqual(result["skipped_already_analyzed"], ["B"])
        # 实际提交给 task_queue 的应只剩 A
        call_args = mock_q.submit_tasks_batch.call_args
        self.assertEqual(call_args.kwargs["stock_codes"], ["A"])

    def test_promote_top_n_zero_raises(self) -> None:
        from src.services import screening_service as svc_mod

        service = svc_mod.ScreeningService(repo=MagicMock())
        with service._runs_lock:
            service._runs["sid-promote-1"] = self._make_summary()

        with self.assertRaises(ValueError):
            service.promote_to_llm("sid-promote-1", top_n=0)

    def test_promote_unknown_screening_id_raises_keyerror(self) -> None:
        from src.services import screening_service as svc_mod

        service = svc_mod.ScreeningService(repo=MagicMock())
        service.repo.get_by_screening_id.return_value = []  # DB 也没有

        with self.assertRaises(KeyError):
            service.promote_to_llm("nonexistent", top_n=3)


if __name__ == "__main__":
    unittest.main()
