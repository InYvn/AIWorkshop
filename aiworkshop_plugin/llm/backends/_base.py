#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP / SSE 公共部分（标准库）。

只做三件事：发请求、把 HTTP 错误映射成 LLMError、按 SSE 事件切分。
具体厂商的字段解析在各自 backend 里。
"""

import json
import socket
import urllib.error
import urllib.request

from ..errors import LLMError

UA = "AIWorkshop/0.1"


def _headers(extra=None):
    h = {"Content-Type": "application/json", "User-Agent": UA,
         "Accept": "text/event-stream, application/json"}
    if extra:
        h.update(extra)
    return h


def map_http_error(e):
    """HTTPError → LLMError（保留状态码与 Retry-After）。"""
    detail = ""
    try:
        detail = e.read().decode("utf-8", "replace")[:400]
    except Exception:
        pass
    if e.code in (401, 403):
        return LLMError("auth", detail or ("HTTP %d" % e.code), e.code)
    if e.code == 404:
        return LLMError("config", "接口不存在（HTTP 404）", e.code)
    if e.code == 429:
        ra = None
        try:
            ra = e.headers.get("Retry-After")
        except Exception:
            pass
        return LLMError("rate_limit", detail or "HTTP 429", e.code, ra)
    if e.code >= 500:
        return LLMError("network", "服务端错误（HTTP %d）" % e.code, e.code)
    return LLMError("network", detail or ("HTTP %d" % e.code), e.code)


def request_json(url, *, method="GET", payload=None, headers=None, timeout=20,
                 cancel=None):
    if cancel is not None and cancel.is_set():
        raise LLMError("cancelled")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=_headers(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise map_http_error(e)
    except (socket.timeout, TimeoutError):
        raise LLMError("timeout")
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            raise LLMError("timeout")
        raise LLMError("network", str(getattr(e, "reason", e)))
    except Exception as e:
        raise LLMError("network", repr(e))
    if cancel is not None and cancel.is_set():
        raise LLMError("cancelled")
    try:
        obj = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise LLMError("bad_response", "返回不是 JSON")
    if not isinstance(obj, dict):
        raise LLMError("bad_response", "返回结构不对")
    return obj


def open_stream(url, *, payload, headers=None, timeout=120, cancel=None):
    """POST 并返回可逐行读取的响应；调用方负责 close。"""
    if cancel is not None and cancel.is_set():
        raise LLMError("cancelled")
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers=_headers(headers))
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise map_http_error(e)
    except (socket.timeout, TimeoutError):
        raise LLMError("timeout")
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            raise LLMError("timeout")
        raise LLMError("network", str(getattr(e, "reason", e)))
    except Exception as e:
        raise LLMError("network", repr(e))


def iter_sse(resp, cancel=None):
    """把响应切成 SSE 事件文本（已去掉 `data:` 前缀）。

    * 空行分隔事件；
    * 忽略注释行与 event:/id:/retry:；
    * 收到 [DONE] 原样返回，由调用方决定结束。
    """
    buf = []
    try:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                raise LLMError("cancelled")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if buf:
                    yield "\n".join(buf)
                    buf = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
            # event:/id:/retry: 忽略
        if buf:
            yield "\n".join(buf)
    except LLMError:
        raise
    except Exception as e:
        if cancel is not None and cancel.is_set():
            raise LLMError("cancelled")
        raise LLMError("network", "读取流失败：%r" % (e,))
