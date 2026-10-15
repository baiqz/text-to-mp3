# -*- coding: utf-8 -*-
"""
长文本切分器
============

edge-tts 单次请求的文本量有限,超长文本必须切成小块分别合成。

切分的原则只有一条:**宁可块小,不可断句**。
在句子中间断开会让合成语音的语调直接"摔在地上"——上一句没了尾音,
下一句从半口气起头。所以切分严格按标点层级递降:

    段落  →  句末标点(。！？!?…)  →  停顿标点(，、；：)  →  空格  →  硬切

切完再按顺序装箱(greedy packing)拼到接近 max_chars,减少请求次数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

__all__ = ["split_text", "clean_text", "estimate_seconds", "TextStats", "analyze"]

# 逐级递降的切分点。每一级都是"在标点之后断开"。
_LEVEL_PATTERNS = [
    r"(?<=[。！？!?…；;])",              # 句末
    r"(?<=[，,、：:）)】」』\u201d])",   # 停顿
    r"(?<=[ \t\u3000])",                # 空格(英文为主)
]

# 纯符号行:整行都是这些字符时直接丢弃(读出来是"破折号破折号破折号")
_DECOR_LINE = re.compile(r"^[\s\-—_=*~#・·。.,，、:：;；!！?？>|/\\\[\]()（）\"'“”‘’]+$")

_MD_RULES = [
    (re.compile(r"```[\s\S]*?```"), " "),          # 代码块
    (re.compile(r"`([^`]*)`"), r"\1"),             # 行内代码
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),    # 图片
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"), # 链接→保留文字
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.M), ""),  # 标题
    (re.compile(r"^\s*>\s?", re.M), ""),           # 引用
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),       # 无序列表
    (re.compile(r"^\s*\d+[.)]\s+", re.M), ""),     # 有序列表
    (re.compile(r"\*\*([^*]*)\*\*"), r"\1"),       # 粗体
    (re.compile(r"(?<!\w)\*([^*\n]+)\*(?!\w)"), r"\1"),
    (re.compile(r"_{2,}"), ""),
]


@dataclass
class TextStats:
    chars: int          # 全部字符数
    cjk: int            # 中日韩字数
    words: int          # 英文单词数
    paragraphs: int
    est_seconds: float  # 预估朗读时长(秒)

    @property
    def est_text(self) -> str:
        m, s = divmod(int(round(self.est_seconds)), 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h} 小时 {m} 分"
        return f"{m} 分 {s} 秒" if m else f"{s} 秒"


def clean_text(text: str, *, strip_markdown: bool = True) -> str:
    """归一化空白、清理 Markdown 标记与纯装饰行。"""
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\t", " ")
    text = text.replace("\ufeff", "")

    if strip_markdown:
        for pattern, repl in _MD_RULES:
            text = pattern.sub(repl, text)

    kept: List[str] = []
    for line in text.split("\n"):
        if not line.strip():
            kept.append("")
            continue
        if _DECOR_LINE.match(line.strip()):
            continue                      # 丢弃纯符号行
        kept.append(re.sub(r"[ ]{2,}", " ", line).strip())

    out = "\n".join(kept)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _break(seg: str, max_chars: int, level: int = 0) -> List[str]:
    """把 seg 递归切到每片 <= max_chars。"""
    if len(seg) <= max_chars:
        return [seg]
    if level >= len(_LEVEL_PATTERNS):
        return [seg[i:i + max_chars] for i in range(0, len(seg), max_chars)]

    pieces = [p for p in re.split(_LEVEL_PATTERNS[level], seg) if p]
    if len(pieces) <= 1:
        return _break(seg, max_chars, level + 1)

    out: List[str] = []
    for p in pieces:
        out.extend(_break(p, max_chars, level + 1))
    return out


def _pack(atoms: List[str], max_chars: int) -> List[str]:
    """顺序装箱:累加到接近 max_chars 为止。

    atoms 中的换行分隔符会被原样保留(段落之间必须有换行,
    否则合成时两段会连读成一句)。
    """
    chunks: List[str] = []
    buf = ""
    for a in atoms:
        if not a:
            continue
        if a.strip() == "":
            if buf and not buf.endswith("\n"):
                buf += "\n"
            continue
        if buf and len(buf) + len(a) > max_chars:
            chunks.append(buf)
            buf = a
        else:
            buf += a
    if buf:
        chunks.append(buf)

    # 收尾碎片并回前一块(仍不超 max_chars)
    if len(chunks) >= 2:
        tail = chunks[-1]
        if len(tail.strip()) < max_chars * 0.25:
            merged = chunks[-2] + tail
            if len(merged.strip()) <= max_chars:
                chunks[-2] = merged
                chunks.pop()
    return chunks


def split_text(
    text: str,
    max_chars: int = 1200,
    *,
    strip_markdown: bool = True,
) -> List[str]:
    """把任意长度的文本切成适合逐块合成的片段列表。"""
    if not text:
        return []
    max_chars = max(200, int(max_chars))

    cleaned = clean_text(text, strip_markdown=strip_markdown)
    if not cleaned:
        return []

    # 段落是最自然的边界,优先在此处切。
    # 每个段落末尾补一个换行,装箱后段落之间不会连读。
    atoms: List[str] = []
    for para in re.split(r"\n\s*\n", cleaned):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            atoms.append(para + "\n")
        else:
            sub = _break(para, max_chars)
            for i, s in enumerate(sub):
                atoms.append(s + "\n" if i == len(sub) - 1 else s)

    chunks = [c.strip() for c in _pack(atoms, max_chars) if c.strip()]
    return [c for c in chunks if c]


def estimate_seconds(text: str) -> float:
    """粗略估算朗读时长:中文约 4.2 字/秒,英文约 2.9 词/秒。"""
    if not text:
        return 0.0
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    words = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text))
    digits = len(re.findall(r"\d+", text))
    seconds = cjk / 4.2 + words / 2.9 + digits * 0.35
    # 标点带来的停顿
    pauses = len(re.findall(r"[，,、：:；;]", text)) * 0.18
    stops = len(re.findall(r"[。！？!?…\n]", text)) * 0.35
    return max(0.0, seconds + pauses + stops)


def analyze(text: str) -> TextStats:
    """统计文本规模与预估时长。"""
    cleaned = clean_text(text) if text else ""
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", cleaned))
    words = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", cleaned))
    paras = len([p for p in re.split(r"\n\s*\n", cleaned) if p.strip()])
    return TextStats(
        chars=len(text or ""),
        cjk=cjk,
        words=words,
        paragraphs=paras,
        est_seconds=estimate_seconds(cleaned),
    )
