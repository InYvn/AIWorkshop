#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对真实 Kilo 网关跑一次冒烟（需要联网，不需要 Key）。

用法：python3 tools/smoke_kilo.py [模型]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiworkshop_plugin.llm import LLMClient


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "kilo-auto/free"
    c = LLMClient()
    ms = c.list_models("kilo", force=True)
    print("免费模型 %d 个；前 5：" % len(ms))
    for m in ms[:5]:
        print("  -", m.id, "| ctx", m.context)
    print("测试连接：", c.test("kilo", model))
    print("流式（%s）：" % model)
    text = []
    reasoning = []
    for ch in c.stream("kilo", model, [{"role": "user", "content": "用一句话介绍你自己"}],
                       max_tokens=400, timeout=60):
        if ch.type == "content":
            text.append(ch.text)
            sys.stdout.write(ch.text)
            sys.stdout.flush()
        elif ch.type == "reasoning":
            reasoning.append(ch.text)
        elif ch.type == "done":
            print("\n[实际模型] %s" % (ch.model or c.last_model("kilo")))
    print("\n正文 %d 字，思考 %d 字" % (len("".join(text)), len("".join(reasoning))))
    ok = bool("".join(text).strip())
    print("RESULT:", "ok" if ok else "empty")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
