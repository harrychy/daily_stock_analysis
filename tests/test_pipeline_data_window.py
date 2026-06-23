# -*- coding: utf-8 -*-
"""
Pipeline 数据窗口断言测试
==========================

设计文档 §10：将 enhance/取数路径的窗口由 89 天延长至 260 天，
覆盖 MA200 与 52 周高低位计算。

此测试用源码扫描的方式，避免拉起整套 fetch 链路。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1] / "src" / "core" / "pipeline.py"


def test_no_89_day_window_left_in_pipeline():
    src = PIPELINE.read_text(encoding="utf-8")
    # 不应再出现 89 天的取数窗口（注释里也清掉，避免歧义）
    assert "timedelta(days=89)" not in src, (
        "src/core/pipeline.py 仍存在 timedelta(days=89)；扩展指标依赖 260 天窗口"
    )


def test_pipeline_has_260_day_window():
    src = PIPELINE.read_text(encoding="utf-8")
    # 至少一处 260 天的取数窗口
    matches = re.findall(r"timedelta\(days=260\)", src)
    assert len(matches) >= 1, (
        "src/core/pipeline.py 应当至少有一处 timedelta(days=260) "
        "用于覆盖 MA200 与 52 周高低位"
    )
