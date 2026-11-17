#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一的错误类型：所有后端都映射到这里，UI 只认 kind / user_text()。

不依赖任何宿主模块，也不依赖 AppKit —— 这个包可以整体搬到别的项目。
"""


class LLMError(Exception):
    KINDS = ("config", "auth", "rate_limit", "timeout", "network",
             "bad_response", "not_installed", "cancelled")

    def __init__(self, kind, message="", status=0, retry_after=None):
        super(LLMError, self).__init__(message or kind)
        self.kind = kind if kind in self.KINDS else "network"
        self.message = message or ""
        self.status = int(status or 0)
        self.retry_after = retry_after

    def user_text(self):
        base = {
            "config": "配置不完整",
            "auth": "密钥无效或没有权限",
            "rate_limit": "请求太频繁或额度用完了",
            "timeout": "请求超时",
            "network": "网络连接失败",
            "bad_response": "模型返回的内容无法解析",
            "not_installed": "依赖未安装",
            "cancelled": "已取消",
        }.get(self.kind, "请求失败")
        if self.kind == "rate_limit" and self.retry_after:
            base += "（建议 %s 秒后重试）" % self.retry_after
        if self.message:
            base += "：%s" % self.message
        return base

    def __repr__(self):
        return "LLMError(%s, %r, status=%s)" % (self.kind, self.message, self.status)
