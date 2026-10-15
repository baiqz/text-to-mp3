# -*- coding: utf-8 -*-
"""
文字转 MP3 —— 本地服务
======================

界面:      http://127.0.0.1:5300
接口:
  GET  /api/voices                  音色清单(精选 + 全量)
  POST /api/analyze                 文本统计(字数/预估时长/将分几块)
  POST /api/tts                     创建转换任务(不限文本长度)
  GET  /api/jobs/<id>               任务状态快照
  GET  /api/jobs/<id>/events        任务进度(SSE 流)
  GET  /api/jobs/<id>/audio         在线试听
  GET  /api/jobs/<id>/download      下载 MP3
  POST /api/jobs/<id>/cancel        取消任务
  GET  /api/history                 历史生成记录
  POST /api/extract                 上传 txt/md/docx 提取文字
  GET  /api/selftest                引擎自检

服务只监听本机回环地址,不对外开放。
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from ttsmp3 import catalog, engine, extract, netcheck, splitter

# 控制台代码页可能是 GBK，输出里遇到无法编码的字符时不要整程序崩掉
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")     # type: ignore[union-attr]
    except Exception:                             # noqa: BLE001
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

PORT = int(os.environ.get("TTSMP3_PORT", "5300"))
HOST = "127.0.0.1"

MAX_UPLOAD = 64 * 1024 * 1024        # 上传上限 64MB
JOB_TTL = 12 * 3600                  # 任务状态保留 12 小时

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
app.config["JSON_AS_ASCII"] = False

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _get_job(job_id: str):
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _gc_jobs() -> None:
    """清理过期任务状态(生成的文件不动)。"""
    now = time.time()
    with _JOBS_LOCK:
        stale = [k for k, v in _JOBS.items()
                 if v.finished and (v.finished_at or now) + JOB_TTL < now]
        for k in stale:
            _JOBS.pop(k, None)


def _jobs_list() -> list:
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [j.snapshot() for j in jobs[:30]]


def _history(limit: int = 60) -> list:
    """扫描 outputs 目录,列出历史生成的 MP3。"""
    items = []
    try:
        for name in os.listdir(OUTPUT_DIR):
            if not name.lower().endswith(".mp3"):
                continue
            path = os.path.join(OUTPUT_DIR, name)
            if not os.path.isfile(path):
                continue
            st = os.stat(path)
            items.append({
                "name": name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "mtime_text": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
            })
    except OSError:
        pass
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def _safe_output(name: str):
    """把请求里的文件名约束在 outputs 目录内。"""
    name = os.path.basename(name or "")
    if not name.lower().endswith(".mp3"):
        return None
    path = os.path.join(OUTPUT_DIR, name)
    if not os.path.isfile(path):
        return None
    return path


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:path>")
def static_files(path: str):
    return send_from_directory(STATIC_DIR, path)


@app.route("/favicon.ico")
def favicon():
    path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.isfile(path):
        return send_from_directory(STATIC_DIR, "favicon.ico")
    return ("", 204)


# ---------------------------------------------------------------------------
# 音色 / 分析 / 自检
# ---------------------------------------------------------------------------
@app.route("/api/voices")
def api_voices():
    data = catalog.catalog()
    curated_ids = set(catalog.CURATED_IDS)
    all_voices = [v for v in data.get("all", []) if v["id"] not in curated_ids]
    return jsonify({
        "ok": True,
        "curated": data["curated"],
        "all": all_voices,
        "default": data["default"],
        "total": len(curated_ids) + len(all_voices),
    })


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    chunk_chars = engine.Options().chunk_chars
    try:
        chunk_chars = int(payload.get("chunk_chars") or chunk_chars)
    except (TypeError, ValueError):
        pass
    chunk_chars = max(200, min(5000, chunk_chars))

    stats = splitter.analyze(text)
    chunks = splitter.split_text(text, chunk_chars,
                                 strip_markdown=bool(payload.get("strip_markdown", True)))
    return jsonify({
        "ok": True,
        "chars": stats.chars,
        "cjk": stats.cjk,
        "words": stats.words,
        "paragraphs": stats.paragraphs,
        "est_seconds": round(stats.est_seconds, 1),
        "est_text": stats.est_text,
        "chunks": len(chunks),
        "chunk_chars": chunk_chars,
    })


@app.route("/api/selftest")
def api_selftest():
    voice = request.args.get("voice") or None
    return jsonify(engine.selftest(voice))


@app.route("/api/diagnose")
def api_diagnose():
    """完整网络诊断：环境 → DNS → TCP → HTTPS → 代理 → 引擎合成。

    ``format=text`` 时直接返回人类可读报告（网页端就用这个）。
    """
    voice = request.args.get("voice") or None
    deep = request.args.get("deep", "1") != "0"
    rep = netcheck.diagnose(with_engine=deep, voice=voice)
    if request.args.get("format") == "text":
        return Response(netcheck.render_report(rep),
                        mimetype="text/plain; charset=utf-8")
    return jsonify(rep)


# ---------------------------------------------------------------------------
# 转换
# ---------------------------------------------------------------------------
@app.route("/api/tts", methods=["POST"])
def api_tts():
    _gc_jobs()
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""

    if not text.strip():
        return jsonify({"ok": False, "error": "请输入要转换的文字"}), 400

    voice = (payload.get("voice") or catalog.DEFAULT_VOICE).strip()
    if not catalog.is_valid_voice(voice):
        voice = catalog.DEFAULT_VOICE

    def num(value, lo, hi, default=0.0):
        try:
            return max(lo, min(hi, float(value)))
        except (TypeError, ValueError):
            return default

    try:
        chunk_chars = int(payload.get("chunk_chars") or 1200)
    except (TypeError, ValueError):
        chunk_chars = 1200

    try:
        concurrency = int(payload.get("concurrency") or 3)
    except (TypeError, ValueError):
        concurrency = 3

    options = engine.Options(
        voice=voice,
        rate=num(payload.get("rate"), -100, 200, 0),
        volume=num(payload.get("volume"), -100, 100, 0),
        pitch=num(payload.get("pitch"), -100, 100, 0),
        chunk_chars=max(200, min(5000, chunk_chars)),
        concurrency=max(1, min(6, concurrency)),
        title=(payload.get("title") or "").strip()[:60],
        strip_markdown=bool(payload.get("strip_markdown", True)),
        keep_partial=bool(payload.get("keep_partial", True)),
        retries=3,
    )

    try:
        job = engine.start_job(text, options, OUTPUT_DIR)
    except engine.SynthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with _JOBS_LOCK:
        _JOBS[job.id] = job
    return jsonify({"ok": True, "job": job.snapshot()})


@app.route("/api/jobs")
def api_jobs():
    return jsonify({"ok": True, "jobs": _jobs_list()})


@app.route("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = _get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404
    return jsonify({"ok": True, "job": job.snapshot()})


@app.route("/api/jobs/<job_id>/events")
def api_job_events(job_id: str):
    """SSE 进度流。客户端断开后循环自然结束。"""
    job = _get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404

    def stream():
        last_sent = 0.0
        while True:
            snap = job.snapshot()
            now = time.time()
            if now - last_sent > 0.25 or job.finished:
                payload = json.dumps(snap, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                last_sent = now
            if job.finished:
                yield "event: end\ndata: {}\n\n"
                return
            time.sleep(0.12)

    return Response(stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id: str):
    job = _get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404
    job.cancel()
    return jsonify({"ok": True, "job": job.snapshot()})


def _send(job, as_attachment: bool):
    if not job or not job.output_path or not os.path.exists(job.output_path):
        return jsonify({"ok": False, "error": "音频还没有生成"}), 404
    return send_file(
        job.output_path,
        mimetype="audio/mpeg",
        as_attachment=as_attachment,
        download_name=job.output_name,
        conditional=True,
    )


@app.route("/api/jobs/<job_id>/audio")
def api_job_audio(job_id: str):
    return _send(_get_job(job_id), as_attachment=False)


@app.route("/api/jobs/<job_id>/download")
def api_job_download(job_id: str):
    return _send(_get_job(job_id), as_attachment=True)


# ---------------------------------------------------------------------------
# 历史记录
# ---------------------------------------------------------------------------
@app.route("/api/history")
def api_history():
    return jsonify({"ok": True, "files": _history()})


@app.route("/api/history/<path:name>")
def api_history_file(name: str):
    path = _safe_output(name)
    if not path:
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    inline = request.args.get("inline") == "1"
    return send_file(path, mimetype="audio/mpeg", as_attachment=not inline,
                     download_name=os.path.basename(path), conditional=True)


@app.route("/api/history/<path:name>", methods=["DELETE"])
def api_history_delete(name: str):
    path = _safe_output(name)
    if not path:
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    try:
        os.remove(path)
    except OSError as exc:
        return jsonify({"ok": False, "error": f"删除失败:{exc}"}), 500
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 上传文件提取文字
# ---------------------------------------------------------------------------
@app.route("/api/extract", methods=["POST"])
def api_extract():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "没有收到文件"}), 400

    data = file.read()
    if not data:
        return jsonify({"ok": False, "error": "文件内容为空"}), 400
    if len(data) > MAX_UPLOAD:
        return jsonify({"ok": False, "error": "文件过大(上限 64MB)"}), 413

    try:
        text = extract.extract_text(file.filename, data)
    except extract.ExtractError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:                       # noqa: BLE001
        return jsonify({"ok": False, "error": f"解析失败:{exc}"}), 500

    if not text.strip():
        return jsonify({"ok": False, "error": "文件里没有可识别的文字"}), 400

    stats = splitter.analyze(text)
    return jsonify({
        "ok": True,
        "name": os.path.basename(file.filename),
        "text": text,
        "chars": len(text),
        "est_seconds": round(stats.est_seconds, 1),
        "est_text": stats.est_text,
    })


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "service": "text-to-mp3", "version": "2.1",
                    "pid": os.getpid(), "engine": engine.engine_info()})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """仅供本机重启流程使用：让旧实例让出端口后退出。"""
    print("[服务] 收到关闭请求，正在退出…", flush=True)

    def _die() -> None:
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()
    return jsonify({"ok": True})


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"ok": False, "error": "上传文件过大(上限 64MB)"}), 413


@app.errorhandler(500)
def server_error(_e):
    return jsonify({"ok": False, "error": "服务内部错误,请看控制台日志"}), 500


def _port_busy(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.35)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_ours(port: int) -> bool:
    """端口上跑的是不是本服务。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("service") == "text-to-mp3"
    except Exception:                              # noqa: BLE001
        return False


def _request_shutdown(port: int) -> bool:
    """请旧实例自己退出，比强杀进程干净。"""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/shutdown", method="POST",
            data=b"{}", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            return resp.status == 200
    except Exception:                              # noqa: BLE001
        return False


def _wait_port_free(port: int, timeout: float = 8.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not _port_busy(port):
            return True
        time.sleep(0.25)
    return not _port_busy(port)


def _open_browser(url: str) -> None:
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:                              # noqa: BLE001
        print(f"（自动打开浏览器失败，请手动访问 {url}）", flush=True)


def _network_line() -> str:
    """启动时的一行网络体检 —— 网络不通要当场看见，别等转换完才发现。"""
    try:
        ping = netcheck.tcp_ping(netcheck.SPEECH_HOST, 443, timeout=5.0)
        if ping.get("ok"):
            return f"语音服务可达（{ping.get('tcp_ms', 0):.0f} ms）"
        stage = "域名解析失败" if ping.get("stage") == "dns" else "连接失败"
        return f"语音服务{stage}：{str(ping.get('error', ''))[:70]}"
    except Exception as exc:                       # noqa: BLE001
        return f"网络检查异常：{exc}"


def main() -> None:
    global PORT

    # 0) 第一件事：摘掉从父环境继承来的**失效代理**。
    #    否则请求会全部撞在死代理上，现象和断网一模一样，用户根本无从下手。
    cleaned = netcheck.sanitize_env()
    for note in cleaned["notes"]:
        print(f"[启动检查] {note}", flush=True)

    reuse = "--reuse" in sys.argv or os.environ.get("TTSMP3_REUSE") == "1"

    # 1) 已经有一个本服务在跑
    if _port_busy(PORT) and _is_ours(PORT):
        url = f"http://{HOST}:{PORT}"
        if reuse:
            print(f"已有服务实例在运行，直接打开界面：{url}", flush=True)
            _open_browser(url)
            return

        print("=" * 62, flush=True)
        print(f"  检测到已在运行的服务实例（端口 {PORT}）", flush=True)
        print("  正在关闭它并启动新实例……", flush=True)
        print("  说明：旧实例可能带着失效的网络设置，这恰恰是转换失败最常见的原因。", flush=True)
        print("=" * 62, flush=True)
        _request_shutdown(PORT)
        if _wait_port_free(PORT):
            print("  旧实例已退出。", flush=True)
        else:
            print("  旧实例没能在超时内退出，将改用其它端口启动。", flush=True)

    # 2) 端口被别人占了就往后找
    while _port_busy(PORT):
        PORT += 1

    url = f"http://{HOST}:{PORT}"
    print("=" * 62, flush=True)
    print("  文字转 MP3  ·  本地服务已启动", flush=True)
    print(f"  界面地址:  {url}", flush=True)
    print(f"  输出目录:  {OUTPUT_DIR}", flush=True)
    print(f"  网络体检:  {_network_line()}", flush=True)
    print("  文本长度不设上限，自动分块合成后拼接。", flush=True)
    print("  关闭此窗口即停止服务；如提示转换失败，点界面上的「网络诊断」。", flush=True)
    print("=" * 62, flush=True)

    if "--no-browser" not in sys.argv and os.environ.get("TTSMP3_NO_BROWSER") != "1":
        import threading
        threading.Timer(1.0, lambda: _open_browser(url)).start()

    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
