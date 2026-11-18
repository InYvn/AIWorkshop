#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型设置：服务、Base URL、模型、Key、连接测试。

密钥写进钥匙串，不写配置文件；测试在后台线程跑，不卡 UI。
"""

import threading

import objc
from AppKit import (NSApplication, NSFont, NSMakeRect, NSPanel, NSPopUpButton,
                    NSSecureTextField, NSTextField, NSWindowStyleMaskTitled,
                    NSWindowStyleMaskClosable, NSBackingStoreBuffered)
from Foundation import NSObject

from .. import store
from ..llm import LLMError
from . import theme


class SettingsSheet(NSObject):
    def init(self):
        self = objc.super(SettingsSheet, self).init()
        if self is None:
            return None
        self.chat = None
        self.panel = None
        self._popup = None
        self._base = None
        self._model = None
        self._key = None
        self._result = None
        self._test_result = None
        return self

    @objc.python_method
    def present(self, chat):
        self.chat = chat
        W, H = 540.0, 360.0
        p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        p.setTitle_("模型设置")
        p.setReleasedWhenClosed_(False)
        v = p.contentView()

        def lab(text, y, w=110):
            f = NSTextField.alloc().initWithFrame_(NSMakeRect(20, H - y - 18, w, 18))
            f.setStringValue_(text)
            f.setFont_(NSFont.systemFontOfSize_(12.0))
            f.setTextColor_(theme.DIM)
            f.setBezeled_(False)
            f.setDrawsBackground_(False)
            f.setEditable_(False)
            v.addSubview_(f)

        lab("服务", 24)
        self._popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(140, H - 28, 370, 26), False)
        ids = chat.client.provider_ids()
        self._popup.addItemsWithTitles_(
            ["%s" % chat.client.provider(pid)["title"] for pid in ids])
        pid = chat._provider()
        self._popup.selectItemAtIndex_(ids.index(pid) if pid in ids else 0)
        self._popup.setTarget_(self)
        self._popup.setAction_("providerChanged:")
        v.addSubview_(self._popup)

        lab("Base URL", 64)
        self._base = NSTextField.alloc().initWithFrame_(NSMakeRect(140, H - 68, 370, 24))
        v.addSubview_(self._base)

        lab("模型", 104)
        self._model = NSTextField.alloc().initWithFrame_(NSMakeRect(140, H - 108, 370, 24))
        v.addSubview_(self._model)

        lab("API Key", 144)
        self._key = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(140, H - 148, 370, 24))
        self._key.setPlaceholderString_("已保存则留空；Kilo 免费与 LiteLLM Proxy 可留空")
        v.addSubview_(self._key)

        self._hint = NSTextField.alloc().initWithFrame_(NSMakeRect(20, H - 190, 490, 34))
        self._hint.setFont_(NSFont.systemFontOfSize_(10.5))
        self._hint.setTextColor_(theme.DIM)
        self._hint.setBezeled_(False)
        self._hint.setDrawsBackground_(False)
        self._hint.setEditable_(False)
        self._hint.setUsesSingleLineMode_(False)
        self._hint.cell().setWraps_(True)
        v.addSubview_(self._hint)

        self._test_btn = self._button("测试连接", 20, H - 240, 100, self, "testClicked:")
        v.addSubview_(self._test_btn)
        self._result = NSTextField.alloc().initWithFrame_(NSMakeRect(132, H - 234, 380, 18))
        self._result.setFont_(NSFont.systemFontOfSize_(11.0))
        self._result.setTextColor_(theme.DIM)
        self._result.setBezeled_(False)
        self._result.setDrawsBackground_(False)
        self._result.setEditable_(False)
        v.addSubview_(self._result)

        save = self._button("保存", W - 190, 14, 84, self, "saveClicked:")
        cancel = self._button("取消", W - 100, 14, 84, self, "cancelClicked:")
        v.addSubview_(save)
        v.addSubview_(cancel)

        self.panel = p
        self._load_provider()
        p.center()
        p.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _button(self, title, x, y, w, target, action):
        from AppKit import NSButton
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 28))
        b.setTitle_(title)
        b.setBezelStyle_(1)
        b.setFont_(NSFont.systemFontOfSize_(12.0))
        b.setTarget_(target)
        b.setAction_(action)
        return b

    # ------------------------------------------------------------ 数据
    @objc.python_method
    def _pid(self):
        ids = self.chat.client.provider_ids()
        i = self._popup.indexOfSelectedItem()
        return ids[max(0, min(i, len(ids) - 1))]

    @objc.python_method
    def _load_provider(self):
        pid = self._pid()
        meta = self.chat.client.provider(pid)
        cfg = self.chat.client.config(pid)
        self._base.setStringValue_(cfg.get("base") or "")
        self._base.setPlaceholderString_(meta.get("base") or "")
        self._model.setStringValue_(cfg.get("model") or "")
        self._model.setPlaceholderString_(meta.get("default_model") or "")
        self._key.setStringValue_("")
        self._hint.setStringValue_(meta.get("hint") or "")
        self._result.setStringValue_("")

    def providerChanged_(self, sender):
        self._load_provider()

    # ------------------------------------------------------------ 测试
    def testClicked_(self, sender):
        pid = self._pid()
        base = self._base.stringValue().strip()
        model = self._model.stringValue().strip()
        key = self._key.stringValue().strip()
        self.chat.client.configure(pid, base=base or None, model=model or None)
        if key:
            self.chat.client.configure(pid, key=key)
        self._result.setStringValue_("测试中…")
        threading.Thread(target=self._run_test, args=(pid, model), daemon=True).start()

    @objc.python_method
    def _run_test(self, pid, model):
        try:
            r = self.chat.client.test(pid, model or None)
            self._test_result = r
        except LLMError as e:
            self._test_result = e.user_text()
        except Exception as e:
            self._test_result = repr(e)
        self.performSelectorOnMainThread_withObject_waitUntilDone_("testDone:", None, False)

    def testDone_(self, _):
        r = self._test_result
        if hasattr(r, "ok"):
            self._result.setStringValue_(("连接正常 · %s · %d ms" % (r.model, r.latency_ms))
                                         if r.ok else ("失败：%s" % r.detail))
        else:
            self._result.setStringValue_("失败：%s" % (r,))

    # ------------------------------------------------------------ 保存
    def saveClicked_(self, sender):
        pid = self._pid()
        base = self._base.stringValue().strip()
        model = self._model.stringValue().strip()
        key = self._key.stringValue().strip()
        self.chat.client.configure(pid, base=base or None, model=model or None)
        if key:
            try:
                store.set_key(pid, key)
                self.chat.client.configure(pid, key=key)
            except Exception as e:
                self._result.setStringValue_("Key 保存失败：%r" % (e,))
                return
        self.chat.cfg["provider"] = pid
        if model:
            self.chat.cfg["model"] = model
        provs = self.chat.cfg.setdefault("providers", {})
        provs[pid] = {"base": base, "model": model}
        store.save_config(self.chat.cfg)
        self.chat.client.invalidate(pid)
        self.chat.set_provider_model(pid, model)
        self._close()

    def cancelClicked_(self, sender):
        self._close()

    @objc.python_method
    def _close(self):
        try:
            NSApplication.sharedApplication().stopModal()
            self.panel.close()
        except Exception:
            pass
        self.panel = None
