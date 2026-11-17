#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 AI 许愿工坊窗口离屏渲染成 PNG（给设计评审/README 用）。

用法：python3 tools/preview_ui.py /tmp/aiworkshop_ui.png
需要带 PyObjC 的 Python（项目 .venv 或宿主进程）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                    NSBitmapImageFileTypePNG, NSMakeSize)
from Foundation import NSRunLoop, NSDate


def seed(win):
    win.messages = [
        {"role": "user", "text": "帮我写一个 Swift 的节流函数，带注释。"},
        {"role": "assistant", "text":
            "可以。用 `Date` 记上次执行时间就行：\n\n"
            "```swift\n"
            "func throttle<T>(_ interval: TimeInterval, _ work: @escaping (T) -> Void) -> (T) -> Void {\n"
            "    var last: Date = .distantPast\n"
            "    return { value in\n"
            "        let now = Date()\n"
            "        guard now.timeIntervalSince(last) >= interval else { return }\n"
            "        last = now\n"
            "        work(value)\n"
            "    }\n"
            "}\n"
            "```\n\n"
            "要点：**间隔** 单位是秒；只在超过间隔时执行，避免高频抖动。"},
        {"role": "user", "text": "再帮我加一个取消机制。"},
        {"role": "assistant", "text":
            "可以把节流器包成一个类，用 `isCancelled` 标记；取消后直接 return。"},
    ]
    win._render_history()
    # 预览用：假装模型列表已加载
    from aiworkshop_plugin.llm.types import ModelInfo
    win._fill_models([ModelInfo("kilo-auto/free", "Auto Free", True, 256000, "kilo")])


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/aiworkshop_ui.png"
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    if os.environ.get("PREVIEW_DARK"):
        from AppKit import NSAppearance
        app.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
    from aiworkshop_plugin.ui.window import ChatWindow
    win = ChatWindow.alloc().init()
    p = win.build()
    seed(win)
    p.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.8))
    win._layout()
    v = p.contentView()
    v.displayIfNeeded()
    rep = v.bitmapImageRepForCachingDisplayInRect_(v.bounds())
    v.cacheDisplayInRect_toBitmapImageRep_(v.bounds(), rep)
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    ok = data.writeToFile_atomically_(path, True)
    print("saved", path, bool(ok))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
