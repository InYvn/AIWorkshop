#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成插件的静态校验：清单、路径、import、危险调用。

边界（v1）：
- 只允许 3 类文件：plugin.json、README.md / LICENSE、<entry>/__init__.py
- import 只允许白名单模块；禁止 os/sys/subprocess/socket/ctypes 等
- 禁止 eval/exec/compile/open/__import__/globals 等动态能力
- 禁止 dunder 属性（__class__ / __globals__ / __subclasses__ ...）
"""

import ast
import json
import re

ALLOWED_IMPORTS = {
    "objc", "AppKit", "Foundation", "json", "math", "time", "datetime",
    "re", "typing", "dataclasses", "dptouch_sdk",
}
FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals",
    "vars", "breakpoint", "input", "setattr", "delattr",
}
FORBIDDEN_ATTRS = {
    "__class__", "__globals__", "__subclasses__", "__bases__", "__mro__",
    "__dict__", "__code__", "__builtins__", "__loader__", "__spec__",
}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
VER_RE = re.compile(r"^\d+\.\d+\.\d+$")
MAX_FILE = 60_000
MAX_TOTAL = 200_000


def _err(errors, msg):
    errors.append(msg)


def _check_manifest(meta, errors):
    if not isinstance(meta, dict):
        _err(errors, "plugin.json 必须是对象")
        return None
    pid = str(meta.get("id") or "")
    if not ID_RE.match(pid):
        _err(errors, "id 只能是 3-32 位小写字母/数字/下划线，且以字母开头")
    if not VER_RE.match(str(meta.get("version") or "")):
        _err(errors, "version 必须是 x.y.z")
    if int(meta.get("api", 0) or 0) != 1:
        _err(errors, "api 必须是 1")
    entry = str(meta.get("entry") or "")
    if entry != (pid + "_plugin"):
        _err(errors, "entry 必须是 %s_plugin" % pid)
    if not str(meta.get("name") or "").strip():
        _err(errors, "缺少 name")
    page = meta.get("page")
    if not isinstance(page, dict) or not page.get("id") or not page.get("title"):
        _err(errors, "需要 page:{id,title}，否则侧栏不会出现入口")
    return {"id": pid, "entry": entry, "page": page or {}}


def _check_python(path, source, errors):
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        _err(errors, "%s 语法错误：%s（第 %s 行）" % (path, e.msg, e.lineno))
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in ALLOWED_IMPORTS:
                    _err(errors, "%s 不允许 import %s" % (path, a.name))
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if node.level:
                _err(errors, "%s 不允许相对 import" % path)
            elif top not in ALLOWED_IMPORTS:
                _err(errors, "%s 不允许 from %s import" % (path, node.module))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                _err(errors, "%s 不允许调用 %s()" % (path, node.func.id))
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                _err(errors, "%s 不允许访问 %s" % (path, node.attr))


def validate(files, full=False):
    """files: {path: text}；full=True 时跳过代码层的导入/调用限制。"""
    errors, warnings = [], []
    if not isinstance(files, dict) or not files:
        return {"ok": False, "errors": ["没有生成任何文件"], "warnings": [],
                "manifest": None, "entry": ""}
    total = 0
    for path, text in files.items():
        if not isinstance(path, str) or not isinstance(text, str):
            _err(errors, "文件必须是 文本 -> 文本")
            continue
        if path.startswith("/") or ".." in path.split("/") or "\\" in path:
            _err(errors, "非法路径：%r" % path)
        total += len(text)
        if len(text) > MAX_FILE:
            _err(errors, "%s 太大（%d 字节）" % (path, len(text)))
        if "\x00" in text:
            _err(errors, "%s 含二进制内容" % path)
    if total > MAX_TOTAL:
        _err(errors, "总大小超限（%d 字节）" % total)

    raw = files.get("plugin.json")
    manifest = None
    if raw is None:
        _err(errors, "缺少 plugin.json")
    else:
        try:
            meta = json.loads(raw)
        except Exception as e:
            _err(errors, "plugin.json 不是合法 JSON：%s" % e)
            meta = None
        if meta is not None:
            manifest = _check_manifest(meta, errors)

    entry = (manifest or {}).get("entry") or ""
    allowed = {"plugin.json", "README.md", "LICENSE"}
    if entry:
        allowed.add("%s/__init__.py" % entry)
    for path in files:
        if path not in allowed:
            _err(errors, "不允许的文件：%s（只允许 plugin.json、README.md、LICENSE、%s/__init__.py）"
                 % (path, entry or "<entry>"))
    py_path = "%s/__init__.py" % entry if entry else ""
    if py_path and py_path not in files:
        _err(errors, "缺少入口文件 %s" % py_path)
    if py_path and isinstance(files.get(py_path), str):
        src = files[py_path]
        if full:
            try:
                ast.parse(src, filename=py_path)
            except SyntaxError as e:
                _err(errors, "%s 语法错误：%s（第 %s 行）" % (py_path, e.msg, e.lineno))
        else:
            _check_python(py_path, src, errors)
        if "def create(" not in src and "def build_page(" not in src:
            _err(errors, "入口文件需要定义 create(...) 或 build_page(...)")
        if "dptouch_sdk" not in src:
            warnings.append("没有使用 dptouch_sdk：插件无法读写设置，只能是纯展示页")
    if "README.md" not in files:
        warnings.append("建议生成 README.md")

    if full:
        warnings.append("完全权限：不做 import/调用限制，插件可读写文件、执行命令、访问网络")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "manifest": manifest, "entry": entry, "full": bool(full)}
