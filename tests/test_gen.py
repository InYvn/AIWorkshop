#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成插件后端：校验、沙箱、打包、安装发现。"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from aiworkshop_plugin.gen import contract, draft, sandbox, validate

ok, bad = 0, []


def ck(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
    else:
        bad.append("%s %s" % (name, extra))


VALID_MANIFEST = {
    "id": "quick_mode", "name": "快速切换", "blurb": "一键切成笔模式",
    "version": "0.1.0", "min_host": "2.2.5", "api": 1,
    "entry": "quick_mode_plugin", "symbol": "puzzlepiece.extension",
    "page": {"id": "quick_mode", "title": "快速切换", "icon": "puzzlepiece.extension", "order": 95},
}


def files_for(manifest, source):
    return {"plugin.json": json.dumps(manifest, ensure_ascii=False, indent=2),
            "quick_mode_plugin/__init__.py": source,
            "README.md": "# 快速切换\n"}


def main():
    src = contract.TEMPLATE
    files = files_for(VALID_MANIFEST, src)
    v = validate.validate(files)
    ck("模板通过校验", v["ok"], v["errors"])
    ck("识别入口", v["entry"] == "quick_mode_plugin")

    # 危险 import
    bad1 = dict(files)
    bad1["quick_mode_plugin/__init__.py"] = "import os\n" + src
    ck("拒绝 os", not validate.validate(bad1)["ok"])

    # 危险调用
    bad2 = dict(files)
    bad2["quick_mode_plugin/__init__.py"] = src + "\nopen('/etc/passwd')\n"
    ck("拒绝 open", not validate.validate(bad2)["ok"])

    # dunder
    bad3 = dict(files)
    bad3["quick_mode_plugin/__init__.py"] = src + "\nx = ().__class__\n"
    ck("拒绝 dunder", not validate.validate(bad3)["ok"])

    # 多文件
    bad4 = dict(files)
    bad4["quick_mode_plugin/extra.py"] = "x = 1\n"
    ck("拒绝额外文件", not validate.validate(bad4)["ok"])

    # 清单不匹配
    m = dict(VALID_MANIFEST)
    m["entry"] = "other_plugin"
    ck("拒绝 entry 不匹配", not validate.validate(files_for(m, src))["ok"])

    # 路径穿越
    bad5 = dict(files)
    bad5["../evil.py"] = "x = 1\n"
    ck("拒绝路径穿越", not validate.validate(bad5)["ok"])

    # 落盘 + 沙箱
    d = draft.write_draft("quick_mode", files)
    ck("草稿落盘", os.path.isfile(os.path.join(d, "plugin.json")))
    sb = sandbox.run(d, "quick_mode_plugin")
    ck("沙箱导入", sb["ok"], sb["output"][-300:])

    # 打包结构
    z = draft.zip_draft("quick_mode", os.path.join(tempfile.mkdtemp(), "q.zip"))
    import zipfile
    names = set(zipfile.ZipFile(z).namelist())
    ck("zip 根目录有 plugin.json", "plugin.json" in names, names)
    ck("zip 有入口", "quick_mode_plugin/__init__.py" in names, names)

    print(json.dumps({"ok": not bad, "passed": ok, "fail": bad}, ensure_ascii=False))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
