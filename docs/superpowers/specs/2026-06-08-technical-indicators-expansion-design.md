# 技术面指标扩展（ATR / ADX / 布林带 / OBV / MA60-MA200-52周 / Donchian / KDJ / 蜡烛形态）

- 状态：草案待评审
- 创建日期：2026-06-08
- 关联：`src/stock_analyzer.py`、`src/analyzer.py`、`src/core/pipeline.py`、`docs/two-stage-screening.md`

## 1. 背景与动机

当前两阶段筛选第二阶段给 LLM 的技术面字段集中在均线 / 乖离 / 量能 / MACD / RSI 五项，缺以下结构性维度：

1. 无任何**波动率**度量（ATR）— 模型给止损/目标价没有锚点。
2. 无**趋势真伪过滤器**（ADX）— 震荡市的均线信号会反复打脸。
3. 无**自归一化位置度量**（布林带 %B / 带宽分位数）— 不同股票的"5% 偏离"不可比。
4. 无**量价背离检测**（OBV）— 缺少经典反转预警。
5. 无**中长期锚点**（MA60 已算未露出、MA200 缺失、52 周高低位缺失）。
6. 无**突破/回踩状态机**（Donchian）— 模型识别不出"首日突破"与"突破后回踩"。
7. 无 KDJ — A 股散户惯用，模型推理时没有这层确认。
8. MACD/RSI 仅以文字呈现，**数值没露出**，无法在不同股票间细粒度比较。
9. 无规则化**蜡烛形态识别**（吞没 / 锤子 / 射击之星 / 十字星等）。

本设计在不动 `signal_score` 第一阶段排序逻辑的前提下，把上述 8 类（含蜡烛形态）扩展指标加入到第二阶段 LLM prompt。

## 2. 范围与非目标

### 范围

- 新增模块 `src/technical_indicators.py`，提供 8 类指标的纯计算函数与 `ExtendedIndicators` 数据结构。
- `StockTrendAnalyzer.analyze()` 末尾追加一次扩展指标计算，结果挂在 `TrendAnalysisResult.extended` 字段。
- `analyzer.py` prompt 模板新增 4 个子版块（波动率与位置 / 趋势真伪与中长期锚点 / 动量与突破 / 蜡烛形态），legacy 与新版模板同时注入。
- `analyzer.py:3270-3286` 的"重点关注"问题清单新增 2 条强制问答，绑定 ADX 与 ATR 止损位。
- `pipeline.py:406` 与 `:3125` 的 K 线取数窗口由 89 天 → 260 天，使 MA200、52 周高低位可计算。
- 完整单元测试覆盖（指标计算正确性 + 容错 + prompt 渲染 + 现有 signal_score 回归 + 数据窗口断言）。
- `docs/two-stage-screening.md` 与 `docs/CHANGELOG.md` 同步更新。

### 非目标

- **不**修改第一阶段 `signal_score` 计算公式与权重（保持原有 6 维 100 分制）。
- **不**修改 `ScreeningResult` 字段集，**不**修改 `technical_screening_history` DB 表。
- **不**把扩展指标推入第一阶段 SSE 事件，保持轻量。
- **不**修改 Web 前端（`apps/dsa-web/`）与桌面端（`apps/dsa-desktop/`）。
- **不**引入第三方依赖（不装 talib / pandas-ta），蜡烛形态手写实现。
- **不**做 retention / DB 清理策略，存量 K 线表数据量增大可接受。

## 3. 关键设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 蜡烛形态实现 | 手写 5 个核心形态（吞没/锤子/倒锤/射击之星/十字星） | 避免 talib C 依赖，保持环境一致 |
| 字段组织 | 独立子结构 `ExtendedIndicators` + 各指标自己的 dataclass | 单一职责，可独立测试，避免 `TrendAnalysisResult` 膨胀至 60+ 字段 |
| signal_score 公式 | 保持不变 | 排序行为兼容，回归风险最小 |
| 数据窗口 | `pipeline.py:406` 与 `:3125` 同时改 89 → 260 天 | 两阶段都能拿到 MA200 与 52 周高低 |
| prompt 模板 | 抽出 `_format_extended_indicators_section()`，legacy 与新版模板都注入 | 避免复制；用户切到 legacy 也能受益 |
| 计算位置 | 独立模块 `src/technical_indicators.py` | 关注点分离；现有 `stock_analyzer.py` 已 800+ 行，不再扩张 |

## 4. 模块边界

```
新增：
  src/technical_indicators.py             # 8 类指标的纯计算函数 + dataclass
  tests/test_technical_indicators.py      # 单元测试
  tests/test_analyzer_extended_section.py # prompt 渲染测试
  tests/test_stock_analyzer_signal_score_regression.py  # 回归
  tests/test_pipeline_data_window.py      # 数据窗口断言

修改：
  src/stock_analyzer.py                   # TrendAnalysisResult 加 .extended；analyze() 末尾调一次
  src/analyzer.py                         # 抽出 _format_extended_indicators_section()，两套模板都注入
  src/core/pipeline.py                    # 数据窗口 89→260（两处）
  docs/two-stage-screening.md             # 补充新增指标说明
  docs/CHANGELOG.md                       # [Unreleased] 新增一行 [新功能]
```

## 5. 数据流

```
pipeline 取 260 天 K 线
        ↓
StockTrendAnalyzer.analyze(df, code)
  ├─ 现有 7 步逻辑（趋势/乖离/量能/支撑/MACD/RSI/合成）  ← 不动
  └─ NEW: result.extended = compute_extended_indicators(df)
        ↓
analyzer.py: prompt 拼装
  ├─ 原有"技术与结构分析"表（不动）
  └─ NEW: + _format_extended_indicators_section(result.extended)
        ↓
LLM 收到的 prompt：原表格 + 4 个新子版块（最多）+ 2 条新增"重点关注"问题
```

## 6. 数据结构

### 6.1 顶层容器

```python
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

    # 现有 MACD / RSI 数值化补强（不重复计算）
    macd_extras: Optional[MacdExtras] = None
    rsi_multi_period: Optional[RsiMultiPeriod] = None

    def to_dict(self) -> Dict[str, Any]: ...
```

### 6.2 各指标子结构

#### `AtrResult`
- `atr_14: float`、`atr_pct: float`（ATR / close * 100）
- `suggested_stop: float`（close - 1.5×ATR）、`suggested_target: float`（close + 2.5×ATR）
- `interpretation: str`（例："日均波幅 1.89%（中等波动）"）

#### `AdxResult`
- `adx_14: float`、`plus_di: float`、`minus_di: float`
- `trend_regime: str`：`"强趋势"` / `"中等趋势"` / `"弱趋势"` / `"震荡市"`
- `direction: str`：`"多头"` / `"空头"` / `"无方向"`
- `interpretation: str`

#### `BollingerResult`
- `upper / middle / lower: float`
- `percent_b: float`（(close - lower) / (upper - lower)）
- `bandwidth_pct: float`、`bandwidth_percentile_60d: float`（0-100）
- `interpretation: str`

#### `ObvResult`
- `obv_current / obv_ma_20: float`
- `divergence: str`：`"顶背离"` / `"底背离"` / `"无背离"`
- `interpretation: str`

#### `MaLongTermResult`
- `ma60: float`、`ma200: Optional[float]`（< 200 天为 None）
- `distance_to_ma60_pct: float`、`distance_to_ma200_pct: Optional[float]`
- `high_52w / low_52w: float`、`position_52w: float`（0-1）
- `interpretation: str`

#### `DonchianResult`
- `donchian_high_20 / donchian_low_20: float`
- `breakout_status: str`：
  - `"向上突破近20日新高（首日）"`
  - `"突破后回踩"`
  - `"向下跌破近20日新低（首日）"`
  - `"跌破后反抽"`
  - `"无突破"`
- `days_since_breakout: int`、`interpretation: str`

#### `KdjResult`
- `k / d / j: float`
- `status: str`：`"金叉超卖区"` / `"死叉超买区"` / `"钝化"` / `"中性"`
- `interpretation: str`

#### `candle_patterns: List[str]`
中文形态名 + 出现日，按时间倒序，**最多展示最近 2 日**（today / yesterday）。无形态时为空列表。例：`["今日看跌吞没", "昨日射击之星"]`。

支持形态：
1. 看跌吞没 / 看涨吞没
2. 锤子线 / 倒锤
3. 射击之星（上影长 + 阴线）
4. 长下影十字（蜻蜓）
5. 十字星（doji）

#### `MacdExtras`
- `bar_recent: List[float]`（最近 3 根柱）
- `bar_trend: str`：`"持续放大"` / `"持续缩短（动能衰减）"` / `"震荡"`

#### `RsiMultiPeriod`
- `rsi_6 / rsi_12 / rsi_24: float`
- `divergence: str`：`"三线发散多头"` / `"三线发散空头"` / `"三线缠绕"`

> 注：`MacdExtras` 与 `RsiMultiPeriod` 中的数值都来自现有 `TrendAnalysisResult`，不重复计算。

## 7. 计算细节

### 7.1 ATR（Wilder 平滑）

```python
prev_close = df['close'].shift(1)
tr = pd.concat([
    df['high'] - df['low'],
    (df['high'] - prev_close).abs(),
    (df['low'] - prev_close).abs(),
], axis=1).max(axis=1)
atr = tr.ewm(alpha=1/14, adjust=False).mean()
```

首日 prev_close 为 NaN → TR 第一行只用 `high - low`。

### 7.2 ADX（标准 Welles Wilder 1978）

```python
up_move = df['high'].diff()
down_move = -df['low'].diff()
plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0)
minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0)

atr_for_di = tr.ewm(alpha=1/14, adjust=False).mean()
plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_for_di
minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_for_di

dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
adx = dx.ewm(alpha=1/14, adjust=False).mean()
```

阈值：`< 20` 震荡 / `20-25` 弱趋势 / `25-40` 中等 / `≥ 40` 强趋势。`(plus_di + minus_di) == 0` 时 DX 设为 0。

### 7.3 布林带（带宽 60 日分位数）

```python
mid = df['close'].rolling(20).mean()
std = df['close'].rolling(20).std(ddof=0)
upper = mid + 2 * std
lower = mid - 2 * std
bandwidth = (upper - lower) / mid * 100
percentile_60d = bandwidth.iloc[-60:].rank(pct=True).iloc[-1] * 100
```

### 7.4 OBV 与背离检测

```python
sign = np.sign(df['close'].diff()).fillna(0)
obv = (sign * df['volume']).cumsum()

recent = df.iloc[-20:]
price_high_idx = recent['close'].idxmax()
obv_high_idx = obv.iloc[-20:].idxmax()
divergence = (
    "顶背离" if price_high_idx == recent.index[-1] and obv_high_idx != recent.index[-1]
    else "底背离" if price_low_idx == recent.index[-1] and obv_low_idx != recent.index[-1]
    else "无背离"
)
```

### 7.5 中长期锚点

```python
ma60 = df['close'].rolling(60).mean().iloc[-1]
ma200 = df['close'].rolling(200).mean().iloc[-1] if len(df) >= 200 else None
high_52w = df['high'].iloc[-min(252, len(df)):].max()
low_52w = df['low'].iloc[-min(252, len(df)):].min()
position_52w = (close - low_52w) / (high_52w - low_52w)
```

数据 < 252 天时 52 周高低位用现有数据近似，并在 `interpretation` 标注。

### 7.6 Donchian 通道（不含今日避免数据泄漏）

```python
high_20 = df['high'].rolling(20).max().shift(1)
low_20 = df['low'].rolling(20).min().shift(1)
```

突破状态机判定（顺序：首日突破 > 回踩 > 跌破首日 > 反抽 > 无）。

### 7.7 KDJ（标准 9/3/3）

```python
low_9 = df['low'].rolling(9).min()
high_9 = df['high'].rolling(9).max()
rsv = 100 * (df['close'] - low_9) / (high_9 - low_9)
k = rsv.ewm(alpha=1/3, adjust=False).mean()
d = k.ewm(alpha=1/3, adjust=False).mean()
j = 3 * k - 2 * d
```

状态：金叉 + K<20 → 金叉超卖区；死叉 + K>80 → 死叉超买区；K 在 80+ 持续多日 → 钝化；其他 → 中性。

### 7.8 蜡烛形态（手写 5 个）

```python
def detect_bearish_engulfing(df) -> bool:
    if len(df) < 2: return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    return (
        prev['close'] > prev['open']
        and curr['close'] < curr['open']
        and curr['open'] >= prev['close']
        and curr['close'] <= prev['open']
    )

def detect_hammer(df) -> bool:
    bar = df.iloc[-1]
    body = abs(bar['close'] - bar['open'])
    if body == 0:
        return False
    lower_shadow = min(bar['open'], bar['close']) - bar['low']
    upper_shadow = bar['high'] - max(bar['open'], bar['close'])
    return lower_shadow >= 2 * body and upper_shadow <= 0.3 * body

# 类似实现 detect_bullish_engulfing / detect_shooting_star / detect_doji / detect_inverted_hammer
```

每个形态附 docstring 引用经典定义。

## 8. Prompt 集成

### 8.1 公开 API

```python
def _format_extended_indicators_section(
    ext: Optional[ExtendedIndicators],
    *,
    report_language: str = "zh",
) -> str:
    """返回 markdown；ext 为空或全 None 时返回空字符串。"""
```

注入位置：`analyzer.py:3140`（legacy 模板）与 `analyzer.py:3172`（新版模板）的 `risk_factors` 行之后、一致性约束之前。

### 8.2 输出版式

#### 子版块 ①：波动率与位置
```
#### 波动率与位置
| 指标 | 数值 | 解读 |
|------|------|------|
| ATR(14) | 4.21 元（1.89%） | 日均波幅，中等波动 |
| 建议止损 | 216.50 元 | 现价 - 1.5×ATR（技术止损位） |
| 建议目标 | 232.30 元 | 现价 + 2.5×ATR（风报比 1.67） |
| 布林位置 %B | 0.78 | 上轨附近，强势但接近压力 |
| 布林带宽 | 4.2%（近60日 P15） | 带宽收口，注意变盘 |
```

#### 子版块 ②：趋势真伪与中长期锚点
```
#### 趋势真伪与中长期锚点
| 指标 | 数值 | 解读 |
|------|------|------|
| ADX(14) | 28 | 中等趋势 |
| +DI / -DI | 25.4 / 18.6 | 多头方向 |
| 价格 vs MA60 | +3.2% | 中期趋势上方 |
| 价格 vs MA200 | -8.5% | 牛熊分界下方（弱势） |
| 52周位置 | 0.42（中位偏下） | 距高点 -28%、距低点 +35% |

> ADX < 20 时均线信号失效，请明确按震荡市策略评估。
```

#### 子版块 ③：动量与突破
```
#### 动量与突破
| 指标 | 数值 | 解读 |
|------|------|------|
| MACD DIF/DEA | 0.82 / 0.45 | 柱状图 +0.74 |
| MACD 柱变化 | 连续 3 日缩短 | ⚠️ 多头动能减弱 |
| RSI 6/12/24 | 65.2 / 58.4 / 52.1 | 三线发散多头 |
| KDJ K/D/J | 78 / 65 / 104 | 死叉超买区 |
| Donchian 20 | 高 226.50 / 低 198.20 | 突破后回踩（距突破 3 日） |
| OBV 背离 | 顶背离 | ⚠️ 价格新高但量未跟 |
```

#### 子版块 ④：蜡烛形态
```
#### 蜡烛形态（最近 2 日）
- 今日：看跌吞没
- 昨日：射击之星

> 形态需结合趋势/位置确认；单一形态信号不构成结论。
```

无形态时整段省略。

### 8.3 一致性提示

4 个子表格末尾追加固定 disclaimer：

```
> 上述扩展指标供综合判断参考，不替代主表的趋势/乖离率/系统评分。
> 同向多指标共振时增强信心；冲突时优先以趋势状态为准并写明矛盾点。
```

### 8.4 "重点关注"新增问答

`analyzer.py:3270-3286` 两套模板都新增：
```
6. ❓ 当前 ADX 是否支持顺势交易？震荡市请明确说明波段或观望策略。
7. ❓ 用 ATR 推算的止损/目标位是否落在合理支撑/压力上？是否需调整？
```

### 8.5 N/A 显示约定

- 数值字段为 None：渲染 `N/A`
- 解读字段为空：渲染 `—`
- 整个子结构为 None：该行整行省略
- 整个子表全空：该子表头省略，不输出空表

## 9. 错误处理（4 层）

| 层级 | 行为 | 日志级别 |
|---|---|---|
| 单指标 `calculate_*` | 数据不足或异常 → 返回 None；永不抛出 | debug |
| 聚合 `compute_extended_indicators` | 8 个独立 try/except；失败的字段为 None | warning（每只股票） |
| `analyze()` 调用处 | 整体失败 → `result.extended = ExtendedIndicators()`，`signal_score` 不受影响 | warning |
| prompt 渲染 | 子表全空跳过；空容器返回空字符串 | 无 |

## 10. 数据窗口拉长的兼容性

`pipeline.py:406` 与 `:3125` 改 89 → 260：

| 风险 | 评估 | 处置 |
|---|---|---|
| 首次 fetch 耗时 | 各 fetcher 已支持 90+ 天历史，实际增量在网络层 | 复用现有 `fetch_and_save_stock_data` 断点续传，无新代码 |
| `daily_kline` 表数据量 | 单股 ~89 行 → ~260 行（3×）；存量股影响累积 | 接受。日 K 表本来用于长存；不引入 retention 策略 |
| 现有 `analyze()` 行为 | 算法只看 latest 几行；MA60 计算更稳 | 增加回归测试，断言 signal_score 在已知样本上不变 |
| 增量 fetch 路径 | `db.get_data_range` 已支持任意区间 | 不改 fetch 层 |

## 11. 测试策略

### 11.1 新增测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/test_technical_indicators.py` | 8 类指标 × 3-5 用例 + 蜡烛形态正反例 + 容错路径 |
| `tests/test_analyzer_extended_section.py` | prompt 渲染（全字段/部分/全空/legacy/新版） |
| `tests/test_stock_analyzer_signal_score_regression.py` | 3-5 只构造样本，断言 signal_score / trend_status / buy_signal 不变 |
| `tests/test_pipeline_data_window.py` | 断言两处取数窗口为 260 天 |

### 11.2 关键测试矩阵

| 测试 | 用例 |
|---|---|
| `test_atr_known_values` | 人工 OHLC，断言 ATR 与手算一致到 4 位小数 |
| `test_atr_insufficient_data` | < 15 行返回 None |
| `test_adx_strong_trend` | 单调上涨 → ADX > 40，direction="多头" |
| `test_adx_choppy` | 上下震荡 → ADX < 20，trend_regime="震荡市" |
| `test_bollinger_squeeze_detected` | 收口数据 → bandwidth_percentile_60d < 20 |
| `test_obv_bearish_divergence` | 价新高 + 量缩 → "顶背离" |
| `test_ma_long_term_insufficient_for_ma200` | 100 行数据 → ma200=None |
| `test_donchian_breakout_today` | 今日突破 → "向上突破近20日新高（首日）" |
| `test_donchian_pullback` | 突破后 3 日回踩 → "突破后回踩" |
| `test_kdj_overbought_death_cross` | K 高位下穿 D → "死叉超买区" |
| `test_compute_one_indicator_raises` | monkeypatch ADX 抛异常 → 其他 7 个仍出 |
| `test_format_section_full` | 全字段 → 4 子表 + disclaimer |
| `test_format_section_all_none` | 空容器 → 空字符串 |
| `test_signal_score_regression_strong_bull` | 多头样本 signal_score 与改前一致 |
| `test_screening_window_is_260_days` | `_screen_one_stock` 取 260 天 |

### 11.3 蜡烛形态测试

每形态正反各 1：吞没（多/空各 1）/ 锤子 / 倒锤 / 射击之星 / 十字星，共 10 个用例。

## 12. 验证矩阵

```bash
# 1. 语法检查
.venv/bin/python -m py_compile \
    src/technical_indicators.py src/stock_analyzer.py \
    src/analyzer.py src/core/pipeline.py

# 2. 新增 + 关联测试
.venv/bin/python -m pytest \
    tests/test_technical_indicators.py \
    tests/test_analyzer_extended_section.py \
    tests/test_stock_analyzer_signal_score_regression.py \
    tests/test_pipeline_data_window.py \
    tests/test_stock_analyzer_bias.py \
    tests/test_stock_analyzer_rsi.py \
    tests/test_pipeline_realtime_indicators.py -v

# 3. 整体 backend gate
./scripts/ci_gate.sh

# 4. 全量离线
.venv/bin/python -m pytest -m "not network" -q
```

Web 前端不动（新指标不进 SSE，不进第一阶段表格），跳过 web-gate；桌面端不动。

## 13. 文档更新

- `docs/two-stage-screening.md` 新增"扩展指标"章节，列出 8 类指标与 prompt 版式。
- `docs/CHANGELOG.md` `[Unreleased]` 段新增一行：
  ```
  - [新功能] 第二阶段 LLM prompt 新增 ATR/ADX/布林带/OBV/MA60-200/Donchian/KDJ/蜡烛形态 8 类技术面指标
  ```

## 14. 交付节奏

按用户要求：**先本地实现 + 验证，不提 PR**。后续由用户决定如何整理提交。

## 15. 回滚方式

- 单次提交内完成。如需回滚，`git revert <commit>` 即可。
- 数据窗口改动可独立保留（仅延长 fetch 范围，对现有逻辑无影响）。
- prompt 改动若发现质量问题，可改 `_format_extended_indicators_section` 返回空字符串临时禁用，无需回滚指标计算代码。

## 16. 已知限制 / 后续可选

- **港股/美股**：基本面/资金/筹码受数据源限制；本次扩展指标都是基于 OHLCV，不受市场影响。
- **MA200 在数据 < 200 天时**：直接为 None，prompt 显示 N/A。新引入股票需要等到 fetch 累积足够历史。
- **52 周高低位在数据 < 252 天时**：用现有数据近似，并在 `interpretation` 标注。
- **不进入 signal_score**：本次明确不调权。后续若希望第一阶段排序也用扩展指标，需独立设计 + 回归基准。
- **蜡烛形态噪声**：单一形态信号本身置信度不高，prompt 已用 disclaimer 强调"需结合趋势/位置"，由 LLM 综合判断。

## 17. 决策记录（与用户确认的关键选择）

| 问题 | 选择 |
|---|---|
| 范围 | 全部 P0+P1+P2（8 类） |
| 蜡烛形态实现 | 手写纯 Python（5 个核心形态） |
| 字段组织 | `ExtendedIndicators` 子结构 |
| signal_score | 公式不变 |
| K 线窗口 | 89 → 260 天，两处同步 |
| prompt 模板 | 抽出独立函数，legacy + 新版都补 |
| 实现架构 | 独立模块 `src/technical_indicators.py` |
| 交付节奏 | 先本地实现 + 验证，不提 PR |