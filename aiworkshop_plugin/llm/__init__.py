# -*- coding: utf-8 -*-
"""可移植的 LLM 核心：不依赖 AppKit，也不依赖宿主程序。"""

from .client import LLMClient, PROVIDERS
from .errors import LLMError
from .types import Chunk, ModelInfo, TestResult, Usage

__all__ = ["LLMClient", "PROVIDERS", "LLMError", "Chunk", "ModelInfo",
           "TestResult", "Usage"]
