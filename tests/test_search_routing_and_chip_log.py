# -*- coding: utf-8 -*-
"""
搜索源优先级 + 失败回退 单测

覆盖：
- 配置 SearXNG 时所有维度优先走 SearXNG
- SearXNG 返回空/失败时回退到下一个 provider（如 Tavily）
- 没配 SearXNG 时保持原轮询行为（兼容老用户）
- 筹码分布在美股/港股上日志降级为 INFO（不再 warning）
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub  # noqa: E402

ensure_litellm_stub()


def _build_search_response(success: bool, results: list, error: str = "") -> object:
    """构造 SearchResponse 替身（无需走真实类）。"""
    from src.search_service import SearchResponse

    return SearchResponse(
        query="test",
        results=results,
        provider="mock",
        success=success,
        error_message=error,
    )


class SearchProviderRoutingTests(unittest.TestCase):
    """search_comprehensive_intel 的 provider 选择行为。"""

    def _make_service_with_providers(self, providers: list) -> object:
        """构造一个 SearchService 实例并直接塞入 mock provider。"""
        from src.search_service import SearchService

        # 用 __new__ 跳过 __init__ 中复杂的初始化（API key、配额等）
        service = SearchService.__new__(SearchService)
        service._providers = providers
        # 必填属性（其它方法用到的）
        service.news_strategy_profile = "default"
        service.news_max_age_days = 3
        return service

    def test_searxng_priority_when_available(self) -> None:
        """配了 SearXNG 时，每个维度优先选 SearXNG。"""
        from src.search_service import SearXNGSearchProvider, TavilySearchProvider

        searxng = MagicMock(spec=SearXNGSearchProvider)
        searxng.is_available = True
        searxng.name = "SearXNG"
        searxng.search.return_value = _build_search_response(
            True, [{"title": "T1", "url": "https://x", "content": "ok"}]
        )

        tavily = MagicMock(spec=TavilySearchProvider)
        tavily.is_available = True
        tavily.name = "Tavily"
        tavily.search.return_value = _build_search_response(True, [])

        service = self._make_service_with_providers([tavily, searxng])  # 注意：Tavily 注册更早

        with patch.object(service, "_filter_news_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_normalize_and_limit_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_rank_news_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_should_prefer_chinese_news", return_value=False):
            service.search_comprehensive_intel("NVDA", "英伟达")

        # 至少有一次是 SearXNG 被调用（任一维度）
        self.assertGreater(searxng.search.call_count, 0)
        # SearXNG 全部成功有结果，则 Tavily 不应被调用
        self.assertEqual(tavily.search.call_count, 0, "SearXNG 成功时不应调 Tavily 兜底")

    def test_fallback_to_tavily_when_searxng_returns_empty(self) -> None:
        """SearXNG 返回空结果时，自动回退到 Tavily。"""
        from src.search_service import SearXNGSearchProvider, TavilySearchProvider

        searxng = MagicMock(spec=SearXNGSearchProvider)
        searxng.is_available = True
        searxng.name = "SearXNG"
        # 返回 success=True 但 results=[]，触发回退
        searxng.search.return_value = _build_search_response(True, [])

        tavily = MagicMock(spec=TavilySearchProvider)
        tavily.is_available = True
        tavily.name = "Tavily"
        tavily.search.return_value = _build_search_response(
            True, [{"title": "T", "url": "https://x", "content": "x"}]
        )

        service = self._make_service_with_providers([tavily, searxng])

        with patch.object(service, "_filter_news_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_normalize_and_limit_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_rank_news_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_should_prefer_chinese_news", return_value=False):
            service.search_comprehensive_intel("NVDA", "英伟达")

        # 每个维度先调 SearXNG（5 次），返空后调 Tavily 兜底（5 次）
        self.assertGreater(searxng.search.call_count, 0)
        self.assertGreater(tavily.search.call_count, 0, "SearXNG 空结果时应回退到 Tavily")

    def test_no_searxng_keeps_round_robin_behavior(self) -> None:
        """没配 SearXNG 时，老的轮询行为不应改变。"""
        from src.search_service import TavilySearchProvider

        tavily = MagicMock(spec=TavilySearchProvider)
        tavily.is_available = True
        tavily.name = "Tavily"
        tavily.search.return_value = _build_search_response(
            True, [{"title": "T", "url": "https://x", "content": "x"}]
        )

        service = self._make_service_with_providers([tavily])

        with patch.object(service, "_filter_news_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_normalize_and_limit_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_rank_news_response", side_effect=lambda r, **kw: r), \
             patch.object(service, "_should_prefer_chinese_news", return_value=False):
            service.search_comprehensive_intel("NVDA", "英伟达")

        # max_searches 默认 3 → 调用 3 次（兼容现有轮询行为）
        self.assertEqual(tavily.search.call_count, 3)


class ChipDistributionLogLevelTests(unittest.TestCase):
    """筹码分布在不支持的市场上不再用 WARNING 级别。"""

    def test_us_stock_logs_info_not_warning(self) -> None:
        """美股 NVDA 走完所有数据源 → INFO（不再 warning）。"""
        from data_provider.base import DataFetcherManager

        # 直接测纯函数路径太重；用一个最小 mock：fetcher 列表为空时也会走到末尾分支
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._chip_fetchers = []  # 空列表 → 直接走到末尾日志
        manager._chip_circuit_breaker = MagicMock()
        manager._chip_circuit_breaker.allow_call.return_value = True

        with self.assertLogs("data_provider.base", level="INFO") as cm:
            manager.get_chip_distribution("NVDA")

        joined = "\n".join(cm.output)
        # 美股应该看到"该市场不支持"INFO，且不应有 WARNING
        self.assertIn("该市场不支持", joined)
        self.assertNotIn("所有数据源均失败", joined)

    def test_a_share_keeps_warning(self) -> None:
        """A 股 600519 真的失败时仍是 WARNING（这是合法的故障告警）。"""
        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._chip_fetchers = []
        manager._chip_circuit_breaker = MagicMock()
        manager._chip_circuit_breaker.allow_call.return_value = True

        with self.assertLogs("data_provider.base", level="WARNING") as cm:
            manager.get_chip_distribution("600519")

        joined = "\n".join(cm.output)
        self.assertIn("所有数据源均失败", joined)


if __name__ == "__main__":
    unittest.main()
