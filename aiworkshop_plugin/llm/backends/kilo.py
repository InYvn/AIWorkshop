#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kilo AI Gateway 免费通道（独立实现，零依赖、零鉴权）。

实测（2026-11）:
  GET  /models            匿名 200
  POST /chat/completions  匿名 200（不带 Authorization）
  stream=true             SSE，delta 里有 content / reasoning

本后端不经过 LiteLLM，也不 import 任何第三方包。
"""

import json

from ..errors import LLMError
from ..types import Chunk, ModelInfo, TestResult, Usage
from ._base import iter_sse, open_stream, request_json

BASE = "https://ai-gateway.kilo.ai/api/v1"
DEFAULT_MODEL = "kilo-auto/free"


def _is_free(m):
    p = (m.get("pricing") or {})
    try:
        return float(p.get("prompt") or 0) == 0.0 and float(p.get("completion") or 0) == 0.0
    except Exception:
        return False


def list_models(base=BASE, free_only=True, timeout=20, cancel=None):
    obj = request_json(base.rstrip("/") + "/models", timeout=timeout, cancel=cancel)
    out = []
    for m in obj.get("data") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        if free_only and not _is_free(m):
            continue
        out.append(ModelInfo(
            id=m["id"],
            name=m.get("name") or m["id"],
            free=_is_free(m),
            context=int((m.get("top_provider") or {}).get("context_length") or 0),
            provider="kilo",
            raw=m,
        ))
    # 免费的自动路由排最前
    out.sort(key=lambda x: (x.id != DEFAULT_MODEL, not x.free, x.id))
    return out


def stream(model, messages, *, base=BASE, temperature=0.7, max_tokens=1200,
           timeout=120, cancel=None):
    """返回生成器：Chunk(content/reasoning/usage/done)。"""
    if not model:
        raise LLMError("config", "没有选择模型")
    payload = {"model": model, "messages": messages, "stream": True,
               "temperature": float(temperature), "max_tokens": int(max_tokens)}
    resp = open_stream(base.rstrip("/") + "/chat/completions", payload=payload,
                       timeout=timeout, cancel=cancel)
    got_any = False
    bad = 0
    seen_model = ""
    try:
        for data in iter_sse(resp, cancel=cancel):
            if data.strip() == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except Exception:
                bad += 1
                if bad > 20:
                    raise LLMError("bad_response", "流里坏行过多")
                continue
            if not isinstance(ev, dict):
                continue
            seen_model = ev.get("model") or seen_model
            usage = ev.get("usage")
            if usage:
                yield Chunk.usage_chunk(Usage.from_dict(usage), seen_model)
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or {}
                # 思维链与正文分开：不能把 reasoning 当回答
                r = delta.get("reasoning")
                if isinstance(r, str) and r:
                    got_any = True
                    yield Chunk.reasoning(r, seen_model)
                c = delta.get("content")
                if isinstance(c, str) and c:
                    got_any = True
                    yield Chunk.content(c, seen_model)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if cancel is not None and cancel.is_set():
        raise LLMError("cancelled")
    if not got_any and bad:
        raise LLMError("bad_response", "流里没有可解析的内容")
    yield Chunk.done(seen_model)


def test(model=DEFAULT_MODEL, base=BASE, timeout=30, cancel=None):
    """连通性 + 一次最小对话。返回 TestResult，不抛异常。"""
    import time
    t0 = time.time()
    try:
        models = list_models(base=base, free_only=True, timeout=timeout, cancel=cancel)
        if not models:
            return TestResult(False, "模型列表为空", model)
        mid = model or models[0].id
        payload = {"model": mid, "messages": [{"role": "user", "content": "ping"}],
                   "max_tokens": 8, "stream": False}
        obj = request_json(base.rstrip("/") + "/chat/completions", method="POST",
                           payload=payload, timeout=timeout, cancel=cancel)
        ok = bool(obj.get("choices"))
        return TestResult(ok, "连接正常" if ok else "返回里没有 choices",
                          mid, int((time.time() - t0) * 1000))
    except LLMError as e:
        return TestResult(False, e.user_text(), model, int((time.time() - t0) * 1000))
    except Exception as e:
        return TestResult(False, repr(e), model, int((time.time() - t0) * 1000))
