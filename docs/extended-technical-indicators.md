# 扩展技术指标（LLM 提示词增强）

本文档说明第二阶段 LLM 综合分析所收到的扩展技术指标版块。

> 相关：[设计文档](superpowers/specs/2026-06-08-technical-indicators-expansion-design.md) · 源码 `src/technical_indicators.py` · 注入 `src/analyzer.py::_format_extended_indicators_section`

## 设计目标

为第二阶段 LLM 综合分析提供更丰富的技术面信号。**不**修改第一阶段 `signal_score` 排序逻辑，不进入第一阶段 SSE 轻量字段，不影响 Web/Desktop 前端契约。

## 指标清单

| 类别 | 指标 | 字段 | 数据来源 |
|---|---|---|---|
| 波动率 | ATR(14) Wilder 平滑 | `atr_14`、`atr_pct`、`suggested_stop`（close-1.5×ATR）、`suggested_target`（close+2.5×ATR） | OHLC |
| 趋势真伪 | ADX(14) Welles Wilder | `adx_14`、`plus_di`、`minus_di`、`trend_regime`（震荡/弱/中/强）、`direction`（多/空/无） | OHLC |
| 通道位置 | 布林带 20/±2σ | `upper/middle/lower`、`percent_b`、`bandwidth_pct`、`bandwidth_percentile_60d` | Close |
| 量价背离 | OBV + 20日新高/新低对照 | `obv_current`、`obv_ma_20`、`divergence`（顶/底/无） | Close + Volume |
| 中长期锚点 | MA60 / MA200 / 52 周高低 | `ma60`、`ma200`（< 200 天为 None）、`distance_to_ma60_pct`、`distance_to_ma200_pct`、`high_52w / low_52w`、`position_52w`（0-1） | Close + High/Low |
| 突破/回踩 | Donchian 20（不含今日） | `donchian_high_20 / low_20`、`breakout_status`（首日突破/回踩/反抽/无）、`days_since_breakout` | High/Low + Close |
| 动量 | KDJ 9/3/3 | `k / d / j`、`status`（金叉超卖/死叉超买/钝化/中性） | High/Low + Close |
| 形态 | 蜡烛形态（手写 5 个） | `candle_patterns: List[str]`，最多最近 2 日 | OHLC |
| MACD 数值化 | 最近 3 根柱 + 趋势 | `bar_recent`、`bar_trend`（持续放大/缩短/震荡） | Close |
| RSI 数值化 | 6/12/24 + 三线发散 | `rsi_6/12/24`、`divergence`（三线发散多/空/缠绕） | Close |

## Prompt 版式

`_format_extended_indicators_section()` 输出 4 个子版块（任一为空整段省略），末尾附统一 disclaimer：

```
#### 波动率与位置
| ATR(14) … | 建议止损 … | 建议目标 … | 布林位置 %B … | 布林带宽 … |

#### 趋势真伪与中长期锚点
| ADX(14) … | +DI / -DI … | 价格 vs MA60 / MA200 … | 52周位置 … |

> ⚠️ ADX < 20 时均线信号失效，请明确按震荡市策略评估。  ← 震荡市才出现

#### 动量与突破
| MACD 柱(最近3根) … | RSI 6/12/24 … | KDJ K/D/J … | Donchian 20 … | OBV 背离 … |

#### 蜡烛形态（最近 2 日）
- 今日…
- 昨日…
> 形态需结合趋势/位置确认；单一形态信号不构成结论。

> 上述扩展指标供综合判断参考，不替代主表的趋势/乖离率/系统评分。
> 同向多指标共振时增强信心；冲突时优先以趋势状态为准并写明矛盾点。
```

## 「重点关注」新增问答

legacy 与新版模板的「重点关注（必须明确回答）」清单都新增了两条：

```
6. ❓ 当前 ADX 是否支持顺势交易？震荡市请明确说明波段或观望策略
7. ❓ 用 ATR 推算的止损/目标位是否落在合理支撑/压力上？是否需调整？
```

## 容错与降级

- 单指标计算失败（数据不足 / 异常）→ 对应字段 None，**永不向外抛**。
- 聚合 `compute_extended_indicators()` 8 个独立 try/except；任一失败不影响其他指标。
- `StockTrendAnalyzer.analyze()` 末尾整体失败 → `result.extended = ExtendedIndicators()`（空容器），`signal_score` 不受影响。
- prompt 渲染：子结构 None → 整行省略；4 个子表全空 → 整段返回空字符串，不输出空表头。

## 数据窗口要求

`src/core/pipeline.py` 的历史 K 线取数窗口由 89 天延长至 260 天，覆盖 MA200 与 52 周高低位。新引入股票需要等到 fetch 累积到 200 天后才出 MA200。

## 不在范围内

- 不修改第一阶段 `signal_score` 公式与权重。
- 不进入第一阶段 SSE 事件，保持轻量。
- 不修改 Web 前端与桌面端。
- 不引入第三方依赖（talib / pandas-ta）。
- 不修改 `technical_screening_history` DB schema。
