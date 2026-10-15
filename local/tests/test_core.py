# -*- coding: utf-8 -*-
"""核心模块单元测试:切分器 / MP3 帧工具 / 拼接。

运行: python tests/test_core.py
涉及真实合成的用例需要联网。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mutagen.mp3 import MP3  # noqa: E402

from ttsmp3 import mp3join, splitter  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""))


def section(title):
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


# ---------------------------------------------------------------------------
section("1. 切分器")
# ---------------------------------------------------------------------------
long_cn = "这是一句话。" * 200                      # 1200 字,无空行
chunks = splitter.split_text(long_cn, 300)
check("长中文按句切分且不超限", all(len(c) <= 300 for c in chunks), [len(c) for c in chunks][:5])
check("长中文切分后内容无损", sum(len(c) for c in chunks) == len(long_cn.replace(" ", "")),
      sum(len(c) for c in chunks))

no_punct = "啊" * 1000                              # 完全没有标点
chunks = splitter.split_text(no_punct, 200)
check("无标点长串可硬切", all(len(c) <= 200 for c in chunks) and sum(len(c) for c in chunks) == 1000,
      [len(c) for c in chunks])

mixed = "第一段内容。\n\nSecond paragraph here. It has two sentences!\n\n第三段，短。"
chunks = splitter.split_text(mixed, 60)
put_together = "\n".join(chunks)
check("段落之间保留换行(不会连读)", "\n" in put_together, repr(put_together))
check("三段内容都在", all(s in put_together for s in ("第一段内容", "Second paragraph", "第三段")),
      put_together)
check("中英混排不丢字", "Second paragraph" in put_together)

# 长段落被切开时,段落边界仍然保留
two_long = ("甲" * 400) + "\n\n" + ("乙" * 400)
chunks = splitter.split_text(two_long, 250)
check("超长无标点段落硬切不超限", all(len(c) <= 250 for c in chunks), [len(c) for c in chunks])
check("硬切后内容无损",
      "".join(chunks).replace("\n", "") == two_long.replace("\n", ""),
      sum(len(c) for c in chunks))
check("甲乙两段没有混在一句里",
      any("甲" in c for c in chunks) and any("乙" in c for c in chunks))

# 真实场景:多段落文本,块边界必须落在段落/句子边界上
many = "\n\n".join(f"这是第{i}段的内容，用来测试切分是否会在句中断开。" for i in range(60))
chunks = splitter.split_text(many, 200)
check("多段落切分不超限", all(len(c) <= 200 for c in chunks), [len(c) for c in chunks][:6])
check("多段落切分内容完整",
      "".join(chunks).replace("\n", "") == many.replace("\n", ""),
      (len(chunks), sum(len(c) for c in chunks)))
check("块内段落之间有换行分隔", any("\n" in c for c in chunks),
      [c.count("\n") for c in chunks][:8])
check("每块都以句末标点收尾(不在句中断开)",
      all(c.rstrip()[-1] in "。！？!?…" for c in chunks),
      [c[-6:] for c in chunks][:4])

md = "# 标题\n\n这是**加粗**文字,还有[链接](http://x.com)和`代码`。\n\n---\n\n> 引用内容"
cleaned = splitter.clean_text(md)
check("Markdown 标记被清理", "#" not in cleaned and "**" not in cleaned
      and "http" not in cleaned and "---" not in cleaned, repr(cleaned[:50]))
check("Markdown 正文保留", "加粗" in cleaned and "链接" in cleaned and "引用内容" in cleaned)

check("纯符号文本清理后为空", splitter.clean_text("----- \n *** \n ===") == "")
check("空输入返回空列表", splitter.split_text("") == [])
check("极短输入单块", len(splitter.split_text("你好。")) == 1)

stats = splitter.analyze(long_cn)
check("字数统计正确", stats.chars == len(long_cn), stats.chars)
check("预估时长合理", 200 < stats.est_seconds < 4000, round(stats.est_seconds, 1))

# 切分保序
src = "".join(f"第{i}句话内容。" for i in range(150))
pieces = splitter.split_text(src, 137)
check("切分保序无损", "".join(pieces) == src, (len(pieces), len(src)))

# ---------------------------------------------------------------------------
section("2. MP3 帧工具")
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
import edge_tts  # noqa: E402


async def synth(text, voice="zh-CN-XiaoxiaoNeural"):
    c = edge_tts.Communicate(text, voice)
    buf = bytearray()
    async for ch in c.stream():
        if ch.get("type") == "audio":
            buf += ch["data"]
    return bytes(buf)


async def get_samples():
    a = await synth("今天天气不错，我们出去走走。")
    b = await synth("第二段测试内容，用于验证拼接。")
    c = await synth("Third part in English for join testing.")
    return a, b, c


t0 = time.time()
A, B, C = asyncio.run(get_samples())
print(f"  (合成 3 段样本, 耗时 {time.time() - t0:.1f}s, 大小 {len(A)}/{len(B)}/{len(C)})")

info_a = mp3join.probe(A)
check("单段探测合法", info_a["frames"] > 0 and info_a["duration"] > 0, info_a)
check("采样率 24kHz/48kbps/单声道",
      info_a["sample_rate"] == 24000 and info_a["bitrate"] == 48000 and info_a["channels"] == 1,
      (info_a["sample_rate"], info_a["bitrate"], info_a["channels"]))
check("单段无首尾垃圾", info_a["leading_junk"] == 0 and info_a["clean_tail"], info_a)

joined = mp3join.join_mp3([A, B, C])
fi = mp3join.probe(joined)
expect = info_a["duration"] + mp3join.probe(B)["duration"] + mp3join.probe(C)["duration"]
check("拼接后帧数 = 各段之和",
      fi["frames"] == sum(mp3join.probe(x)["frames"] for x in (A, B, C)),
      (fi["frames"], [mp3join.probe(x)["frames"] for x in (A, B, C)]))
check("拼接后时长 = 各段之和 (±1ms)", abs(fi["duration"] - expect) < 0.001,
      (round(fi["duration"], 4), round(expect, 4)))
check("拼接后无垃圾字节", fi["leading_junk"] == 0 and fi["trailing_junk"] == 0 and fi["clean_tail"], fi)

# 带 ID3 的输入也要能正确取帧
tagged = mp3join.build_id3v2(title="测试标题", comment="注释", encoder="text-to-mp3")
check("ID3 标签可生成", tagged[:3] == b"ID3" and len(tagged) > 20, len(tagged))
with_tag = tagged + joined
check("带 ID3 的流可被正确跳过", mp3join.probe(with_tag)["leading_junk"] == len(tagged)
      and mp3join.probe(with_tag)["frames"] == fi["frames"])

# 写入临时文件让 mutagen 独立验证
import tempfile  # noqa: E402
tmp = tempfile.mkdtemp()
p1 = os.path.join(tmp, "joined.mp3")
with open(p1, "wb") as f:
    f.write(joined)
m = MP3(p1)
check("mutagen 认可拼接文件", abs(m.info.length - expect) < 0.05,
      (round(m.info.length, 3), round(expect, 3)))

p2 = os.path.join(tmp, "tagged.mp3")
with open(p2, "wb") as f:
    f.write(mp3join.build_id3v2(title="长文本测试", comment="demo") + joined)
m2 = MP3(p2)
check("mutagen 能读出 ID3 标题", (m2.tags or {}).get("TIT2") is not None,
      (m2.tags or {}).get("TIT2"))
check("带 ID3 时长不受影响", abs(m2.info.length - expect) < 0.05, round(m2.info.length, 3))

# 残帧容错:尾部切掉 37 字节 → 少一个完整帧,残片不入音频
truncated = joined[:-37]
ti = mp3join.probe(truncated)
check("尾部残缺字节被丢弃", ti["frames"] == fi["frames"] - 1 and ti["clean_tail"] is False,
      (ti["frames"], fi["frames"], ti["trailing_junk"]))
# 整帧切除 → 帧数减一且尾部干净
exact = joined[:-144]
ei = mp3join.probe(exact)
check("整帧切除后尾部干净", ei["frames"] == fi["frames"] - 1 and ei["trailing_junk"] == 0,
      (ei["frames"], ei["trailing_junk"]))
# 拼接时残片不会被带进结果
check("拼接会自动剔除残片", len(mp3join.join_mp3([truncated])) == len(joined) - 144,
      (len(mp3join.join_mp3([truncated])), len(joined) - 144))

# 参数不一致应报错
try:
    mp3join.join_mp3([A, A], strict=True)
    ok_strict = True
except mp3join.Mp3Error:
    ok_strict = False
check("同构片段拼接不报错", ok_strict)

try:
    mp3join.join_mp3([A, b"\xff\xfb\x90\x64" + b"\x00" * 500])
    strict_failed = False
except mp3join.Mp3Error:
    strict_failed = True
check("异构片段被拒绝", strict_failed)

# ---------------------------------------------------------------------------
print("\n" + "=" * 66)
print(f"通过 {len(PASS)} 项, 失败 {len(FAIL)} 项")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  - " + f)
print("=" * 66)
sys.exit(1 if FAIL else 0)
