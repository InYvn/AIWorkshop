#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插件自己的配置、钥匙串与会话历史。

路径：~/Library/Application Support/MipadDPTouch/plugins/aiworkshop/
密钥：macOS 钥匙串，service = MipadDPTouch-aiworkshop，account = provider
"""

import json
import os
import subprocess
import time

BASE_DIR = os.path.expanduser(
    "~/Library/Application Support/MipadDPTouch/plugins/aiworkshop")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
KEYCHAIN_SERVICE = "MipadDPTouch-aiworkshop"
SECURITY = "/usr/bin/security"


def _ensure():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    _ensure()
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------- 钥匙串

def _run(args):
    try:
        p = subprocess.run([SECURITY] + args, capture_output=True, text=True, timeout=15)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 1, "", repr(e)


def get_key(provider):
    rc, out, _err = _run(["find-generic-password", "-w", "-s", KEYCHAIN_SERVICE,
                          "-a", str(provider)])
    return out if rc == 0 and out else ""


def set_key(provider, key):
    _run(["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", str(provider)])
    rc, _out, err = _run(["add-generic-password", "-a", str(provider),
                          "-s", KEYCHAIN_SERVICE, "-w", key, "-U"])
    if rc != 0:
        raise RuntimeError("写入钥匙串失败：%s" % (err or "security rc=%d" % rc))


def delete_key(provider):
    _run(["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", str(provider)])


# ---------------------------------------------------------------- 会话

def _session_path(name):
    safe = "".join(c for c in str(name) if c.isalnum() or c in "-_") or "default"
    return os.path.join(SESSIONS_DIR, safe + ".json")


def load_session(name="default"):
    try:
        with open(_session_path(name), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_session(messages, name="default", limit=200):
    _ensure()
    data = [m for m in messages if isinstance(m, dict)][-limit:]
    tmp = _session_path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _session_path(name))


def list_sessions():
    out = []
    try:
        for n in os.listdir(SESSIONS_DIR):
            if n.endswith(".json"):
                p = os.path.join(SESSIONS_DIR, n)
                out.append({"name": n[:-5],
                            "mtime": os.path.getmtime(p),
                            "time": time.strftime("%m-%d %H:%M",
                                                  time.localtime(os.path.getmtime(p)))})
    except Exception:
        pass
    return sorted(out, key=lambda x: -x["mtime"])


# ---------------------------------------------------------------- 插件权限
def full_permissions():
    """生成插件是否允许完全权限（读写文件/执行命令/网络）。默认关。"""
    return bool(load_config().get("allow_full_permissions"))


def set_full_permissions(on):
    cfg = load_config()
    cfg["allow_full_permissions"] = bool(on)
    save_config(cfg)
