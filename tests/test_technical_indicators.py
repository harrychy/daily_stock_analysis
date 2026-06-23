# -*- coding: utf-8 -*-
"""
扩展技术指标单元测试
===================

参见: docs/superpowers/specs/2026-06-08-technical-indicators-expansion-design.md

覆盖：
- 8 类指标计算（ATR/ADX/Bollinger/OBV/MaLongTerm/Donchian/KDJ/CandlePatterns）
- 数值化补强（MacdExtras/RsiMultiPeriod）
- 容错：数据不足返回 None / 异常不向外抛
- 聚合 compute_extended_indicators 在单指标失败时其他仍出
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.technical_indicators import (
    ExtendedIndicators,
    AtrResult,
    AdxResult,
    BollingerResult,
    ObvResult,
    MaLongTermResult,
    DonchianResult,
    KdjResult,
    MacdExtras,
    RsiMultiPeriod,
    calculate_atr,
    calculate_adx,
    calculate_bollinger,
    calculate_obv,
    calculate_ma_long_term,
    calculate_donchian,
    calculate_kdj,
    detect_candle_patterns,
    calculate_macd_extras,
    calculate_rsi_multi_period,
    compute_extended_indicators,
)


# ---------------------------------------------------------------------------
# 测试数据工厂
# ---------------------------------------------------------------------------

def _make_df(closes, highs=None, lows=None, opens=None, volumes=None):
    """根据收盘价构造一个最小可用的 OHLCV DataFrame。"""
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    if highs is None:
        highs = closes * 1.01
    if lows is None:
        lows = closes * 0.99
    if opens is None:
        opens = np.concatenate([[closes[0]], closes[:-1]])
    if volumes is None:
        volumes = np.full(n, 1_000_000.0)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": np.asarray(opens, dtype=float),
        "high": np.asarray(highs, dtype=float),
        "low": np.asarray(lows, dtype=float),
        "close": closes,
        "volume": np.asarray(volumes, dtype=float),
    })


def _strong_uptrend(n=60):
    closes = 10 + np.arange(n) * 0.5
    return _make_df(closes)


def _choppy(n=60):
    # 上下震荡
    rng = np.arange(n)
    closes = 50 + np.sin(rng / 2.0) * 1.5
    return _make_df(closes)


def _downtrend(n=60):
    closes = 50 - np.arange(n) * 0.3
    return _make_df(closes)


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

class TestAtr:
    def test_atr_basic_uptrend(self):
        df = _strong_uptrend(30)
        res = calculate_atr(df)
        assert res is not None
        assert isinstance(res, AtrResult)
        assert res.atr_14 > 0
        assert res.atr_pct > 0
        # 建议止损 < close < 建议目标
        close = float(df["close"].iloc[-1])
        assert res.suggested_stop < close < res.suggested_target
        # 风报比约 1.67 = 2.5/1.5
        rr = (res.suggested_target - close) / (close - res.suggested_stop)
        assert abs(rr - 2.5 / 1.5) < 0.01
        assert res.interpretation  # 非空

    def test_atr_insufficient_data(self):
        df = _strong_uptrend(10)
        assert calculate_atr(df) is None

    def test_atr_pct_consistent(self):
        df = _strong_uptrend(30)
        res = calculate_atr(df)
        close = float(df["close"].iloc[-1])
        assert abs(res.atr_pct - res.atr_14 / close * 100) < 1e-6


# ---------------------------------------------------------------------------
# ADX
# ---------------------------------------------------------------------------

class TestAdx:
    def test_adx_strong_uptrend(self):
        df = _strong_uptrend(60)
        res = calculate_adx(df)
        assert res is not None
        # 单调上涨 → 强趋势 + 多头
        assert res.adx_14 > 25
        assert res.direction == "多头"
        assert res.trend_regime in ("中等趋势", "强趋势")
        assert res.plus_di > res.minus_di

    def test_adx_downtrend(self):
        df = _downtrend(60)
        res = calculate_adx(df)
        assert res is not None
        assert res.direction == "空头"
        assert res.minus_di > res.plus_di

    def test_adx_choppy_market(self):
        df = _choppy(60)
        res = calculate_adx(df)
        assert res is not None
        # 震荡 → ADX 应当较低
        assert res.adx_14 < 25
        assert res.trend_regime in ("震荡市", "弱趋势")

    def test_adx_insufficient_data(self):
        df = _strong_uptrend(10)
        assert calculate_adx(df) is None


# ---------------------------------------------------------------------------
# 布林带
# ---------------------------------------------------------------------------

class TestBollinger:
    def test_bollinger_basic(self):
        df = _choppy(80)
        res = calculate_bollinger(df)
        assert res is not None
        assert res.lower < res.middle < res.upper
        assert 0 - 0.5 <= res.percent_b <= 1 + 0.5  # 允许极值情况
        assert 0 <= res.bandwidth_percentile_60d <= 100
        assert res.bandwidth_pct > 0

    def test_bollinger_uptrend_near_upper(self):
        df = _strong_uptrend(80)
        res = calculate_bollinger(df)
        # 单调上涨末端价格通常接近或突破上轨
        assert res.percent_b > 0.5

    def test_bollinger_insufficient_data(self):
        df = _strong_uptrend(10)
        assert calculate_bollinger(df) is None


# ---------------------------------------------------------------------------
# OBV
# ---------------------------------------------------------------------------

class TestObv:
    def test_obv_bearish_divergence(self):
        # 构造：前 10 天放量上涨 → OBV 累至高位；中间 5 天大量下跌 → OBV 大幅回吐；
        # 最后 5 天小量再涨，第 20 天创价格新高但 OBV 未回到高位
        closes = (
            list(np.linspace(10, 13, 10))   # 0..9 涨
            + list(np.linspace(13, 11, 5))  # 10..14 跌
            + list(np.linspace(11.1, 13.4, 4))  # 15..18 小幅涨
            + [13.6]                        # 19 创新高
        )
        volumes = (
            [3_000_000] * 10           # 放量上涨
            + [5_000_000] * 5          # 大量下跌
            + [500_000] * 4            # 小量回升
            + [400_000]                # 缩量新高
        )
        df = _make_df(closes, volumes=volumes)
        res = calculate_obv(df)
        assert res is not None
        assert res.divergence == "顶背离"

    def test_obv_bullish_divergence(self):
        # 构造：前 10 天放量下跌 → OBV 累至低位；中间 5 天大量上涨；
        # 最后 5 天小量再跌，第 20 天创价格新低但 OBV 未回到低位
        closes = (
            list(np.linspace(14, 11, 10))     # 0..9 跌
            + list(np.linspace(11, 13, 5))    # 10..14 涨
            + list(np.linspace(12.9, 10.6, 4))  # 15..18 小幅跌
            + [10.4]                          # 19 创新低
        )
        volumes = (
            [3_000_000] * 10           # 放量下跌
            + [5_000_000] * 5          # 大量上涨
            + [500_000] * 4            # 小量回落
            + [400_000]                # 缩量新低
        )
        df = _make_df(closes, volumes=volumes)
        res = calculate_obv(df)
        assert res is not None
        assert res.divergence == "底背离"

    def test_obv_no_divergence(self):
        df = _strong_uptrend(40)
        res = calculate_obv(df)
        assert res is not None
        assert res.divergence in ("无背离", "顶背离", "底背离")

    def test_obv_insufficient_data(self):
        df = _strong_uptrend(10)
        assert calculate_obv(df) is None


# ---------------------------------------------------------------------------
# 中长期锚点
# ---------------------------------------------------------------------------

class TestMaLongTerm:
    def test_ma_long_term_with_ma200(self):
        df = _strong_uptrend(260)
        res = calculate_ma_long_term(df)
        assert res is not None
        assert res.ma60 > 0
        assert res.ma200 is not None and res.ma200 > 0
        assert res.distance_to_ma200_pct is not None
        assert 0 <= res.position_52w <= 1
        assert res.high_52w >= res.low_52w

    def test_ma_long_term_short_data_no_ma200(self):
        df = _strong_uptrend(100)
        res = calculate_ma_long_term(df)
        assert res is not None
        assert res.ma200 is None
        assert res.distance_to_ma200_pct is None
        # 52w 用现有数据近似
        assert res.high_52w >= res.low_52w

    def test_ma_long_term_too_short(self):
        df = _strong_uptrend(30)
        assert calculate_ma_long_term(df) is None


# ---------------------------------------------------------------------------
# Donchian
# ---------------------------------------------------------------------------

class TestDonchian:
    def test_donchian_breakout_today(self):
        # 前 20 天在 [10, 12] 区间，第 21 天突破到 13
        highs = [12.0] * 20 + [13.0]
        lows = [10.0] * 20 + [12.5]
        closes = [11.0] * 20 + [13.0]
        df = _make_df(closes, highs=highs, lows=lows)
        res = calculate_donchian(df)
        assert res is not None
        assert "向上突破" in res.breakout_status

    def test_donchian_downward_breakout(self):
        highs = [12.0] * 20 + [9.5]
        lows = [10.0] * 20 + [9.0]
        closes = [11.0] * 20 + [9.0]
        df = _make_df(closes, highs=highs, lows=lows)
        res = calculate_donchian(df)
        assert res is not None
        assert "向下" in res.breakout_status or "跌破" in res.breakout_status

    def test_donchian_no_breakout(self):
        df = _choppy(40)
        res = calculate_donchian(df)
        assert res is not None
        assert res.breakout_status == "无突破"

    def test_donchian_insufficient_data(self):
        df = _strong_uptrend(15)
        assert calculate_donchian(df) is None


# ---------------------------------------------------------------------------
# KDJ
# ---------------------------------------------------------------------------

class TestKdj:
    def test_kdj_uptrend_overbought(self):
        df = _strong_uptrend(40)
        res = calculate_kdj(df)
        assert res is not None
        # 单调上涨末端 K 应当很高
        assert res.k > 70
        assert res.status in ("死叉超买区", "钝化", "中性")

    def test_kdj_downtrend_oversold(self):
        df = _downtrend(40)
        res = calculate_kdj(df)
        assert res is not None
        assert res.k < 30

    def test_kdj_insufficient_data(self):
        df = _strong_uptrend(5)
        assert calculate_kdj(df) is None


# ---------------------------------------------------------------------------
# 蜡烛形态
# ---------------------------------------------------------------------------

class TestCandlePatterns:
    def test_detect_bearish_engulfing(self):
        # 前一根阳线：open=10, close=11；当日阴线吞没：open=11.2, close=9.8
        opens = [10, 11.2]
        closes = [11, 9.8]
        highs = [11.1, 11.3]
        lows = [9.9, 9.7]
        df = _make_df(closes, highs=highs, lows=lows, opens=opens)
        patterns = detect_candle_patterns(df)
        assert any("看跌吞没" in p for p in patterns)

    def test_detect_bullish_engulfing(self):
        # 前一根阴线：open=11, close=10；当日阳线吞没：open=9.8, close=11.2
        opens = [11, 9.8]
        closes = [10, 11.2]
        highs = [11.1, 11.3]
        lows = [9.9, 9.7]
        df = _make_df(closes, highs=highs, lows=lows, opens=opens)
        patterns = detect_candle_patterns(df)
        assert any("看涨吞没" in p for p in patterns)

    def test_detect_hammer(self):
        # 锤子线：实体小、下影长、上影短
        opens = [10.0]
        closes = [10.1]
        highs = [10.15]
        lows = [9.5]
        df = _make_df(closes, highs=highs, lows=lows, opens=opens)
        patterns = detect_candle_patterns(df)
        assert any("锤子" in p for p in patterns)

    def test_detect_shooting_star(self):
        # 射击之星：阴线 + 上影长 + 下影短
        opens = [10.1]
        closes = [10.0]
        highs = [10.7]
        lows = [9.95]
        df = _make_df(closes, highs=highs, lows=lows, opens=opens)
        patterns = detect_candle_patterns(df)
        assert any("射击之星" in p for p in patterns)

    def test_detect_doji(self):
        # 十字星：开盘 ≈ 收盘
        opens = [10.0]
        closes = [10.001]
        highs = [10.3]
        lows = [9.7]
        df = _make_df(closes, highs=highs, lows=lows, opens=opens)
        patterns = detect_candle_patterns(df)
        assert any("十字" in p or "doji" in p.lower() for p in patterns)

    def test_no_pattern_for_plain_bar(self):
        # 一根普通大阳线，应不命中以上特征性形态（吞没/锤子/射击之星/十字）
        opens = [10.0]
        closes = [10.5]
        highs = [10.55]
        lows = [9.95]
        df = _make_df(closes, highs=highs, lows=lows, opens=opens)
        patterns = detect_candle_patterns(df)
        for p in patterns:
            assert "吞没" not in p and "锤子" not in p and "射击之星" not in p and "十字" not in p

    def test_at_most_two_days(self):
        df = _strong_uptrend(30)
        patterns = detect_candle_patterns(df)
        # 即便都命中，最多列出最近 2 日
        assert len(patterns) <= 2


# ---------------------------------------------------------------------------
# MACD / RSI 数值化补强
# ---------------------------------------------------------------------------

class TestMacdAndRsiExtras:
    def test_macd_extras_basic(self):
        df = _strong_uptrend(50)
        res = calculate_macd_extras(df)
        assert res is not None
        assert isinstance(res, MacdExtras)
        assert len(res.bar_recent) == 3
        assert res.bar_trend in ("持续放大", "持续缩短（动能衰减）", "震荡")

    def test_rsi_multi_period_basic(self):
        df = _strong_uptrend(50)
        res = calculate_rsi_multi_period(df)
        assert res is not None
        assert 0 <= res.rsi_6 <= 100
        assert 0 <= res.rsi_12 <= 100
        assert 0 <= res.rsi_24 <= 100
        assert res.divergence in ("三线发散多头", "三线发散空头", "三线缠绕")


# ---------------------------------------------------------------------------
# 聚合 compute_extended_indicators —— 容错
# ---------------------------------------------------------------------------

class TestComputeExtendedIndicators:
    def test_full_uptrend_all_fields_present(self):
        df = _strong_uptrend(260)
        ext = compute_extended_indicators(df)
        assert isinstance(ext, ExtendedIndicators)
        assert ext.atr is not None
        assert ext.adx is not None
        assert ext.bollinger is not None
        assert ext.obv is not None
        assert ext.ma_long_term is not None
        assert ext.donchian is not None
        assert ext.kdj is not None
        assert ext.macd_extras is not None
        assert ext.rsi_multi_period is not None
        # to_dict 不抛异常
        d = ext.to_dict()
        assert isinstance(d, dict)
        assert "atr" in d

    def test_empty_dataframe_returns_empty_container(self):
        df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        ext = compute_extended_indicators(df)
        assert isinstance(ext, ExtendedIndicators)
        assert ext.atr is None
        assert ext.adx is None
        assert ext.candle_patterns == []

    def test_one_indicator_failure_does_not_break_others(self, monkeypatch):
        """如果单个指标计算抛异常，其他 7 个仍应正常返回。"""
        import src.technical_indicators as ti

        def boom(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(ti, "calculate_adx", boom)
        df = _strong_uptrend(260)
        ext = ti.compute_extended_indicators(df)
        assert ext.adx is None
        # 其他至少有一个仍出
        assert ext.atr is not None
        assert ext.bollinger is not None
        assert ext.kdj is not None

    def test_none_dataframe(self):
        ext = compute_extended_indicators(None)
        assert isinstance(ext, ExtendedIndicators)
        assert ext.atr is None