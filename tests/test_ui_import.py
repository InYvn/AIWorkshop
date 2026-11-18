"""UI 模块必须能在导入期完成 ObjC 类注册（BadPrototypeError 回归测试）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_settings_sheet_imports():
    from aiworkshop_plugin.ui.settings import SettingsSheet
    assert hasattr(SettingsSheet, "present")
    assert not hasattr(SettingsSheet, "show") or SettingsSheet.show is not None


def test_page_imports():
    from aiworkshop_plugin.ui.page import ChatPage
    assert hasattr(ChatPage, "render")
