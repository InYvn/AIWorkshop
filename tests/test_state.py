"""状态对象：快照不可变、后台线程拿到的是独立数据。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiworkshop_plugin.ui.state import Request, WorkshopState


def test_request_is_frozen_and_tuple():
    st = WorkshopState(provider="kilo", model="m", messages=[
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "yo"},
        {"role": "user", "text": "改右键"},
    ])
    req = st.build_request("SYS")
    assert isinstance(req, Request)
    assert isinstance(req.messages, tuple)
    assert req.messages[0] == ("system", "SYS")
    assert req.messages[-1] == ("user", "改右键")
    try:
        req.model = "other"
    except Exception:
        pass
    else:
        raise AssertionError("Request 应该是 frozen")


def test_snapshot_does_not_follow_later_mutation():
    st = WorkshopState(messages=[{"role": "user", "text": "a"}])
    req = st.build_request("SYS")
    st.messages.append({"role": "user", "text": "b"})
    assert req.messages[-1] == ("user", "a")


def test_begin_resets_pending():
    st = WorkshopState(result_text="x", summary="s", pending_ops=({"op": "set_gain", "value": 1.0},))
    st.begin("新想法")
    assert st.busy and st.status == "正在分析…"
    assert st.result_text == "" and st.pending_ops == () and st.summary == ""
    assert st.messages[-1] == {"role": "user", "text": "新想法"}
