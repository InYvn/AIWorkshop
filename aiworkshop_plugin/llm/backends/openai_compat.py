#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI 兼容后端：OpenRouter / DeepSeek / OpenCode Go / Gemini(OpenAI 口) / 自建。

只依赖标准库；不经过 LiteLLM。适合"用户已经有 Key、只想直接对话"的场景。
"""

import json

from ..errors import LLMError
from ..types import Chunk, ModelInfo, TestResult, Usage
from ._base import iter_sse, open_stream, request_json


def _auth(key):
    return {"Authorization": "Bearer " + key} if key else {}


def list_models(base, key="", timeout=20, cancel=None):
    if not base:
        raise LLMError("config", "Base URL 为空")
    obj = request_json(base.rstrip("/") + "/models", headers=_auth(key),
                       timeout=timeout, cancel=cancel)
    out = []
    for m in obj.get("data") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        out.append(ModelInfo(id=m["id"], name=m.get("name") or m["id"],
                             free=False, context=int(m.get("context_length") or 0),
                             provider="openai", raw=m))
    out.sort(key=lambda x: x.id)
    return out


def stream(model, messages, *, base, key="", temperature=0.7, max_tokens=1200,
           timeout=120, cancel=None):
    if not base:
        raise LLMError("config", "Base URL 为空")
    if not model:
        raise LLMError("config", "没有选择模型")
    payload = {"model": model, "messages": messages, "stream": True,
               "temperature": float(temperature), "max_tokens": int(max_tokens)}
    resp = open_stream(base.rstrip("/") + "/chat/completions", payload=payload,
                       headers=_auth(key), timeout=timeout, cancel=cancel)
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
            if ev.get("usage"):
                yield Chunk.usage_chunk(Usage.from_dict(ev["usage"]), seen_model)
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or {}
                r = delta.get("reasoning") or delta.get("reasoning_content")
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


def test(model="", base="", key="", timeout=30, cancel=None):
    import time
    t0 = time.time()
    try:
        payload = {"model": model, "messages": [{"role": "user", "content": "ping"}],
                   "max_tokens": 8, "stream": False}
        obj = request_json(base.rstrip("/") + "/chat/completions", method="POST",
                           payload=payload, headers=_auth(key),
                           timeout=timeout, cancel=cancel)
        ok = bool(obj.get("choices"))
        return TestResult(ok, "连接正常" if ok else "返回里没有 choices",
                          model, int((time.time() - t0) * 1000))
    except LLMError as e:
        return TestResult(False, e.user_text(), model, int((time.time() - t0) * 1000))
    except Exception as e:
        return TestResult(False, repr(e), model, int((time.time() - t0) * 1000))
