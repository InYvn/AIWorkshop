#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 许愿工坊的「侧栏页」版本（宿主内嵌，不弹独立窗口）。

宿主给一个 flipped 容器和宽高，这里按左上角本地坐标画：
标题 + 模型下拉 / 对话滚动区 / 输入框。流式输出自己用 NSTimer 驱动。
"""

import queue
import threading

import objc
from AppKit import (NSApplication, NSButton, NSFont, NSMakeRect, NSMakeSize,
                    NSPopUpButton, NSScrollView, NSTextField, NSView,
                    NSWindow)
from Foundation import NSObject, NSTimer

from .. import store
from ..llm import LLMClient, LLMError
from . import theme
from .chat_view import ChatList, Composer, _symbol


class ChatPage(NSObject):
    def init(self):
        self = objc.super(ChatPage, self).init()
        if self is None:
            return None
        self.cfg = store.load_config()
        provs = self.cfg.get("providers") or {}
        for pid in list(provs):
            k = store.get_key(pid)
            if k:
                provs[pid]["key"] = k
        self.client = LLMClient(provs)
        self.messages = store.load_session("default")
        self._busy = False
        self._cancel = threading.Event()
        self._q = queue.Queue()
        self._row = None
        self._timer = None
        self._box = None
        self._models = []
        pass
        return self

    # ------------------------------------------------------------ 构建
    @objc.python_method
    def render(self, box, w, h):
        self._box = box
        self._w, self._h = float(w), float(h)

        self._title = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 22))
        self._title.setStringValue_("AI 许愿工坊")
        self._title.setFont_(NSFont.boldSystemFontOfSize_(16.0))
        self._title.setBezeled_(False)
        self._title.setDrawsBackground_(False)
        self._title.setEditable_(False)
        box.addSubview_(self._title)

        self._sub = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 22, 420, 16))
        self._sub.setStringValue_("Kilo 免费 · 无需登录 / 无需 API Key")
        self._sub.setFont_(NSFont.systemFontOfSize_(11.0))
        self._sub.setTextColor_(theme.DIM)
        self._sub.setBezeled_(False)
        self._sub.setDrawsBackground_(False)
        self._sub.setEditable_(False)
        box.addSubview_(self._sub)

        self._model = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(w - 240, 8, 210, 26), False)
        self._model.setFont_(NSFont.systemFontOfSize_(12.0))
        self._model.addItemWithTitle_("加载中…")
        self._model.setTarget_(self)
        self._model.setAction_("modelChanged:")
        box.addSubview_(self._model)

        self._gear = NSButton.alloc().initWithFrame_(NSMakeRect(w - 272, 10, 26, 26))
        self._gear.setBordered_(False)
        img = _symbol("slider.horizontal.3", 15, theme.DIM)
        if img is not None:
            self._gear.setImage_(img)
        self._gear.setContentTintColor_(theme.DIM)
        self._gear.setToolTip_("模型设置")
        self._gear.setTarget_(self)
        self._gear.setAction_("openSettings:")
        box.addSubview_(self._gear)

        composer_h = 64.0
        top = 46.0
        self._scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, top, w, max(80.0, h - top - composer_h - 10)))
        self._scroll.setDrawsBackground_(False)
        self._scroll.setBorderType_(0)
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setAutohidesScrollers_(True)
        try:
            self._scroll.setScrollerStyle_(1)
        except Exception:
            pass
        self._chat = ChatList.alloc().initWithFrame_(NSMakeRect(0, 0, w - 14, 100))
        self._scroll.setDocumentView_(self._chat)
        box.addSubview_(self._scroll)

        self._composer = Composer.alloc().initWithFrame_(
            NSMakeRect(0, h - composer_h - 2, w, composer_h))
        self._composer.on_send = self.onSend
        self._composer.resizeSubviewsWithOldSize_(NSMakeSize(0, 0))
        box.addSubview_(self._composer)

        self._render_history()
        self._load_models_async()
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.05, self, "drain:", None, True)

    @objc.python_method
    def _render_history(self):
        self._chat.remove_all()
        for m in self.messages:
            self._chat.add_message(m.get("role", "user"), m.get("text", ""))
        self._relayout()
        self._scroll_bottom()

    @objc.python_method
    def _relayout(self):
        w = self._scroll.contentView().bounds().size.width
        self._chat.relayout(w)
        self._scroll_bottom()

    @objc.python_method
    def _scroll_bottom(self):
        try:
            ch = self._chat.content_height()
            vh = self._scroll.contentView().bounds().size.height
            self._scroll.contentView().scrollToPoint_((0, max(0.0, ch - vh)))
            self._scroll.reflectScrolledClipView_(self._scroll.contentView())
        except Exception:
            pass

    # ------------------------------------------------------------ 发送
    @objc.python_method
    def _provider(self):
        pid = self.cfg.get("provider") or "kilo"
        return pid if pid in self.client.provider_ids() else "kilo"

    @objc.python_method
    def _selected_model(self):
        try:
            return str(self._model.titleOfSelectedItem() or "")
        except Exception:
            return "kilo-auto/free"

    @objc.python_method
    def _append(self, role, text):
        self.messages.append({"role": role, "text": text})
        self._chat.add_message(role, text)
        self._relayout()
        store.save_session(self.messages)
        return self._chat.rows[-1]

    def onSend(self):
        if self._busy:
            self.stop()
            return
        text = self._composer.text().strip()
        if not text:
            return
        self._composer.clear()
        self._append("user", text)
        self._row = self._append("assistant", "")
        self._row.setStreaming_(True)
        self._busy = True
        self._cancel.clear()
        self._composer.set_stop(True)
        self._sub.setStringValue_("正在生成… · %s" % (self._selected_model() or "Kilo"))
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    @objc.python_method
    def stop(self):
        if not self._busy:
            return
        self._cancel.set()
        self._busy = False
        self._composer.set_stop(False)
        if self._row is not None:
            self._row.setStreaming_(False)

    @objc.python_method
    def _worker(self, text):
        msgs = []
        for m in self.messages[-20:]:
            if m.get("role") in ("user", "assistant") and m.get("text"):
                msgs.append({"role": m["role"], "content": m["text"]})
        if not msgs or msgs[-1].get("role") != "user":
            msgs.append({"role": "user", "content": text})
        try:
            for ch in self.client.stream(self._provider(), self._selected_model(),
                                         msgs, cancel=self._cancel):
                self._q.put(ch)
        except LLMError as e:
            self._q.put(("error", e.user_text()))
        except Exception as e:
            self._q.put(("error", repr(e)))
        finally:
            self._q.put(("done", None))

    def drain_(self, timer):
        self._drain()

    @objc.python_method
    def _drain(self):
        changed = False
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                kind, payload = item
                if kind == "models":
                    self._fill_models(payload)
                    continue
                if kind == "models_error":
                    self._sub.setStringValue_("模型列表加载失败：%s" % payload)
                    continue
                if self._row is None:
                    continue
                if kind == "error":
                    self._row.append_text("\n\n⚠️ " + str(payload))
                    changed = True
                elif kind == "done":
                    self._finish()
                    changed = True
                continue
            if self._row is None:
                continue
            if getattr(item, "type", "") == "content":
                self._row.append_text(item.text)
                changed = True
        if changed:
            self._relayout()

    @objc.python_method
    def _finish(self):
        if self._row is not None:
            self._row.setStreaming_(False)
        self._busy = False
        self._composer.set_stop(False)
        self._sub.setStringValue_("Kilo 免费 · 无需登录 / 无需 API Key")
        self.messages = [m for m in self.messages
                         if not (m.get("role") == "assistant" and not m.get("text"))]
        store.save_session(self.messages)
        self._row = None

    # ------------------------------------------------------------ 模型 / 设置
    @objc.python_method
    def _load_models_async(self):
        def run():
            try:
                self._q.put(("models", self.client.list_models(self._provider(), force=True)))
            except Exception as e:
                self._q.put(("models_error", str(e)))
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def _fill_models(self, models):
        self._models = models
        self._model.removeAllItems()
        for m in models[:40]:
            self._model.addItemWithTitle_(m.id)
        if not models:
            self._model.addItemWithTitle_("无可用模型")
        want = self.cfg.get("model") or "kilo-auto/free"
        i = self._model.indexOfItemWithTitle_(want)
        if i >= 0:
            self._model.selectItemAtIndex_(i)

    def modelChanged_(self, sender):
        self.cfg["model"] = self._selected_model()
        store.save_config(self.cfg)

    def openSettings_(self, sender):
        try:
            from .settings import SettingsSheet
            if not getattr(self, "_settings", None):
                self._settings = SettingsSheet.alloc().init()
            self._settings.show(self)
        except Exception as e:
            self._sub.setStringValue_("打开设置失败：%r" % (e,))
