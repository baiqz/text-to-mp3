# -*- coding: utf-8 -*-
"""
文字转 MP3 · 一键网络诊断
=========================

双击 `诊断.bat` 就会运行本脚本。

它按"人是怎么排障的"顺序逐层检查：运行环境 → 域名 → 端口 → 加密连接 →
代理 → 真实合成一小段音频，最后给出结论和下一步建议。

报告同时写到 `logs\\diagnose_*.txt`，方便把内容贴给别人看。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ttsmp3 import netcheck                     # noqa: E402


def collect() -> tuple[str, dict]:
    """生成报告文本（渲染逻辑在 netcheck 里，网页端共用同一份）。"""
    rep = netcheck.diagnose(with_engine=True)
    return netcheck.render_report(rep), rep


def main() -> int:
    text, rep = collect()
    print(text, flush=True)

    base = os.path.dirname(os.path.abspath(__file__))
    logdir = os.path.join(base, "logs")
    try:
        os.makedirs(logdir, exist_ok=True)
        path = os.path.join(logdir, f"diagnose_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"  报告已保存：{path}", flush=True)
    except OSError as exc:
        print(f"  （报告保存失败：{exc}）", flush=True)

    print(flush=True)
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
