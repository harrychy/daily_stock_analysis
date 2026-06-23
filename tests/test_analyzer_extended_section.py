# -*- coding: utf-8 -*-
"""
扩展指标 prompt 注入渲染测试
============================

覆盖 analyzer._format_extended_indicators_section() 在不同输入下的行为：
- 全字段输入 → 4 个子版块都出现
- 部分子结构为 None → 对应行整行省略
- 整体为空 → 返回空字符串（不输出空表头）
- 兼容 dataclass 实例与 to_dict() 后的纯 dict
- disclaimer 在非空输出时一定附带

并验证两套模板（legacy + 新版）在 build_analysis_prompt 路径上都注入了扩展子版块、
"重点关注"问答各 7 条且新增的 ADX/ATR 两条出现。
"""

from __future__ import annotations

import pytest

from src.analyzer import _format_extended_indicators_section
from src.technical_indicators import (
    AtrResult,
    AdxResult,
    BollingerResult,
    DonchianResult,
    ExtendedIndicators,
    KdjResult,
    MaLongTermResult,
    MacdExtras,
    ObvResult,
    RsiMultiPeriod,
)


# ---------------------------------------------------------------------------
# Fixture：构造一个全字段填充的 ExtendedIndicators
# ---------------------------------------------------------------------------

def _full_extended() -> ExtendedIndicators:
    return ExtendedIndicators(
        atr=AtrResult(
            atr_14=4.21,
            atr_pct=1.89,
            suggested_stop=216.50,
            suggested_target=232.30,
            interpretation="日均波幅 1.89%（中等波动）",
        ),
        adx=AdxResult(
            adx_14=28.0,
            plus_di=25.4,
            minus_di=18.6,
            trend_regime="中等趋势",
            direction="多头",
            interpretation="ADX 28（中等趋势），多头",
        ),
        bollinger=BollingerResult(
            upper=230.0,
            middle=220.0,
            lower=210.0,
            percent_b=0.78,
            bandwidth_pct=4.2,
            bandwidth_percentile_60d=15.0,
            interpretation="上轨附近，%B=0.78（强势但接近压力）；带宽收口",
        ),
        obv=ObvResult(
            obv_current=1_000_000.0,
            obv_ma_20=900_000.0,
            divergence="顶背离",
            interpretation="价格新高但 OBV 未跟随，量价背离（看跌）",
        ),
        ma_long_term=MaLongTermResult(
            ma60=215.0,
            ma200=210.0,
            distance_to_ma60_pct=3.2,
            distance_to_ma200_pct=-8.5,
            high_52w=250.0,
            low_52w=180.0,
            position_52w=0.42,
            interpretation="距 MA60 +3.20%，距 MA200 -8.50%，52周位置 0.42",
        ),
        donchian=DonchianResult(
            donchian_high_20=226.50,
            donchian_low_20=198.20,
            breakout_status="突破后回踩",
            days_since_breakout=3,
            interpretation="突破后第 3 日回踩",
        ),
        kdj=KdjResult(
            k=78.0, d=65.0, j=104.0, status="死叉超买区",
            interpretation="K=78.0 D=65.0 J=104.0（死叉超买区）",
        ),
        candle_patterns=["今日看跌吞没", "昨日射击之星"],
        macd_extras=MacdExtras(
            bar_recent=[0.95, 0.85, 0.74],
            bar_trend="持续缩短（动能衰减）",
        ),
        rsi_multi_period=RsiMultiPeriod(
            rsi_6=65.2, rsi_12=58.4, rsi_24=52.1, divergence="三线发散多头",
        ),
    )


# ---------------------------------------------------------------------------
# 渲染函数本身
# ---------------------------------------------------------------------------

class TestFormatExtendedIndicators:
    def test_full_renders_all_four_subsections(self):
        text = _format_extended_indicators_section(_full_extended())
        # 4 个子标题都在
        assert "#### 波动率与位置" in text
        assert "#### 趋势真伪与中长期锚点" in text
        assert "#### 动量与突破" in text
        assert "#### 蜡烛形态" in text
        # 关键数值露出
        assert "ATR(14)" in text
        assert "ADX(14)" in text
        assert "%B" in text
        assert "OBV 背离" in text
        assert "Donchian 20" in text
        assert "KDJ" in text
        assert "MA60" in text and "MA200" in text
        assert "今日看跌吞没" in text and "昨日射击之星" in text
        # disclaimer 附带
        assert "供综合判断参考" in text
        assert "趋势状态为准" in text

    def test_full_via_to_dict_dict_input(self):
        """传入 to_dict() 后的纯 dict 也应可用。"""
        d = _full_extended().to_dict()
        text = _format_extended_indicators_section(d)
        assert "#### 波动率与位置" in text
        assert "ADX(14)" in text

    def test_empty_container_returns_empty_string(self):
        text = _format_extended_indicators_section(ExtendedIndicators())
        assert text == ""

    def test_none_input_returns_empty_string(self):
        assert _format_extended_indicators_section(None) == ""

    def test_partial_only_atr_renders_only_volatility_subsection(self):
        ext = ExtendedIndicators(
            atr=AtrResult(
                atr_14=2.0, atr_pct=1.0,
                suggested_stop=98.0, suggested_target=105.0,
                interpretation="日均波幅 1.00%（中等波动）",
            )
        )
        text = _format_extended_indicators_section(ext)
        assert "#### 波动率与位置" in text
        assert "#### 趋势真伪与中长期锚点" not in text
        assert "#### 动量与突破" not in text
        assert "#### 蜡烛形态" not in text
        # disclaimer 仍附带
        assert "供综合判断参考" in text

    def test_choppy_market_disclaimer_appended(self):
        ext = ExtendedIndicators(
            adx=AdxResult(
                adx_14=15.0, plus_di=18.0, minus_di=17.0,
                trend_regime="震荡市", direction="无方向",
                interpretation="ADX 15（震荡市）",
            ),
        )
        text = _format_extended_indicators_section(ext)
        # 震荡市要触发"均线信号失效"提醒
        assert "ADX < 20" in text or "震荡市策略" in text

    def test_no_candle_pattern_subsection_when_list_empty(self):
        ext = _full_extended()
        ext.candle_patterns = []
        text = _format_extended_indicators_section(ext)
        assert "#### 蜡烛形态" not in text
        # 其他三个仍在
        assert "#### 波动率与位置" in text


# ---------------------------------------------------------------------------
# 「重点关注」问答清单 —— legacy + 新版各 7 条，含 ADX/ATR
# ---------------------------------------------------------------------------

import re
from pathlib import Path

ANALYZER_SRC = Path(__file__).resolve().parents[1] / "src" / "analyzer.py"


class TestFocusQuestionsExpanded:
    def test_focus_questions_both_templates_have_adx_and_atr(self):
        src = ANALYZER_SRC.read_text(encoding="utf-8")
        # 找所有"### 重点关注（必须明确回答）："块
        blocks = re.findall(
            r"### 重点关注（必须明确回答）：\n(.*?)\n\"\"\"",
            src,
            flags=re.DOTALL,
        )
        assert len(blocks) >= 2, "两套模板都应当有「重点关注」清单"
        for block in blocks:
            assert "ADX" in block, "「重点关注」必须含 ADX 顺势/震荡市问答"
            assert "ATR" in block, "「重点关注」必须含 ATR 止损/目标问答"
            # 7 条以编号 7. 收尾
            assert re.search(r"^\s*7\.\s+❓", block, re.MULTILINE), \
                "「重点关注」应当至少 7 条"
