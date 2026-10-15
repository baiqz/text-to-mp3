# -*- coding: utf-8 -*-
"""
音色可用性回归测试
==================

针对真实故障:用户在界面上选中「云泽」(zh-CN-YunzeNeural),11 段全部合成失败,
报错只有一句 "No audio was received"。

根因是两点叠加:
  1. 精选音色清单是人工快照,而服务端已经下架了云泽等一批音色 —— 用户能选到一个必挂的音色;
  2. 引擎对这种"不报错、只是没有音频"的音色没有任何兜底。

这里把两种情况都钉死:
  * 已下架音色不得出现在给前端的清单里,也不得被判定为合法;
  * 万一还是选中了,任务必须自动换默认音色并把原因告诉用户,而不是整体失败。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttsmp3 import catalog, engine, netcheck  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_fallback")
os.makedirs(OUT, exist_ok=True)

DEAD = "zh-CN-YunzeNeural"
GOOD = "zh-CN-XiaoxiaoNeural"

passed = 0
failed = 0


def check(name: str, ok: bool, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


def wait(job, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = job.snapshot()
        if snap["status"] in ("done", "error", "canceled"):
            return snap
        time.sleep(0.4)
    return job.snapshot()


# ---------------------------------------------------------------------------
print("=== 1. 声库清单以服务端实际声库为准 ===")
live = catalog.live_voice_ids()
print(f"  服务端当前音色数: {len(live)}")
check("能拉到服务端声库", len(live) > 50, len(live))

cat = catalog.catalog()
curated_ids = {v["id"] for v in cat["curated"]}
check("精选清单里不再出现已下架的云泽", DEAD not in curated_ids, sorted(curated_ids)[:5])
check("精选清单里仍有晓晓", GOOD in curated_ids, "")
check("已下架的音色被单独列出来", DEAD in cat.get("missing", []), cat.get("missing"))
check("精选清单没有空掉（下架过滤不能误伤）", len(curated_ids) >= 8, len(curated_ids))

check("已下架音色不再被判为合法", catalog.is_valid_voice(DEAD) is False, "")
check("正常音色仍判为合法", catalog.is_valid_voice(GOOD) is True, "")
check("瞎写的音色被判为非法", catalog.is_valid_voice("xx-XX-NopeNeural") is False, "")
check("空音色名被判为非法", catalog.is_valid_voice("") is False, "")
check("带空格的音色名被判为非法", catalog.is_valid_voice("zh-CN- XiaoxiaoNeural") is False, "")

# ---------------------------------------------------------------------------
print("")
print("=== 2. 选中已下架音色时，任务必须自动兜底 ===")
text = ("这是第一段测试文字，用来验证音色兜底逻辑。\n\n"
        "这是第二段，确保切分出多个片段，从而覆盖多段合成的情况。\n\n"
        "这是第三段，确认整批失败后会用默认音色重跑一遍。") * 8

job = engine.start_job(text, engine.Options(voice=DEAD, chunk_chars=200, concurrency=3), OUT)
snap = wait(job)

check("任务最终成功（而不是整体失败）", snap["status"] == "done", f"{snap['status']}: {snap.get('error')}")
check("确实分了多段", snap["chunks_total"] >= 3, snap["chunks_total"])
check("音色已被换成默认音色", snap["options"]["voice"] == GOOD, snap["options"]["voice"])
check("原因被记录下来告诉用户",
      any("已自动改用" in e for e in snap.get("errors", [])), snap.get("errors"))
check("产出了音频文件", bool(snap["filename"]) and snap["size_bytes"] > 1000,
      f"{snap.get('filename')} {snap.get('size_bytes')}")
check("时长合理（>5 秒）", snap["duration"] > 5, snap["duration"])
check("失败的片段数为 0", not snap["failures"], snap["failures"])

if snap.get("filename"):
    path = os.path.join(OUT, snap["filename"])
    check("文件真的落在磁盘上", os.path.exists(path), path)
    from ttsmp3 import mp3join
    info = mp3join.probe(open(path, "rb").read())
    check("产物是结构合法的 MP3", info["frames"] > 100, info["frames"])
    check("拼接时长与报告一致", abs(info["duration"] - snap["duration"]) < 0.15,
          f"{info['duration']} vs {snap['duration']}")

# ---------------------------------------------------------------------------
print("")
print("=== 3. 用正常音色时不应该触发兜底（避免误伤）===")
job2 = engine.start_job(text, engine.Options(voice=GOOD, chunk_chars=200, concurrency=3), OUT)
snap2 = wait(job2)
check("正常音色任务成功", snap2["status"] == "done", snap2["status"])
check("音色保持不变", snap2["options"]["voice"] == GOOD, snap2["options"]["voice"])
check("没有多余的兜底提示",
      not any("已自动改用" in e for e in snap2.get("errors", [])), snap2.get("errors"))

# ---------------------------------------------------------------------------
print("")
print("=== 4. 网络诊断给的建议不该误判为版本问题 ===")
hint = netcheck.quick_hint()
print(f"  诊断建议: {hint}")
check("诊断能给出文字建议", isinstance(hint, str) and len(hint) > 4, hint)

print("")
print(f"通过 {passed} / 失败 {failed}")
sys.exit(1 if failed else 0)
