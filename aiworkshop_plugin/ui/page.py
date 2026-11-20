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
                    NSPopUpButton, NSScrollView, NSSegmentedControl, NSTextField, NSTextView,
                    NSView, NSLineBreakByWordWrapping,
                    NSFontAttributeName, NSForegroundColorAttributeName,
                    NSImageScaleProportionallyUpOrDown)
from Foundation import NSObject, NSTimer

from .. import store
from ..llm import LLMClient, LLMError
from .state import Request, WorkshopState

W_MAX = 760.0
PAD = 32.0
ACCENT = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.929, 0.416, 0.341, 1.0)
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
    # Oink 风格：浅色纯白卡片 / 深色 #232220
    return NSColor.colorWithCalibratedWhite_alpha_(0.137 if _dark() else 1.0, 1.0)


def _primary_bg():
    return NSColor.colorWithCalibratedWhite_alpha_(0.96 if _dark() else 0.11, 1.0)


def _on_primary():
    return NSColor.colorWithCalibratedWhite_alpha_(0.11 if _dark() else 0.96, 1.0)


_ACTION_CN = {
    "left": "左键", "right": "右键", "middle": "中键", "double": "双击",
    "scroll": "滚动", "drag": "拖拽", "none": "无", "space": "空格",
    "back": "后退", "screen": "截屏",
}


def _show(v):
    if v is True:
        return "开"
    if v is False:
        return "关"
    return _ACTION_CN.get(str(v), str(v))


def _page_bg():
    # Oink 风格：浅色 #F9F9FB / 深色 #1B1A18
    return NSColor.colorWithCalibratedWhite_alpha_(0.105 if _dark() else 0.976, 1.0)


def _border():
    return NSColor.colorWithCalibratedWhite_alpha_(1.0 if _dark() else 0.0, 0.07)


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


def _card(box, x, y, w, h, radius=20.0):
    v = _Card.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    v.setWantsLayer_(True)
    v.layer().setBackgroundColor_(_card_bg().CGColor())
    v.layer().setCornerRadius_(radius)
    try:
        v.layer().setCornerCurve_("continuous")
    except Exception:
        pass
    v.layer().setBorderWidth_(1.0)
    v.layer().setBorderColor_(_border().CGColor())
    # 轻微浮起，接近 Oink 的 inset 高光 + 低对比阴影
    v.layer().setShadowOpacity_(0.16 if _dark() else 0.05)
    v.layer().setShadowRadius_(10.0)
    v.layer().setShadowOffset_((0.0, -1.0))
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
        pid = self.cfg.get("provider") or "kilo"
        if pid not in self.client.provider_ids():
            pid = "kilo"
        self.state = WorkshopState(provider=pid,
                                   model=str(self.cfg.get("model") or ""),
                                   messages=store.load_session("default"))
        self._cancel = threading.Event()
        self._q = queue.Queue()
        self._timer = None
        self._doc = None
        self._w = 0.0
        self._h = 0.0
        self._result_view = None
        self._settings = None
        self._result_card = None
        self._recent_title = None
        self._recent_count = None
        self._recent_box = None
        self._built = False
        return self

    # ------------------------------------------------------------ 页面
    @objc.python_method
    def render(self, box, w, h):
        self._box, self._w, self._h = box, float(w), float(h)
        old = getattr(self, "_scroll", None)
        if old is not None:
            try:
                old.removeFromSuperview()
            except Exception:
                pass
        # 宿主每次切页都会重新调用 render：重建骨架，不能沿用上一个 doc
        self._built = False
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
        self._build_page()
        self._load_models_async()
        if self._timer is None:
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.05, self, "drain:", None, True)

    @objc.python_method
    def _build_page(self):
        """静态骨架只建一次：品牌行 / 标题 / 输入卡 / chips。"""
        if self._built:
            return
        self._built = True
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
        hero = _label("想让平板怎样工作？", x, y, cw, 40, size=28, bold=True)
        try:
            rf = NSFont.fontWithName_size_("SFProRounded-Bold", 28.0)
            if rf is not None:
                hero.setFont_(rf)
        except Exception:
            pass
        doc.addSubview_(hero)
        y += 44
        doc.addSubview_(_label("描述你的想法：现有设置能实现就直接改，做不到就生成一个插件。",
                               x, y, cw, 18, size=12, color=DIM))
        y += 40

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
        self._placeholder = _label("把触控笔轻点改成右键，或做一个截图按钮插件", 24, 16, cw - 48, 20,
                                   size=14, color=FAINT)
        card.addSubview_(self._placeholder)
        card.addSubview_(_label("⏎  /  Ctrl + Enter 分析", 20, ph - 30, 240, 16,
                                size=10.5, color=DIM))
        self._send_btn = NSButton.alloc().initWithFrame_(NSMakeRect(cw - 148, ph - 42, 128, 34))
        self._send_btn.setBordered_(False)
        self._send_btn.setWantsLayer_(True)
        self._send_btn.layer().setBackgroundColor_(_primary_bg().CGColor())
        self._send_btn.layer().setCornerRadius_(17.0)
        self._send_btn.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
            "✦  分析想法",
            {NSFontAttributeName: NSFont.boldSystemFontOfSize_(12.5),
             NSForegroundColorAttributeName: _on_primary()}))
        self._send_btn.setTarget_(self)
        self._send_btn.setAction_("sendClicked:")
        card.addSubview_(self._send_btn)
        self._update_send(False)
        y += ph + 14

        # ---- chips
        self._chips = []
        chip_label = _label("试试", x, y + 4, 40, 16, size=11, color=DIM)
        doc.addSubview_(chip_label)
        self._chips.append(chip_label)
        cx = x + 44
        for i, t in enumerate(("笔手势映射", "手指双击", "做个笔模式面板")):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(cx, y, 96, 26))
            b.setTitle_(t)
            b.setBezelStyle_(1)
            b.setFont_(NSFont.systemFontOfSize_(11.0))
            b.setTag_(i)
            b.setBordered_(False)
            b.setWantsLayer_(True)
            b.layer().setBackgroundColor_(_card_bg().CGColor())
            b.layer().setCornerRadius_(13.0)
            b.layer().setBorderWidth_(1.0)
            b.layer().setBorderColor_(_border().CGColor())
            b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
                t, {NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
                    NSForegroundColorAttributeName: TEXT}))
            b.setTarget_(self)
            b.setAction_("chipClicked:")
            b.sizeToFit()
            b.setFrameSize_(NSMakeSize(max(84.0, b.frame().size.width + 20), 26))
            doc.addSubview_(b)
            self._chips.append(b)
            cx += b.frame().size.width + 8
        self._full_cb = NSButton.alloc().initWithFrame_(
            NSMakeRect(x + cw - 214, y - 2, 214, 22))
        self._full_cb.setButtonType_(3)
        self._full_cb.setTitle_("允许插件使用完全权限")
        self._full_cb.setFont_(NSFont.systemFontOfSize_(11.0))
        self._full_cb.setState_(1 if store.full_permissions() else 0)
        self._full_cb.setToolTip_("开启后生成的插件可读写文件、执行命令、访问网络；"
                                  "只在你信任模型输出时使用")
        self._full_cb.setTarget_(self)
        self._full_cb.setAction_("fullPermChanged:")
        doc.addSubview_(self._full_cb)
        y += 44
        self._dynamic_y = y
        self._refresh_cards()

    @objc.python_method
    def _refresh_cards(self):
        """只重建会变的卡片（建议方案 / 最近的想法），输入区与模型选择器不动。"""
        doc = self._doc
        for v in (self._result_card, self._recent_title, self._recent_count,
                  self._recent_box):
            if v is not None:
                try:
                    v.removeFromSuperview()
                except Exception:
                    pass
        self._result_card = None
        self._recent_title = None
        self._recent_count = None
        self._recent_box = None

        w = self._w
        cw = min(W_MAX, w - 2 * PAD)
        x = (w - cw) / 2.0
        y = self._dynamic_y

        doc.addSubview_(_label("建议方案", x, y, 200, 20, size=15, bold=True))
        doc.addSubview_(_label("模型给出方案 · 确认后应用", x + cw - 220, y + 3, 220, 14,
                               size=10, color=FAINT, align=2))
        y += 28
        rh = self._result_height()
        card = _card(doc, x, y, cw, rh)
        self._result_card = card
        self._render_result(card, cw, rh)
        y += rh + 26

        recent = self.state.recent(3)
        self._recent_title = _label("最近的想法", x, y, 200, 20, size=15, bold=True)
        doc.addSubview_(self._recent_title)
        self._recent_count = _label("%d 条" % len(recent), x + cw - 60, y + 3, 60, 14,
                                    size=10, color=FAINT, align=2)
        doc.addSubview_(self._recent_count)
        y += 28
        box = NSView.alloc().initWithFrame_(NSMakeRect(x, y, cw, 1))
        self._recent_box = box
        doc.addSubview_(box)
        by = 0.0
        if not recent:
            box.addSubview_(_label("还没有想法。上面写一句，回车或点「分析想法」。",
                                   0, 0, cw, 18, size=11, color=FAINT))
            by += 28
        for m in reversed(recent):
            row = _card(box, 0, by, cw, 56, radius=12)
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
            by += 64
        box.setFrameSize_(NSMakeSize(cw, by))
        y += by + 30
        doc.setFrameSize_(NSMakeSize(w, max(y, self._h)))

    @objc.python_method
    def _result_height(self):
        if self.state.draft:
            files = (self.state.draft.get("files") or {})
            check = self.state.draft_check or {}
            errs = len(check.get("errors") or [])
            extra = 20.0 if check.get("full") else 0.0
            return 200.0 + extra + 24.0 * min(4, len(files)) + (36.0 if errs else 0.0)
        if self.state.pending_ops and self.state.preview:
            return min(440.0, 150.0 + 26.0 * len(self.state.preview))
        if self.state.result_text or self.state.busy:
            return 268.0 if self.state.undo_available else 240.0
        return 96.0

    @objc.python_method
    def _render_result(self, card, cw, rh):
        tag_bg = NSView.alloc().initWithFrame_(NSMakeRect(20, 18, 64, 22))
        tag_bg.setWantsLayer_(True)
        tag_bg.layer().setBackgroundColor_(GREEN.colorWithAlphaComponent_(0.12).CGColor())
        tag_bg.layer().setCornerRadius_(6.0)
        card.addSubview_(tag_bg)
        card.addSubview_(_label("AI 分析", 30, 22, 56, 14, size=10, color=GREEN))
        pending = bool(self.state.pending_ops and self.state.preview)
        if pending:
            meta = self.state.status or "待确认 · 点右下角应用"
        elif self.state.draft:
            meta = "插件草稿 · 确认后安装"
        elif self.state.undo_available:
            meta = "已应用 · 可撤销"
        else:
            meta = self.state.status or "分析后给出方案 · 确认后生效"
        card.addSubview_(_label(meta, 96, 23, 320, 14, size=10, color=FAINT))
        self._result_view = None

        if self.state.draft:
            self._render_draft(card, cw, rh)
            return

        # ---- 待确认：逐项差异（将修改 / 未修改）
        if pending:
            noop = sum(1 for c in self.state.preview
                       if str(c.warn or "").startswith("已经是这个值"))
            card.addSubview_(_label(self.state.summary or "建议修改", 20, 46, cw - 40, 20,
                                    size=13, bold=True))
            card.addSubview_(_label("%d 项将修改 · %d 项不变"
                                    % (len(self.state.preview) - noop, noop),
                                    20, 68, cw - 40, 14, size=10, color=FAINT))
            ry = 94.0
            for c in self.state.preview:
                same = str(c.warn or "").startswith("已经是这个值")
                row = _label("%s：%s → %s" % (c.title, _show(c.old), _show(c.new)),
                             20, ry, cw - 220, 18, size=11.5,
                             color=FAINT if same else TEXT)
                if c.warn:
                    row.setToolTip_(str(c.warn))
                card.addSubview_(row)
                card.addSubview_(_label("未修改" if same else "将修改",
                                        cw - 176, ry, 56, 14, size=9.5,
                                        color=FAINT, align=2))
                if c.warn and not same and len(str(c.warn)) <= 10:
                    card.addSubview_(_label(str(c.warn), cw - 112, ry, 92, 14, size=9.5,
                                            color=FAINT, align=2))
                ry += 26
            bw = 104.0
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(cw - bw - 20, rh - 42, bw, 30))
            btn.setBordered_(False)
            btn.setWantsLayer_(True)
            btn.layer().setBackgroundColor_(_primary_bg().CGColor())
            btn.layer().setCornerRadius_(15.0)
            btn.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
                "应用修改", {NSFontAttributeName: NSFont.boldSystemFontOfSize_(12.0),
                          NSForegroundColorAttributeName: _on_primary()}))
            btn.setTarget_(self)
            btn.setAction_("applyClicked:")
            card.addSubview_(btn)
            return

        # ---- 结果文本（已应用 / 未修改 / 失败）
        tv_h = rh - 64 - (40.0 if self.state.undo_available else 0.0)
        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(20, 50, cw - 40, tv_h))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setDrawsBackground_(False)
        tv.setFont_(NSFont.systemFontOfSize_(12.5))
        tv.setTextColor_(TEXT)
        tv.setVerticallyResizable_(True)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.setString_(self.state.result_text
                      or (self.state.status or "还没有分析结果。写一句想法，点「分析想法」。"))
        card.addSubview_(tv)
        self._result_view = tv

        if not self.state.result_text:
            tv.setTextColor_(FAINT)

        if self.state.undo_available:
            ub = NSButton.alloc().initWithFrame_(NSMakeRect(cw - 104, rh - 42, 84, 28))
            ub.setTitle_("撤销")
            ub.setBezelStyle_(1)
            ub.setFont_(NSFont.systemFontOfSize_(12.0))
            ub.setTarget_(self)
            ub.setAction_("undoClicked:")
            card.addSubview_(ub)
            card.addSubview_(_label("点「撤销」把上面这些设置恢复原值", 20, rh - 38, cw - 130, 16,
                                    size=10, color=FAINT))

        self._scroll_result_bottom()

    @objc.python_method
    def _render_draft(self, card, cw, rh):
        from ..gen import draft as D
        st = self.state
        check = st.draft_check or {}
        files = st.draft.get("files") or {}
        card.addSubview_(_label(st.draft.get("summary") or "插件草稿", 20, 46, cw - 40, 20,
                                size=13, bold=True))
        ok = bool(check.get("ok"))
        status = "静态校验通过" if ok else ("%d 个问题" % len(check.get("errors") or []))
        card.addSubview_(_label(status, 20, 70, 200, 16, size=10.5,
                                color=GREEN if ok else NSColor.systemRedColor()))
        if st.draft_sandbox:
            sb = st.draft_sandbox
            card.addSubview_(_label("沙箱试运行：" + ("通过" if sb.get("ok") else "失败"),
                                    150, 70, 240, 16, size=10.5,
                                    color=GREEN if sb.get("ok") else NSColor.systemRedColor()))
        ry = 94.0
        if check.get("full"):
            card.addSubview_(_label("⚠ 完全权限：生成代码可读写文件 / 执行命令 / 访问网络",
                                    20, 88, cw - 40, 16, size=10,
                                    color=NSColor.systemRedColor()))
            ry = 112.0
        for path in list(files)[:4]:
            text = files.get(path) or ""
            card.addSubview_(_label(path, 20, ry, cw - 220, 16, size=11))
            card.addSubview_(_label("%d 字节" % len(text), cw - 190, ry, 80, 16,
                                    size=10, color=FAINT, align=2))
            ry += 24
        errs = check.get("errors") or []
        if errs:
            card.addSubview_(_label("· " + errs[0][:96], 20, ry + 2, cw - 40, 16,
                                    size=10, color=NSColor.systemRedColor()))
            ry += 22
        if st.draft_installed:
            card.addSubview_(_label("已安装 %s · 重启 App 后出现在左侧栏" % st.draft_installed,
                                    20, rh - 38, cw - 240, 16, size=10.5, color=GREEN))
        elif st.installing:
            card.addSubview_(_label("正在沙箱试运行并安装…", 20, rh - 38, cw - 240, 16,
                                    size=10.5, color=DIM))

        bw = 116.0
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(cw - bw - 20, rh - 42, bw, 30))
        btn.setBordered_(False)
        btn.setWantsLayer_(True)
        btn.layer().setBackgroundColor_((_primary_bg() if ok
                                         else _primary_bg().colorWithAlphaComponent_(0.35)).CGColor())
        btn.layer().setCornerRadius_(15.0)
        btn.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
            "安装插件", {NSFontAttributeName: NSFont.boldSystemFontOfSize_(12.0),
                      NSForegroundColorAttributeName: _on_primary()}))
        btn.setEnabled_(ok and not st.installing and not st.draft_installed)
        btn.setTarget_(self)
        btn.setAction_("installDraftClicked:")
        card.addSubview_(btn)

        ob = NSButton.alloc().initWithFrame_(NSMakeRect(cw - bw - 132, rh - 42, 104, 28))
        ob.setTitle_("草稿目录")
        ob.setBezelStyle_(1)
        ob.setFont_(NSFont.systemFontOfSize_(11.5))
        ob.setTarget_(self)
        ob.setAction_("revealDraftClicked:")
        card.addSubview_(ob)

    def fullPermChanged_(self, sender):
        store.set_full_permissions(bool(sender.state()))
        self._refresh_cards()

    def installDraftClicked_(self, sender):
        st = self.state
        if st.installing or not st.draft:
            return
        st.installing = True
        st.status = "正在试运行…"
        self._refresh_cards()
        threading.Thread(target=self._install_worker, args=(dict(st.draft),),
                         daemon=True).start()

    @objc.python_method
    def _install_worker(self, draft_obj):
        from ..gen import draft as D, sandbox
        try:
            files = draft_obj.get("files") or {}
            meta = json.loads(files.get("plugin.json") or "{}")
            pid = str(meta.get("id") or "")
            entry = str(meta.get("entry") or (pid + "_plugin"))
            d = D.write_draft(pid, files)
            sb = sandbox.run(d, entry, full=bool((self.state.draft_check or {}).get("full")))
            if not sb.get("ok"):
                self._q.put(("draft_done", (sb, None, pid)))
                return
            rec = D.install(pid, D.zip_draft(pid))
            self._q.put(("draft_done", (sb, rec, pid)))
        except Exception as e:
            self._q.put(("draft_done", ({"ok": False, "output": repr(e)}, None, "")))

    def revealDraftClicked_(self, sender):
        from AppKit import NSWorkspace
        from ..gen import draft as D
        try:
            meta = json.loads((self.state.draft.get("files") or {}).get("plugin.json") or "{}")
            d = D.draft_dir(str(meta.get("id") or "draft"))
            NSWorkspace.sharedWorkspace().openFile_(d)
        except Exception:
            pass

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
            text = str(self._tv.string() or "")
            self.state.input_text = text
            has = bool(text.strip())
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
            (_primary_bg() if has else _primary_bg().colorWithAlphaComponent_(0.35)).CGColor())

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
        texts = ("把笔的轻点改成右键", "让手指双击变成一次左键单击", "做一个一键切成笔模式的插件页面")
        i = max(0, min(int(sender.tag()), len(texts) - 1))
        self._tv.setString_(texts[i])
        self.state.input_text = texts[i]
        self._placeholder.setHidden_(True)
        self._update_send(True)

    def sendClicked_(self, sender):
        self._submit()

    @objc.python_method
    def _submit(self):
        if self.state.busy:
            return
        text = str(self._tv.string() or "").strip()
        if not text:
            return
        self._tv.setString_("")
        self._placeholder.setHidden_(False)
        self._update_send(False)

        # 主线程：写状态 → 生成快照；后台线程之后只读快照
        self.state.begin(text)
        store.save_session(self.state.messages)
        self._cancel.clear()
        req = self.state.build_request(self._system_prompt(), self._cancel)
        self.state.clear_draft()

        self._refresh_cards()
        self._scroll_page_top()
        threading.Thread(target=self._worker, args=(req,), daemon=True).start()

    @objc.python_method
    def _worker(self, req):
        # 后台线程：不碰任何控件，不读 self.state
        try:
            for ch in self.client.stream(req.provider, req.model, req.as_payload(),
                                         max_tokens=req.max_tokens, cancel=req.cancel):
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
                if kind == "draft_done":
                    sb, rec, pid = payload
                    self.state.draft_sandbox = sb
                    self.state.installing = False
                    self.state.status = ""
                    if rec:
                        self.state.draft_installed = "%s %s" % (pid, rec.get("version"))
                        self.state.result_text = ""
                    else:
                        self.state.result_text = ("未安装：沙箱试运行未通过。\n"
                                                  + str(sb.get("output") or "")[-400:])
                    self._refresh_cards()
                    continue
                if kind == "models":
                    pid, models = payload
                    if pid == self.state.provider:
                        self._fill_models(models)
                    continue
                if kind == "models_error":
                    continue
                if kind == "error":
                    self.state.result_text += ("\n\n⚠️ " + str(payload))
                    changed = True
                elif kind == "done":
                    self.state.busy = False
                    self.state.status = ""
                    self._finish_reply()
                continue
            if getattr(item, "type", "") == "content":
                self.state.result_text += item.text
                changed = True
        if changed and self._result_view is not None:
            self._result_view.setTextColor_(TEXT)
            self._result_view.setString_(self.state.result_text)
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
        from ..gen import contract
        api = self._api()
        ctx = api.context_text() if api else "（宿主没有提供设置接口：只能解释，不能改设置）"
        route = (
            "\n\n先判断用户的请求属于哪一类，只输出一个 JSON：\n"
            "- 现有设置能实现 → settings_change（直接用下面的 operations）\n"
            "- 现有设置做不到、需要新界面或新交互（如截图按钮、悬浮面板、"
            "按 App 切换的工具栏）→ plugin_draft（按插件契约生成文件）\n"
            "- 只是询问/解释 → answer\n"
            "- 明确越界（改系统设置、装软件、执行命令、读写文件）→ unsupported\n"
            "不要向用户反问\"你是想改设置还是做插件\"，自己判断。\n"
        )
        prompt = ("你是 Mipad DP Touch 的助手，既能改现有设置，也能生成新插件。\n\n"
                  + ctx + route + "\n\n" + contract.SYSTEM_PROMPT)
        if store.full_permissions():
            prompt += ("\n\n用户已开启「完全权限」：可以使用 os / sys / subprocess / socket / "
                       "ctypes / pathlib 等任意模块，也可以读写文件、执行命令。"
                       "仍然必须遵守文件结构与入口契约。")
        draft = self.state.draft or {}
        if draft.get("files"):
            prompt += ("\n\n当前插件草稿（仅当用户要求修改这个插件时使用，返回完整文件）：\n"
                       + json.dumps(draft["files"], ensure_ascii=False))
        return prompt

    @objc.python_method
    def _finish_reply(self):
        st = self.state
        text = (st.result_text or "").strip()
        obj = _extract_json(text)
        if not text:
            st.result_text = ("模型没有返回内容（可能把预算都花在推理上了）。\n"
                              "可以在右上角设置里换一个模型，例如 "
                              "nvidia/nemotron-3-ultra-550b-a55b:free。")
            st.messages.append({"role": "assistant", "text": st.result_text})
            store.save_session(st.messages)
            self._refresh_cards()
            return
        if obj and obj.get("type") == "plugin_draft":
            self._finish_plugin_reply(obj, text)
            return
        api = self._api()
        saved = False

        if obj and obj.get("type") == "settings_change" and isinstance(obj.get("operations"), list):
            manifest = api.manifest() if api else []
            ops = _repair_ops(obj["operations"], manifest)
            if ops is None or not _valid_ops(ops):
                st.result_text = ("未修改：模型给的方案格式不对，无法应用。\n\n原始回复：\n"
                                  + text[:600])
            elif api is None:
                st.result_text = "未修改：这个方案需要改设置，但当前宿主没有提供设置接口。"
            else:
                try:
                    prev = tuple(api.preview(tuple(ops)))
                except Exception as e:
                    st.result_text = "未修改：方案没通过校验（%s）。" % (e,)
                else:
                    noop = [c for c in prev
                            if str(c.warn or "").startswith("已经是这个值")]
                    st.preview = prev
                    st.summary = obj.get("summary") or ""
                    if prev and len(noop) == len(prev):
                        st.pending_ops = ()
                        st.result_text = ("未修改：这些设置已经是目标值\n"
                                          + "\n".join("· %s：%s" % (c.title, _show(c.new))
                                                      for c in noop))
                    else:
                        st.pending_ops = tuple(ops)
                        st.result_text = ""
                        st.messages.append({"role": "assistant",
                                            "text": "建议修改：" + (st.summary or "（已生成方案）")})
                        store.save_session(st.messages)
                        saved = True
        elif obj and obj.get("type") == "answer":
            st.result_text = obj.get("text") or text
        elif obj and obj.get("type") == "unsupported":
            st.result_text = "未修改：" + "\n".join(
                x for x in (obj.get("reason"), obj.get("why")) if x)

        if st.result_text and not saved:
            st.messages.append({"role": "assistant", "text": st.result_text})
            store.save_session(st.messages)
        self._refresh_cards()

    @objc.python_method
    def _finish_plugin_reply(self, obj, text):
        from ..gen import validate
        st = self.state
        if not (obj and obj.get("type") == "plugin_draft"
                and isinstance(obj.get("files"), dict)):
            st.result_text = ("未生成插件：模型没有按协议返回 plugin_draft。\n\n"
                              + text[:400])
        else:
            res = validate.validate(obj["files"], full=store.full_permissions())
            st.draft = {"summary": obj.get("summary") or "", "files": obj["files"]}
            st.draft_check = res
            st.draft_sandbox = {}
            st.draft_installed = ""
            st.result_text = ""
            st.messages.append({"role": "assistant",
                                "text": "插件草稿：" + (obj.get("summary") or "")})
            store.save_session(st.messages)
        self._refresh_cards()

    def applyClicked_(self, sender):
        st = self.state
        api = self._api()
        if api is None or not st.pending_ops:
            return
        pending = list(st.pending_ops)
        res = api.apply(pending)
        if res.ok:
            lines = ["已修改 %d 项（读回验证通过）：" % len(res.applied)]
            for c in res.applied:
                lines.append("· %s：%s → %s" % (c.title, _show(c.old), _show(c.new)))
            st.result_text = "\n".join(lines)
            st.undo_available = True
            st.undo_txn = res.txn
        else:
            msg = ["未修改：应用失败" + ("，已回滚到修改前" if res.rolled_back else "")]
            if res.errors:
                msg.append("原因：" + "；".join(res.errors))
            if res.rollback_failed:
                msg.append("⚠️ 回滚未完成：" + "；".join(res.rollback_failed))
                msg.append("请在「手势映射」里手动核对以上项目。")
            elif res.rolled_back:
                msg.append("已回滚：" + "、".join(res.rolled_back))
            st.result_text = "\n".join(msg)
            st.undo_available = False
            st.undo_txn = ""
        st.clear_pending()
        st.messages.append({"role": "assistant", "text": st.result_text})
        store.save_session(st.messages)
        self._refresh_cards()

    def undoClicked_(self, sender):
        st = self.state
        api = self._api()
        if api is None:
            return
        txn = st.undo_txn
        # 绑定明确事务；旧宿主没有 undo(txn) 时退回 undo_last()
        if txn and hasattr(api, "undo"):
            res = api.undo(txn)
        else:
            res = api.undo_last()
        if res is None:
            st.result_text = "撤销：没有可撤销的修改。"
            st.undo_available = False
            st.undo_txn = ""
        elif res.ok:
            lines = ["已撤销 %d 项（读回验证通过）：" % len(res.applied)]
            for c in res.applied:
                lines.append("· %s：%s → %s" % (c.title, _show(c.old), _show(c.new)))
            st.result_text = "\n".join(lines)
            st.undo_available = False
            st.undo_txn = ""
        else:
            msg = ["撤销失败，设置未恢复："]
            if res.errors:
                msg.append("原因：" + "；".join(res.errors))
            if res.rollback_failed:
                msg.append("未能恢复：" + "；".join(res.rollback_failed))
            st.result_text = "\n".join(msg)
            st.undo_available = True
        st.messages.append({"role": "assistant", "text": st.result_text})
        store.save_session(st.messages)
        self._refresh_cards()

    @objc.python_method
    def _provider(self):
        pid = self.state.provider or "kilo"
        return pid if pid in self.client.provider_ids() else "kilo"

    @objc.python_method
    def _model_id(self):
        return self.state.model or ""

    def modelChanged_(self, sender):
        t = str(self._model.titleOfSelectedItem() or "")
        self.state.model = "" if t in ("Kilo 免费", "加载中…") else t
        self.cfg["model"] = self.state.model
        store.save_config(self.cfg)

    @objc.python_method
    def set_provider_model(self, pid, model):
        """设置面板保存后回写状态（面板不直接改页面字段）。"""
        self.state.provider = pid
        if model is not None:
            self.state.model = model or ""
        self._load_models_async()

    def openSettings_(self, sender):
        try:
            from .settings import SettingsSheet
            if self._settings is None:
                self._settings = SettingsSheet.alloc().init()
            self._settings.present(self)
        except Exception as e:
            self.state.result_text = "打开设置失败：%r" % (e,)
            self._refresh_cards()

    @objc.python_method
    def _load_models_async(self):
        pid = self._provider()
        client = self.client

        def run():
            try:
                self._q.put(("models", (pid, client.list_models(pid, force=True))))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def _fill_models(self, models):
        self.state.models = tuple(models)
        self._model.removeAllItems()
        titles = [m.id for m in models[:20]] or ["Kilo 免费"]
        for t in titles:
            self._model.addItemWithTitle_(t)
        want = self.state.model
        if not want:
            pref = ("nvidia/nemotron-3-ultra-550b-a55b:free",
                    "cohere/north-mini-code:free", "kilo-auto/free")
            for p in pref:
                if p in titles:
                    want = p
                    break
        i = self._model.indexOfItemWithTitle_(want)
        if i >= 0:
            self._model.selectItemAtIndex_(i)
            self.state.model = want
