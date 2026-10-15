# -*- coding: utf-8 -*-
"""前端静态自检:不打开浏览器也能查出大部分低级错误。

检查项:
  1. HTML 里 <script> 的 JS 语法(node --check)
  2. JS 里引用的每个 DOM id 都能在 HTML 中找到
  3. JS 调用的事件方法(EventSource/fetch)与后端路由对得上
  4. CSS 里用到的关键选择器存在
  5. 没有残留的外链资源(必须完全离线可用)

运行: python tests/test_ui.py
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML = os.path.join(ROOT, "static", "index.html")
APP = os.path.join(ROOT, "app.py")

NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""),
          flush=True)


html = open(HTML, encoding="utf-8").read()
app_src = open(APP, encoding="utf-8").read()

print("=" * 66)
print("前端静态自检")
print("=" * 66)

# --- 1. JS 语法 ------------------------------------------------------------
scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
check("页面里有内联脚本", len(scripts) >= 1, len(scripts))
js = "\n".join(scripts)

if os.path.exists(NODE):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    tmp.write(js)
    tmp.close()
    p = subprocess.run([NODE, "--check", tmp.name], capture_output=True, text=True)
    check("JS 语法正确(node --check)", p.returncode == 0,
          (p.stderr or "").strip().splitlines()[:3])
    os.unlink(tmp.name)
else:
    print("  [skip] 没找到 node,跳过语法检查")

# --- 2. DOM id 引用 --------------------------------------------------------
defined_ids = set(re.findall(r'\bid="([^"]+)"', html))
used_ids = set(re.findall(r'\$\("([^"$]+)"\)', js))
used_ids |= set(re.findall(r'getElementById\("([^"]+)"\)', js))
missing = sorted(i for i in used_ids if i not in defined_ids)
check("所有 DOM id 都有对应元素", not missing, missing)
check("id 引用数量合理", len(used_ids) > 30, len(used_ids))

# 反向:HTML 里定义的 id 是否被用到(纯提醒,不算失败)
unused = sorted(i for i in defined_ids if i not in used_ids)
if unused:
    print(f"  [note] 未被脚本引用的 id: {unused}")

# --- 3. 接口一致性 ---------------------------------------------------------
js_endpoints = set(re.findall(r'["\'`](/api/[a-zA-Z0-9_/${}<>.\-]*)["\'`]', js))
# SSE 模板串里的路径
js_endpoints |= {e.replace("${job0.id}", "<id>").replace("${snap.id}", "<id>")
                 for e in js_endpoints}

app_routes = set(re.findall(r'@app\.route\("([^"]+)"', app_src))


def norm(p):
    p = re.sub(r"<[^>]+>", "<x>", p)
    p = re.sub(r"\$\{[^}]+\}", "<x>", p)
    return p


app_norm = {norm(r) for r in app_routes}
bad = []
for e in js_endpoints:
    e2 = norm(e)
    if e2 in app_norm:
        continue
    # 允许前缀匹配(如 /api/jobs/<x>/download)
    if any(e2.startswith(a) or a.startswith(e2) for a in app_norm):
        continue
    bad.append(e)
check("前端调用的接口后端都有", not bad, sorted(bad))
check("后端路由数量合理", len(app_routes) >= 12, len(app_routes))

# --- 4. 关键样式 -----------------------------------------------------------
for cls in (".voice", ".pfill", ".drawer", ".toast", ".panel-block", ".hist"):
    check(f"样式 {cls} 已定义", cls + "{" in html.replace(" ", "") or cls + " {" in html)

# --- 5. 离线可用 -----------------------------------------------------------
ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
check("没有外部资源依赖(可离线运行)", not ext, ext)
check("没有残留的模板占位符", "{{" not in html and "${" not in html.split("<script>")[0], )

# --- 6. 关键交互元素 -------------------------------------------------------
for need, desc in [
    ('id="text"', "文本框"),
    ('id="btn-go"', "开始按钮"),
    ('id="btn-download"', "下载按钮"),
    ('id="player"', "播放器"),
    ('id="btn-upload"', "导入文件按钮"),
    ('id="btn-history"', "历史按钮"),
]:
    check(f"存在{desc}", need in html)

check("支持 Ctrl+Enter 快捷开始", "ctrlKey" in js and "Enter" in js)
check("支持拖拽上传", "dragenter" in js and "dataTransfer" in js)
check("进度用 EventSource 实时推送", "new EventSource" in js)
check("SSE 断线有轮询兜底", "onerror" in js and "/api/jobs/" in js)

print("\n" + "=" * 66)
print(f"通过 {len(PASS)} 项, 失败 {len(FAIL)} 项")
for f in FAIL:
    print("  - " + f)
print("=" * 66)
sys.exit(1 if FAIL else 0)
