#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聊天气泡、消息列表、输入框。

视觉：
* 用户：右对齐、accent 实色气泡、白字；
* 助手：左侧 sparkles 图标 + 正文块（代码块单独成卡片，可横向滚动）；
* 输入：中性色圆角卡片，右下角圆形发送键，流式时变停止键。
"""

import objc
from AppKit import (NSBezierPath, NSButton, NSColor, NSFont, NSImageView,
                    NSImage, NSMakePoint, NSMakeRect, NSMakeSize,
                    NSScrollView, NSTextField, NSTextView, NSView,
                    NSViewWidthSizable, NSViewHeightSizable,
                    NSLineBreakByWordWrapping,
                    NSImageScaleProportionallyUpOrDown, NSColorWell)

from . import markdown, theme


class _Flipped(NSView):
    def isFlipped(self):
        return True


def _symbol(name, size=14, color=None):
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


def _field(rect):
    f = NSTextField.alloc().initWithFrame_(rect)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(True)
    f.setUsesSingleLineMode_(False)
    f.cell().setWraps_(True)
    f.setLineBreakMode_(NSLineBreakByWordWrapping)
    return f


class CodeBlockView(_Flipped):
    """圆角卡片 + 等宽代码；长行横向滚动，不软换行。"""

    PAD_X, PAD_Y = 14.0, 11.0
    LINE_H = 17.0

    def initWithText_width_(self, text, width):
        self = objc.super(CodeBlockView, self).initWithFrame_(NSMakeRect(0, 0, width, 60))
        self.text = text or ""
        self._scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 40))
        self._scroll.setDrawsBackground_(False)
        self._scroll.setBorderType_(0)
        self._scroll.setHasHorizontalScroller_(True)
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setAutohidesScrollers_(True)
        try:
            self._scroll.setScrollerStyle_(1)
        except Exception:
            pass
        self._tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 40))
        self._tv.setEditable_(False)
        self._tv.setSelectable_(True)
        self._tv.setDrawsBackground_(False)
        self._tv.setFont_(theme.F_MONO)
        self._tv.setTextColor_(theme.TEXT)
        self._tv.setVerticallyResizable_(True)
        self._tv.setHorizontallyResizable_(True)
        self._tv.textContainer().setWidthTracksTextView_(False)
        self._tv.textContainer().setContainerSize_(NSMakeSize(100000.0, 100000.0))
        self._tv.setMaxSize_(NSMakeSize(100000.0, 100000.0))
        self._tv.setAutoresizingMask_(0)
        self._tv.setString_(self.text)
        self._scroll.setDocumentView_(self._tv)
        self.addSubview_(self._scroll)
        return self

    @objc.python_method
    def layoutForWidth_(self, width):
        lines = self.text.count("\n") + 1
        h = min(340.0, max(44.0, lines * self.LINE_H + 18.0))
        inner_w = max(60.0, width - 2 * self.PAD_X)
        self._scroll.setFrame_(NSMakeRect(self.PAD_X, self.PAD_Y, inner_w, h - 2 * self.PAD_Y + 2))
        self._tv.setFrame_(NSMakeRect(0, 0, inner_w, max(20.0, h - 2 * self.PAD_Y)))
        self.setFrameSize_(NSMakeSize(width, h))
        return h

    def drawRect_(self, rect):
        b = self.bounds().copy()
        b.origin.x += 0.5
        b.origin.y += 0.5
        b.size.width -= 1
        b.size.height -= 1
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 10.0, 10.0)
        theme.code_bg().setFill()
        path.fill()
        theme.BORDER.setStroke()
        path.setLineWidth_(0.8)
        path.stroke()


class MessageRow(_Flipped):
    """assistant = 图标 + 文本/代码块；user = 右对齐 accent 气泡。"""

    def initWithRole_text_ts_(self, role, text, ts):
        self = objc.super(MessageRow, self).initWithFrame_(NSMakeRect(0, 0, 600, 20))
        self.role = role
        self.text = text or ""
        self.streaming = False
        self._w = 600.0
        self._blocks = []
        self._bubble = None
        self._icon = None
        self._stream_field = None

        if role == "assistant":
            self._icon = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 2, 20, 20))
            self._icon.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            img = _symbol("sparkles", 15, theme.ACCENT)
            if img is not None:
                self._icon.setImage_(img)
                self._icon.setContentTintColor_(theme.ACCENT)
            self.addSubview_(self._icon)
        self._build()
        return self

    # ------------------------------------------------------------ 内容
    @objc.python_method
    def _clear(self):
        for _kind, v in self._blocks:
            v.removeFromSuperview()
        self._blocks = []
        if self._stream_field is not None:
            self._stream_field.removeFromSuperview()
            self._stream_field = None

    @objc.python_method
    def _build(self):
        self._clear()
        if self.role == "user":
            f = _field(NSMakeRect(0, 0, 100, 20))
            f.setAttributedStringValue_(markdown.render_inline(self.text, NSColor.whiteColor()))
            self.addSubview_(f)
            self._blocks = [("field", f)]
            return
        body = self.text + ("▍" if self.streaming else "")
        if self.streaming:
            f = _field(NSMakeRect(0, 0, 100, 20))
            f.setAttributedStringValue_(markdown.render_inline(body, theme.TEXT))
            self.addSubview_(f)
            self._stream_field = f
            self._blocks = [("stream", f)]
            return
        for blk in markdown.parse_blocks(body):
            if blk[0] == "code":
                v = CodeBlockView.alloc().initWithText_width_(blk[2], self._w - 30.0)
                self.addSubview_(v)
                self._blocks.append(("code", v))
            else:
                f = _field(NSMakeRect(0, 0, 100, 20))
                f.setAttributedStringValue_(markdown.render_inline(blk[1], theme.TEXT))
                self.addSubview_(f)
                self._blocks.append(("text", f))

    @objc.python_method
    def append_text(self, s):
        self.text += s
        if self.streaming and self._stream_field is not None:
            self._stream_field.setAttributedStringValue_(
                markdown.render_inline(self.text + "▍", theme.TEXT))
        else:
            self._build()

    @objc.python_method
    def setText_(self, s):
        self.text = s or ""
        self._build()

    @objc.python_method
    def setStreaming_(self, on):
        on = bool(on)
        if on == self.streaming:
            return
        self.streaming = on
        self._build()

    # ------------------------------------------------------------ 布局
    @objc.python_method
    def layoutForWidth_(self, width):
        self._w = float(width)
        if self.role == "user":
            pad_x, pad_y = 13.0, 9.0
            max_w = min(theme.MAX_TEXT_WIDTH, self._w * 0.78)
            f = self._blocks[0][1]
            attr = f.attributedStringValue()
            r = attr.boundingRectWithSize_options_context_((max_w - 2 * pad_x, 100000.0), 1 | 32, None)
            text_w = min(max_w - 2 * pad_x, float(r.size.width) + 6.0)
            text_h = max(18.0, float(r.size.height))
            bubble_w = text_w + 2 * pad_x
            bubble_h = text_h + 2 * pad_y
            x = self._w - bubble_w
            f.setFrame_(NSMakeRect(x + pad_x, pad_y, text_w, text_h))
            self._bubble = NSMakeRect(x, 0, bubble_w, bubble_h)
            self.setFrameSize_(NSMakeSize(self._w, bubble_h))
            return bubble_h

        text_w = min(theme.MAX_TEXT_WIDTH, self._w - 30.0)
        code_w = min(820.0, self._w - 30.0)
        y = 0.0
        for kind, v in self._blocks:
            if kind == "code":
                h = v.layoutForWidth_(code_w)
            else:
                attr = v.attributedStringValue()
                r = attr.boundingRectWithSize_options_context_((text_w, 100000.0), 1 | 32, None)
                h = max(18.0, float(r.size.height))
                v.setFrame_(NSMakeRect(28, y, text_w, h))
            v.setFrameOrigin_(NSMakePoint(28, y))
            y += h + 6.0
        total = max(22.0, y - 6.0)
        if self._icon is not None:
            self._icon.setFrame_(NSMakeRect(0, 2, 20, 20))
        self.setFrameSize_(NSMakeSize(self._w, total))
        return total

    def drawRect_(self, rect):
        if self.role != "user" or self._bubble is None:
            return
        b = self._bubble
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 14.0, 14.0)
        theme.ACCENT.setFill()
        path.fill()


class ChatList(_Flipped):
    GAP = 20.0

    def initWithFrame_(self, frame):
        self = objc.super(ChatList, self).initWithFrame_(frame)
        self.rows = []
        return self

    @objc.python_method
    def add_message(self, role, text="", ts=""):
        row = MessageRow.alloc().initWithRole_text_ts_(role, text, ts)
        self.rows.append(row)
        self.addSubview_(row)
        return row

    @objc.python_method
    def remove_all(self):
        for r in self.rows:
            r.removeFromSuperview()
        self.rows = []

    @objc.python_method
    def relayout(self, width):
        y = 6.0
        for r in self.rows:
            h = r.layoutForWidth_(width)
            r.setFrame_(NSMakeRect(0, y, width, h))
            y += h + self.GAP
        self.setFrameSize_(NSMakeSize(width, max(y, 1.0)))
        return y

    @objc.python_method
    def content_height(self):
        return float(self.frame().size.height)


class Composer(_Flipped):
    HEIGHT = 64.0

    def initWithFrame_(self, frame):
        self = objc.super(Composer, self).initWithFrame_(frame)
        self.on_send = None
        self._stop = False
        self._tv = NSTextView.alloc().initWithFrame_(NSMakeRect(14, 10, 100, 40))
        self._tv.setFont_(NSFont.systemFontOfSize_(14.0))
        self._tv.setDrawsBackground_(False)
        self._tv.setRichText_(False)
        self._tv.setVerticallyResizable_(True)
        self._tv.setHorizontallyResizable_(False)
        self._tv.textContainer().setWidthTracksTextView_(True)
        self._tv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._tv.setDelegate_(self)
        self.addSubview_(self._tv)

        self._ph = NSTextField.alloc().initWithFrame_(NSMakeRect(18, 20, 300, 18))
        self._ph.setStringValue_("输入消息…")
        self._ph.setBezeled_(False)
        self._ph.setDrawsBackground_(False)
        self._ph.setEditable_(False)
        self._ph.setSelectable_(False)
        self._ph.setTextColor_(theme.DIM)
        self._ph.setFont_(NSFont.systemFontOfSize_(14.0))
        self.addSubview_(self._ph)

        self._btn = NSButton.alloc().initWithFrame_(NSMakeRect(100, 17, 30, 30))
        self._btn.setBordered_(False)
        self._btn.setImagePosition_(1)
        self._btn.setContentTintColor_(theme.ACCENT)
        self._btn.setTarget_(self)
        self._btn.setAction_("sendClicked:")
        self.addSubview_(self._btn)

        self._hint = NSTextField.alloc().initWithFrame_(NSMakeRect(14, 46, 220, 12))
        self._hint.setStringValue_("Enter 发送 · Shift+Enter 换行")
        self._hint.setFont_(NSFont.systemFontOfSize_(9.5))
        self._hint.setTextColor_(theme.DIM)
        self._hint.setBezeled_(False)
        self._hint.setDrawsBackground_(False)
        self._hint.setEditable_(False)
        self.addSubview_(self._hint)

        self.set_stop(False)
        return self

    @objc.python_method
    def set_stop(self, on):
        self._stop = bool(on)
        img = _symbol("stop.circle.fill" if on else "arrow.up.circle.fill", 24, theme.ACCENT)
        if img is not None:
            self._btn.setImage_(img)
        self._btn.setToolTip_("停止" if on else "发送（回车）")
        self._update_send()

    @objc.python_method
    def _update_send(self):
        can = bool(self._stop) or bool(self.text().strip())
        self._btn.setEnabled_(can)
        self._btn.setContentTintColor_(theme.ACCENT if can else theme.DIM)
        self._btn.setAlphaValue_(1.0 if can else 0.55)

    @objc.python_method
    def setText_(self, s):
        self._tv.setString_(s or "")
        self.textChanged()

    @objc.python_method
    def text(self):
        return str(self._tv.string() or "")

    @objc.python_method
    def clear(self):
        self._tv.setString_("")
        self._ph.setHidden_(False)
        self._update_send()

    @objc.python_method
    def focus(self):
        self.window().makeFirstResponder_(self._tv)

    def sendClicked_(self, sender):
        if callable(self.on_send):
            self.on_send()

    def textDidChange_(self, note):
        self.textChanged()

    @objc.python_method
    def textChanged(self):
        self._ph.setHidden_(bool(self.text().strip()))
        self._update_send()

    def textView_doCommandBySelector_(self, tv, sel):
        if sel == "insertNewline:":
            from AppKit import NSApp
            ev = NSApp.currentEvent()
            if ev is not None and (ev.modifierFlags() & (1 << 17)):
                tv.insertNewline_(None)
                return True
            if callable(self.on_send):
                self.on_send()
            return True
        return False

    def drawRect_(self, rect):
        b = self.bounds().copy()
        b.origin.x += 1
        b.origin.y += 1
        b.size.width -= 2
        b.size.height -= 2
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 12.0, 12.0)
        theme.CARD.setFill()
        path.fill()
        theme.BORDER.setStroke()
        path.setLineWidth_(0.8)
        path.stroke()

    def resizeSubviewsWithOldSize_(self, old):
        if not hasattr(self, "_tv"):
            return
        w = self.frame().size.width
        h = self.frame().size.height
        self._tv.setFrame_(NSMakeRect(14, 8, max(20.0, w - 60.0), h - 24.0))
        self._btn.setFrame_(NSMakeRect(w - 42.0, (h - 30.0) / 2.0, 30, 30))
        self._ph.setFrame_(NSMakeRect(18, (h - 18.0) / 2.0 - 1, max(20.0, w - 80.0), 18.0))
        self._hint.setFrame_(NSMakeRect(14, h - 16.0, 220, 12))
