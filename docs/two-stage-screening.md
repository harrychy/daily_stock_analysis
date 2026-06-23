# 两阶段股票筛选

## 是什么

把"日常自选股监控"分解成两个阶段：

1. **技术面预筛（不调 LLM）**：用 Python 死规则计算 `signal_score`（0-100，综合 MA/MACD/RSI/量能等技术指标），批量扫描可一次最多 200 只股票。
2. **Top N 进入 LLM 综合分析**：用户根据第一阶段结果手挑 Top N，仅这些股票才会真正调 LLM（Claude Opus 等），且自动跳过"今日已跑过 LLM 分析"的股票。

预期能把 LLM 调用量降到原来的 1/5 ~ 1/10，对"日级监控自选池"场景特别合适。

## 用户视角的工作流

1. 浏览器打开首页 → 当未选中任何报告时，"开始分析"卡片显示"🚀 两阶段筛选"按钮
2. 进入 `/two-stage-screening` 页面
3. **股票池输入**：从自选股多选 + 文本框手输（逗号 / 换行 / 空格 任意分隔），合并去重
4. 点击"开始技术面扫描"
5. 进度条 + 表格实时刷新（SSE 推送）
6. 跑完后输入 Top N（每次手填，无默认值）
7. 点击"提升到 LLM 分析"
8. 跳回首页观察 LLM 分析进度（复用现有 `/api/v1/analysis/tasks/stream`）

## API 契约

### POST `/api/v1/screening/technical`

启动一次技术面预筛。

**Request**:
```json
{
  "stock_codes": ["NVDA", "AAPL", "600519", "hk00700"]
}
```

**约束**：
- `stock_codes` 长度 1 ~ 200
- 自动去重 + 标准化（大写、剥空白）

**Response 202**:
```json
{
  "screening_id": "472f5b926cfb",
  "parent_task_id": "7a034907baeb447dadd3f6172677802b",
  "total_count": 4
}
```

### GET `/api/v1/screening/{screening_id}`

查询筛选状态/结果。优先从内存返回（实时），进程重启后从 DB 兜底返回。

**Response 200**:
```json
{
  "screening_id": "472f5b926cfb",
  "status": "completed",
  "total_count": 4,
  "success_count": 4,
  "failed_count": 0,
  "started_at": "2026-06-03T17:00:24.040181",
  "completed_at": "2026-06-03T17:00:25.520224",
  "results": [
    {
      "code": "NVDA",
      "name": "英伟达",
      "signal_score": 64,
      "trend_strength": 55.0,
      "trend_status": "弱势多头",
      "buy_signal": "买入",
      "ma_alignment": "弱势多头，MA5>MA10 但 MA10≤MA20",
      "current_price": 222.82,
      "volume_ratio": 0.97,
      "error_msg": ""
    }
  ]
}
```

`status` 取值：`pending` / `running` / `completed` / `failed`。

### POST `/api/v1/screening/{screening_id}/promote`

把 Top N 提升到 LLM 综合分析队列。

**Request**:
```json
{
  "top_n": 5,
  "report_type": "detailed"
}
```

**约束**：
- `top_n >= 1`
- `report_type ∈ {simple, detailed, full, brief}`

**Response 200**:
```json
{
  "screening_id": "472f5b926cfb",
  "requested_top_n": 5,
  "promoted_count": 3,
  "promoted_codes": ["AAPL", "600519", "hk00700"],
  "skipped_already_analyzed": ["NVDA", "BABA"],
  "accepted_tasks": [
    {"task_id": "...", "stock_code": "AAPL", "status": "pending"}
  ],
  "duplicate_tasks": []
}
```

服务端逻辑：
1. 按 `signal_score desc` 取前 `top_n` 只
2. 查 `AnalysisHistory` 过滤当日已跑过 LLM 的股票
3. 调现有 `task_queue.submit_tasks_batch` 提交 LLM 异步任务

### SSE 事件契约

复用现有端点 `GET /api/v1/analysis/tasks/stream`。新增以下事件类型：

| 事件类型 | 数据字段 | 时机 |
|---|---|---|
| `screening_started` | `{screening_id, task_id, total_count, started_at}` | 启动后立即推送 |
| `screening_score` | `{screening_id, code, name, signal_score, trend_strength, trend_status, buy_signal, ma_alignment, current_price, volume_ratio, error_msg, progress}` | 每只股票完成时推送 |
| `screening_completed` | `{screening_id, total_count, success_count, failed_count, saved_count, completed_at}` | 整轮完成时推送 |
| `screening_failed` | `{screening_id, error}` | 整轮失败时推送 |

未识别的 event_type 在老前端会被自动忽略，不会破坏现有 SSE 消费。

## 数据库 Schema

### `technical_screening_history` 表

```sql
CREATE TABLE technical_screening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    screening_id VARCHAR(64) NOT NULL,        -- 批次 ID
    code VARCHAR(10) NOT NULL,
    name VARCHAR(50),
    signal_score INTEGER DEFAULT 0,
    trend_strength FLOAT DEFAULT 0.0,
    trend_status VARCHAR(50),
    buy_signal VARCHAR(20),
    ma_alignment VARCHAR(100),
    current_price FLOAT,
    volume_ratio FLOAT,
    error_msg TEXT,
    created_at DATETIME
);
CREATE INDEX ix_tech_screen_code_time ON technical_screening_history (code, created_at);
```

新表会在仓库启动时由 `Base.metadata.create_all` 自动创建，无需迁移脚本。

## 配置项

无新增 `.env` 配置项。复用：
- `MAX_WORKERS`：第一阶段并发度（默认 3）
- `MAX_SCREENING_BATCH_SIZE`（仓库内常量，目前固定 200）

## 已知限制

### 1. SSE 多 worker 部署

`task_queue` 是单进程内存队列。如果用 `uvicorn --workers > 1` 部署，前端连到 worker A 而任务跑在 worker B 时永远收不到 SSE 事件。

**对策**：
- 部署时用 `--workers 1` 或 sticky session
- 前端已实现 SSE 失败时自动降级到轮询（`GET /screening/{id}` 每 3 秒）

### 2. 单次扫描股票数 ≤ 200

防止数据源被反爬限制。如需更大批次，建议拆成多次扫描或调整 `MAX_SCREENING_BATCH_SIZE`（注意改前先看仓库 `fetcher_manager.prefetch_realtime_quotes` 是否支持）。

### 3. 第一阶段不依赖 LLM

如果你想要"基本面 + 新闻情绪"也参与排序，需要走第二阶段（LLM 综合分析）。第一阶段的 `signal_score` 是纯技术面，看不到基本面/资金面/新闻信息。

### 4. 跨日 promote 行为

如果某只股票昨天跑过 LLM 分析，今天 promote 时**会被重新跑**（`AnalysisHistory.created_at >= today_start` 过滤是按"今日"的语义）。这是预期行为：跨日的市场情绪、舆情、价格都已经变化。

## 测试

后端单测在 `tests/test_screening_service.py`：

```bash
.venv/bin/python -m pytest tests/test_screening_service.py -v
```

涵盖：
- ScreeningResult dataclass 基本契约
- ScreeningRepository CRUD（用 in-memory SQLite）
- promote_to_llm 的 Top N 切片
- 当日已分析的股票被跳过
- top_n=0 抛 ValueError
- 不存在的 screening_id 抛 KeyError

## 端到端验证

```bash
# 1. 启动 webui
.venv/bin/python main.py --webui-only &

# 2. 启动技术面预筛
SID=$(curl -s -X POST http://localhost:8000/api/v1/screening/technical \
  -H "Content-Type: application/json" \
  -d '{"stock_codes":["NVDA","LI","600519"]}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['screening_id'])")

# 3. 等几秒后查状态
sleep 5
curl -s http://localhost:8000/api/v1/screening/$SID | python3 -m json.tool

# 4. promote Top 1
curl -s -X POST http://localhost:8000/api/v1/screening/$SID/promote \
  -H "Content-Type: application/json" \
  -d '{"top_n":1}' | python3 -m json.tool

# 5. 浏览器打开 http://localhost:8000/two-stage-screening 用 UI 跑一次
```

## 与 AlphaSift 选股的关系

两者是互补不冲突的：

| 维度 | AlphaSift | 两阶段筛选 |
|---|---|---|
| 输入池 | 全市场（数千只） | 用户自选池（数十到 200 只） |
| 市场覆盖 | 仅 A 股 | A 股 + 港股 + 美股 |
| 第一步算法 | 内置策略 + 因子 | 仓库现有 `signal_score`（MA/MACD/RSI/量能） |
| 第二步 | 可选 LLM 重排 | 必走 LLM 综合分析 |
| 适用场景 | "今天该选哪只入池" | "我池子里今天哪只值得深看" |
