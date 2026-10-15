# -*- coding: utf-8 -*-
"""
网络通路诊断与代理自愈的测试
============================

重点覆盖这次真正踩到的坑：

* 进程继承了**已失效的代理变量** → 请求全撞死代理，现象酷似断网；
* 整批失败后**不会换通路**，用户只能看到"请检查网络"这种没法下手的话。

所以两个核心回归测试是：
  1. 失效代理必须被自动摘掉；
  2. 默认通路整批失败时，引擎必须自动改用下一条通路并补齐结果。
     第 2 条用测试替身造出第二条通路，不依赖运行环境的网络状况。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttsmp3 import engine, netcheck                 # noqa: E402

PASS = FAIL = 0
FAILED: list = []


def check(name: str, cond, extra=None) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}" + (f"  -> {extra}" if extra is not None else ""), flush=True)
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name}  -> {extra}", flush=True)


def section(title: str) -> None:
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}", flush=True)


PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


# ---------------------------------------------------------------------------
section("1. 代理地址归一化")

for raw, want in [
    ("127.0.0.1:8080", "http://127.0.0.1:8080"),
    ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
    ("  http://proxy.local:3128  ", "http://proxy.local:3128"),
    ('"http://1.2.3.4:80"', "http://1.2.3.4:80"),
    ("", ""),
]:
    got = netcheck.norm_proxy(raw)
    check(f"norm_proxy({raw!r})", got == want, f"{got!r}")


# ---------------------------------------------------------------------------
section("2. 失效的代理必须被自动摘掉（核心回归）")

FAKE = "http://127.0.0.1:59987"       # 保留端口，必然无人监听
saved = {k: os.environ.get(k) for k in PROXY_KEYS}

try:
    for k in PROXY_KEYS:
        os.environ[k] = FAKE

    check("假代理已被环境读到", FAKE in netcheck.env_proxies().values(),
          list(netcheck.env_proxies().values())[:1])
    check("假代理被判定为不可用", netcheck.proxy_alive(FAKE, timeout=2.0) is False)

    report = netcheck.sanitize_env()
    check("失效代理被记录", FAKE in report["removed"].values())
    check("失效代理已从环境移除", FAKE not in netcheck.env_proxies().values(),
          netcheck.env_proxies())
    check("给出了人话说明", any("已失效" in n for n in report["notes"]),
          report["notes"][:1])
    check("摘除后默认通路即真正的直连",
          netcheck.effective_key(None) == "direct", netcheck.effective_key(None))

    report2 = netcheck.sanitize_env()
    check("二次清洗不再重复摘除", not report2["removed"], report2["removed"])
finally:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
section("3. 候选通路：顺序、去重、标识")

routes = netcheck.candidate_routes()
check("第一条是默认通路", bool(routes) and routes[0]["proxy"] is None,
      routes[0] if routes else None)
check("通路不重复", len({r["proxy"] for r in routes}) == len(routes),
      [r["proxy"] for r in routes])
check("每条通路都有可读标签", all(r.get("label") for r in routes))

check("显式代理有独立标识",
      netcheck.effective_key("http://a:1") == "proxy:http://a:1",
      netcheck.effective_key("http://a:1"))

saved = {k: os.environ.get(k) for k in PROXY_KEYS}
try:
    for k in PROXY_KEYS:
        os.environ.pop(k, None)
    check("无代理时默认通路标识为 direct",
          netcheck.effective_key(None) == "direct", netcheck.effective_key(None))
finally:
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


# ---------------------------------------------------------------------------
section("4. 真实连通性探测（联网）")

ping = netcheck.tcp_ping(netcheck.SPEECH_HOST, 443, timeout=8.0)
check("语音服务 DNS/TCP 可达", ping.get("ok") is True, ping)
if ping.get("ok"):
    check("解析出了 IP", bool(ping.get("ips")), ping.get("ips"))
    direct = netcheck.probe_direct(timeout=12.0)
    check("直连取音色列表返回 200", direct.get("ok") is True,
          f"HTTP {direct.get('status')} {direct.get('ms')}ms")

bad = netcheck.probe_proxy("http://127.0.0.1:59987", timeout=3.0)
check("连不上的代理被如实报告", bad.get("ok") is False, str(bad.get("error"))[:60])


# ---------------------------------------------------------------------------
section("5. 诊断报告的结构与渲染")

rep = netcheck.diagnose(with_engine=False)
check("报告含结论", bool(rep.get("verdict")), rep.get("verdict"))
check("报告含环境信息", bool(rep.get("env", {}).get("python")), rep["env"].get("python"))
check("报告含网络分层", "dns_tcp" in rep.get("network", {}))
check("报告含候选通路", bool(rep["network"].get("routes")))
check("报告带耗时", isinstance(rep.get("elapsed_ms"), (int, float)), rep.get("elapsed_ms"))

text = netcheck.render_report(rep)
for keyword in ("网络诊断", "运行环境", "网络连通", "结论", "edge-tts"):
    check(f"渲染文本含「{keyword}」", keyword in text)


# ---------------------------------------------------------------------------
section("6. 引擎：默认通路整批失败后自动换通路（核心回归）")

# 用替身造出"第二条可用通路"，让这条测试不依赖本机网络环境
orig_routes = netcheck.candidate_routes
orig_alive = netcheck.proxy_alive
orig_sanitize = netcheck.sanitize_env
netcheck.candidate_routes = lambda *a, **k: [        # type: ignore[assignment]
    {"label": "默认通路", "proxy": None},
    {"label": "备用代理", "proxy": "http://127.0.0.1:59988"},
]
netcheck.proxy_alive = lambda url, timeout=4.0: True  # type: ignore[assignment]
netcheck.sanitize_env = lambda *a, **k: {"removed": {}, "kept": {}, "notes": []}  # type: ignore[assignment]

opt = engine.Options(concurrency=1, retries=1, keep_partial=True)
job = engine.Job("用于测试的文本。", opt)
job.chunks_total = 2

fake_mp3 = b"\xff\xf3" + b"\x00" * 400
seen: list = []

orig_collect = engine._collect
orig_probe = engine.mp3join.probe


async def fake_collect(text, o, proxy=None):
    seen.append(proxy)
    if len(seen) <= 2:                  # 默认通路的两段都失败
        raise RuntimeError("模拟：默认通路不可用")
    return fake_mp3                     # 换通路后成功


engine._collect = fake_collect                      # type: ignore[assignment]
engine.mp3join.probe = lambda data: {"frames": 4, "duration": 1.0}   # type: ignore[assignment]

tmpdir = tempfile.mkdtemp(prefix="ttsmp3-test-")
try:
    results = asyncio.run(engine._synth_all(job, ["第一段文字。", "第二段文字。"], tmpdir))
finally:
    engine._collect = orig_collect                  # type: ignore[assignment]
    engine.mp3join.probe = orig_probe               # type: ignore[assignment]
    netcheck.candidate_routes = orig_routes         # type: ignore[assignment]
    netcheck.proxy_alive = orig_alive               # type: ignore[assignment]
    netcheck.sanitize_env = orig_sanitize           # type: ignore[assignment]

check("最终两段都拿到了结果", all(r is not None for r in results),
      [None if r is None else r[0] for r in results])
check("确实换过通路重试", len(seen) > 2, f"合成调用 {len(seen)} 次")
check("重试时带上了备用代理",
      any(p == "http://127.0.0.1:59988" for p in seen), seen)
check("记录了真实错误样本", bool(job.error_samples), job.error_samples[:1])
check("通路字段被更新", job.route == "备用代理", job.route)
check("成功段落计数正确", job.chunks_done == 2, job.chunks_done)


# ---------------------------------------------------------------------------
section("7. 引擎：完全无法合成时，错误信息要说人话")

# 清空代理候选，让这一节跑得快（否则每条通路都要探测一遍）
saved = {k: os.environ.get(k) for k in PROXY_KEYS}
for k in PROXY_KEYS:
    os.environ.pop(k, None)

job2 = engine.Job("测试。", engine.Options(concurrency=1, retries=1, keep_partial=True))
job2.chunks_total = 1

orig_collect = engine._collect


async def always_fail(text, o, proxy=None):
    raise RuntimeError("403 Forbidden（模拟被服务端拒绝）")


engine._collect = always_fail                       # type: ignore[assignment]
try:
    outdir = tempfile.mkdtemp(prefix="ttsmp3-out-")
    engine.run_job(job2, outdir)
finally:
    engine._collect = orig_collect                  # type: ignore[assignment]
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v

check("任务被标记为失败", job2.status == "error", job2.status)
msg = job2.error or ""
check("错误里带了真实异常", "403" in msg, msg[:150])
check("不再是含糊的『请检查网络后重试』",
      "所有片段都合成失败,请检查网络后重试" not in msg, msg[:150])
check("附带了网络诊断结论", "网络诊断" in msg, msg[-90:])


# ---------------------------------------------------------------------------
print(f"\n{'=' * 66}")
print(f"通过 {PASS} 项, 失败 {FAIL} 项")
if FAILED:
    print("失败项:")
    for n in FAILED:
        print("  -", n)
print("=" * 66)
sys.exit(1 if FAIL else 0)
