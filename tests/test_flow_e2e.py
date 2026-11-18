#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：需求分析 → 预览 → 确认执行 → 读回验证 → 失败回滚 → 撤销。

需要宿主源码（ai_settings.py / ai_protocol.py）：
    DPTOUCH_SRC=/path/to/mipad-dp-touch/src python tests/test_flow_e2e.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
HOST_SRC = os.environ.get("DPTOUCH_SRC", "")
if HOST_SRC:
    sys.path.insert(0, HOST_SRC)

try:
    import ai_settings as AIS
except Exception as e:  # 宿主源码不在时跳过
    print(json.dumps({"ok": True, "skipped": "no host src: %r" % (e,)}, ensure_ascii=False))
    sys.exit(0)


def ck(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad.append("%s %s" % (name, extra))


ok, bad = 0, []


class FakeClient:
    """按队列返回预设回复；不联网。"""

    def __init__(self, replies):
        self.replies = list(replies)

    def stream(self, *a, **k):
        from aiworkshop_plugin.llm.types import Chunk
        text = self.replies.pop(0) if self.replies else '{"type":"answer","text":"ok"}'
        yield Chunk.content(text)

    def list_models(self, *a, **k):
        return []

    def provider_ids(self):
        return ["kilo"]


def run():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSMakeRect
    from Foundation import NSDate, NSRunLoop

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    import dptouch_plugin as PLUG
    import aiworkshop_plugin.ui.page as P

    td = tempfile.mkdtemp(prefix="flow-")
    bridge = AIS.AISettingsBridge(AIS._FakeApp(), history_path=os.path.join(td, "h.jsonl"))

    class Host:
        settings_api = bridge

    PLUG.set_host(Host())

    page = P.ChatPage.alloc().init()
    box = P._Doc.alloc().initWithFrame_(NSMakeRect(0, 0, 980, 700))
    page.render(box, 980, 700)

    page.client = FakeClient([
        '{"type":"settings_change","summary":"笔轻点改右键","operations":[{"setting":"笔 · 轻点","value":"right"}]}',
        '{"type":"settings_change","summary":"再改一次","operations":[{"setting":"笔 · 轻点","value":"right"}]}',
        '{"type":"settings_change","summary":"调速度","operations":[{"op":"set_gain","value":2.0}]}',
    ])

    def pump(t=0.7):
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(t))

    def ask(text):
        page._tv.setString_(text)
        page._submit()
        pump()

    # 1) 分析 → 预览
    ask("把笔的轻点改成右键")
    ck("预览已生成", len(page.state.preview) == 1)
    c = page.state.preview[0]
    ck("预览旧值新值", (c.old, c.new) == ("left", "right"), "%r→%r" % (c.old, c.new))
    ck("待确认", bool(page.state.pending_ops))
    ck("尚未改配置", bridge.app.cfg["bind_tap"] == "left")

    # 2) 确认执行 → 读回验证
    page.applyClicked_(None)
    pump(0.1)
    ck("执行后才改", bridge.app.cfg["bind_tap"] == "right")
    ck("反馈已修改", page.state.result_text.startswith("已修改"), page.state.result_text[:40])
    ck("出现撤销", page.state.undo_available)

    # 3) 已经是目标值 → 明确“未修改”（撤销前测，才处于已应用状态）
    ask("再设一次右键")
    ck("不生成待确认", not page.state.pending_ops)
    ck("反馈未修改", page.state.result_text.startswith("未修改"), page.state.result_text[:40])

    # 4) 撤销 → 恢复并读回
    page.undoClicked_(None)
    pump(0.1)
    ck("撤销恢复", bridge.app.cfg["bind_tap"] == "left")
    ck("反馈已撤销", page.state.result_text.startswith("已撤销"), page.state.result_text[:40])
    ck("撤销按钮收起", not page.state.undo_available)

    # 5) 失败 → 回滚 → 未修改
    bridge.app.set_gain = lambda g: None       # 模拟 setter 静默不生效
    ask("把滚动速度调到 2 倍")
    ck("失败前有预览", bool(page.state.pending_ops))
    page.applyClicked_(None)
    pump(0.1)
    ck("失败后未修改", bridge.app.cfg["gain"] == 1.0)
    ck("反馈未修改", page.state.result_text.startswith("未修改"), page.state.result_text[:60])
    ck("给了原因", "原因" in page.state.result_text, page.state.result_text[:80])
    ck("没有撤销按钮", not page.state.undo_available)

    return {"ok": not bad, "passed": ok, "fail": bad}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
    sys.exit(0 if not bad else 1)
