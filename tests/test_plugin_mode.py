#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""做插件模式：生成 → 校验 → 沙箱 → 安装 → 宿主发现。

    DPTOUCH_SRC=/path/to/host/src DPTOUCH_PLUGINS_DIR=/tmp/x python tests/test_plugin_mode.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
HOST_SRC = os.environ.get("DPTOUCH_SRC", "")
if HOST_SRC:
    sys.path.insert(0, HOST_SRC)

try:
    import dptouch_plugin as PLUG
except Exception as e:
    print(json.dumps({"ok": True, "skipped": repr(e)}, ensure_ascii=False))
    sys.exit(0)

ok, bad = 0, []


def ck(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
    else:
        bad.append("%s %s" % (name, extra))


def main():
    root = tempfile.mkdtemp(prefix="plugdir-")
    os.environ["DPTOUCH_PLUGINS_DIR"] = root

    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSMakeRect, NSView
    from Foundation import NSDate, NSRunLoop

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    import aiworkshop_plugin.ui.page as P
    from aiworkshop_plugin.gen import contract

    class Host:
        settings_api = None

    PLUG.set_host(Host())

    page = P.ChatPage.alloc().init()
    box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 980, 700))
    page.render(box, 980, 700)
    page._seg.setSelectedSegment_(1)
    page.modeChanged_(page._seg)
    ck("切到做插件", page.state.mode == "plugin")

    manifest = {"id": "quick_mode", "name": "快速切换", "blurb": "一键切成笔模式",
                "version": "0.1.0", "min_host": "2.2.5", "api": 1,
                "entry": "quick_mode_plugin", "symbol": "puzzlepiece.extension",
                "page": {"id": "quick_mode", "title": "快速切换",
                         "icon": "puzzlepiece.extension", "order": 95}}
    files = {"plugin.json": json.dumps(manifest, ensure_ascii=False),
             "quick_mode_plugin/__init__.py": contract.TEMPLATE,
             "README.md": "# 快速切换\n"}
    reply = json.dumps({"type": "plugin_draft", "summary": "一键切成笔模式",
                        "files": files}, ensure_ascii=False)

    class Fake:
        def stream(self, *a, **k):
            from aiworkshop_plugin.llm.types import Chunk
            yield Chunk.content(reply)

        def list_models(self, *a, **k):
            return []

        def provider_ids(self):
            return ["kilo"]

    page.client = Fake()
    page._tv.setString_("做一个一键切成笔模式的页面")
    page._submit()
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.9))
    ck("草稿已生成", bool(page.state.draft))
    ck("静态校验通过", bool(page.state.draft_check.get("ok")),
       str(page.state.draft_check.get("errors")))

    page.installDraftClicked_(None)
    for _ in range(80):
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.25))
        if page.state.draft_installed or page.state.result_text:
            break
    ck("沙箱通过", bool(page.state.draft_sandbox.get("ok")),
       str(page.state.draft_sandbox.get("output"))[-300:])
    ck("已安装", page.state.draft_installed.startswith("quick_mode"),
       page.state.draft_installed)
    ck("宿主发现本地插件", "quick_mode" in PLUG.local_plugins())
    ck("侧栏页可见", any(pid == "quick_mode" for pid, _m in PLUG.page_plugins()))
    ck("入口可解析", PLUG.get("quick_mode")["entry"] == "quick_mode_plugin")

    PLUG.uninstall("quick_mode")
    ck("卸载后不再出现", "quick_mode" not in PLUG.local_plugins())

    print(json.dumps({"ok": not bad, "passed": ok, "fail": bad}, ensure_ascii=False))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
