#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视觉常量：颜色、字体、间距。深色/浅色都用系统语义色。"""

from AppKit import NSColor, NSFont


def color(name, fallback=None):
    try:
        return getattr(NSColor, name)()
    except Exception:
        return fallback or NSColor.labelColor()


ACCENT = color("controlAccentColor")
TEXT = color("labelColor")
DIM = color("secondaryLabelColor")
FAINT = color("tertiaryLabelColor")
BG = color("windowBackgroundColor")
CARD = color("controlBackgroundColor")
BORDER = color("separatorColor")

F_BODY = NSFont.systemFontOfSize_(14.0)
F_BODY_BOLD = NSFont.boldSystemFontOfSize_(14.0)
try:
    F_TITLE = NSFont.systemFontOfSize_weight_(20.0, 0.3)
except Exception:
    F_TITLE = NSFont.boldSystemFontOfSize_(20.0)
F_SMALL = NSFont.systemFontOfSize_(11.0)
F_LABEL = NSFont.systemFontOfSize_(12.0)
F_MONO = NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0)

def is_dark():
    try:
        from AppKit import NSApp
        return "Dark" in str(NSApp.effectiveAppearance().name())
    except Exception:
        return False


def code_bg():
    return NSColor.colorWithCalibratedWhite_alpha_(0.22 if is_dark() else 0.90, 1.0)


def user_bg():
    return ACCENT.colorWithAlphaComponent_(0.14 if not is_dark() else 0.22)


def user_border():
    return ACCENT.colorWithAlphaComponent_(0.22 if not is_dark() else 0.30)


PAD = 22.0
BUBBLE_RADIUS = 13.0
MAX_TEXT_WIDTH = 640.0
