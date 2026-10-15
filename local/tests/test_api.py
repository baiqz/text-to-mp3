# -*- coding: utf-8 -*-
"""接口级端到端测试:对运行中的本地服务发起真实 HTTP 请求。

前置:先启动服务 python app.py --no-browser
运行:python tests/test_api.py
"""

import io
import json
import os
import sys
import time
import zipfile

import requests
from mutagen.mp3 import MP3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.environ.get("TTSMP3_BASE", "http://127.0.0.1:5300")
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""),
          flush=True)


def section(t):
    print("\n" + "=" * 66)
    print(t)
    print("=" * 66, flush=True)


def wait_job(jid, timeout=300):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        r = requests.get(f"{BASE}/api/jobs/{jid}", timeout=10).json()
        last = r.get("job") or last
        if last and last["status"] in ("done", "error", "canceled"):
            return last
        time.sleep(0.6)
    return last


def make_docx(text_paras):
    """手工构造一个最小 docx(不依赖 python-docx)。"""
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in text_paras)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


# ---------------------------------------------------------------------------
section("0. 服务连通")
# ---------------------------------------------------------------------------
try:
    h = requests.get(f"{BASE}/api/health", timeout=5).json()
except Exception as exc:
    print("  服务没有响应,请先启动 python app.py --no-browser")
    print("  " + str(exc))
    sys.exit(2)
check("健康检查通过", h.get("ok") and h.get("service") == "text-to-mp3", h)
check("引擎版本已上报", bool(h.get("engine", {}).get("edge_tts")), h.get("engine"))

# ---------------------------------------------------------------------------
section("1. 音色接口")
# ---------------------------------------------------------------------------
v = requests.get(f"{BASE}/api/voices", timeout=20).json()
check("返回精选音色", len(v["curated"]) >= 30, len(v["curated"]))
check("默认音色为晓晓", v["default"] == "zh-CN-XiaoxiaoNeural", v["default"])
check("包含全部语音", v["total"] > 200, v["total"])
check("精选音色都带中文名", all(x["name"] and x["gender"] for x in v["curated"]))
check("动态音色不与精选重复",
      not (set(x["id"] for x in v["all"]) & set(x["id"] for x in v["curated"])))

# ---------------------------------------------------------------------------
section("2. 文本分析接口")
# ---------------------------------------------------------------------------
text = ("这是第一段。\n\n" + "第二段稍微长一些，用来看看分块结果。" * 30)
a = requests.post(f"{BASE}/api/analyze", json={"text": text, "chunk_chars": 200}, timeout=20).json()
check("字数统计正确", a["chars"] == len(text), (a["chars"], len(text)))
check("预估时长可读", isinstance(a["est_text"], str) and a["est_seconds"] > 0, a["est_text"])
check("分块数合理", a["chunks"] >= 2, a["chunks"])
a2 = requests.post(f"{BASE}/api/analyze", json={"text": "", "chunk_chars": 200}, timeout=20).json()
check("空文本分析不报错", a2["ok"] and a2["chars"] == 0, a2)

# ---------------------------------------------------------------------------
section("3. 创建任务 → SSE 进度 → 下载")
# ---------------------------------------------------------------------------
payload = {
    "text": "这是接口测试用的文本。第二句话用来验证拼接与时长统计。",
    "voice": "zh-CN-XiaoxiaoNeural", "rate": 0, "volume": 0, "pitch": 0,
    "title": "接口测试", "chunk_chars": 200, "concurrency": 2,
}
r = requests.post(f"{BASE}/api/tts", json=payload, timeout=20).json()
check("任务创建成功", r["ok"] and r["job"]["id"], r.get("error"))
jid = r["job"]["id"]

# SSE:读前若干个事件
events = []
try:
    with requests.get(f"{BASE}/api/jobs/{jid}/events", stream=True, timeout=120) as resp:
        check("SSE 响应头正确",
              resp.headers.get("Content-Type", "").startswith("text/event-stream"),
              resp.headers.get("Content-Type"))
        for raw in resp.iter_lines(decode_unicode=True):
            if raw and raw.startswith("data:"):
                events.append(json.loads(raw[5:].strip()))
            if len(events) >= 3:
                break
except Exception as exc:
    check("SSE 可读", False, exc)
check("SSE 事件是合法 JSON 快照", len(events) >= 1 and "status" in events[0], events[:1])
check("SSE 能反映进行中状态", any(e["status"] in ("pending", "running", "done") for e in events),
      [e["status"] for e in events])

snap = wait_job(jid)
check("任务完成", snap["status"] == "done", snap["status"])
check("有产出文件", snap["has_audio"], snap["filename"])
check("文件名含标题", "接口测试" in snap["filename"], snap["filename"])

# 在线试听(支持 Range)
with requests.get(f"{BASE}/api/jobs/{jid}/audio", stream=True, timeout=30) as resp:
    check("试听接口返回 audio/mpeg", resp.headers.get("Content-Type") == "audio/mpeg",
          resp.headers.get("Content-Type"))
    check("试听接口支持 Range(可拖动进度条)",
          requests.get(f"{BASE}/api/jobs/{jid}/audio",
                       headers={"Range": "bytes=0-1023"}, timeout=30).status_code in (206, 200))

# 下载
d = requests.get(f"{BASE}/api/jobs/{jid}/download", timeout=60)
check("下载返回 200", d.status_code == 200, d.status_code)
check("下载内容非空", len(d.content) > 5000, len(d.content))
check("下载为有效 MP3", d.content[:2] == b"\xff\xf3" or d.content[:3] == b"ID3",
      d.content[:4].hex())
tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dl_test.mp3")
with open(tmp, "wb") as f:
    f.write(d.content)
m = MP3(tmp)
check("下载文件 mutagen 认可", m.info.length > 3, round(m.info.length, 3))
check("时长与任务记录一致", abs(m.info.length - snap["duration"]) < 0.3,
      (round(m.info.length, 3), snap["duration"]))
check("ID3 标题正确", str((m.tags or {}).get("TIT2")) == "接口测试", (m.tags or {}).get("TIT2"))
os.remove(tmp)

# ---------------------------------------------------------------------------
section("4. 多段长文本(服务端分块)")
# ---------------------------------------------------------------------------
long_text = "\n\n".join(f"第{i}段内容，用来验证分块顺序是否与原文一致。" for i in range(1, 16))
r = requests.post(f"{BASE}/api/tts", json={
    "text": long_text, "chunk_chars": 200, "concurrency": 3, "title": "多变段测试",
}, timeout=20).json()
jid2 = r["job"]["id"]
check("多段任务分块数正确", r["job"]["chunks_total"] >= 2, r["job"]["chunks_total"])
snap2 = wait_job(jid2)
check("多段任务完成", snap2["status"] == "done", snap2["status"])
check("所有分段成功", snap2["chunks_done"] == snap2["chunks_total"]
      and not snap2["failures"], (snap2["chunks_done"], snap2["failures"]))

# ---------------------------------------------------------------------------
section("5. 取消")
# ---------------------------------------------------------------------------
big = "\n\n".join(f"第{i}段取消测试文本，内容需要足够长才能中途取消。" * 8 for i in range(1, 60))
r = requests.post(f"{BASE}/api/tts", json={"text": big, "chunk_chars": 200}, timeout=20).json()
jid3 = r["job"]["id"]
time.sleep(2.5)
c = requests.post(f"{BASE}/api/jobs/{jid3}/cancel", timeout=10).json()
check("取消请求被接受", c["ok"], c)
snap3 = wait_job(jid3, timeout=120)
check("任务状态为已取消", snap3["status"] == "canceled", snap3["status"])
check("取消后没有产出", not snap3["has_audio"], snap3["filename"])

# ---------------------------------------------------------------------------
section("6. 上传文件提取")
# ---------------------------------------------------------------------------
txt = "这是从 txt 文件里读出来的文字。\n第二行内容。\n\n第三段。"
res = requests.post(f"{BASE}/api/extract",
                    files={"file": ("测试文本.txt", txt.encode("utf-8"), "text/plain")},
                    timeout=30).json()
check("txt 提取成功", res["ok"] and "第二行内容" in res["text"], res.get("error"))
check("txt 字数正确", res["chars"] == len(txt), (res.get("chars"), len(txt)))

gbk_txt = "GBK 编码的中文文件内容测试。".encode("gb18030")
res = requests.post(f"{BASE}/api/extract",
                    files={"file": ("gbk文本.txt", gbk_txt, "text/plain")}, timeout=30).json()
check("GBK 编码能正确识别", res["ok"] and "GBK 编码" in res["text"], res.get("error"))

docx_bytes = make_docx(["第一段 docx 内容。", "第二段 docx 内容，带数字 123。"])
res = requests.post(f"{BASE}/api/extract",
                    files={"file": ("文档.docx", docx_bytes,
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                    timeout=30).json()
check("docx 提取成功", res["ok"], res.get("error"))
check("docx 段落都在", res["ok"] and "第一段" in res["text"] and "123" in res["text"],
      (res.get("text") or "")[:60])
check("docx 段落间保留换行", res["ok"] and "\n" in res["text"], repr((res.get("text") or "")[:40]))

res = requests.post(f"{BASE}/api/extract",
                    files={"file": ("坏文件.pdf", b"%PDF-1.4 broken", "application/pdf")},
                    timeout=30).json()
check("不支持格式有友好提示", (not res["ok"]) and "PDF" in res["error"], res.get("error"))

# ---------------------------------------------------------------------------
section("7. 历史记录")
# ---------------------------------------------------------------------------
h = requests.get(f"{BASE}/api/history", timeout=20).json()
check("历史列表可读", h["ok"] and isinstance(h["files"], list), len(h.get("files", [])))
check("历史里有刚生成的文件", any(f["name"] == snap["filename"] for f in h["files"]),
      snap["filename"])
if h["files"]:
    n0 = h["files"][0]["name"]
    rr = requests.get(f"{BASE}/api/history/{n0}?inline=1", timeout=30)
    check("历史文件可试听", rr.status_code == 200, rr.status_code)

# ---------------------------------------------------------------------------
section("8. 异常与边界")
# ---------------------------------------------------------------------------
r = requests.post(f"{BASE}/api/tts", json={"text": "   "}, timeout=10)
check("空文本返回 400", r.status_code == 400, r.status_code)
r = requests.get(f"{BASE}/api/jobs/deadbeef0000", timeout=10)
check("不存在的任务返回 404", r.status_code == 404, r.status_code)
r = requests.post(f"{BASE}/api/tts",
                  json={"text": "测试非法音色会自动回退。", "voice": "not-a-voice"},
                  timeout=20).json()
check("非法音色自动回退到默认", r["ok"] and r["job"]["options"]["voice"] == "zh-CN-XiaoxiaoNeural",
      r.get("job", {}).get("options"))
check("极端参数被裁剪",
      requests.post(f"{BASE}/api/tts", json={"text": "参数裁剪测试。", "rate": 99999,
                                            "chunk_chars": 1}, timeout=20).json()["ok"])

# ---------------------------------------------------------------------------
print("\n" + "=" * 66)
print(f"通过 {len(PASS)} 项, 失败 {len(FAIL)} 项")
for f in FAIL:
    print("  - " + f)
print("=" * 66)
sys.exit(1 if FAIL else 0)
