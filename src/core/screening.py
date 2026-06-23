# -*- coding: utf-8 -*-
"""
===================================
两阶段筛选 - 数据契约
===================================

定义第一阶段（技术面预筛）的轻量结果对象。

设计原因：
- 不复用 src/analyzer.py 的 AnalysisResult（含 50+ 字段，多数与 LLM 输出有关）。
- 第一阶段不调 LLM，相关字段强行留空会让下游误以为已分析；轻量结构语义更清晰。
- 字段命名对齐 src/stock_analyzer.py::TrendAnalysisResult，便于映射。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ScreeningResult:
    """单只股票的技术面预筛结果（不含 LLM 字段）。"""

    code: str
    name: str = ""

    # 技术面综合评分（0-100，由 src/stock_analyzer.py 的死规则计算）
    signal_score: int = 0

    # 趋势强度（与 signal_score 同源，分别落库便于后续做不同维度排序）
    trend_strength: float = 0.0

    # 趋势状态文字（多头/弱势多头/震荡/弱势空头/空头）
    trend_status: str = ""

    # 买入信号档位（强烈买入/买入/持有/观望/卖出/强烈卖出）
    buy_signal: str = ""

    # 均线排列简述（用于前端表格展示）
    ma_alignment: str = ""

    # 失败时记录原因（fetch 失败 / 数据不足 / 计算异常）；成功时为空
    error_msg: str = ""

    # 实时价格（如果取到了）
    current_price: Optional[float] = None

    # 量比（如果取到了）
    volume_ratio: Optional[float] = None

    def is_success(self) -> bool:
        """这只股票是否成功完成技术面分析。"""
        return not self.error_msg

    def to_dict(self) -> Dict[str, Any]:
        """转换成可 JSON 序列化的字典（用于 SSE / API 返回）。"""
        return asdict(self)


@dataclass
class ScreeningRunSummary:
    """整轮筛选的摘要（用于 GET /screening/{id} 兜底返回）。"""

    screening_id: str
    status: str  # pending / running / completed / failed
    total_count: int
    success_count: int = 0
    failed_count: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    results: List[ScreeningResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "screening_id": self.screening_id,
            "status": self.status,
            "total_count": self.total_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "results": [r.to_dict() for r in self.results],
        }
