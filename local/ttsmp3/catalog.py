# -*- coding: utf-8 -*-
"""
音色目录
========

两套数据:
  * CURATED —— 精选手工整理的音色,带中文名与描述,作为界面默认展示项;
  * 动态全量 —— 运行时从微软拉取全部音色(300+),缓存到 cache/voices.json,
                供"全部语音"搜索使用。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_BASE_DIR, "cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "voices.json")
_CACHE_TTL = 7 * 24 * 3600

_lock = threading.Lock()
_memo: Optional[List[dict]] = None

# ---------------------------------------------------------------------------
# 精选音色(界面默认展示)
# ---------------------------------------------------------------------------
CURATED: List[dict] = [
    # 中文普通话
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "gender": "女", "lang": "中文", "tone": "温暖亲切", "tag": "推荐"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊", "gender": "女", "lang": "中文", "tone": "甜美活泼", "tag": "热门"},
    {"id": "zh-CN-YunxiNeural", "name": "云希", "gender": "男", "lang": "中文", "tone": "阳光少年", "tag": "热门"},
    {"id": "zh-CN-YunjianNeural", "name": "云健", "gender": "男", "lang": "中文", "tone": "浑厚大气", "tag": "解说"},
    {"id": "zh-CN-YunyangNeural", "name": "云扬", "gender": "男", "lang": "中文", "tone": "专业播音", "tag": "新闻"},
    {"id": "zh-CN-YunxiaNeural", "name": "云夏", "gender": "男", "lang": "中文", "tone": "童声", "tag": ""},
    {"id": "zh-CN-XiaomoNeural", "name": "晓墨", "gender": "女", "lang": "中文", "tone": "沉稳知性", "tag": "讲故事"},
    {"id": "zh-CN-XiaoxuanNeural", "name": "晓萱", "gender": "女", "lang": "中文", "tone": "干练利落", "tag": ""},
    {"id": "zh-CN-XiaohanNeural", "name": "晓涵", "gender": "女", "lang": "中文", "tone": "优雅", "tag": ""},
    {"id": "zh-CN-XiaoruiNeural", "name": "晓睿", "gender": "女", "lang": "中文", "tone": "成熟稳重", "tag": ""},
    {"id": "zh-CN-XiaozhenNeural", "name": "晓甄", "gender": "女", "lang": "中文", "tone": "清脆", "tag": ""},
    {"id": "zh-CN-XiaomengNeural", "name": "晓梦", "gender": "女", "lang": "中文", "tone": "轻柔", "tag": ""},
    {"id": "zh-CN-YunfengNeural", "name": "云枫", "gender": "男", "lang": "中文", "tone": "低沉", "tag": ""},
    {"id": "zh-CN-YunhaoNeural", "name": "云皓", "gender": "男", "lang": "中文", "tone": "广告腔", "tag": ""},
    {"id": "zh-CN-YunzeNeural", "name": "云泽", "gender": "男", "lang": "中文", "tone": "沉稳", "tag": ""},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "name": "晓北", "gender": "女", "lang": "东北话", "tone": "东北方言", "tag": "方言"},
    {"id": "zh-CN-shaanxi-XiaoniNeural", "name": "晓妮", "gender": "女", "lang": "陕西话", "tone": "陕西方言", "tag": "方言"},
    # 粤语 / 中国台湾
    {"id": "zh-HK-HiuMaanNeural", "name": "曉曼", "gender": "女", "lang": "粤语", "tone": "粵語女聲", "tag": "方言"},
    {"id": "zh-HK-HiuGaaiNeural", "name": "曉佳", "gender": "女", "lang": "粤语", "tone": "粵語女聲", "tag": ""},
    {"id": "zh-HK-WanLungNeural", "name": "雲龍", "gender": "男", "lang": "粤语", "tone": "粵語男聲", "tag": ""},
    {"id": "zh-TW-HsiaoChenNeural", "name": "曉臻", "gender": "女", "lang": "中国台湾", "tone": "臺灣女聲", "tag": ""},
    {"id": "zh-TW-HsiaoYuNeural", "name": "曉雨", "gender": "女", "lang": "中国台湾", "tone": "臺灣女聲", "tag": ""},
    {"id": "zh-TW-YunJheNeural", "name": "雲哲", "gender": "男", "lang": "中国台湾", "tone": "臺灣男聲", "tag": ""},
    # 英语
    {"id": "en-US-AriaNeural", "name": "Aria", "gender": "女", "lang": "英语", "tone": "自然表达", "tag": ""},
    {"id": "en-US-JennyNeural", "name": "Jenny", "gender": "女", "lang": "英语", "tone": "亲切", "tag": ""},
    {"id": "en-US-GuyNeural", "name": "Guy", "gender": "男", "lang": "英语", "tone": "沉稳", "tag": ""},
    {"id": "en-US-ChristopherNeural", "name": "Christopher", "gender": "男", "lang": "英语", "tone": "专业", "tag": ""},
    {"id": "en-GB-SoniaNeural", "name": "Sonia", "gender": "女", "lang": "英式英语", "tone": "英音", "tag": ""},
    {"id": "en-GB-RyanNeural", "name": "Ryan", "gender": "男", "lang": "英式英语", "tone": "英音", "tag": ""},
    # 其他常用
    {"id": "ja-JP-NanamiNeural", "name": "ナナミ", "gender": "女", "lang": "日语", "tone": "日本語", "tag": ""},
    {"id": "ja-JP-KeitaNeural", "name": "ケイタ", "gender": "男", "lang": "日语", "tone": "日本語", "tag": ""},
    {"id": "ko-KR-SunHiNeural", "name": "선히", "gender": "女", "lang": "韩语", "tone": "한국어", "tag": ""},
    {"id": "ko-KR-InJoonNeural", "name": "인준", "gender": "男", "lang": "韩语", "tone": "한국어", "tag": ""},
    {"id": "fr-FR-DeniseNeural", "name": "Denise", "gender": "女", "lang": "法语", "tone": "Français", "tag": ""},
    {"id": "de-DE-KatjaNeural", "name": "Katja", "gender": "女", "lang": "德语", "tone": "Deutsch", "tag": ""},
    {"id": "es-ES-ElviraNeural", "name": "Elvira", "gender": "女", "lang": "西班牙语", "tone": "Español", "tag": ""},
    {"id": "ru-RU-SvetlanaNeural", "name": "Svetlana", "gender": "女", "lang": "俄语", "tone": "Русский", "tag": ""},
]

CURATED_IDS = [v["id"] for v in CURATED]
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# ---------------------------------------------------------------------------
# 实测结论（2026-10-15，用真实浏览器逐个合成验证，不是靠查文档）
# ---------------------------------------------------------------------------
# 「服务端清单里有」≠「能用」，反过来「清单里没有」也≠「不能用」。两套纠偏表：

# 1) voices/list 不返回、但服务端确实还能合成的音色 —— 必须放行，否则会误杀
#    （云泽那批是"看清单办事"踩的坑；晓萱则是反向的坑）
VERIFIED_ALIVE_EXTRA: Dict[str, dict] = {
    "zh-CN-XiaoxuanNeural": {
        "name": "晓萱", "gender": "女", "locale": "zh-CN", "lang": "中文",
        "tone": "实测可用（不在官方清单）", "tag": "扩展",
    },
}

# 2) 服务端明确回 "Unsupported voice" 的音色 —— 已经从声库里消失
#    即使它们还躺在精选清单里（历史快照），也必须剔除，否则用户选了必然失败
VERIFIED_DEAD = {
    "zh-CN-YunzeNeural",           # 云泽
    "zh-CN-YunfengNeural",         # 云枫
    "zh-CN-YunhaoNeural",          # 云皓
    "zh-CN-YunyeNeural",           # 云野
    "zh-CN-XiaomoNeural",          # 晓墨
    "zh-CN-XiaohanNeural",         # 晓涵
    "zh-CN-XiaoruiNeural",         # 晓睿
    "zh-CN-XiaozhenNeural",        # 晓甄
    "zh-CN-XiaomengNeural",        # 晓梦
}


def _fetch_all() -> List[dict]:
    """从微软拉取全量音色。需要联网,失败时抛异常由调用方兜底。"""
    import asyncio

    import edge_tts

    raw = asyncio.run(edge_tts.list_voices())
    voices = []
    for v in raw:
        voices.append({
            "id": v.get("ShortName", ""),
            "name": v.get("ShortName", "").split("-")[-1].replace("Neural", ""),
            "gender": "女" if v.get("Gender") == "Female" else "男",
            "locale": v.get("Locale", ""),
            "lang": v.get("Locale", ""),
            "tone": v.get("Locale", ""),
            "tag": "",
        })
    return voices


def all_voices(force: bool = False) -> List[dict]:
    """全量音色,带磁盘缓存与内存缓存。"""
    global _memo
    with _lock:
        if _memo and not force:
            return _memo

        if not force and os.path.exists(_CACHE_FILE):
            try:
                age = time.time() - os.path.getmtime(_CACHE_FILE)
                if age < _CACHE_TTL:
                    with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                        _memo = json.load(f)
                    if _memo:
                        return _memo
            except Exception:
                pass

        try:
            voices = _fetch_all()
        except Exception:
            voices = []

        if voices:
            _memo = voices
            try:
                os.makedirs(_CACHE_DIR, exist_ok=True)
                with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(voices, f, ensure_ascii=False)
            except Exception:
                pass

        return _memo or []


def voice_ids() -> set:
    """所有合法 voice id(精选 + 动态)。"""
    ids = set(CURATED_IDS)
    for v in all_voices():
        ids.add(v["id"])
    return ids


def live_voice_ids() -> set:
    """服务端**当前真的能用**的音色 id 集合(取不到时返回空集)。

    精选清单是人工整理的快照,服务端会不打招呼地下架音色(例如云泽、
    晓墨、云枫等一批中文音色已经消失)。所以判断"能不能用"必须以这份
    实时清单为准,否则用户会选中一个必挂的音色。

    但清单本身也有两个方向的偏差,用实测表纠偏:
      * VERIFIED_ALIVE_EXTRA —— 清单里没有却仍能合成(晓萱),要放行;
      * VERIFIED_DEAD        —— 清单里可能还有、实际已拒绝,要剔除。
    """
    try:
        ids = {v["id"] for v in all_voices() if v.get("id")}
    except Exception:                              # noqa: BLE001
        ids = set()
    if not ids:
        return set()                               # 拿不到清单时不纠偏,交给调用方兜底
    return (ids | set(VERIFIED_ALIVE_EXTRA)) - VERIFIED_DEAD


def is_valid_voice(vid: str) -> bool:
    vid = (vid or "").strip()
    if not vid or len(vid) > 64 or " " in vid:
        return False
    # 合法形如 zh-CN-XiaoxiaoNeural: 只允许字母数字与减号点
    import re
    if not re.fullmatch(r"[A-Za-z0-9\-\.]+", vid):
        return False

    known = live_voice_ids()
    if known:
        return vid in known                  # 在线时以服务端清单为准（唯一权威）
    # 拿不到动态列表时（离线），退回精选清单与命名惯例
    if vid in VERIFIED_DEAD:                 # 实测已下架的，离线也不放行
        return False
    if vid in CURATED_IDS or vid in VERIFIED_ALIVE_EXTRA:
        return True
    return bool(re.fullmatch(r"[a-z]{2,3}-[A-Za-z]{2,4}-[A-Za-z]+Neural", vid))


def catalog() -> Dict[str, object]:
    """给前端用的音色清单。已下架的精选音色会被剔除,不展示给用户。"""
    known = live_voice_ids()
    curated = CURATED
    missing: List[str] = []
    if known:
        curated = [v for v in CURATED if v["id"] in known]
        missing = [v["id"] for v in CURATED if v["id"] not in known]

    # 全量列表补上"清单外但实测可用"的音色，否则在搜索里也找不到它们
    allv = all_voices()
    have = {v["id"] for v in allv}
    for vid, meta in VERIFIED_ALIVE_EXTRA.items():
        if vid in have or vid in VERIFIED_DEAD:
            continue
        allv = allv + [{
            "id": vid, "name": meta["name"], "gender": meta["gender"],
            "locale": meta["locale"], "lang": meta["lang"],
            "tone": meta["tone"], "tag": meta["tag"], "extra": True,
        }]

    return {
        "curated": curated,
        "default": DEFAULT_VOICE,
        "all": allv,
        "missing": missing,
        "extra": sorted(VERIFIED_ALIVE_EXTRA.keys()),
    }
