#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 许愿工坊窗口：现代聊天布局 + 流式输出。

宿主契约（api=1）由 aiworkshop_plugin/__init__.py 的 Controller 适配；
这里只管窗口与对话。
"""

import queue
import threading

import objc
from AppKit import (NSApplication, NSButton, NSColor, NSFont, NSImage,
                    NSImageView, NSMakePoint, NSMakeRect, NSMakeSize,
                    NSPanel, NSPopUpButton, NSScrollView, NSTextField, NSView,
                    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskMiniaturizable,
                    NSWindowStyleMaskFullSizeContentView, NSBackingStoreBuffered,
                    NSImageScaleProportionallyUpOrDown)
from Foundation import NSObject, NSTimer

from . import markdown, theme
from .. import store
from .chat_view import ChatList, Composer, _symbol
from ..llm import LLMClient, LLMError

W, H = 880.0, 640.0
HEADER_H = 92.0
COMPOSER_H = 78.0
SIDE = 24.0


class ChatWindow(NSObject):
    def init(self):
        self = objc.super(ChatWindow, self).init()
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
        self._reasoning = ""
        self._panel = None
        self._timer = None
        self._models = []
        self._on_hide = None
        self._log = lambda _m: None
        return self

    # ------------------------------------------------------------ 生命周期
    @objc.python_method
    def build(self, on_hide=None, log=None):
        self._on_hide = on_hide
        if log is not None:
            self._log = log
        mask = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                | NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable
                | NSWindowStyleMaskFullSizeContentView)
        p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), mask, NSBackingStoreBuffered, False)
        p.setTitle_("AI 许愿工坊")
        p.setTitleVisibility_(1)
        p.setTitlebarAppearsTransparent_(True)
        p.setReleasedWhenClosed_(False)
        p.setMinSize_(NSMakeSize(560, 420))
        p.setDelegate_(self)
        self._panel = p
        v = p.contentView()
        v.setWantsLayer_(True)
        v.layer().setBackgroundColor_(theme.BG.CGColor())

        # ---- 头部（让开红绿灯）
        self._title = NSTextField.alloc().initWithFrame_(NSMakeRect(SIDE, H - 66, 360, 24))
        self._title.setStringValue_("AI 许愿工坊")
        self._title.setFont_(NSFont.boldSystemFontOfSize_(16.0))
        self._title.setBezeled_(False)
        self._title.setDrawsBackground_(False)
        self._title.setEditable_(False)
        v.addSubview_(self._title)

        self._sub = NSTextField.alloc().initWithFrame_(NSMakeRect(SIDE, H - 84, 420, 16))
        self._sub.setStringValue_("Kilo 免费 · 无需登录 / 无需 API Key")
        self._sub.setFont_(NSFont.systemFontOfSize_(11.0))
        self._sub.setTextColor_(theme.DIM)
        self._sub.setBezeled_(False)
        self._sub.setDrawsBackground_(False)
        self._sub.setEditable_(False)
        v.addSubview_(self._sub)

        self._model = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(W - SIDE - 210, H - 62, 210, 26), False)
        self._model.setFont_(NSFont.systemFontOfSize_(12.0))
        self._model.addItemWithTitle_("Kilo 免费 · 加载中…")
        self._model.setTarget_(self)
        self._model.setAction_("modelChanged:")
        v.addSubview_(self._model)

        self._gear = NSButton.alloc().initWithFrame_(NSMakeRect(W - SIDE - 246, H - 60, 26, 26))
        self._gear.setBordered_(False)
        img = _symbol("slider.horizontal.3", 15, theme.DIM)
        if img is not None:
            self._gear.setImage_(img)
        self._gear.setContentTintColor_(theme.DIM)
        self._gear.setToolTip_("模型设置")
        self._gear.setTarget_(self)
        self._gear.setAction_("openSettings:")
        v.addSubview_(self._gear)

        # ---- 对话区
        self._scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(SIDE, COMPOSER_H + 24, W - 2 * SIDE, H - HEADER_H - COMPOSER_H - 24))
        self._scroll.setDrawsBackground_(False)
        self._scroll.setBorderType_(0)
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setAutohidesScrollers_(True)
        try:
            self._scroll.setScrollerStyle_(1)      # overlay，别留白色轨道
        except Exception:
            pass
        self._chat = ChatList.alloc().initWithFrame_(NSMakeRect(0, 0, W - 2 * SIDE - 14, 100))
        self._scroll.setDocumentView_(self._chat)
        v.addSubview_(self._scroll)

        # ---- 空状态
        self._empty = NSView.alloc().initWithFrame_(NSMakeRect(SIDE, 120, W - 2 * SIDE, 240))
        icon = NSImageView.alloc().initWithFrame_(NSMakeRect((W - 2 * SIDE - 40) / 2, 150, 40, 40))
        img = _symbol("sparkles", 30, theme.ACCENT)
        if img is not None:
            icon.setImage_(img)
            icon.setContentTintColor_(theme.ACCENT)
        self._empty.addSubview_(icon)
        t = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 108, W - 2 * SIDE, 24))
        t.setStringValue_("有什么可以帮你？")
        t.setFont_(NSFont.boldSystemFontOfSize_(18.0))
        t.setAlignment_(2)
        t.setBezeled_(False)
        t.setDrawsBackground_(False)
        t.setEditable_(False)
        self._empty.addSubview_(t)
        s = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 84, W - 2 * SIDE, 18))
        s.setStringValue_("模型由 Kilo 免费提供；也可以接自己的 API")
        s.setFont_(NSFont.systemFontOfSize_(12.0))
        s.setTextColor_(theme.DIM)
        s.setAlignment_(2)
        s.setBezeled_(False)
        s.setDrawsBackground_(False)
        s.setEditable_(False)
        self._empty.addSubview_(s)
        chips = ("介绍一下你自己", "写一段 Swift 代码", "把这段话翻译成英文")
        total = 0
        for i, text in enumerate(chips):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(0, 40, 150, 28))
            b.setTitle_(text)
            b.setBezelStyle_(1)
            b.setFont_(NSFont.systemFontOfSize_(12.0))
            b.setTag_(i)
            b.setTarget_(self)
            b.setAction_("suggestionClicked:")
            b.sizeToFit()
            b.setFrameSize_(NSMakeSize(max(120.0, b.frame().size.width + 24), 28))
            self._empty.addSubview_(b)
            total += b.frame().size.width + 10
        x = (W - 2 * SIDE - total + 10) / 2
        for b in self._empty.subviews()[3:]:
            b.setFrameOrigin_(NSMakePoint(x, 40))
            x += b.frame().size.width + 10
        v.addSubview_(self._empty)

        # ---- 输入框
        self._composer = Composer.alloc().initWithFrame_(NSMakeRect(SIDE, 18, W - 2 * SIDE, COMPOSER_H))
        self._composer.on_send = self.onComposerSend
        self._composer.resizeSubviewsWithOldSize_(NSMakeSize(0, 0))
        v.addSubview_(self._composer)

        self._render_history()
        p.center()
        self._load_models_async()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.05, self, "drain:", None, True)
        return p

    @objc.python_method
    def show(self):
        if self._panel is None:
            self.build(self._on_hide, self._log)
        self._panel.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._composer.focus()

    @objc.python_method
    def hide(self):
        if self._panel is not None:
            self._panel.orderOut_(None)
        if self._on_hide:
            self._on_hide()

    @objc.python_method
    def toggle(self):
        if self.is_visible:
            self.hide()
        else:
            self.show()

    @property
    @objc.python_method
    def is_visible(self):
        return bool(self._panel is not None and self._panel.isVisible())

    @objc.python_method
    def set_auto(self, _on):
        pass

    @objc.python_method
    def tick(self):
        self._drain()

    @objc.python_method
    def close(self):
        self._cancel.set()
        if self._timer is not None:
            try:
                self._timer.invalidate()
            except Exception:
                pass
            self._timer = None
        if self._panel is not None:
            self._panel.close()
            self._panel = None

    # ------------------------------------------------------------ 布局
    def windowDidResize_(self, note):
        self._layout()

    @objc.python_method
    def _layout(self):
        if self._panel is None:
            return
        v = self._panel.contentView()
        w, h = v.frame().size.width, v.frame().size.height
        self._title.setFrame_(NSMakeRect(SIDE, h - 66, w - 360, 24))
        self._sub.setFrame_(NSMakeRect(SIDE, h - 84, w - 360, 16))
        self._model.setFrame_(NSMakeRect(w - SIDE - 210, h - 62, 210, 26))
        self._gear.setFrame_(NSMakeRect(w - SIDE - 246, h - 60, 26, 26))
        self._scroll.setFrame_(NSMakeRect(SIDE, COMPOSER_H + 24, w - 2 * SIDE,
                                          h - HEADER_H - COMPOSER_H - 24))
        self._composer.setFrame_(NSMakeRect(SIDE, 18, w - 2 * SIDE, COMPOSER_H))
        self._empty.setFrame_(NSMakeRect(SIDE, 90, w - 2 * SIDE, 260))
        self._relayout_chat()

    @objc.python_method
    def _relayout_chat(self):
        w = self._scroll.contentView().bounds().size.width
        self._chat.relayout(w)
        self._scroll_bottom()

    @objc.python_method
    def _scroll_bottom(self):
        try:
            ch = self._chat.content_height()
            vh = self._scroll.contentView().bounds().size.height
            y = max(0.0, ch - vh)
            self._scroll.contentView().scrollToPoint_(NSMakePoint(0, y))
            self._scroll.reflectScrolledClipView_(self._scroll.contentView())
        except Exception:
            pass

    # ------------------------------------------------------------ 消息
    @objc.python_method
    def _render_history(self):
        self._chat.remove_all()
        for m in self.messages:
            self._chat.add_message(m.get("role", "user"), m.get("text", ""))
        self._empty.setHidden_(bool(self.messages))
        self._relayout_chat()

    @objc.python_method
    def _append(self, role, text):
        self.messages.append({"role": role, "text": text})
        self._chat.add_message(role, text)
        self._empty.setHidden_(True)
        self._relayout_chat()
        store.save_session(self.messages)
        return self._chat.rows[-1]

    def suggestionClicked_(self, sender):
        self._composer.setText_(sender.title())
        self._composer.focus()

    @objc.python_method
    def onComposerSend(self):
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
        self._reasoning = ""
        self._busy = True
        self._cancel.clear()
        self._composer.set_stop(True)
        model = self._selected_model()
        self._sub.setStringValue_("正在生成… · %s" % (model or self._provider()))
        threading.Thread(target=self._worker, args=(text, model), daemon=True).start()

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
    def _worker(self, text, model):
        msgs = []
        for m in self.messages[-20:]:
            if m.get("role") in ("user", "assistant") and m.get("text"):
                msgs.append({"role": m["role"], "content": m["text"]})
        if not msgs or msgs[-1].get("role") != "user":
            msgs.append({"role": "user", "content": text})
        try:
            for ch in self.client.stream(self._provider(), model, msgs,
                                         cancel=self._cancel):
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
            elif getattr(item, "type", "") == "reasoning":
                self._reasoning += item.text
            elif getattr(item, "type", "") == "done":
                self._finish()
                changed = True
        if changed:
            self._relayout_chat()

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

    # ------------------------------------------------------------ 模型
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
    def _load_models_async(self):
        def run():
            try:
                models = self.client.list_models(self._provider(), force=True)
            except Exception as e:
                self._q.put(("models_error", str(e)))
                return
            self._q.put(("models", models))
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def _fill_models(self, models):
        self._models = models
        self._model.removeAllItems()
        for m in models[:40]:
            title = m.id
            self._model.addItemWithTitle_(title)
        if not models:
            self._model.addItemWithTitle_(self.cfg.get("model") or "未获取到模型列表")
        # 恢复上次选择
        want = (self.cfg.get("model") or "kilo-auto/free")
        idx = self._model.indexOfItemWithTitle_(want)
        if idx >= 0:
            self._model.selectItemAtIndex_(idx)

    def modelChanged_(self, sender):
        self.cfg["model"] = self._selected_model()
        store.save_config(self.cfg)

    def openSettings_(self, sender):
        try:
            from .settings import SettingsSheet
            if not hasattr(self, "_settings") or self._settings is None:
                self._settings = SettingsSheet.alloc().init()
            self._settings.show(self)
        except Exception as e:
            self._log("打开设置失败: %r" % (e,))
