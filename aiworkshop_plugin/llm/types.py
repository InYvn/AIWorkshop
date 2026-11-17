#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 层的数据类型（纯标准库，可移植）。"""

from dataclasses import dataclass, field


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    free: bool = False
    context: int = 0
    provider: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(int(d.get("prompt_tokens") or 0),
                   int(d.get("completion_tokens") or 0),
                   int(d.get("total_tokens") or 0))

    def __bool__(self):
        return bool(self.prompt_tokens or self.completion_tokens or self.total_tokens)


@dataclass
class Chunk:
    """流式事件：content / reasoning / usage / done。"""
    type: str
    text: str = ""
    usage: Usage = None
    model: str = ""

    @classmethod
    def content(cls, text, model=""):
        return cls("content", text=text, model=model)

    @classmethod
    def reasoning(cls, text, model=""):
        return cls("reasoning", text=text, model=model)

    @classmethod
    def usage_chunk(cls, usage, model=""):
        return cls("usage", usage=usage, model=model)

    @classmethod
    def done(cls, model=""):
        return cls("done", model=model)


@dataclass
class TestResult:
    ok: bool
    detail: str = ""
    model: str = ""
    latency_ms: int = 0
