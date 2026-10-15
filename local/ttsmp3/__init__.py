# -*- coding: utf-8 -*-
"""文字转 MP3 —— 核心库。"""

from . import catalog, engine, extract, mp3join, splitter  # noqa: F401

__all__ = ["catalog", "engine", "extract", "mp3join", "splitter"]
__version__ = "2.0.0"
