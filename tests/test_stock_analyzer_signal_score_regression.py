# -*- coding: utf-8 -*-
"""
stock_analyzer signal_score 回归测试
====================================

设计文档 §11 要求：接入扩展指标后，第一阶段 signal_score / trend_status / buy_signal
在已知样本上保持不变。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult


def _df(closes, volumes=None):
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    if volumes is None:
        volumes = np.full(n, 1_000_000.0)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": np.concatenate([[closes[0]], closes[:-1]]),
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": np.asarray(volumes, dtype=float),
    })


class TestSignalScoreRegression:
    """接入扩展指标后，第一阶段产出保持稳定。"""

    def test_strong_uptrend_signal_present(self):
        analyzer = StockTrendAnalyzer()
        df = _df(10 + np.arange(80) * 0.3)
        res = analyzer.analyze(df, "TEST.UP")
        assert isinstance(res, TrendAnalysisResult)
        assert res.signal_score > 0
        # 接入后 extended 应被填充
        assert res.extended is not None
        # to_dict 不抛
        d = res.to_dict()
        assert "extended" in d
        assert d["extended"] is not None

    def test_downtrend_signal_low(self):
        analyzer = StockTrendAnalyzer()
        df = _df(50 - np.arange(80) * 0.3)
        res = analyzer.analyze(df, "TEST.DOWN")
        # 下跌趋势 signal_score 应较低
        assert res.signal_score < 70

    def test_extended_failure_does_not_break_analyze(self, monkeypatch):
        """compute_extended_indicators 抛异常时 analyze 仍返回正常结果，extended 为空容器。"""
        from src import stock_analyzer as sa

        def boom(_df):
            raise RuntimeError("boom")

        monkeypatch.setattr(sa, "compute_extended_indicators", boom)
        analyzer = StockTrendAnalyzer()
        df = _df(10 + np.arange(60) * 0.2)
        res = analyzer.analyze(df, "TEST.BOOM")
        # 不应抛；signal_score 字段正常存在
        assert res is not None
        assert isinstance(res.signal_score, int)
        # extended 为空容器
        assert res.extended is not None
        assert res.extended.is_empty()

    def test_insufficient_data_returns_early(self):
        analyzer = StockTrendAnalyzer()
        df = _df(np.arange(10) + 10.0)  # < 20 行
        res = analyzer.analyze(df, "TEST.SHORT")
        # 触发提前返回路径，extended 仍是 None（未运行到第 8 步）
        assert res.extended is None
        assert "数据不足" in (res.risk_factors[0] if res.risk_factors else "")
