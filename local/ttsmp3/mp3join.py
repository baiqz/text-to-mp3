# -*- coding: utf-8 -*-
"""
MP3 帧级工具
============

edge-tts 返回的是裸 MPEG-1/2 Layer III 帧流(24kHz / 48kbps / 单声道)。
长文本会被切成上百个片段分别合成,再把它们拼成一个 MP3。

裸字节拼接之所以危险:任意一个片段若带 ID3v2 标签、或结尾有半个残帧,
拼进去就会造成后续解码器"错位",表现为杂音、时长变短、甚至整个文件打不开。

所以在拼接前对每段做一次帧级体检:
  1. 跳过开头的 ID3v2 标签 / 找第一个合法帧同步字;
  2. 从第一个帧起,逐帧按帧长前进,只保留完整帧;
  3. 丢弃结尾的残缺字节(通常是 encoder 收尾的半帧)。

只输出"干净的连续帧流",拼接结果即为结构合法的 MP3。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence

__all__ = [
    "Mp3Error",
    "FrameHeader",
    "parse_frame_header",
    "iter_frames",
    "audio_span",
    "probe",
    "join_mp3",
    "build_id3v2",
    "strip_id3",
]


class Mp3Error(Exception):
    """MP3 结构不合法时抛出。"""


# MPEG 版本: 0=MPEG2.5, 2=MPEG2, 3=MPEG1 (1 为保留值)
_SAMPLE_RATES = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),
}
_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, -1)
_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, -1)
_CHANNEL_MODES = {0: "立体声", 1: "联合立体声", 2: "双声道", 3: "单声道"}


@dataclass(frozen=True)
class FrameHeader:
    """一个 MPEG 音频帧头解析结果。"""

    version: int          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
    layer: int            # 3=Layer I, 2=Layer II, 1=Layer III
    bitrate: int          # bps
    sample_rate: int      # Hz
    padding: int
    channels_mode: int
    samples_per_frame: int
    length: int           # 本帧总字节数(含帧头)

    @property
    def duration(self) -> float:
        return self.samples_per_frame / self.sample_rate

    @property
    def mpeg_name(self) -> str:
        return {3: "MPEG-1", 2: "MPEG-2", 0: "MPEG-2.5"}.get(self.version, "?")

    @property
    def layer_name(self) -> str:
        return {1: "Layer I", 2: "Layer II", 3: "Layer III"}.get(self.layer, "?")

    @property
    def channels_name(self) -> str:
        return _CHANNEL_MODES.get(self.channels_mode, "?")

    @property
    def channels(self) -> int:
        return 1 if self.channels_mode == 3 else 2

    def signature(self) -> tuple:
        """用于判断两段音频能否直接拼接。"""
        return (self.version, self.layer, self.sample_rate, self.bitrate, self.channels)


def parse_frame_header(buf: bytes) -> Optional[FrameHeader]:
    """解析 4 字节帧头;不是合法帧则返回 None。"""
    if len(buf) < 4:
        return None
    b0, b1, b2 = buf[0], buf[1], buf[2]

    # 帧同步字: 11 个 1
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None

    version = (b1 >> 3) & 0x03          # 0/2/3
    layer_bits = (b1 >> 1) & 0x03       # Layer: 1->III, 2->II, 3->I
    if version == 1 or layer_bits == 0:
        return None
    layer = {1: 3, 2: 2, 3: 1}[layer_bits]

    bitrate_index = (b2 >> 4) & 0x0F
    rate_index = (b2 >> 2) & 0x03
    padding = (b2 >> 1) & 0x01
    if bitrate_index in (0, 15) or rate_index == 3:
        return None

    sample_rate = _SAMPLE_RATES[version][rate_index]
    table = _BITRATES_V1 if version == 3 else _BITRATES_V2
    if layer == 1:
        table = table  # Layer I 使用 V1 码率表的 32~448kbps,edge-tts 不会产出,略
    bitrate = table[bitrate_index] * 1000
    if bitrate <= 0:
        return None

    channels_mode = (buf[3] >> 6) & 0x03

    if layer == 1:
        samples_per_frame = 384 if version == 3 else 192
    elif layer == 2:
        samples_per_frame = 1152 if version == 3 else 576
    else:  # Layer III
        samples_per_frame = 1152 if version == 3 else 576

    if layer == 1:
        length = (12 * bitrate) // sample_rate + padding * 4
    elif layer == 3 and version != 3:      # MPEG-2/2.5 Layer III
        length = (72 * bitrate) // sample_rate + padding
    else:
        length = (144 * bitrate) // sample_rate + padding

    if length < 8:
        return None

    return FrameHeader(
        version=version,
        layer=layer,
        bitrate=bitrate,
        sample_rate=sample_rate,
        padding=padding,
        channels_mode=channels_mode,
        samples_per_frame=samples_per_frame,
        length=length,
    )


def _id3v2_size(data: bytes, offset: int = 0) -> int:
    """若 offset 处是 ID3v2 标签,返回标签总长度,否则返回 0。"""
    if len(data) < offset + 10 or data[offset:offset + 3] != b"ID3":
        return 0
    h = data[offset + 6: offset + 10]
    size = ((h[0] & 0x7F) << 21) | ((h[1] & 0x7F) << 14) | ((h[2] & 0x7F) << 7) | (h[3] & 0x7F)
    flags = data[offset + 5]
    total = 10 + size
    if flags & 0x10:      # footer present
        total += 10
    return total


def strip_id3(data: bytes) -> bytes:
    """去掉开头连续的 ID3v2 标签(可能拼接了多个)。

    注意 pos 必须推进:漏掉自增会在这里死循环。
    """
    pos = 0
    total = len(data)
    while pos + 10 <= total:
        size = _id3v2_size(data, pos)
        if not size:
            break
        pos += size
    return data[pos:]


def iter_frames(data: bytes) -> Iterator[tuple]:
    """从 data 中顺序产出 (offset, FrameHeader)。

    遇到非法字节时向后寻找下一个同步字,保证不因一处异常而中断扫描。
    """
    pos = 0
    n = len(data)
    while pos + 4 <= n:
        header = parse_frame_header(data[pos: pos + 4])
        if header is None:
            nxt = data.find(b"\xff", pos + 1)
            if nxt < 0:
                return
            pos = nxt
            continue
        yield pos, header
        pos += header.length


def audio_span(data: bytes) -> tuple:
    """返回 (起始偏移, 结束偏移, 帧数, 首帧头)。

    结束偏移保证落在最后一个完整帧的末尾 —— 低于它的是音频帧序列,
    高于它的是 ID3v1 / 半截帧等垃圾字节。
    """
    if not data:
        raise Mp3Error("空的音频数据")

    body = strip_id3(data)
    skipped = len(data) - len(body)
    if not body:
        raise Mp3Error("只有 ID3 标签,没有音频帧")

    first_offset = None
    first_header = None
    last_end = 0
    count = 0

    for offset, header in iter_frames(body):
        if offset + header.length > len(body):
            break                      # 尾部残帧,不要
        if first_offset is None:
            first_offset = offset
            first_header = header
        count += 1
        last_end = offset + header.length

    if first_offset is None or count == 0 or first_header is None:
        raise Mp3Error("未找到合法的 MP3 音频帧")

    return skipped + first_offset, skipped + last_end, count, first_header


def probe(data: bytes) -> dict:
    """体检一段 MP3 字节流,返回帧数、时长、采样率等信息。"""
    start, end, count, header = audio_span(data)
    body = data[start:end]
    total_samples = 0
    n = 0
    for offset, h in iter_frames(body):
        if offset + h.length > len(body):
            break
        total_samples += h.samples_per_frame
        n += 1
    return {
        "frames": n,
        "bytes": end - start,
        "duration": total_samples / header.sample_rate if n else 0.0,
        "sample_rate": header.sample_rate,
        "bitrate": header.bitrate,
        "channels": header.channels,
        "channels_name": header.channels_name,
        "mpeg": header.mpeg_name,
        "layer": header.layer_name,
        "leading_junk": start,
        "trailing_junk": len(data) - end,
        # 尾部是否干净:原始数据的结尾正好是一个完整帧的结尾
        "clean_tail": len(data) == end,
    }


def join_mp3(parts: Sequence[bytes], *, strict: bool = True) -> bytes:
    """把多个 MP3 片段拼成一个连续帧流。

    strict=True 时,若各段音频参数(版本/采样率/码率/声道)不一致会报错,
    避免拼出无法播放的文件。
    """
    bodies: List[bytes] = []
    signatures = set()
    first_sig = None

    for i, part in enumerate(parts):
        if not part:
            continue
        start, end, count, header = audio_span(part)
        if count == 0:
            continue
        sig = header.signature()
        if first_sig is None:
            first_sig = sig
        signatures.add(sig)
        bodies.append(part[start:end])

    if not bodies:
        raise Mp3Error("没有可拼接的音频帧")

    if strict and len(signatures) > 1:
        raise Mp3Error(
            "各片段音频参数不一致,无法直接拼接: %s" % sorted(signatures)
        )

    return b"".join(bodies)


def build_id3v2(title: str = "", comment: str = "", encoder: str = "") -> bytes:
    """构造一个最小的 ID3v2.3 标签(TIT2 标题 / TSSE 编码软件)。

    只写这两个帧:它们结构简单(TIT2 与 TSSE 都是纯文本帧,编码字节 + 文本),
    不会像 COMM 那样还需要语言码与描述终止符 —— 帧结构一旦不合法,
    部分解析器(如 mutagen)会在畸形帧上打转甚至失败。

    只写标签本身,不写入任何作者信息。
    """
    frames = bytearray()

    def text_frame(fid: bytes, text: str) -> bytes:
        """UTF-16LE + BOM,兼容中日韩文字。"""
        if not text:
            return b""
        payload = b"\x01\xff\xfe" + text.encode("utf-16-le")
        return fid + struct.pack(">I", len(payload)) + b"\x00\x00" + payload

    frames += text_frame(b"TIT2", title)
    frames += text_frame(b"TSSE", " · ".join(x for x in (encoder, comment) if x))

    if not frames:
        return b""

    size = len(frames)
    header = b"ID3" + bytes([3, 0, 0]) + bytes([
        (size >> 21) & 0x7F,
        (size >> 14) & 0x7F,
        (size >> 7) & 0x7F,
        size & 0x7F,
    ])
    return bytes(header) + bytes(frames)
