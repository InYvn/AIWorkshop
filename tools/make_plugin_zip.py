#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把插件打成宿主可安装的 ZIP：plugin.json + aiworkshop_plugin/。

用法：python3 tools/make_plugin_zip.py [输出目录]
输出：aiworkshop-plugin-0.1.0.zip（并打印 SHA256）
"""

import hashlib
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(ROOT, "plugin.json"), encoding="utf-8") as f:
        meta = json.load(f)
    name = "aiworkshop-plugin-%s.zip" % meta["version"]
    path = os.path.join(out_dir, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(ROOT, "plugin.json"), "plugin.json")
        pkg = os.path.join(ROOT, meta["entry"])
        for base, dirs, files in os.walk(pkg):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if fn.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(base, fn)
                rel = os.path.relpath(full, ROOT)
                z.write(full, rel)
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    print(path)
    print("size", os.path.getsize(path))
    print("sha256", h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
