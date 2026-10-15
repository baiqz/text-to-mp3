# -*- coding: utf-8 -*-
"""引擎端到端测试:长文本分块合成、进度、取消、失败处理。

运行: python tests/test_engine.py     (需要联网)
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mutagen.mp3 import MP3  # noqa: E402

from ttsmp3 import engine, splitter  # noqa: E402

PASS, FAIL = [], []
OUT = tempfile.mkdtemp(prefix="ttsmp3-test-")


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""),
          flush=True)


def section(t):
    print("\n" + "=" * 66)
    print(t)
    print("=" * 66, flush=True)


def wait(job, timeout=420, interval=0.5):
    t0 = time.time()
    seen = set()
    while not job.finished:
        snap = job.snapshot()
        seen.add((snap["chunks_done"], snap["status"]))
        if time.time() - t0 > timeout:
            job.cancel()
            raise TimeoutError(f"任务超时: {snap}")
        time.sleep(interval)
    return job.snapshot(), seen


# ---------------------------------------------------------------------------
section("1. 超大文本的切分性能(不合成,只验证可切分性)")
# ---------------------------------------------------------------------------
huge = ("这是一句测试用的中文句子，用来观察超长文本的切分表现。" * 20 + "\n\n") * 2000
print(f"  构造文本 {len(huge):,} 字符")
t0 = time.time()
chunks = splitter.split_text(huge, 1200)
dt = time.time() - t0
check("百万级文本可在数秒内切完", dt < 10, f"{dt:.2f}s, {len(chunks)} 块")
ideal = len(huge) / 1200
check("块数与理论值同量级", ideal * 0.7 < len(chunks) < ideal * 1.5,
      (len(chunks), round(ideal, 1)))
check("每块不超限", max(len(c) for c in chunks) <= 1200, max(len(c) for c in chunks))
check("切分无损", "".join(chunks).replace("\n", "") ==
      splitter.clean_text(huge).replace("\n", ""), sum(len(c) for c in chunks))

# ---------------------------------------------------------------------------
section("2. 短文本完整流程")
# ---------------------------------------------------------------------------
opt = engine.Options(voice="zh-CN-XiaoxiaoNeural", chunk_chars=1200, title="短文本测试")
job = engine.start_job("你好，这是第一段测试文字。今天天气不错，适合出门走走。", opt, OUT)
snap, seen = wait(job, timeout=120)
check("任务完成", snap["status"] == "done", snap["status"])
check("进度到 100%", snap["progress"] == 1.0, snap["progress"])
check("产出文件存在", snap["has_audio"], snap["filename"])
check("文件名带标题", "短文本测试" in (snap["filename"] or ""), snap["filename"])
check("文件非空", snap["size_bytes"] > 5000, snap["size_bytes"])
path = os.path.join(OUT, snap["filename"])
m = MP3(path)
check("mutagen 认可输出", m.info.length > 2, round(m.info.length, 3))
check("时长与引擎统计一致", abs(m.info.length - snap["duration"]) < 0.2,
      (round(m.info.length, 3), snap["duration"]))
check("ID3 标题正确", str((m.tags or {}).get("TIT2")) == "短文本测试",
      (m.tags or {}).get("TIT2"))
check("合成参数已记录", snap["options"]["voice"] == "zh-CN-XiaoxiaoNeural", snap["options"])

# ---------------------------------------------------------------------------
section("3. 长文本分块合成(验证不限长度)")
# ---------------------------------------------------------------------------
long_text = "\n\n".join(
    f"第{i}段。这段话用来说明长文本转换的可靠性，每一段都会被单独合成，"
    f"然后按顺序拼接成一个完整的音频文件。"
    for i in range(1, 13)
)
print(f"  文本 {len(long_text)} 字符,预估 {splitter.estimate_seconds(long_text):.0f} 秒音频")
opt = engine.Options(voice="zh-CN-XiaoxiaoNeural", chunk_chars=200, concurrency=3,
                     title="长文本测试")
t0 = time.time()
job = engine.start_job(long_text, opt, OUT)
snap, seen = wait(job, timeout=400)
cost = time.time() - t0
check("长文本任务完成", snap["status"] == "done", snap["status"])
check("确实分了多块", snap["chunks_total"] >= 3, snap["chunks_total"])
check("全部块都成功", snap["chunks_done"] == snap["chunks_total"], snap)
check("无失败段", len(snap["failures"]) == 0, snap["failures"])
check("进度在过程中被多次上报", len(seen) >= 3, sorted(seen))

path = os.path.join(OUT, snap["filename"])
m = MP3(path)
check("长文本输出可解析", m.info.length > 60, round(m.info.length, 2))
check("长文本时长与逐块统计一致", abs(m.info.length - snap["duration"]) < 0.5,
      (round(m.info.length, 3), round(snap["duration"], 3)))
reported = snap["est_seconds"]
check("实际时长与预估同量级(0.6~1.6 倍)", 0.6 < m.info.length / reported < 1.6,
      (round(m.info.length, 1), round(reported, 1)))
print(f"  (耗时 {cost:.1f}s,音频 {m.info.length:.1f}s,加速比 {m.info.length / cost:.1f}x)")

# 拼接正确性:块之间不该有异常静音或缺失,帧数连续
from ttsmp3 import mp3join  # noqa: E402
data = open(path, "rb").read()
pi = mp3join.probe(data)
check("输出为连续帧流无垃圾", pi["leading_junk"] > 0 and pi["trailing_junk"] == 0,
      (pi["leading_junk"], pi["trailing_junk"]))
check("输出帧数与时长自洽", abs(pi["frames"] * 576 / 24000 - pi["duration"]) < 0.01,
      (pi["frames"], round(pi["duration"], 3)))

# ---------------------------------------------------------------------------
section("3.5 万字级实战(真跑一遍超长文本)")
# ---------------------------------------------------------------------------
para = ("这段话用来说明长文本转换的可靠性。真正的难点不在合成，而在顺序、边界和失败重试："
        "文本要被切成语义完整的小段，每段独立合成后按原顺序拼回一个文件，中间不能丢失也不能错位。"
        "只要有一个环节出错，几十分钟的音频就会在第几分钟处突然断掉或者串台。")
huge2 = "\n\n".join(f"第{i}节。{para}" for i in range(1, 81))
print(f"  文本 {len(huge2):,} 字符,预估 {splitter.estimate_seconds(huge2)/60:.1f} 分钟音频")
opt = engine.Options(voice="zh-CN-XiaoxiaoNeural", chunk_chars=400, concurrency=4,
                     title="万字实战")
t0 = time.time()
job = engine.start_job(huge2, opt, OUT)
snap, seen = wait(job, timeout=900, interval=0.4)
cost = time.time() - t0
check("万字任务完成", snap["status"] == "done", snap["status"])
check("块数达到数十段", snap["chunks_total"] >= 25, snap["chunks_total"])
check("全部段落成功", snap["chunks_done"] == snap["chunks_total"]
      and not snap["failures"], (snap["chunks_done"], snap["failures"]))
p = os.path.join(OUT, snap["filename"])
m = MP3(p)
check("万字音频时长超过 20 分钟", m.info.length > 1200, round(m.info.length, 1))
check("拼接时长与统计完全一致", abs(m.info.length - snap["duration"]) < 0.6,
      (round(m.info.length, 3), round(snap["duration"], 3)))
check("文件大小与码率自洽", abs(snap["size_bytes"] / (m.info.length * 1024 / 8) / 48 - 1) < 0.05,
      (snap["size_bytes"], round(m.info.length, 1)))
print(f"  (耗时 {cost:.1f}s,音频 {m.info.length/60:.2f} 分钟,加速比 {m.info.length/cost:.1f}x,"
      f"文件 {snap['size_bytes']/1048576:.2f} MB)")

# ---------------------------------------------------------------------------
section("4. 语速参数确实生效")
# ---------------------------------------------------------------------------
txt = "这句话用来测量语速参数带来的时长差异，请仔细听。" * 3
fast = engine.start_job(txt, engine.Options(rate=80, chunk_chars=1200, title="快"), OUT)
slow = engine.start_job(txt, engine.Options(rate=-40, chunk_chars=1200, title="慢"), OUT)
sf, _ = wait(fast, timeout=180)
ss, _ = wait(slow, timeout=180)
check("两种语速都成功", sf["status"] == "done" and ss["status"] == "done",
      (sf["status"], ss["status"]))
check("加速后更短", sf["duration"] < ss["duration"] * 0.75,
      (round(sf["duration"], 2), round(ss["duration"], 2)))

# ---------------------------------------------------------------------------
section("5. 取消与异常处理")
# ---------------------------------------------------------------------------
big = "\n\n".join(f"这是第{i}段用于取消测试的文本，内容需要足够长。" * 6 for i in range(1, 40))
job = engine.start_job(big, engine.Options(chunk_chars=200, concurrency=2, title="取消测试"), OUT)
time.sleep(3.0)
job.cancel()
snap, _ = wait(job, timeout=90)
check("取消后状态为 canceled", snap["status"] == "canceled", snap["status"])
check("取消不产出文件", not snap["has_audio"], snap["filename"])
check("取消发生在中途", snap["progress"] < 1.0, snap["progress"])

bad = engine.start_job("测试错误处理。", engine.Options(voice="xx-XX-NotARealVoice", retries=1), OUT)
snap, _ = wait(bad, timeout=120)
check("非法音色被报告为失败", snap["status"] == "error", snap["status"])
check("错误信息可读", bool(snap["error"]) and len(snap["error"]) > 5, snap["error"])

# 空白/无效文本应当在创建任务时就当场拒绝(切分是同步做的)
for label, txt in [("空白文本", "   \n\n  "), ("纯符号文本", "----- \n\n *** \n ====")]:
    try:
        engine.start_job(txt, engine.Options(), OUT)
        check(f"{label}被当场拒绝", False, "居然创建成功了")
    except engine.SynthError as exc:
        check(f"{label}被当场拒绝", True, str(exc))

# 超长文本上限
try:
    engine.start_job("字" * (engine.MAX_TEXT_CHARS + 1), engine.Options(), OUT)
    check("超上限文本被当场拒绝", False, "居然创建成功了")
except engine.SynthError as exc:
    check("超上限文本被当场拒绝", "上限" in str(exc), str(exc)[:60])

# 任务创建后立即可知总段数(前端要马上显示)
probe_job = engine.start_job("第一段。\n\n第二段。", engine.Options(chunk_chars=200), OUT)
check("创建任务时即有总段数", probe_job.chunks_total >= 1, probe_job.chunks_total)
wait(probe_job, timeout=120)

# ---------------------------------------------------------------------------
section("6. 引擎自检")
# ---------------------------------------------------------------------------
st = engine.selftest()
check("自检通过", st["ok"], st)
check("自检返回可用音频", st.get("duration", 0) > 0.5, st.get("duration"))
check("引擎版本可读", bool(st["engine"]["edge_tts"]), st["engine"])

# ---------------------------------------------------------------------------
shutil.rmtree(OUT, ignore_errors=True)
print("\n" + "=" * 66)
print(f"通过 {len(PASS)} 项, 失败 {len(FAIL)} 项")
for f in FAIL:
    print("  - " + f)
print("=" * 66)
sys.exit(1 if FAIL else 0)
