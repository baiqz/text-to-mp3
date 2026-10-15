# -*- coding: utf-8 -*-
"""
输入源提取
==========

长文本通常不会手打,而是躺在文件里。这里把常见格式抽成纯文本:

* .txt / .md / .csv / .json / .srt / .log —— 多编码自动探测
* .docx / .docm —— 用标准库解 zip 里的 document.xml(不依赖 python-docx)
* .html / .htm —— 去标签

.docx 里的段落会被换成换行,表格单元格用制表符分隔,尽量保留可朗读的结构。
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

__all__ = ["extract_text", "SUPPORTED_EXTS"]

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SUPPORTED_EXTS = (
    ".txt", ".md", ".markdown", ".text", ".csv", ".tsv", ".json",
    ".srt", ".vtt", ".log", ".html", ".htm", ".xml",
    ".docx", ".docm",
)

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16", "latin-1")


class ExtractError(Exception):
    pass


def _decode(data: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\ufffd" not in text[:4000]:
            return text
    return data.decode("utf-8", "replace")


def _from_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n in ("word/document.xml",)]
            if not names:
                names = [n for n in zf.namelist() if n.startswith("word/document")]
            if not names:
                raise ExtractError("这个 docx 里没有找到正文(word/document.xml)")
            xml = zf.read(names[0])
    except zipfile.BadZipFile as exc:
        raise ExtractError(
            "不是有效的 docx(注意:.doc 老格式请先在 Word 里另存为 .docx)"
        ) from exc

    root = ElementTree.fromstring(xml)
    lines = []
    for para in root.iter(f"{_W}p"):
        parts = []
        for node in para.iter():
            tag = node.tag
            if tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif tag == f"{_W}tab":
                parts.append("\t")
            elif tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _from_html(data: bytes) -> str:
    text = _decode(data)
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(filename: str, data: bytes) -> str:
    """从上传的文件字节流中提取纯文本。"""
    if not data:
        raise ExtractError("文件是空的")

    name = (filename or "").lower()
    ext = ""
    if "." in name:
        ext = name[name.rfind("."):]

    if ext in (".docx", ".docm"):
        return _from_docx(data)
    if ext in (".html", ".htm"):
        return _from_html(data)
    if ext == ".doc":
        raise ExtractError(".doc 老格式无法直接解析,请在 Word 里另存为 .docx 或 .txt 后重试")
    if ext == ".pdf":
        raise ExtractError("PDF 暂不支持,请复制文字后直接粘贴,或换成 txt/docx")

    text = _decode(data)
    # srt/vtt 去时间轴
    if ext in (".srt", ".vtt"):
        lines = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.isdigit() or "-->" in s or s.upper().startswith("WEBVTT"):
                continue
            lines.append(s)
        text = "\n".join(lines)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
