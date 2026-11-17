#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 许愿工坊 · 侧栏页（工作台布局）。

参考稿：灰底 + 白色圆角卡片、大标题、大输入卡、建议方案卡、最近的想法。
不是聊天气泡；AI 回答显示在「建议方案」卡里，流式追加。
"""

import json
import queue
import re
import threading

import objc
from AppKit import (NSApplication, NSAttributedString, NSButton, NSColor,
                    NSFont, NSImageView, NSImage, NSMakeRect, NSMakeSize,
                    NSPopUpButton, NSScrollView, NSTextField, NSTextView,
                    NSView, NSLineBreakByWordWrapping,
                    NSFontAttributeName, NSForegroundColorAttributeName,
                    NSImageScaleProportionallyUpOrDown)
from Foundation import NSObject, NSTimer

from .. import store
from ..llm import LLMClient, LLMError

W_MAX = 760.0
PAD = 32.0
ACCENT = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.42, 0.36, 0.91, 1.0)
GREEN = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.13, 0.62, 0.42, 1.0)
TEXT = NSColor.labelColor()
DIM = NSColor.secondaryLabelColor()
FAINT = NSColor.tertiaryLabelColor()


def _dark():
    try:
        from AppKit import NSApp
        return "Dark" in str(NSApp.effectiveAppearance().name())
    except Exception:
        return False


def _card_bg():
    return NSColor.colorWithCalibratedWhite_alpha_(0.17 if _dark() else 1.0, 1.0)


def _page_bg():
    return NSColor.colorWithCalibratedWhite_alpha_(0.10 if _dark() else 0.965, 1.0)


def _border():
    return NSColor.colorWithCalibratedWhite_alpha_(1.0 if _dark() else 0.0, 0.12)


def _label(text, x, y, w, h, size=13.0, bold=False, color=None, align=0):
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    f.setStringValue_(text)
    f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    f.setTextColor_(color or TEXT)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    if align:
        f.setAlignment_(align)
    return f


def _card(box, x, y, w, h, radius=14.0):
    v = _Card.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    v.setWantsLayer_(True)
    v.layer().setBackgroundColor_(_card_bg().CGColor())
    v.layer().setCornerRadius_(radius)
    v.layer().setBorderWidth_(1.0)
    v.layer().setBorderColor_(_border().CGColor())
    box.addSubview_(v)
    return v


def _symbol(name, size=15):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    try:
        from AppKit import NSImageSymbolConfiguration
        cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, 0.0)
        img = img.imageWithSymbolConfiguration_(cfg) or img
    except Exception:
        pass
    img.setTemplate_(True)
    return img


_OPS = {
    "set_bind": ("op", "gesture", "action"),
    "set_gain": ("op", "value"),
    "set_natural": ("op", "value"),
    "set_hold_ms": ("op", "value"),
    "set_rc_hold": ("op", "value"),
    "set_input_mode": ("op", "value"),
    "set_edid_correct": ("op", "value"),
}


def _coerce(op):
    """把模型常见的字符串值转成宿主需要的类型。"""
    o = dict(op)
    name = o.get("op")
    if name == "set_gain":
        try: o["value"] = float(o.get("value"))
        except Exception: return None
    elif name in ("set_hold_ms", "set_rc_hold"):
        try: o["value"] = int(float(o.get("value")))
        except Exception: return None
    elif name == "set_edid_correct":
        v = o.get("value")
        o["value"] = v if isinstance(v, bool) else str(v).lower() in ("true", "1", "on", "yes")
    elif name == "set_input_mode":
        o["value"] = str(o.get("value") or "").strip()
    elif name == "set_natural":
        o["value"] = str(o.get("value") or "").strip()
    elif name == "set_bind":
        o["gesture"] = str(o.get("gesture") or "").strip()
        o["action"] = str(o.get("action") or "").strip()
    return o


def _repair_ops(ops, manifest):
    """容忍模型用 {setting,value} 而不是 {op,...}：按标题映射回标准 op。"""
    if not isinstance(ops, list):
        return None
    def _norm(x):
        return re.sub(r"[\s·:：]+", "", str(x or ""))

    by_title = {m.get("title"): m for m in (manifest or [])}
    by_norm = {_norm(m.get("title")): m for m in (manifest or [])}
    alias = {"轻点": "tap", "快速滑动": "swipe", "滑动": "swipe",
             "停住再滑": "hold_swipe", "停住不动": "hold"}
    out = []
    for raw in ops:
        if not isinstance(raw, dict):
            return None
        if raw.get("op"):
            fixed = _coerce(raw)
            if fixed is None:
                return None
            out.append(fixed)
            continue
        title = raw.get("setting") or raw.get("name") or raw.get("title")
        m = by_title.get(title) or by_norm.get(_norm(title))
        if not m and str(title).strip() in alias:
            g = alias[str(title).strip()]
            m = next((x for x in (manifest or []) if x.get("gesture") == g), None)
        if not m:
            return None
        val = raw.get("value", raw.get("action"))
        if m["op"] == "set_bind":
            fixed = _coerce({"op": "set_bind", "gesture": m.get("gesture"),
                             "action": val})
        else:
            fixed = _coerce({"op": m["op"], "value": val})
        if fixed is None:
            return None
        out.append(fixed)
    return out


def _valid_ops(ops):
    if not isinstance(ops, list) or not ops:
        return False
    for op in ops:
        if not isinstance(op, dict):
            return False
        name = op.get("op")
        if name not in _OPS:
            return False
        if any(k not in op for k in _OPS[name]):
            return False
    return True


def _extract_json(text):
    """从回复里取第一个配平的 JSON 对象；取不到返回 None。"""
    if not text:
        return None
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except Exception:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    return None


class _Doc(NSView):
    def isFlipped(self):
        return True


class _Card(NSView):
    def isFlipped(self):
        return True


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
        self._timer = None
        self._doc = None
        self._w = 0.0
        self._h = 0.0
        self._result_text = ""
        self._result_view = None
        self._models = []
        self._settings = None
        self._pending_ops = None
        self._preview = []
        self._summary = ""
        self._status = ""
        return self

    # ------------------------------------------------------------ 页面
    @objc.python_method
    def render(self, box, w, h):
        self._box, self._w, self._h = box, float(w), float(h)
        self._scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self._scroll.setDrawsBackground_(True)
        self._scroll.setBackgroundColor_(_page_bg())
        self._scroll.setBorderType_(0)
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setAutohidesScrollers_(True)
        try:
            self._scroll.setScrollerStyle_(1)
        except Exception:
            pass
        self._doc = _Doc.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self._scroll.setDocumentView_(self._doc)
        box.addSubview_(self._scroll)
        self._layout_page()
        self._load_models_async()
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.05, self, "drain:", None, True)

    @objc.python_method
    def _layout_page(self):
        doc = self._doc
        for v in list(doc.subviews()):
            v.removeFromSuperview()
        w = self._w
        cw = min(W_MAX, w - 2 * PAD)
        x = (w - cw) / 2.0
        y = 24.0

        # ---- 品牌行
        icon_bg = NSView.alloc().initWithFrame_(NSMakeRect(x, y, 44, 44))
        icon_bg.setWantsLayer_(True)
        icon_bg.layer().setBackgroundColor_(ACCENT.CGColor())
        icon_bg.layer().setCornerRadius_(12.0)
        doc.addSubview_(icon_bg)
        ic = NSImageView.alloc().initWithFrame_(NSMakeRect(12, 12, 20, 20))
        img = _symbol("sparkles", 18)
        if img is not None:
            ic.setImage_(img)
            ic.setContentTintColor_(NSColor.whiteColor())
        icon_bg.addSubview_(ic)
        doc.addSubview_(_label("AI 许愿工坊", x + 58, y + 2, 300, 22, size=16, bold=True))
        doc.addSubview_(_label("轻量工作台 · 从一个想法开始", x + 58, y + 24, 320, 16,
                               size=11, color=DIM))

        self._model = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(x + cw - 154, y + 8, 118, 28), False)
        self._model.setFont_(NSFont.systemFontOfSize_(11.5))
        self._model.addItemWithTitle_("Kilo 免费")
        self._model.setTarget_(self)
        self._model.setAction_("modelChanged:")
        doc.addSubview_(self._model)

        gear = NSButton.alloc().initWithFrame_(NSMakeRect(x + cw - 30, y + 8, 28, 28))
        gear.setBordered_(False)
        gimg = _symbol("gearshape", 15)
        if gimg is not None:
            gear.setImage_(gimg)
        gear.setContentTintColor_(DIM)
        gear.setToolTip_("模型设置")
        gear.setTarget_(self)
        gear.setAction_("openSettings:")
        doc.addSubview_(gear)
        y += 78

        # ---- Hero
        doc.addSubview_(_label("想让平板怎样工作？", x, y, cw, 38, size=26, bold=True))
        y += 44
        doc.addSubview_(_label("描述你的想法，我先分析能否用现有设置实现，还是需要开发新插件。",
                               x, y, cw, 18, size=12, color=DIM))
        y += 30

        # ---- 输入卡
        ph = 140.0
        card = _card(doc, x, y, cw, ph)
        self._tv = NSTextView.alloc().initWithFrame_(NSMakeRect(20, 14, cw - 40, 72))
        self._tv.setFont_(NSFont.systemFontOfSize_(14.0))
        self._tv.setDrawsBackground_(False)
        self._tv.setRichText_(False)
        self._tv.setVerticallyResizable_(True)
        self._tv.textContainer().setWidthTracksTextView_(True)
        self._tv.setDelegate_(self)
        card.addSubview_(self._tv)
        self._placeholder = _label("把触控笔轻点改成右键", 24, 16, cw - 48, 20,
                                   size=14, color=FAINT)
        card.addSubview_(self._placeholder)
        card.addSubview_(_label("⏎  /  Ctrl + Enter 分析", 20, ph - 30, 240, 16,
                                size=10.5, color=DIM))
        self._send_btn = NSButton.alloc().initWithFrame_(NSMakeRect(cw - 148, ph - 42, 128, 34))
        self._send_btn.setBordered_(False)
        self._send_btn.setWantsLayer_(True)
        self._send_btn.layer().setBackgroundColor_(ACCENT.CGColor())
        self._send_btn.layer().setCornerRadius_(17.0)
        self._send_btn.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
            "✦  分析想法",
            {NSFontAttributeName: NSFont.boldSystemFontOfSize_(12.5),
             NSForegroundColorAttributeName: NSColor.whiteColor()}))
        self._send_btn.setTarget_(self)
        self._send_btn.setAction_("sendClicked:")
        card.addSubview_(self._send_btn)
        self._update_send(False)
        y += ph + 14

        # ---- chips
        doc.addSubview_(_label("试试", x, y + 4, 40, 16, size=11, color=DIM))
        cx = x + 44
        for i, t in enumerate(("笔手势映射", "手指双击", "截图按钮")):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(cx, y, 96, 26))
            b.setTitle_(t)
            b.setBezelStyle_(1)
            b.setFont_(NSFont.systemFontOfSize_(11.0))
            b.setTag_(i)
            b.setTarget_(self)
            b.setAction_("chipClicked:")
            b.sizeToFit()
            b.setFrameSize_(NSMakeSize(max(84.0, b.frame().size.width + 20), 26))
            doc.addSubview_(b)
            cx += b.frame().size.width + 8
        y += 44

        # ---- 建议方案
        doc.addSubview_(_label("建议方案", x, y, 200, 20, size=14, bold=True))
        doc.addSubview_(_label("基于模型回答 · 不执行操作", x + cw - 220, y + 3, 220, 14,
                               size=10, color=FAINT, align=2))
        y += 28
        rh = self._result_height()
        card2 = _card(doc, x, y, cw, rh)
        self._render_result(card2, cw, rh)
        y += rh + 26

        # ---- 最近的想法
        recent = [m for m in self.messages if m.get("role") == "user"][-3:]
        doc.addSubview_(_label("最近的想法", x, y, 200, 20, size=14, bold=True))
        doc.addSubview_(_label("%d 条" % len(recent), x + cw - 60, y + 3, 60, 14,
                               size=10, color=FAINT, align=2))
        y += 28
        if not recent:
            doc.addSubview_(_label("还没有想法。上面写一句，回车或点「分析想法」。",
                                   x, y, cw, 18, size=11, color=FAINT))
            y += 28
        for m in reversed(recent):
            row = _card(doc, x, y, cw, 56, radius=10)
            ic = NSImageView.alloc().initWithFrame_(NSMakeRect(16, 19, 18, 18))
            img = _symbol("bolt", 14)
            if img is not None:
                ic.setImage_(img)
                ic.setContentTintColor_(FAINT)
            row.addSubview_(ic)
            row.addSubview_(_label(str(m.get("text") or "")[:60], 46, 11, cw - 90, 18,
                                   size=12.5))
            row.addSubview_(_label("最近 · 设置", 46, 30, 200, 14, size=10, color=FAINT))
            chev = NSImageView.alloc().initWithFrame_(NSMakeRect(cw - 30, 21, 14, 14))
            cimg = _symbol("chevron.right", 11)
            if cimg is not None:
                chev.setImage_(cimg)
                chev.setContentTintColor_(FAINT)
            row.addSubview_(chev)
            y += 64

        y += 30
        doc.setFrameSize_(NSMakeSize(w, max(y, self._h)))

    @objc.python_method
    def _result_height(self):
        if self._pending_ops and self._preview:
            return min(420.0, 132.0 + 26.0 * len(self._preview))
        if self._result_text:
            return 240.0
        return 96.0

    @objc.python_method
    def _render_result(self, card, cw, rh):
        tag_bg = NSView.alloc().initWithFrame_(NSMakeRect(20, 18, 64, 22))
        tag_bg.setWantsLayer_(True)
        tag_bg.layer().setBackgroundColor_(GREEN.colorWithAlphaComponent_(0.12).CGColor())
        tag_bg.layer().setCornerRadius_(6.0)
        card.addSubview_(tag_bg)
        card.addSubview_(_label("AI 分析", 30, 22, 56, 14, size=10, color=GREEN))
        meta = self._status or ("待确认 · 点右下角应用" if self._pending_ops else "分析后给出方案 · 确认后生效")
        card.addSubview_(_label(meta, 96, 23, 300, 14, size=10, color=FAINT))

        # 待确认的方案：摘要 + 逐项差异 + 应用按钮
        if self._pending_ops and self._preview:
            card.addSubview_(_label(self._summary or "建议修改", 20, 50, cw - 40, 20,
                                    size=13, bold=True))
            ry = 78.0
            for c in self._preview:
                old = "开" if c.old is True else "关" if c.old is False else str(c.old)
                new = "开" if c.new is True else "关" if c.new is False else str(c.new)
                card.addSubview_(_label("%s：%s → %s" % (c.title, old, new),
                                        20, ry, cw - 160, 18, size=11.5))
                if c.warn:
                    card.addSubview_(_label(c.warn, cw - 150, ry, 130, 14, size=9.5,
                                            color=FAINT, align=2))
                ry += 26
            bw = 104.0
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(cw - bw - 20, rh - 42, bw, 30))
            btn.setBordered_(False)
            btn.setWantsLayer_(True)
            btn.layer().setBackgroundColor_(ACCENT.CGColor())
            btn.layer().setCornerRadius_(15.0)
            btn.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
                "应用修改", {NSFontAttributeName: NSFont.boldSystemFontOfSize_(12.0),
                          NSForegroundColorAttributeName: NSColor.whiteColor()}))
            btn.setTarget_(self)
            btn.setAction_("applyClicked:")
            card.addSubview_(btn)
            return

        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(20, 50, cw - 40, rh - 64))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setDrawsBackground_(False)
        tv.setFont_(NSFont.systemFontOfSize_(12.5))
        tv.setTextColor_(TEXT)
        tv.setVerticallyResizable_(True)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.setString_(self._result_text or (self._status or "还没有分析结果。写一句想法，点「分析想法」。"))
        card.addSubview_(tv)
        self._result_view = tv

        if not self._result_text:
            tv.setTextColor_(FAINT)
        self._scroll_result_bottom()

    @objc.python_method
    def _scroll_result_bottom(self):
        try:
            tv = self._result_view
            n = len(str(tv.string() or ""))
            tv.scrollRangeToVisible_((n, 0))
        except Exception:
            pass

    @objc.python_method
    def _scroll_page_top(self):
        try:
            self._scroll.contentView().scrollToPoint_((0, 0))
            self._scroll.reflectScrolledClipView_(self._scroll.contentView())
        except Exception:
            pass

    # ------------------------------------------------------------ 输入
    def textDidChange_(self, note):
        try:
            has = bool(str(self._tv.string() or "").strip())
            self._placeholder.setHidden_(has)
            self._update_send(has)
        except Exception:
            pass

    @objc.python_method
    def _update_send(self, has=None):
        if has is None:
            has = bool(str(self._tv.string() or "").strip())
        self._send_btn.setEnabled_(bool(has))
        self._send_btn.layer().setBackgroundColor_(
            (ACCENT if has else ACCENT.colorWithAlphaComponent_(0.38)).CGColor())

    def textView_doCommandBySelector_(self, tv, sel):
        from AppKit import NSApp
        if sel == "insertNewline:":
            ev = NSApp.currentEvent()
            if ev is not None and (ev.modifierFlags() & (1 << 18)):
                tv.insertNewline_(None)
                return True
            self._submit()
            return True
        return False

    def chipClicked_(self, sender):
        texts = ("把笔的轻点改成右键", "让手指双击变成一次左键单击", "做一个截图按钮插件")
        i = max(0, min(int(sender.tag()), len(texts) - 1))
        self._tv.setString_(texts[i])
        self._placeholder.setHidden_(True)

    def sendClicked_(self, sender):
        self._submit()

    @objc.python_method
    def _submit(self):
        if self._busy:
            return
        text = str(self._tv.string() or "").strip()
        if not text:
            return
        self._tv.setString_("")
        self._placeholder.setHidden_(False)
        self._update_send(False)
        self.messages.append({"role": "user", "text": text})
        store.save_session(self.messages)
        self._result_text = ""
        self._pending_ops = None
        self._preview = []
        self._summary = ""
        self._busy = True
        self._status = "正在分析…"
        self._cancel.clear()
        self._layout_page()
        self._scroll_page_top()
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    @objc.python_method
    def _worker(self, text):
        msgs = [{"role": "system", "content": self._system_prompt()}]
        for m in self.messages[-20:]:
            if m.get("role") in ("user", "assistant") and m.get("text"):
                msgs.append({"role": m["role"], "content": m["text"]})
        if not msgs or msgs[-1].get("role") != "user":
            msgs.append({"role": "user", "content": text})
        try:
            for ch in self.client.stream(self._provider(), self._model_id(),
                                         msgs, max_tokens=1600, cancel=self._cancel):
                self._q.put(ch)
        except LLMError as e:
            self._q.put(("error", e.user_text()))
        except Exception as e:
            self._q.put(("error", repr(e)))
        finally:
            self._q.put(("done", None))

    def drain_(self, timer):
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
                    continue
                if kind == "error":
                    self._result_text += ("\n\n⚠️ " + str(payload))
                    changed = True
                elif kind == "done":
                    self._busy = False
                    self._status = ""
                    self._finish_reply()
                continue
            if getattr(item, "type", "") == "content":
                self._result_text += item.text
                changed = True
        if changed and self._result_view is not None:
            self._result_view.setTextColor_(TEXT)
            self._result_view.setString_(self._result_text)
            self._scroll_result_bottom()

    @objc.python_method
    def _api(self):
        try:
            import dptouch_plugin as PLUG
            return getattr(PLUG.host(), "settings_api", None)
        except Exception:
            return None

    @objc.python_method
    def _system_prompt(self):
        api = self._api()
        ctx = api.context_text() if api else "（宿主没有提供设置接口：只能解释，不能改设置）"
        return "你是 Mipad DP Touch 的设置助手。\n\n" + ctx

    @objc.python_method
    def _finish_reply(self):
        text = (self._result_text or "").strip()
        obj = _extract_json(text)
        api = self._api()
        if obj and obj.get("type") == "settings_change" and isinstance(obj.get("operations"), list):
            api0 = self._api()
            manifest = api0.manifest() if api0 else []
            ops = _repair_ops(obj["operations"], manifest)
            if ops is None or not _valid_ops(ops):
                self._result_text = ("模型给的方案格式不对，无法应用。\n\n原始回复：\n"
                                     + text[:600])
                self.messages.append({"role": "assistant", "text": self._result_text})
                store.save_session(self.messages)
                self._layout_page()
                return
            if api is None:
                self._result_text = "这个方案需要改设置，但当前宿主没有提供设置接口。"
            else:
                try:
                    self._preview = api.preview(ops)
                    self._pending_ops = ops
                    self._summary = obj.get("summary") or ""
                    self._result_text = ""
                except Exception as e:
                    self._result_text = "方案无法应用：%r" % (e,)
        elif obj and obj.get("type") == "answer":
            self._result_text = obj.get("text") or text
        elif obj and obj.get("type") == "unsupported":
            self._result_text = "\n".join(x for x in (obj.get("reason"), obj.get("why")) if x)
        if self._result_text:
            self.messages.append({"role": "assistant", "text": self._result_text})
            store.save_session(self.messages)
        self._layout_page()

    def applyClicked_(self, sender):
        api = self._api()
        if api is None or not self._pending_ops:
            return
        res = api.apply(self._pending_ops)
        if res.ok:
            lines = ["已应用 %d 项修改：" % len(res.applied)]
            for c in res.applied:
                old = "开" if c.old is True else "关" if c.old is False else str(c.old)
                new = "开" if c.new is True else "关" if c.new is False else str(c.new)
                lines.append("· %s：%s → %s" % (c.title, old, new))
            self._result_text = "\n".join(lines)
        else:
            msg = "应用失败：" + "；".join(res.errors or ["未知原因"])
            if res.rollback_failed:
                msg += "\n未能回滚：" + "；".join(res.rollback_failed)
            elif res.rolled_back:
                msg += "\n已回滚：" + "、".join(res.rolled_back)
            self._result_text = msg
        self._pending_ops = None
        self._preview = []
        self._summary = ""
        self.messages.append({"role": "assistant", "text": self._result_text})
        store.save_session(self.messages)
        self._layout_page()

    @objc.python_method
    def _provider(self):
        pid = self.cfg.get("provider") or "kilo"
        return pid if pid in self.client.provider_ids() else "kilo"

    @objc.python_method
    def _model_id(self):
        try:
            t = str(self._model.titleOfSelectedItem() or "")
            return "" if t in ("Kilo 免费", "加载中…") else t
        except Exception:
            return ""

    def modelChanged_(self, sender):
        self.cfg["model"] = self._model_id()
        store.save_config(self.cfg)

    def openSettings_(self, sender):
        try:
            from .settings import SettingsSheet
            if self._settings is None:
                self._settings = SettingsSheet.alloc().init()
            self._settings.show(self)
        except Exception as e:
            self._result_text = "打开设置失败：%r" % (e,)
            self._layout_page()

    @objc.python_method
    def _load_models_async(self):
        def run():
            try:
                self._q.put(("models", self.client.list_models(self._provider(), force=True)))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def _fill_models(self, models):
        self._models = models
        self._model.removeAllItems()
        titles = [m.id for m in models[:20]] or ["Kilo 免费"]
        for t in titles:
            self._model.addItemWithTitle_(t)
        want = self.cfg.get("model") or "kilo-auto/free"
        i = self._model.indexOfItemWithTitle_(want)
        if i >= 0:
            self._model.selectItemAtIndex_(i)
