#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沙箱试运行：在独立解释器里 import 生成的插件。

- 用桩模块替代 AppKit / Foundation / objc / dptouch_sdk：
  不连接真实 WindowServer、不读真实设置、不写真实数据；
- 子进程 + 临时 HOME/TMPDIR + 超时；
- 只能验证「导入期不会炸、入口函数存在」，不能替代真机 UI 验收。
"""

import os
import shutil
import subprocess
import sys
import tempfile

OBJC_STUB = '''def python_method(f):
    return f


class _Super(object):
    def __init__(self, obj):
        self._obj = obj

    def init(self):
        return self._obj

    def __getattr__(self, name):
        return lambda *a, **k: None


def super(cls, obj):
    return _Super(obj)
'''

APPKIT_STUB = '''class _Meta(type):
    def __getattr__(cls, name):
        return lambda *a, **k: _Any()


class _Any(object, metaclass=_Meta):
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: _Any()

    @classmethod
    def alloc(cls):
        return _Any()


def __getattr__(name):
    return _Any
'''

SDK_STUB = '''def settings_manifest():
    return []


def settings_context():
    return ""


def settings_preview(ops):
    return []


def settings_apply(ops):
    return {"ok": True, "applied": [], "errors": [], "txn": "stub"}


def settings_undo(txn=None):
    return {"ok": True, "applied": [], "errors": [], "txn": "stub"}


def front_app():
    return {"name": "", "bundle": ""}


def screens():
    return []


def data_dir(pid):
    return "/tmp"


def log(text):
    return True


def dump(obj):
    return repr(obj)
'''

RUNNER = '''
import sys, importlib.util
stubs, draft, entry, entry_path, entry_dir = sys.argv[1:6]
sys.path[:0] = [stubs, draft]
spec = importlib.util.spec_from_file_location(entry, entry_path,
                                              submodule_search_locations=[entry_dir])
mod = importlib.util.module_from_spec(spec)
sys.modules[entry] = mod
spec.loader.exec_module(mod)
has_create = callable(getattr(mod, "create", None))
has_page = callable(getattr(mod, "build_page", None))
assert has_create or has_page, "缺少 create() 或 build_page()"
print("SANDBOX_OK")
'''


def run(draft_dir, entry, full=False):
    """返回 {ok, output}；full=True 时只做语法检查（不 import 未知代码）。"""
    tmp = tempfile.mkdtemp(prefix="plugin-sandbox-")
    try:
        stubs = os.path.join(tmp, "stubs")
        os.makedirs(stubs)
        for name, body in (("objc.py", OBJC_STUB), ("AppKit.py", APPKIT_STUB),
                           ("Foundation.py", APPKIT_STUB), ("dptouch_sdk.py", SDK_STUB)):
            with open(os.path.join(stubs, name), "w", encoding="utf-8") as f:
                f.write(body)
        entry_dir = os.path.join(draft_dir, entry)
        entry_path = os.path.join(entry_dir, "__init__.py")
        if not os.path.isfile(entry_path):
            return {"ok": False, "output": "找不到入口文件 %s" % entry_path}
        if full:
            import py_compile
            try:
                py_compile.compile(entry_path, cfile=os.path.join(tmp, "x.pyc"),
                                   doraise=True)
                return {"ok": True, "output": "已跳过沙箱（完全权限），仅语法检查通过"}
            except Exception as e:
                return {"ok": False, "output": "语法检查失败：%r" % (e,)}
        env = dict(os.environ)
        env.update({"HOME": tmp, "TMPDIR": tmp, "PYTHONNOUSERSITE": "1",
                    "PYTHONDONTWRITEBYTECODE": "1"})
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env.pop(k, None)
        py = sys.executable or "/usr/bin/python3"
        proc = subprocess.run(
            [py, "-I", "-c", RUNNER, stubs, draft_dir, entry, entry_path, entry_dir],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=20)
        out = (proc.stdout or "") + (proc.stderr or "")
        return {"ok": proc.returncode == 0 and "SANDBOX_OK" in out,
                "output": out.strip()[-4000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "沙箱超时（20s）"}
    except Exception as e:
        return {"ok": False, "output": repr(e)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
