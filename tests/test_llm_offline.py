#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 核心离线自检（不联网）。

跑法：python3 tests/test_llm_offline.py
覆盖：模型列表/免费过滤、SSE 解析（content+reasoning+usage）、401/429、
      非流式测试、取消、OpenAI 兼容鉴权。
"""

import http.server
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiworkshop_plugin.llm import LLMClient, LLMError
from aiworkshop_plugin.llm.backends import kilo, openai_compat

MODELS = {
    "data": [
        {"id": "kilo-auto/free", "name": "Auto Free",
         "pricing": {"prompt": "0", "completion": "0"},
         "top_provider": {"context_length": 256000}},
        {"id": "nvidia/nemotron-3.5-super-vl:free", "name": "Nemotron (free)",
         "pricing": {"prompt": "0", "completion": "0"},
         "top_provider": {"context_length": 1000000}},
        {"id": "anthropic/claude-opus-5.5", "name": "Claude",
         "pricing": {"prompt": "0.000004", "completion": "0.00002"},
         "top_provider": {"context_length": 200000}},
    ]
}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if "/401" in self.path:
            self._json({"error": "bad key sk-secret"}, 401)
        elif "/429" in self.path:
            self._json({"error": "slow down"}, 429, {"Retry-After": "3"})
        elif self.path.endswith("/models"):
            self._json(MODELS)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if "/401" in self.path:
            self._json({"error": "bad key sk-secret"}, 401)
            return
        if "/429" in self.path:
            self._json({"error": "rate"}, 429, {"Retry-After": "3"})
            return
        if not body.get("stream"):
            self._json({"choices": [{"message": {"content": "pong"}}],
                        "usage": {"total_tokens": 3}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def ev(obj):
            self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            self.wfile.flush()

        ev({"model": "fake/model", "choices": [{"delta": {"reasoning": "think "}}]})
        ev({"model": "fake/model", "choices": [{"delta": {"content": "你"}}]})
        ev({"model": "fake/model", "choices": [{"delta": {"content": "好"}}]})
        ev({"model": "fake/model", "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    ok = 0
    bad = []

    def ck(name, cond, extra=""):
        nonlocal ok
        if cond:
            ok += 1
        else:
            bad.append("%s %s" % (name, extra))

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d/v1" % port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # 模型列表 + 免费过滤
        free = kilo.list_models(base=base)
        ck("免费过滤", [m.id for m in free] == ["kilo-auto/free",
                                                "nvidia/nemotron-3.5-super-vl:free"],
           str([m.id for m in free]))
        allm = kilo.list_models(base=base, free_only=False)
        ck("全部模型", len(allm) == 3)

        # 流式：reasoning 与 content 分开
        chunks = list(kilo.stream("kilo-auto/free", [{"role": "user", "content": "hi"}],
                                  base=base))
        kinds = [c.type for c in chunks]
        ck("流式顺序", kinds[:3] == ["reasoning", "content", "content"] and kinds[-2:] == ["usage", "done"], str(kinds))
        ck("正文拼接", "".join(c.text for c in chunks if c.type == "content") == "你好")
        ck("实际模型名", chunks[-1].model == "fake/model")

        # 非流式测试
        r = kilo.test("kilo-auto/free", base=base)
        ck("test 正常", r.ok and r.model == "kilo-auto/free", r.detail)

        # 401 / 429
        try:
            kilo.list_models(base=base + "/401")
            ck("401", False)
        except LLMError as e:
            ck("401", e.kind == "auth" and "sk-secret" in e.message)
        try:
            kilo.list_models(base=base + "/429")
            ck("429", False)
        except LLMError as e:
            ck("429", e.kind == "rate_limit" and e.retry_after == "3")

        # OpenAI 兼容：带 key
        chunks2 = list(openai_compat.stream("m", [{"role": "user", "content": "hi"}],
                                            base=base, key="k"))
        ck("openai 流式", any(c.type == "content" for c in chunks2))

        # Client 分发
        c = LLMClient({"kilo": {"base": base}})
        ck("client 列表", len(c.list_models("kilo")) == 2)
        out = list(c.stream("kilo", "kilo-auto/free", [{"role": "user", "content": "hi"}]))
        ck("client 流式", "".join(x.text for x in out if x.type == "content") == "你好")
        ck("client 记住实际模型", c.last_model("kilo") == "fake/model")

        # 取消（流开始前）
        ev = threading.Event()
        ev.set()
        try:
            list(kilo.stream("kilo-auto/free", [], base=base, cancel=ev))
            ck("取消", False)
        except LLMError as e:
            ck("取消", e.kind == "cancelled")

        # 配置缺失
        try:
            c2 = LLMClient({"openrouter": {}})
            list(c2.stream("openrouter", "x", []))
            ck("缺 key", False)
        except LLMError as e:
            ck("缺 key", e.kind == "config")
    finally:
        srv.shutdown()
        srv.server_close()

    print(json.dumps({"ok": not bad, "passed": ok, "fail": bad}, ensure_ascii=False))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
