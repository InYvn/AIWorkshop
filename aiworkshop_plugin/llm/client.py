#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 LLM Client：把不同后端抽象成同一套调用。

    client = LLMClient({"kilo": {...}, "openrouter": {"key": "..."}})
    for ch in client.stream("kilo", "kilo-auto/free", messages):
        ...

宿主/UI 只认 provider id 与 Chunk，不接触 Kilo 的 JSON 或 LiteLLM 的参数。
"""

import time

from .backends import kilo, openai_compat
from .errors import LLMError
from .types import TestResult

# provider id → 后端类型与默认值（UI 从这里生成设置项）
PROVIDERS = {
    "kilo": {
        "kind": "kilo", "title": "Kilo 免费", "needs_key": False,
        "base": kilo.BASE, "default_model": kilo.DEFAULT_MODEL,
        "hint": "无需登录 / 无需 API Key；自动路由到免费模型",
    },
    "openrouter": {
        "kind": "openai", "title": "OpenRouter", "needs_key": True,
        "base": "https://openrouter.ai/api/v1", "default_model": "",
        "hint": "模型名带厂商前缀，例如 anthropic/claude-sonnet-4.5",
    },
    "deepseek": {
        "kind": "openai", "title": "DeepSeek", "needs_key": True,
        "base": "https://api.deepseek.com/v1", "default_model": "deepseek-chat",
        "hint": "",
    },
    "opencode_go": {
        "kind": "openai", "title": "OpenCode Go", "needs_key": True,
        "base": "https://opencode.ai/zen/go/v1", "default_model": "",
        "hint": "订阅制；限额按美元用量计算",
    },
    "gemini": {
        "kind": "openai", "title": "Gemini", "needs_key": True,
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "",
        "hint": "走 Gemini 的 OpenAI 兼容口",
    },
    "litellm": {
        "kind": "litellm", "title": "LiteLLM（本机 sidecar）", "needs_key": "optional",
        "base": "", "default_model": "",
        "hint": "需要本机安装 LiteLLM（M3 提供）",
    },
    "litellm_proxy": {
        "kind": "openai", "title": "LiteLLM Proxy", "needs_key": "optional",
        "base": "http://127.0.0.1:4000/v1", "default_model": "",
        "hint": "已经跑着 litellm --port 4000 的话直接用，无需安装",
    },
}

MODEL_TTL = 600


class LLMClient(object):
    def __init__(self, config=None, log=None):
        self._cfg = dict(config or {})
        self._log = log or (lambda _m: None)
        self._models = {}          # (pid, free_only) -> (ts, [ModelInfo])
        self._last_model = {}      # pid -> 实际模型名

    # ------------------------------------------------------------ 配置
    def provider_ids(self):
        return list(PROVIDERS)

    def provider(self, pid):
        if pid not in PROVIDERS:
            raise LLMError("config", "未知 provider：%r" % (pid,))
        return dict(PROVIDERS[pid])

    def configure(self, pid, **kw):
        cur = dict(self._cfg.get(pid) or {})
        cur.update({k: v for k, v in kw.items() if v is not None})
        self._cfg[pid] = cur

    def config(self, pid):
        return dict(self._cfg.get(pid) or {})

    # ------------------------------------------------------------ 模型
    def list_models(self, pid, *, free_only=True, force=False, cancel=None):
        meta = self.provider(pid)
        key = (pid, bool(free_only))
        now = time.time()
        if not force:
            hit = self._models.get(key)
            if hit and now - hit[0] < MODEL_TTL:
                return list(hit[1])
        cfg = self.config(pid)
        if meta["kind"] == "kilo":
            models = kilo.list_models(base=cfg.get("base") or meta["base"],
                                      free_only=free_only, cancel=cancel)
        elif meta["kind"] == "openai":
            models = openai_compat.list_models(cfg.get("base") or meta["base"],
                                               cfg.get("key") or "", cancel=cancel)
        else:
            raise LLMError("not_installed", "LiteLLM 后端尚未接入（M3）")
        self._models[key] = (now, list(models))
        return models

    # ------------------------------------------------------------ 对话
    def stream(self, pid, model, messages, *, temperature=0.7, max_tokens=1200,
               timeout=120, cancel=None):
        meta = self.provider(pid)
        cfg = self.config(pid)
        model = model or cfg.get("model") or meta.get("default_model")
        if meta["kind"] == "kilo":
            it = kilo.stream(model, messages,
                             base=cfg.get("base") or meta["base"],
                             temperature=temperature, max_tokens=max_tokens,
                             timeout=timeout, cancel=cancel)
        elif meta["kind"] == "openai":
            key = cfg.get("key") or ""
            if meta.get("needs_key") and not key:
                raise LLMError("config", "还没有填 API Key")
            it = openai_compat.stream(model, messages,
                                      base=cfg.get("base") or meta["base"],
                                      key=key, temperature=temperature,
                                      max_tokens=max_tokens, timeout=timeout,
                                      cancel=cancel)
        else:
            raise LLMError("not_installed", "LiteLLM 后端尚未接入（M3）")
        for ch in it:
            if ch.model:
                self._last_model[pid] = ch.model
            yield ch

    def last_model(self, pid):
        return self._last_model.get(pid, "")

    # ------------------------------------------------------------ 测试
    def test(self, pid, model=None):
        meta = self.provider(pid)
        cfg = self.config(pid)
        model = model or cfg.get("model") or meta.get("default_model")
        if meta["kind"] == "kilo":
            return kilo.test(model, base=cfg.get("base") or meta["base"])
        if meta["kind"] == "openai":
            return openai_compat.test(model, base=cfg.get("base") or meta["base"],
                                      key=cfg.get("key") or "")
        return TestResult(False, "LiteLLM 后端尚未接入（M3）", model or "")

    def invalidate(self, pid=None):
        if pid is None:
            self._models.clear()
        else:
            for k in list(self._models):
                if k[0] == pid:
                    self._models.pop(k, None)
