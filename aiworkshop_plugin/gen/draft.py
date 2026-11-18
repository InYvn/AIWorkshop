#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""草稿落盘、打包、安装。"""

import json
import os
import shutil
import tempfile
import time
import zipfile

DRAFT_ROOT = os.path.expanduser(
    "~/Library/Application Support/MipadDPTouch/plugins/aiworkshop/drafts")


def draft_dir(pid):
    return os.path.join(DRAFT_ROOT, pid)


def write_draft(pid, files):
    """把 {path: text} 写进 drafts/<pid>/（清空旧内容）。"""
    d = draft_dir(pid)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
    for path, text in files.items():
        target = os.path.join(d, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
    return d


def read_draft(pid):
    d = draft_dir(pid)
    out = {}
    if not os.path.isdir(d):
        return out
    for base, _dirs, names in os.walk(d):
        for n in names:
            full = os.path.join(base, n)
            rel = os.path.relpath(full, d)
            try:
                with open(full, encoding="utf-8") as f:
                    out[rel] = f.read()
            except Exception:
                pass
    return out


def zip_draft(pid, out_path=None):
    """打插件包：plugin.json 在 zip 根目录。"""
    d = draft_dir(pid)
    out_path = out_path or os.path.join(tempfile.gettempdir(),
                                        "%s-0.0.0.zip" % pid)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, names in os.walk(d):
            for n in names:
                full = os.path.join(base, n)
                rel = os.path.relpath(full, d).replace(os.sep, "/")
                z.write(full, rel)
    return out_path


def host_version():
    try:
        import dptouch_plugin as PLUG
        h = PLUG.host()
        v = getattr(h, "APP_VERSION", None) or getattr(h, "version", None)
        if v:
            return str(v)
    except Exception:
        pass
    return "2.2.5"


def install(pid, zip_path=None):
    """调用宿主的安装逻辑（校验清单 + 解压到 plugins/<pid>/<ver>）。"""
    import dptouch_plugin as PLUG
    zip_path = zip_path or zip_draft(pid)
    rec = PLUG.install_from_zip(zip_path, source="ai-generated",
                                host_version=host_version(), pid=pid)
    return rec


def remove(pid):
    import dptouch_plugin as PLUG
    PLUG.uninstall(pid)


def stamp():
    return time.strftime("%H:%M:%S")
