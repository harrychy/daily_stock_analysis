# -*- coding: utf-8 -*-
"""
扩展技术指标（ATR / ADX / 布林带 / OBV / MA60-MA200-52周 / Donchian / KDJ / 蜡烛形态）
=================================================================================

设计目的：为两阶段筛选第二阶段给 LLM 提供更丰富的技术面信号。
**不**修改第一阶段 signal_score 排序逻辑，不进入 SSE 第一阶段轻量字段。

详细设计：docs/superpowers/specs/2026-06-08-technical-indicators-expansion-design.md

关注点：
- 所有 calculate_* 函数：数据不足或异常 → 返回 None，永不向外抛
- 聚合 compute_extended_indicators：8 个独立 try/except，单指标失败不影响其他
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构（dataclass）
# ---------------------------------------------------------------------------

@dataclass
class AtrResult:
    atr_14: float
    atr_pct: float
    suggested_stop: float
    suggested_target: float
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atr_14": round(self.atr_14, 4),
            "atr_pct": round(self.atr_pct, 2),
            "suggested_stop": round(self.suggested_stop, 4),
            "suggested_target": round(self.suggested_target, 4),
            "interpretation": self.interpretation,
        }


@dataclass
class AdxResult:
    adx_14: float
    plus_di: float
    minus_di: float
    trend_regime: str
    direction: str
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adx_14": round(self.adx_14, 2),
            "plus_di": round(self.plus_di, 2),
            "minus_di": round(self.minus_di, 2),
            "trend_regime": self.trend_regime,
            "direction": self.direction,
            "interpretation": self.interpretation,
        }


@dataclass
class BollingerResult:
    upper: float
    middle: float
    lower: float
    percent_b: float
    bandwidth_pct: float
    bandwidth_percentile_60d: float
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "upper": round(self.upper, 4),
            "middle": round(self.middle, 4),
            "lower": round(self.lower, 4),
            "percent_b": round(self.percent_b, 3),
            "bandwidth_pct": round(self.bandwidth_pct, 2),
            "bandwidth_percentile_60d": round(self.bandwidth_percentile_60d, 1),
            "interpretation": self.interpretation,
        }


@dataclass
class ObvResult:
    obv_current: float
    obv_ma_20: float
    divergence: str
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "obv_current": float(self.obv_current),
            "obv_ma_20": float(self.obv_ma_20),
            "divergence": self.divergence,
            "interpretation": self.interpretation,
        }


@dataclass
class MaLongTermResult:
    ma60: float
    ma200: Optional[float]
    distance_to_ma60_pct: float
    distance_to_ma200_pct: Optional[float]
    high_52w: float
    low_52w: float
    position_52w: float
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ma60": round(self.ma60, 4),
            "ma200": None if self.ma200 is None else round(self.ma200, 4),
            "distance_to_ma60_pct": round(self.distance_to_ma60_pct, 2),
            "distance_to_ma200_pct": (
                None
                if self.distance_to_ma200_pct is None
                else round(self.distance_to_ma200_pct, 2)
            ),
            "high_52w": round(self.high_52w, 4),
            "low_52w": round(self.low_52w, 4),
            "position_52w": round(self.position_52w, 3),
            "interpretation": self.interpretation,
        }


@dataclass
class DonchianResult:
    donchian_high_20: float
    donchian_low_20: float
    breakout_status: str
    days_since_breakout: int = 0
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "donchian_high_20": round(self.donchian_high_20, 4),
            "donchian_low_20": round(self.donchian_low_20, 4),
            "breakout_status": self.breakout_status,
            "days_since_breakout": int(self.days_since_breakout),
            "interpretation": self.interpretation,
        }


@dataclass
class KdjResult:
    k: float
    d: float
    j: float
    status: str
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "k": round(self.k, 2),
            "d": round(self.d, 2),
            "j": round(self.j, 2),
            "status": self.status,
            "interpretation": self.interpretation,
        }


@dataclass
class MacdExtras:
    bar_recent: List[float]
    bar_trend: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bar_recent": [round(float(x), 4) for x in self.bar_recent],
            "bar_trend": self.bar_trend,
        }


@dataclass
class RsiMultiPeriod:
    rsi_6: float
    rsi_12: float
    rsi_24: float
    divergence: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rsi_6": round(self.rsi_6, 2),
            "rsi_12": round(self.rsi_12, 2),
            "rsi_24": round(self.rsi_24, 2),
            "divergence": self.divergence,
        }


@dataclass
class ExtendedIndicators:
    """所有扩展指标的统一容器。任一字段为 None 表示该指标不可用。"""
    atr: Optional[AtrResult] = None
    adx: Optional[AdxResult] = None
    bollinger: Optional[BollingerResult] = None
    obv: Optional[ObvResult] = None
    ma_long_term: Optional[MaLongTermResult] = None
    donchian: Optional[DonchianResult] = None
    kdj: Optional[KdjResult] = None
    candle_patterns: List[str] = field(default_factory=list)
    macd_extras: Optional[MacdExtras] = None
    rsi_multi_period: Optional[RsiMultiPeriod] = None

    def is_empty(self) -> bool:
        return (
            self.atr is None
            and self.adx is None
            and self.bollinger is None
            and self.obv is None
            and self.ma_long_term is None
            and self.donchian is None
            and self.kdj is None
            and self.macd_extras is None
            and self.rsi_multi_period is None
            and not self.candle_patterns
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atr": None if self.atr is None else self.atr.to_dict(),
            "adx": None if self.adx is None else self.adx.to_dict(),
            "bollinger": None if self.bollinger is None else self.bollinger.to_dict(),
            "obv": None if self.obv is None else self.obv.to_dict(),
            "ma_long_term": (
                None if self.ma_long_term is None else self.ma_long_term.to_dict()
            ),
            "donchian": None if self.donchian is None else self.donchian.to_dict(),
            "kdj": None if self.kdj is None else self.kdj.to_dict(),
            "candle_patterns": list(self.candle_patterns),
            "macd_extras": (
                None if self.macd_extras is None else self.macd_extras.to_dict()
            ),
            "rsi_multi_period": (
                None if self.rsi_multi_period is None else self.rsi_multi_period.to_dict()
            ),
        }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

_REQUIRED_COLS = ("open", "high", "low", "close", "volume")


def _validate(df: Optional[pd.DataFrame], min_rows: int) -> Optional[pd.DataFrame]:
    """通用前置检查；返回 None 则上层视为不可用。"""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if len(df) < min_rows:
        return None
    for col in _REQUIRED_COLS:
        if col not in df.columns:
            return None
    return df


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    # 首日 prev_close 为 NaN -> 退化到 high - low
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def calculate_atr(df: pd.DataFrame, period: int = 14) -> Optional[AtrResult]:
    """Wilder 平滑 ATR。"""
    try:
        df = _validate(df, min_rows=period + 1)
        if df is None:
            return None
        tr = _true_range(df)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        atr_14 = float(atr.iloc[-1])
        close = float(df["close"].iloc[-1])
        if close <= 0 or not np.isfinite(atr_14) or atr_14 <= 0:
            return None
        atr_pct = atr_14 / close * 100
        if atr_pct < 1.0:
            label = "低波动"
        elif atr_pct < 3.0:
            label = "中等波动"
        elif atr_pct < 6.0:
            label = "较高波动"
        else:
            label = "高波动"
        return AtrResult(
            atr_14=atr_14,
            atr_pct=atr_pct,
            suggested_stop=close - 1.5 * atr_14,
            suggested_target=close + 2.5 * atr_14,
            interpretation=f"日均波幅 {atr_pct:.2f}%（{label}）",
        )
    except Exception as exc:  # pragma: no cover - 防御
        logger.debug("calculate_atr failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# ADX
# ---------------------------------------------------------------------------

def calculate_adx(df: pd.DataFrame, period: int = 14) -> Optional[AdxResult]:
    """Welles Wilder 1978 标准 ADX。"""
    try:
        df = _validate(df, min_rows=period * 2 + 1)
        if df is None:
            return None

        up_move = df["high"].diff()
        down_move = -df["low"].diff()

        plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0)
        minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0)

        tr = _true_range(df)
        atr_for_di = tr.ewm(alpha=1 / period, adjust=False).mean()

        # 防御除零
        atr_safe = atr_for_di.replace(0, np.nan)
        plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_safe
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_safe

        di_sum = (plus_di + minus_di).replace(0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / di_sum
        dx = dx.fillna(0)
        adx_series = dx.ewm(alpha=1 / period, adjust=False).mean()

        adx_14 = float(adx_series.iloc[-1])
        pdi = float(plus_di.iloc[-1]) if np.isfinite(plus_di.iloc[-1]) else 0.0
        mdi = float(minus_di.iloc[-1]) if np.isfinite(minus_di.iloc[-1]) else 0.0
        if not np.isfinite(adx_14):
            return None

        if adx_14 < 20:
            regime = "震荡市"
        elif adx_14 < 25:
            regime = "弱趋势"
        elif adx_14 < 40:
            regime = "中等趋势"
        else:
            regime = "强趋势"

        if pdi > mdi * 1.05:
            direction = "多头"
        elif mdi > pdi * 1.05:
            direction = "空头"
        else:
            direction = "无方向"

        interp = f"ADX {adx_14:.1f}（{regime}），{direction}"
        if regime == "震荡市":
            interp += "；均线信号易反复"
        return AdxResult(
            adx_14=adx_14,
            plus_di=pdi,
            minus_di=mdi,
            trend_regime=regime,
            direction=direction,
            interpretation=interp,
        )
    except Exception as exc:  # pragma: no cover - 防御
        logger.debug("calculate_adx failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 布林带
# ---------------------------------------------------------------------------

def calculate_bollinger(df: pd.DataFrame, period: int = 20) -> Optional[BollingerResult]:
    """20 日 ±2σ 布林带 + 带宽 60 日分位数。"""
    try:
        df = _validate(df, min_rows=period + 1)
        if df is None:
            return None
        mid = df["close"].rolling(period).mean()
        std = df["close"].rolling(period).std(ddof=0)
        upper = mid + 2 * std
        lower = mid - 2 * std

        u, m, lo = float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1])
        close = float(df["close"].iloc[-1])
        if not (np.isfinite(u) and np.isfinite(m) and np.isfinite(lo)) or u == lo:
            return None
        percent_b = (close - lo) / (u - lo)
        bandwidth = (upper - lower) / mid * 100
        bw_last = float(bandwidth.iloc[-1])
        # 60 日分位
        window = bandwidth.iloc[-60:].dropna()
        if len(window) >= 2:
            rank = (window <= bw_last).mean() * 100
        else:
            rank = 50.0

        if percent_b > 0.95:
            interp = f"接近/突破上轨，%B={percent_b:.2f}（极强势，警惕回调）"
        elif percent_b > 0.7:
            interp = f"上轨附近，%B={percent_b:.2f}（强势但接近压力）"
        elif percent_b > 0.3:
            interp = f"通道中部，%B={percent_b:.2f}"
        elif percent_b > 0.05:
            interp = f"下轨附近，%B={percent_b:.2f}（弱势）"
        else:
            interp = f"接近/跌破下轨，%B={percent_b:.2f}（极弱势）"
        if rank < 20:
            interp += "；带宽收口，注意变盘"
        elif rank > 80:
            interp += "；带宽放大，行情活跃"

        return BollingerResult(
            upper=u,
            middle=m,
            lower=lo,
            percent_b=percent_b,
            bandwidth_pct=bw_last,
            bandwidth_percentile_60d=float(rank),
            interpretation=interp,
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("calculate_bollinger failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# OBV + 背离
# ---------------------------------------------------------------------------

def calculate_obv(df: pd.DataFrame, window: int = 20) -> Optional[ObvResult]:
    try:
        df = _validate(df, min_rows=window)
        if df is None:
            return None
        sign = np.sign(df["close"].diff()).fillna(0.0)
        obv = (sign * df["volume"]).cumsum()
        obv_ma = obv.rolling(window).mean()

        recent_close = df["close"].iloc[-window:]
        recent_obv = obv.iloc[-window:]
        # 最高/最低发生在最近一天即视为"新高/新低"
        price_high_today = recent_close.idxmax() == recent_close.index[-1]
        price_low_today = recent_close.idxmin() == recent_close.index[-1]
        obv_high_today = recent_obv.idxmax() == recent_obv.index[-1]
        obv_low_today = recent_obv.idxmin() == recent_obv.index[-1]

        if price_high_today and not obv_high_today:
            divergence = "顶背离"
            interp = "价格新高但 OBV 未跟随，量价背离（看跌）"
        elif price_low_today and not obv_low_today:
            divergence = "底背离"
            interp = "价格新低但 OBV 未跟随，量价背离（看涨）"
        else:
            divergence = "无背离"
            interp = "OBV 与价格同向"

        return ObvResult(
            obv_current=float(obv.iloc[-1]),
            obv_ma_20=float(obv_ma.iloc[-1]) if np.isfinite(obv_ma.iloc[-1]) else float(obv.iloc[-1]),
            divergence=divergence,
            interpretation=interp,
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("calculate_obv failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 中长期锚点
# ---------------------------------------------------------------------------

def calculate_ma_long_term(df: pd.DataFrame) -> Optional[MaLongTermResult]:
    try:
        df = _validate(df, min_rows=60)
        if df is None:
            return None
        close = float(df["close"].iloc[-1])
        ma60 = float(df["close"].rolling(60).mean().iloc[-1])
        if not np.isfinite(ma60) or ma60 <= 0:
            return None
        distance_60 = (close - ma60) / ma60 * 100

        ma200: Optional[float] = None
        distance_200: Optional[float] = None
        if len(df) >= 200:
            v = float(df["close"].rolling(200).mean().iloc[-1])
            if np.isfinite(v) and v > 0:
                ma200 = v
                distance_200 = (close - v) / v * 100

        window = min(252, len(df))
        high_52w = float(df["high"].iloc[-window:].max())
        low_52w = float(df["low"].iloc[-window:].min())
        position_52w = (
            (close - low_52w) / (high_52w - low_52w)
            if high_52w > low_52w
            else 0.5
        )
        position_52w = float(np.clip(position_52w, 0.0, 1.0))

        bits = [f"距 MA60 {distance_60:+.2f}%"]
        if ma200 is not None:
            bits.append(f"距 MA200 {distance_200:+.2f}%")
        bits.append(f"52周位置 {position_52w:.2f}")
        if window < 252:
            bits.append(f"（高低位用近 {window} 个交易日近似）")
        interp = "，".join(bits)

        return MaLongTermResult(
            ma60=ma60,
            ma200=ma200,
            distance_to_ma60_pct=distance_60,
            distance_to_ma200_pct=distance_200,
            high_52w=high_52w,
            low_52w=low_52w,
            position_52w=position_52w,
            interpretation=interp,
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("calculate_ma_long_term failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Donchian 通道
# ---------------------------------------------------------------------------

def calculate_donchian(df: pd.DataFrame, period: int = 20) -> Optional[DonchianResult]:
    """不含今日（shift(1)）避免数据泄漏。"""
    try:
        df = _validate(df, min_rows=period + 1)
        if df is None:
            return None
        # 比较时用昨日及之前的高低（shift(1)）
        high_n = df["high"].rolling(period).max().shift(1)
        low_n = df["low"].rolling(period).min().shift(1)

        latest_close = float(df["close"].iloc[-1])
        h = float(high_n.iloc[-1])
        l = float(low_n.iloc[-1])
        if not (np.isfinite(h) and np.isfinite(l)):
            return None

        breakout_status = "无突破"
        days_since = 0

        # 状态判定（首日突破 > 回踩 > 跌破首日 > 反抽 > 无）
        if latest_close > h:
            breakout_status = "向上突破近20日新高（首日）"
        elif latest_close < l:
            breakout_status = "向下跌破近20日新低（首日）"
        else:
            # 回看最近 5 日是否出现过突破，判断是否回踩/反抽
            lookback = min(5, len(df) - 1)
            for i in range(1, lookback + 1):
                idx = -1 - i
                prev_h = float(high_n.iloc[idx]) if np.isfinite(high_n.iloc[idx]) else np.nan
                prev_l = float(low_n.iloc[idx]) if np.isfinite(low_n.iloc[idx]) else np.nan
                prev_close = float(df["close"].iloc[idx])
                if np.isfinite(prev_h) and prev_close > prev_h:
                    breakout_status = "突破后回踩"
                    days_since = i
                    break
                if np.isfinite(prev_l) and prev_close < prev_l:
                    breakout_status = "跌破后反抽"
                    days_since = i
                    break

        if breakout_status == "向上突破近20日新高（首日）":
            interp = f"价格 {latest_close:.2f} 突破 20日新高 {h:.2f}"
        elif breakout_status == "向下跌破近20日新低（首日）":
            interp = f"价格 {latest_close:.2f} 跌破 20日新低 {l:.2f}"
        elif breakout_status == "突破后回踩":
            interp = f"突破后第 {days_since} 日回踩，关注是否站稳"
        elif breakout_status == "跌破后反抽":
            interp = f"跌破后第 {days_since} 日反抽，关注是否再下探"
        else:
            interp = f"区间运行（{l:.2f} - {h:.2f}）"

        return DonchianResult(
            donchian_high_20=h,
            donchian_low_20=l,
            breakout_status=breakout_status,
            days_since_breakout=days_since,
            interpretation=interp,
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("calculate_donchian failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# KDJ
# ---------------------------------------------------------------------------

def calculate_kdj(df: pd.DataFrame, n: int = 9) -> Optional[KdjResult]:
    """标准 9/3/3 KDJ。"""
    try:
        df = _validate(df, min_rows=n + 3)
        if df is None:
            return None
        low_n = df["low"].rolling(n).min()
        high_n = df["high"].rolling(n).max()
        denom = (high_n - low_n).replace(0, np.nan)
        rsv = 100 * (df["close"] - low_n) / denom
        rsv = rsv.fillna(50.0)
        k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
        d = k.ewm(alpha=1 / 3, adjust=False).mean()
        j = 3 * k - 2 * d

        k_last = float(k.iloc[-1])
        d_last = float(d.iloc[-1])
        j_last = float(j.iloc[-1])
        k_prev = float(k.iloc[-2]) if len(k) >= 2 else k_last
        d_prev = float(d.iloc[-2]) if len(d) >= 2 else d_last

        golden = k_prev <= d_prev and k_last > d_last
        dead = k_prev >= d_prev and k_last < d_last

        if golden and k_last < 30:
            status = "金叉超卖区"
        elif dead and k_last > 70:
            status = "死叉超买区"
        elif k_last >= 80:
            status = "钝化"
        elif k_last <= 20:
            status = "低位钝化"
        else:
            status = "中性"

        interp = f"K={k_last:.1f} D={d_last:.1f} J={j_last:.1f}（{status}）"
        return KdjResult(k=k_last, d=d_last, j=j_last, status=status, interpretation=interp)
    except Exception as exc:  # pragma: no cover
        logger.debug("calculate_kdj failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 蜡烛形态（手写 5 个核心）
# ---------------------------------------------------------------------------

def _is_bearish_engulfing(prev: pd.Series, curr: pd.Series) -> bool:
    """前日阳线被当日阴线完全吞没。"""
    return (
        prev["close"] > prev["open"]
        and curr["close"] < curr["open"]
        and curr["open"] >= prev["close"]
        and curr["close"] <= prev["open"]
    )


def _is_bullish_engulfing(prev: pd.Series, curr: pd.Series) -> bool:
    """前日阴线被当日阳线完全吞没。"""
    return (
        prev["close"] < prev["open"]
        and curr["close"] > curr["open"]
        and curr["open"] <= prev["close"]
        and curr["close"] >= prev["open"]
    )


def _is_hammer(bar: pd.Series) -> bool:
    """锤子线：实体小、下影长（≥2 倍实体）、上影 < 实体。"""
    body = abs(bar["close"] - bar["open"])
    rng = bar["high"] - bar["low"]
    if body == 0 or rng == 0:
        return False
    lower = min(bar["open"], bar["close"]) - bar["low"]
    upper = bar["high"] - max(bar["open"], bar["close"])
    return lower >= 2 * body and upper <= body and body / rng < 0.35


def _is_inverted_hammer(bar: pd.Series) -> bool:
    """倒锤：阳线、实体小、上影长（≥2 倍实体）、下影 < 实体。"""
    body = abs(bar["close"] - bar["open"])
    rng = bar["high"] - bar["low"]
    if body == 0 or rng == 0:
        return False
    lower = min(bar["open"], bar["close"]) - bar["low"]
    upper = bar["high"] - max(bar["open"], bar["close"])
    # 与"射击之星"通过阴/阳线区分（这里要求阳线/平线）
    return (
        upper >= 2 * body
        and lower <= body
        and body / rng < 0.35
        and bar["close"] >= bar["open"]
    )


def _is_shooting_star(bar: pd.Series) -> bool:
    """射击之星：阴线 + 上影长（≥2 倍实体）+ 下影 < 实体。"""
    body = abs(bar["close"] - bar["open"])
    rng = bar["high"] - bar["low"]
    if body == 0 or rng == 0:
        return False
    lower = min(bar["open"], bar["close"]) - bar["low"]
    upper = bar["high"] - max(bar["open"], bar["close"])
    return (
        bar["close"] < bar["open"]
        and upper >= 2 * body
        and lower <= body
        and body / rng < 0.35
    )


def _is_doji(bar: pd.Series) -> bool:
    """十字星：开盘 ≈ 收盘（实体 < 全幅 5%）。"""
    rng = bar["high"] - bar["low"]
    if rng == 0:
        return False
    body = abs(bar["close"] - bar["open"])
    return body / rng < 0.05


def detect_candle_patterns(df: pd.DataFrame) -> List[str]:
    """
    扫描最近 2 日的蜡烛形态。

    返回：中文形态名列表，按"今日 / 昨日"排序，最多 2 项。无形态时空列表。
    """
    try:
        if df is None or df.empty or len(df) < 1:
            return []
        out: List[str] = []
        # 今日
        today = df.iloc[-1]
        if len(df) >= 2:
            yesterday = df.iloc[-2]
            if _is_bearish_engulfing(yesterday, today):
                out.append("今日看跌吞没")
            elif _is_bullish_engulfing(yesterday, today):
                out.append("今日看涨吞没")
        if not out:
            if _is_shooting_star(today):
                out.append("今日射击之星")
            elif _is_hammer(today):
                out.append("今日锤子线")
            elif _is_inverted_hammer(today):
                out.append("今日倒锤线")
            elif _is_doji(today):
                out.append("今日十字星")

        # 昨日（独立判断，不与今日冲突）
        if len(df) >= 3:
            yesterday = df.iloc[-2]
            day_before = df.iloc[-3]
            y_pat: Optional[str] = None
            if _is_bearish_engulfing(day_before, yesterday):
                y_pat = "昨日看跌吞没"
            elif _is_bullish_engulfing(day_before, yesterday):
                y_pat = "昨日看涨吞没"
            elif _is_shooting_star(yesterday):
                y_pat = "昨日射击之星"
            elif _is_hammer(yesterday):
                y_pat = "昨日锤子线"
            elif _is_inverted_hammer(yesterday):
                y_pat = "昨日倒锤线"
            elif _is_doji(yesterday):
                y_pat = "昨日十字星"
            if y_pat:
                out.append(y_pat)

        return out[:2]
    except Exception as exc:  # pragma: no cover
        logger.debug("detect_candle_patterns failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# MACD / RSI 数值化补强
# ---------------------------------------------------------------------------

def calculate_macd_extras(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> Optional[MacdExtras]:
    try:
        df = _validate(df, min_rows=slow + signal)
        if df is None:
            return None
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        bar = (dif - dea) * 2
        recent = bar.iloc[-3:].tolist()
        if len(recent) < 3:
            return None
        abs_recent = [abs(x) for x in recent]
        if abs_recent[0] < abs_recent[1] < abs_recent[2]:
            trend = "持续放大"
        elif abs_recent[0] > abs_recent[1] > abs_recent[2]:
            trend = "持续缩短（动能衰减）"
        else:
            trend = "震荡"
        return MacdExtras(bar_recent=[float(x) for x in recent], bar_trend=trend)
    except Exception as exc:  # pragma: no cover
        logger.debug("calculate_macd_extras failed: %s", exc)
        return None


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50.0)


def calculate_rsi_multi_period(df: pd.DataFrame) -> Optional[RsiMultiPeriod]:
    try:
        df = _validate(df, min_rows=25)
        if df is None:
            return None
        r6 = float(_rsi(df["close"], 6).iloc[-1])
        r12 = float(_rsi(df["close"], 12).iloc[-1])
        r24 = float(_rsi(df["close"], 24).iloc[-1])
        # 三线发散：6 > 12 > 24 多头；6 < 12 < 24 空头
        if r6 > r12 > r24 and r6 > 50:
            divergence = "三线发散多头"
        elif r6 < r12 < r24 and r6 < 50:
            divergence = "三线发散空头"
        else:
            divergence = "三线缠绕"
        return RsiMultiPeriod(rsi_6=r6, rsi_12=r12, rsi_24=r24, divergence=divergence)
    except Exception as exc:  # pragma: no cover
        logger.debug("calculate_rsi_multi_period failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 聚合入口
# ---------------------------------------------------------------------------

def compute_extended_indicators(df: Optional[pd.DataFrame]) -> ExtendedIndicators:
    """
    计算所有扩展指标。单指标失败不影响其他指标。

    Args:
        df: 包含 date/open/high/low/close/volume 的 DataFrame，可为 None。

    Returns:
        ExtendedIndicators 容器。所有失败时返回空容器，但不抛异常。
    """
    ext = ExtendedIndicators()
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return ext
    # 排序保证最新在末尾
    try:
        if "date" in df.columns:
            df = df.sort_values("date").reset_index(drop=True)
    except Exception:
        pass

    # 通过模块属性查找（便于测试 monkeypatch）
    import src.technical_indicators as _ti

    for attr, fn_name in (
        ("atr", "calculate_atr"),
        ("adx", "calculate_adx"),
        ("bollinger", "calculate_bollinger"),
        ("obv", "calculate_obv"),
        ("ma_long_term", "calculate_ma_long_term"),
        ("donchian", "calculate_donchian"),
        ("kdj", "calculate_kdj"),
        ("macd_extras", "calculate_macd_extras"),
        ("rsi_multi_period", "calculate_rsi_multi_period"),
    ):
        try:
            fn = getattr(_ti, fn_name)
            setattr(ext, attr, fn(df))
        except Exception as exc:
            logger.warning("compute_extended_indicators: %s failed: %s", fn_name, exc)
            setattr(ext, attr, None)

    try:
        ext.candle_patterns = _ti.detect_candle_patterns(df)
    except Exception as exc:
        logger.warning("compute_extended_indicators: candle_patterns failed: %s", exc)
        ext.candle_patterns = []

    return ext


__all__ = [
    "AtrResult",
    "AdxResult",
    "BollingerResult",
    "ObvResult",
    "MaLongTermResult",
    "DonchianResult",
    "KdjResult",
    "MacdExtras",
    "RsiMultiPeriod",
    "ExtendedIndicators",
    "calculate_atr",
    "calculate_adx",
    "calculate_bollinger",
    "calculate_obv",
    "calculate_ma_long_term",
    "calculate_donchian",
    "calculate_kdj",
    "calculate_macd_extras",
    "calculate_rsi_multi_period",
    "detect_candle_patterns",
    "compute_extended_indicators",
]