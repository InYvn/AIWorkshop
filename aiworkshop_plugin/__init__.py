#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 许愿工坊插件入口（宿主契约 api=1）。

宿主约定::

    mod = import aiworkshop_plugin
    ctl = mod.create(on_hide=..., log=...)
    ctl.show() / hide() / toggle() / is_visible
    ctl.set_auto(True) / ctl.tick()
    ctl.close()

插件不自带 Dock / 菜单栏入口；窗口按需创建。
"""

import json
import os

VERSION = "0.1.4"

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_MANIFEST = os.path.join(os.path.dirname(_PKG_DIR), "plugin.json")


def meta():
    try:
        with open(_MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class Controller(object):
    def __init__(self, on_hide=None, log=None):
        self._on_hide = on_hide
        self._log = log or (lambda _m: None)
        self._win = None
        self._page = None

    def _ensure(self):
        if self._win is None:
            from .ui.window import ChatWindow
            self._win = ChatWindow.alloc().init()
            self._win.build(on_hide=self._on_hide, log=self._log)
        return self._win

    @property
    def is_visible(self):
        return bool(self._win is not None and self._win.is_visible)

    def show(self):
        self._ensure().show()

    def hide(self):
        if self._win is not None:
            self._win.hide()

    def toggle(self):
        if self.is_visible:
            self.hide()
        else:
            self.show()

    def build_page(self, box, width, height):
        """宿主侧栏页：把聊天界面嵌进给定的 flipped 容器。"""
        from .ui.page import ChatPage
        if self._page is None:
            self._page = ChatPage.alloc().init()
        self._page.render(box, width, height)
        return height

    def set_auto(self, on):
        pass

    def tick(self):
        if self._win is not None:
            self._win.tick()

    def close(self):
        if self._page is not None and self._page._timer is not None:
            try:
                self._page._timer.invalidate()
            except Exception:
                pass
            self._page._timer = None
        if self._win is not None:
            self._win.close()
            self._win = None
        self._page = None


def create(on_hide=None, log=None):
    return Controller(on_hide=on_hide, log=log)
