#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量 Markdown → NSAttributedString（聊天回答够用）。

支持：围栏代码块、行内 `code`、**粗体**、### 小标题。
不追求完整规范，只保证代码块等宽、不把标记符号露给用户。
"""

import re

from AppKit import (NSAttributedString, NSBackgroundColorAttributeName,
                    NSFontAttributeName, NSForegroundColorAttributeName,
                    NSMutableAttributedString, NSMutableParagraphStyle,
                    NSParagraphStyleAttributeName)

from . import theme

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`\n]+)`")
_HEAD = re.compile(r"^(#{1,3})\s+(.*)$")


def _para(spacing=3.0, first=0.0):
    p = NSMutableParagraphStyle.alloc().init()
    p.setLineSpacing_(spacing)
    p.setParagraphSpacing_(6.0)
    if first:
        p.setFirstLineHeadIndent_(first)
    return p


def _inline(text, base_font, base_color):
    out = NSMutableAttributedString.alloc().init()
    pos = 0
    # 先按 **粗体** 切，再在每段里找 `code`
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            out.appendAttributedString_(_plain(text[pos:m.start()], base_font, base_color))
        out.appendAttributedString_(_plain(m.group(1), theme.F_BODY_BOLD, base_color))
        pos = m.end()
    if pos < len(text):
        out.appendAttributedString_(_plain(text[pos:], base_font, base_color))
    # 行内代码：在整段上重新扫一遍（简单起见，仅在没有粗体时处理）
    if not _BOLD.search(text):
        out = NSMutableAttributedString.alloc().init()
        pos = 0
        for m in _CODE.finditer(text):
            if m.start() > pos:
                out.appendAttributedString_(_plain(text[pos:m.start()], base_font, base_color))
            out.appendAttributedString_(_plain(m.group(1), theme.F_MONO, base_color,
                                               bg=theme.CARD))
            pos = m.end()
        if pos < len(text):
            out.appendAttributedString_(_plain(text[pos:], base_font, base_color))
    return out


def _plain(text, font, color, bg=None):
    attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: color,
             NSParagraphStyleAttributeName: _para()}
    if bg is not None:
        attrs[NSBackgroundColorAttributeName] = bg
    return NSAttributedString.alloc().initWithString_attributes_(text, attrs)


def parse_blocks(text):
    """把回复切成 [("text", str)] / [("code", lang, str)]，供 UI 用独立代码块渲染。"""
    blocks = []
    buf = []
    lines = str(text or "").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if buf:
                blocks.append(("text", "\n".join(buf)))
                buf = []
            lang = line.strip()[3:].strip()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", lang, "\n".join(code)))
            continue
        buf.append(line)
        i += 1
    if buf:
        blocks.append(("text", "\n".join(buf)))
    return blocks


def render_inline(text, color=None):
    """不处理代码围栏的行内 Markdown（粗体 / 行内 code）。"""
    color = color or theme.TEXT
    out = NSMutableAttributedString.alloc().init()
    for i, para in enumerate(str(text or "").split("\n")):
        if i:
            out.appendAttributedString_(_plain("\n", theme.F_BODY, color))
        out.appendAttributedString_(_inline(para, theme.F_BODY, color))
    return out


def render(text, color=None):
    """返回 NSAttributedString。"""
    color = color or theme.TEXT
    out = NSMutableAttributedString.alloc().init()
    lines = str(text or "").split("\n")
    i = 0
    buf = []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if buf:
                out.appendAttributedString_(_inline("\n".join(buf), theme.F_BODY, color))
                buf = []
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            p = _para(spacing=1.8)
            p.setHeadIndent_(14)
            p.setFirstLineHeadIndent_(14)
            p.setTailIndent_(-14)
            p.setParagraphSpacingBefore_(6)
            p.setParagraphSpacing_(0)
            p.setLineSpacing_(1.8)
            out.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                "\n".join(code) + "\n",
                {NSFontAttributeName: theme.F_MONO,
                 NSForegroundColorAttributeName: color,
                 NSBackgroundColorAttributeName: theme.code_bg(),
                 NSParagraphStyleAttributeName: p}))
            continue
        m = _HEAD.match(line)
        if m:
            if buf:
                out.appendAttributedString_(_inline("\n".join(buf), theme.F_BODY, color))
                buf = []
            out.appendAttributedString_(_plain(m.group(2), theme.F_BODY_BOLD, color))
            out.appendAttributedString_(_plain("\n", theme.F_BODY, color))
            i += 1
            continue
        buf.append(line)
        i += 1
    if buf:
        out.appendAttributedString_(_inline("\n".join(buf), theme.F_BODY, color))
    return out


def plain_text_width(text, font, width):
    """估算换行后的高度（给气泡用）。"""
    a = NSAttributedString.alloc().initWithString_attributes_(
        str(text or ""), {NSFontAttributeName: font})
    r = a.boundingRectWithSize_options_context_((width, 100000.0), 1 | 32, None)
    return float(r.size.height)
