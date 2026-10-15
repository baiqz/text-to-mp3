# -*- coding: utf-8 -*-
"""
网络通路诊断与代理自愈
======================

语音合成依赖微软 Edge 的在线语音服务（HTTPS 取音色 + WebSocket 传音频）。
这条链路很脆：出口网络稍有别扭，整批片段就会全部失败。

而最坑的一种情况不是"真断网"，而是：**进程从父环境继承了一个已经失效的代理**。
比如在某个终端 / 启动器里带了 `http_proxy=http://127.0.0.1:62201`,之后那个代理
进程退出了,而这个服务进程还活着 —— 于是每一个请求都先撞死在死代理上,
现象看起来跟断网一模一样,报错却只会说"请检查网络",根本没法下手。

所以本模块负责三件事:

1. **自愈** —— 启动时探测环境里的代理是否真的可用,不可用就从进程环境里摘掉,
   让请求回到直连(``sanitize_env``)。
2. **降级** —— 给上层一份"候选通路"清单(直连 / 环境代理 / 系统代理),
   某条路走不通时自动换下一条(``candidate_routes``)。
3. **说人话** —— 输出一份能看懂的诊断报告,一眼知道卡在 DNS、TCP、
   TLS、还是引擎那一层(``diagnose``)。

这里刻意只用标准库做探测(``http.client`` 的 CONNECT 隧道),
好处是能精确区分"直连"与"经代理",不受 aiohttp 环境变量读取行为的干扰。
"""

from __future__ import annotations

import http.client
import os
import socket
import ssl
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

try:                                    # 端点常量直接复用 edge-tts 的,避免过期
    from edge_tts import constants as _et_const
except Exception:                       # noqa: BLE001
    _et_const = None

SPEECH_HOST = "speech.platform.bing.com"
_TOKEN = getattr(_et_const, "TRUSTED_CLIENT_TOKEN", "") or "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
SPEECH_PATH = (
    "/consumer/speech/synthesize/readaloud/voices/list?trustedclienttoken=" + _TOKEN
)

PROXY_ENV_KEYS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
SKIP_PROXY_KEYS = ("no_proxy", "NO_PROXY")

__all__ = [
    "SPEECH_HOST",
    "env_proxies",
    "system_proxy",
    "norm_proxy",
    "probe_direct",
    "probe_proxy",
    "proxy_alive",
    "sanitize_env",
    "candidate_routes",
    "effective_key",
    "diagnose",
    "render_report",
    "quick_hint",
]


# ---------------------------------------------------------------------------
# 代理来源
# ---------------------------------------------------------------------------
def norm_proxy(url: str) -> str:
    """把各种写法归一成 http://host:port。"""
    url = (url or "").strip().strip('"').strip("'")
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


def env_proxies() -> Dict[str, str]:
    """进程环境里的代理变量（保留原始大小写形式，便于精确删除）。"""
    found: Dict[str, str] = {}
    for key in PROXY_ENV_KEYS:
        val = os.environ.get(key)
        if val and val.strip():
            found[key] = val.strip()
    # 只看 https/all —— edge-tts 的请求都是 https,http_proxy 通常不参与
    return found


def system_proxy() -> str:
    """读 Windows 系统代理设置（PAC 除外）。非 Windows 直接返回空。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            try:
                enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except OSError:
                enable = 0
            if not enable:
                return ""
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except OSError:
                return ""
    except Exception:                    # noqa: BLE001
        return ""
    server = (server or "").strip()
    if not server:
        return ""
    # 形如 "host:port" 或 "http=host:port;https=host:port"
    if "=" in server:
        parts = {}
        for item in server.split(";"):
            if "=" in item:
                k, v = item.split("=", 1)
                parts[k.strip().lower()] = v.strip()
        server = parts.get("https") or parts.get("http") or ""
    return norm_proxy(server)


# ---------------------------------------------------------------------------
# 连通性探测
# ---------------------------------------------------------------------------
def tcp_ping(host: str, port: int = 443, timeout: float = 5.0) -> dict:
    """DNS 解析 + TCP 握手，分开报告，便于判断是不是 DNS 的锅。"""
    t0 = time.time()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        dns_ms = (time.time() - t0) * 1000
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "stage": "dns", "error": f"{type(exc).__name__}: {exc}",
                "ips": [], "ms": round((time.time() - t0) * 1000, 1)}

    t1 = time.time()
    try:
        with socket.create_connection((ips[0], port), timeout=timeout):
            pass
        return {"ok": True, "stage": "tcp", "ips": ips,
                "dns_ms": round(dns_ms, 1),
                "tcp_ms": round((time.time() - t1) * 1000, 1)}
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "stage": "tcp", "error": f"{type(exc).__name__}: {exc}",
                "ips": ips, "dns_ms": round(dns_ms, 1),
                "tcp_ms": round((time.time() - t1) * 1000, 1)}


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def probe_direct(timeout: float = 8.0) -> dict:
    """不走任何代理，直连语音服务并请求一次音色列表。"""
    t0 = time.time()
    conn = None
    try:
        conn = http.client.HTTPSConnection(SPEECH_HOST, 443, timeout=timeout, context=_ssl_ctx())
        conn.request("GET", SPEECH_PATH, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        resp = conn.getresponse()
        body = resp.read(4096)
        return {
            "ok": resp.status == 200,
            "status": resp.status,
            "bytes": len(body),
            "ms": round((time.time() - t0) * 1000, 1),
            "error": "" if resp.status == 200 else f"HTTP {resp.status} {resp.reason}",
        }
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "status": 0, "bytes": 0,
                "ms": round((time.time() - t0) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass


def probe_proxy(proxy_url: str, timeout: float = 8.0) -> dict:
    """经 HTTP 代理（CONNECT 隧道）访问语音服务。"""
    url = norm_proxy(proxy_url)
    if not url:
        return {"ok": False, "error": "代理地址为空", "ms": 0}
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": f"不支持的代理协议:{parsed.scheme}(目前只支持 http)",
                "ms": 0}

    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    t0 = time.time()
    conn = None
    try:
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=_ssl_ctx())
        conn.set_tunnel(SPEECH_HOST, 443)
        conn.request("GET", SPEECH_PATH, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        resp = conn.getresponse()
        body = resp.read(4096)
        return {
            "ok": resp.status == 200,
            "status": resp.status,
            "bytes": len(body),
            "ms": round((time.time() - t0) * 1000, 1),
            "error": "" if resp.status == 200 else f"HTTP {resp.status} {resp.reason}",
        }
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "status": 0, "bytes": 0,
                "ms": round((time.time() - t0) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass


def proxy_alive(proxy_url: str, timeout: float = 6.0) -> bool:
    """代理是否真的能把我们带出去。"""
    return bool(probe_proxy(proxy_url, timeout=timeout).get("ok"))


# ---------------------------------------------------------------------------
# 自愈：摘掉失效的代理
# ---------------------------------------------------------------------------
# 被摘掉的代理留个底：万一它只是临时抽风，后面还能作为最后备选
_REMOVED_PROXIES: List[str] = []


def sanitize_env(verbose: bool = False) -> dict:
    """检查进程环境里的代理变量，失效的直接摘掉。

    返回 {"removed": {...}, "kept": {...}, "notes": [...]}。

    注意只动**进程自己的** ``os.environ``，不碰系统设置 —— 目的是让本进程
    不去继承一个已经死掉的出口，而不是替用户改机器。
    """
    removed: Dict[str, str] = {}
    kept: Dict[str, str] = {}
    notes: List[str] = []

    for key, val in env_proxies().items():
        if not val:
            continue
        # 代理本身都连不上，就没必要留着
        if proxy_alive(val, timeout=4.0):
            kept[key] = val
        else:
            removed[key] = val
            os.environ.pop(key, None)
            if val not in _REMOVED_PROXIES:
                _REMOVED_PROXIES.append(val)
            notes.append(f"环境变量 {key}={val} 已失效，已从本进程环境中移除（改走直连）")

    if verbose and removed:
        for n in notes:
            print(f"[netcheck] {n}", flush=True)

    return {"removed": removed, "kept": kept, "notes": notes}


# ---------------------------------------------------------------------------
# 候选通路
# ---------------------------------------------------------------------------
def candidate_routes(include_env_proxy: bool = True) -> List[dict]:
    """按"最可能成功"排序的候选通路。

    第一个永远是"默认通路"（不带显式代理；它到底走直连还是走环境代理，
    由 aiohttp 按环境变量决定）。后面的用于失败后切换。
    """
    routes: List[dict] = [{"label": "默认通路", "proxy": None}]

    if include_env_proxy:
        for key, val in env_proxies().items():
            routes.append({"label": f"环境代理 {key}", "proxy": norm_proxy(val)})

    sys_p = system_proxy()
    if sys_p:
        routes.append({"label": "系统代理", "proxy": sys_p})

    # 曾经失效、已被摘掉的代理放到最后：万一它只是临时抽风
    for url in _REMOVED_PROXIES:
        routes.append({"label": f"已失效代理（复测）{url}", "proxy": url})

    # 去重（按 proxy 值），保持顺序
    seen = set()
    uniq: List[dict] = []
    for r in routes:
        key = r["proxy"] or ""
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def effective_key(proxy: Optional[str]) -> str:
    """描述"这一轮实际上是从哪出去的"，用于判断某条路是不是已经试过。

    显式给了代理就用它；没给时，如果环境里还留着代理，说明 aiohttp 会用它。
    """
    if proxy:
        return "proxy:" + norm_proxy(proxy)
    envp = env_proxies()
    if envp:
        return "env:" + norm_proxy(next(iter(envp.values())))
    return "direct"


# ---------------------------------------------------------------------------
# 诊断报告
# ---------------------------------------------------------------------------
def diagnose(with_engine: bool = True, voice: Optional[str] = None) -> dict:
    """完整诊断：环境 → DNS → TCP → HTTPS → 引擎合成。"""
    t0 = time.time()
    report: dict = {
        "ok": False,
        "verdict": "",
        "hints": [],
        "env": {},
        "proxies": {},
        "network": {},
        "engine": {},
    }

    # --- 环境 ---
    report["env"] = {
        "python": __import__("sys").version.split()[0],
        "platform": __import__("platform").platform(),
        "proxy_env": env_proxies(),
        "no_proxy": {k: os.environ.get(k) for k in SKIP_PROXY_KEYS if os.environ.get(k)},
    }
    try:
        import edge_tts
        report["env"]["edge_tts"] = getattr(edge_tts, "__version__", "unknown")
    except Exception as exc:                       # noqa: BLE001
        report["env"]["edge_tts"] = f"导入失败: {exc}"

    # --- 网络分层 ---
    ping = tcp_ping(SPEECH_HOST, 443, timeout=6.0)
    report["network"]["dns_tcp"] = ping
    if ping.get("ok"):
        report["network"]["https_direct"] = probe_direct(timeout=10.0)

    # --- 代理 ---
    proxy_checks = {}
    for label, url in [("系统代理", system_proxy())] + \
                      [(k, v) for k, v in env_proxies().items()]:
        if url and label not in proxy_checks:
            proxy_checks[label] = {"url": norm_proxy(url), **probe_proxy(url, timeout=8.0)}
    report["proxies"] = proxy_checks

    # --- 引擎（真实合成一小段）---
    if with_engine:
        try:
            import asyncio
            import edge_tts
            from . import catalog as _catalog
            v = voice or _catalog.DEFAULT_VOICE
            te = time.time()
            data_bytes = {"n": 0}

            async def _run():
                c = edge_tts.Communicate("诊断测试。", v)
                async for chunk in c.stream():
                    if chunk.get("type") == "audio" and chunk.get("data"):
                        data_bytes["n"] += len(chunk["data"])

            asyncio.run(asyncio.wait_for(_run(), timeout=45))
            report["engine"] = {
                "ok": data_bytes["n"] > 0,
                "bytes": data_bytes["n"],
                "ms": round((time.time() - te) * 1000, 1),
                "voice": v,
            }
        except Exception as exc:                   # noqa: BLE001
            report["engine"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "voice": voice}

    # --- 结论 ---
    eng_ok = bool(report.get("engine", {}).get("ok"))
    direct_ok = bool(report["network"].get("https_direct", {}).get("ok"))
    any_proxy_ok = any(p.get("ok") for p in proxy_checks.values())

    report["network"]["routes"] = candidate_routes()

    if eng_ok:
        report["ok"] = True
        report["verdict"] = "引擎工作正常，语音服务可达。"
    elif not with_engine and (direct_ok or any_proxy_ok):
        # 只做了网络层检查，结论就别延伸到合成环节
        report["ok"] = True
        report["verdict"] = "网络层连通正常（本次未做合成测试）。"
    elif not ping.get("ok"):
        stage = ping.get("stage")
        if stage == "dns":
            report["verdict"] = "域名解析失败：这台机器解析不了语音服务域名。"
            report["hints"] = [
                "检查 DNS 设置，或把 DNS 临时改成 223.5.5.5 / 119.29.29.29。",
                "部分公司网络或安全软件会拦截该域名。",
            ]
        else:
            report["verdict"] = "TCP 连不上语音服务（443 端口不通）。"
            report["hints"] = [
                "可能是防火墙 / 代理软件 / 上网加速器拦截了连接。",
                "先关掉正在运行的代理或加速软件再试。",
            ]
    elif not direct_ok and any_proxy_ok:
        report["verdict"] = "直连不通，但代理可用 —— 需要走代理才能合成。"
        report["hints"] = [
            '启动服务前先设置代理，例如：set HTTPS_PROXY=http://127.0.0.1:端口 && start.bat',
            "服务启动时会自动检测并使用可用的环境代理。",
        ]
    elif direct_ok:
        report["verdict"] = "网络能连通语音服务，但 WebSocket 合成失败。"
        report["hints"] = [
            "多半是 edge-tts 版本过旧被服务端拒绝，执行：pip install -U edge-tts",
            "校对系统时间：误差超过几分钟会导致鉴权失败。",
        ]
    else:
        report["verdict"] = "网络不通，且没有可用的代理。"
        report["hints"] = [
            "确认这台机器能正常上网。",
            "如果平时需要代理上网，请先设置 HTTPS_PROXY 再启动本服务。",
        ]

    report["elapsed_ms"] = round((time.time() - t0) * 1000, 1)
    return report


def render_report(rep: dict, title: str = "文字转 MP3 · 网络诊断") -> str:
    """把诊断结果渲染成人能直接读的纯文本（命令行与网页共用这一份）。"""
    W = 66
    line = "=" * W
    out: list = []

    def mark(ok) -> str:
        if ok is None:
            return "·"
        return "OK  " if ok else "失败"

    out.append(line)
    out.append("  " + title)
    out.append("  时间 " + time.strftime("%Y-%m-%d %H:%M:%S"))
    out.append(line)
    out.append("")

    env = rep.get("env") or {}
    out.append("【运行环境】")
    out.append(f"  Python          {env.get('python', '?')}")
    out.append(f"  edge-tts        {env.get('edge_tts', '?')}")
    out.append(f"  系统平台        {env.get('platform', '?')}")

    proxy_env = env.get("proxy_env") or {}
    if proxy_env:
        for k, v in proxy_env.items():
            alive = proxy_alive(v, timeout=4.0)
            out.append(f"  环境变量 {k:<12} {v}   {'可用' if alive else '**不可用**'}")
    else:
        out.append("  环境代理        未设置（正常）")
    out.append(f"  系统代理        {system_proxy() or '未启用（正常）'}")
    out.append("")

    net = rep.get("network") or {}
    ping = net.get("dns_tcp") or {}
    out.append("【网络连通】")
    if ping.get("ok"):
        ips = ", ".join((ping.get("ips") or [])[:2])
        out.append(f"  域名解析        {mark(True)}  {ips}  ({ping.get('dns_ms', 0):.0f} ms)")
        out.append(f"  TCP 443         {mark(True)}  ({ping.get('tcp_ms', 0):.0f} ms)")
    else:
        stage = "域名解析" if ping.get("stage") == "dns" else "TCP 连接"
        out.append(f"  {stage:<13} {mark(False)}  {ping.get('error', '')}")

    direct = net.get("https_direct")
    if direct is not None:
        extra = f"  {direct.get('error')}" if direct.get("error") else ""
        out.append(f"  直连语音服务    {mark(direct.get('ok'))}  HTTP {direct.get('status')}"
                   f"  ({direct.get('ms', 0):.0f} ms){extra}")

    for label, info in (rep.get("proxies") or {}).items():
        extra = f"  {info.get('error')}" if info.get("error") else ""
        out.append(f"  {label:<13} {mark(info.get('ok'))}  {info.get('url', '')}{extra}")
    out.append("")

    eng = rep.get("engine") or {}
    out.append("【语音合成（真实试合成一小段）】")
    if eng.get("ok"):
        out.append(f"  合成测试        {mark(True)}  {eng.get('bytes', 0)} 字节  "
                   f"({eng.get('ms', 0) / 1000:.2f} s)  音色 {eng.get('voice')}")
    else:
        out.append(f"  合成测试        {mark(False)}")
        out.append(f"    错误          {eng.get('error', '未知')}")
    out.append("")

    out.append("【结论】")
    out.append("  " + (rep.get("verdict") or ""))
    hints = rep.get("hints") or []
    if hints:
        out.append("")
        out.append("【建议】")
        for h in hints:
            out.append("  · " + h)
    out.append("")
    out.append(line)
    out.append(f"  总耗时 {(rep.get('elapsed_ms') or 0) / 1000:.1f} 秒")
    out.append(line)
    return "\n".join(out)


def quick_hint(limit: int = 2) -> str:
    """给失败信息配一句"人话原因"，避免只甩一句"请检查网络"。"""
    ping = tcp_ping(SPEECH_HOST, 443, timeout=4.0)
    if not ping.get("ok"):
        if ping.get("stage") == "dns":
            return "网络诊断：域名解析失败（DNS 不可用或被拦截）"
        return "网络诊断：连不上语音服务（可能被防火墙或代理软件拦截）"

    direct = probe_direct(timeout=6.0)
    if direct.get("ok"):
        # 网络明明是通的，就别再甩"请检查网络"了。按实测概率给排查顺序：
        # 最常见的真凶是音色被服务端下架（不报错、只是没有音频），其次才是版本/时间。
        return ("网络诊断：网络连通正常，所以多半不是网络问题 —— "
                "先换一个音色（服务端会不打招呼地下架音色，用已下架的音色必然没有音频）；"
                "若换音色仍失败，再升级 edge-tts（令牌版本过期会返回 403），"
                "最后检查系统时间是否偏差过大")

    alive = []
    for label, url in [("系统代理", system_proxy())] + list(env_proxies().items()):
        if url and proxy_alive(url, timeout=4.0):
            alive.append(label)
    if alive:
        return f"网络诊断：直连被阻断，但 {'/'.join(alive)} 可用 —— 请设置代理后重启服务"
    return "网络诊断：直连与代理都不通，请检查出口网络"
