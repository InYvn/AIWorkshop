#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作台状态：页面只读这里，后台线程只拿不可变快照。

约定：
- WorkshopState 只在主线程读写（AppKit 回调都在主线程）。
- Request 是不可变的（frozen dataclass + tuple），创建后不再引用任何控件。
- 后台线程只调用 LLM，不 import AppKit 控件，也不读 self._tv 之类。
"""

from dataclasses import dataclass, field, replace
from typing import Any, Tuple


@dataclass(frozen=True)
class Request:
    """一次发送的完整快照；创建于主线程，之后只读。"""

    provider: str
    model: str
    messages: Tuple[Tuple[str, str], ...]
    max_tokens: int = 1600
    cancel: Any = None

    def as_payload(self):
        return [{"role": r, "content": c} for r, c in self.messages]


@dataclass
class WorkshopState:
    provider: str = "kilo"
    model: str = ""
    input_text: str = ""
    messages: list = field(default_factory=list)
    models: tuple = ()
    busy: bool = False
    status: str = ""
    result_text: str = ""
    summary: str = ""
    pending_ops: tuple = ()
    preview: tuple = ()

    # ------------------------------------------------------------ 派生
    def recent(self, limit=3):
        users = [m for m in self.messages if m.get("role") == "user"]
        return users[-limit:]

    def build_request(self, system_prompt, cancel=None, history=20):
        """在主线程调用；返回不可变快照，后台线程只读。"""
        msgs = [("system", system_prompt)]
        for m in self.messages[-history:]:
            if m.get("role") in ("user", "assistant") and m.get("text"):
                msgs.append((m["role"], m["text"]))
        return Request(provider=self.provider, model=self.model,
                       messages=tuple(msgs), max_tokens=1600, cancel=cancel)

    # ------------------------------------------------------------ 变更
    def begin(self, text):
        """记录用户输入并进入分析态；返回本次请求快照。"""
        self.messages.append({"role": "user", "text": text})
        self.input_text = ""
        self.result_text = ""
        self.summary = ""
        self.pending_ops = ()
        self.preview = ()
        self.busy = True
        self.status = "正在分析…"

    def finish_text(self, text):
        self.result_text = text or ""
        self.busy = False
        self.status = ""

    def clear_pending(self):
        self.pending_ops = ()
        self.preview = ()
        self.summary = ""
