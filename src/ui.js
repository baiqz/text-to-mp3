/* 界面逻辑：把引擎、切分器、声库串成一个可用的页面 */

(function () {
  const $ = (id) => document.getElementById(id);
  const LS_KEY = "tts-web-settings-v1";

  const el = {
    count: $("voice-count"), search: $("voice-search"), list: $("voices"),
    text: $("text"), stats: $("stats"), drop: $("drop"), file: $("file"),
    rate: $("rate"), volume: $("volume"), pitch: $("pitch"),
    chunk: $("chunk"), conc: $("conc"),
    bar: $("bar"), ptext: $("progress-text"),
    go: $("btn-go"), cancel: $("btn-cancel"),
    result: $("result"), player: $("player"), rstats: $("result-stats"),
    rnote: $("result-note"),
    log: $("log"), logCount: $("log-count"), logPanel: $("log-panel"),
  };

  const state = {
    voice: VOICES.defaultVoice,
    running: false,
    controller: null,
    lastBlob: null,
    lastUrl: null,
    logs: 0,
  };

  /* ---------- 设置持久化 ---------- */
  function loadSettings() {
    try {
      const s = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
      if (s.voice && VOICES.voices.some((v) => v.id === s.voice)) state.voice = s.voice;
      for (const k of ["rate", "volume", "pitch", "chunk", "conc"]) {
        if (s[k] !== undefined) el[k].value = s[k];
      }
    } catch { /* 忽略损坏的存档 */ }
  }

  function saveSettings() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        voice: state.voice,
        rate: el.rate.value, volume: el.volume.value, pitch: el.pitch.value,
        chunk: el.chunk.value, conc: el.conc.value,
      }));
    } catch { /* 无痕模式下写不了，忽略 */ }
  }

  /* ---------- 声库 ---------- */
  const zhFirst = (v) => (v.locale.startsWith("zh") ? 0 : 1);

  function renderVoices(keyword = "") {
    const kw = keyword.trim().toLowerCase();
    const items = VOICES.voices.filter((v) => {
      if (v.dead) return false;
      if (!kw) return true;
      return (v.id + " " + v.name + " " + v.localeName + " " + v.locale + " " +
        (v.gender === "Female" ? "女声 female" : "男声 male")).toLowerCase().includes(kw);
    }).sort((a, b) => zhFirst(a) - zhFirst(b) || a.name.localeCompare(b.name, "zh"));

    const max = kw ? 60 : 26;
    el.list.replaceChildren();
    for (const v of items.slice(0, max)) {
      const row = document.createElement("div");
      row.className = "voice" + (v.id === state.voice ? " on" : "");
      row.title = v.id;
      const name = document.createElement("span");
      name.className = "vname";
      name.textContent = v.name;
      const meta = document.createElement("span");
      meta.className = "vmeta";
      meta.textContent = `${v.localeName} · ${v.gender === "Female" ? "女声" : "男声"}`;
      row.append(name, meta);
      row.addEventListener("click", () => {
        state.voice = v.id;
        saveSettings();
        renderVoices(el.search.value);
      });
      el.list.append(row);
    }
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "voice";
      empty.textContent = "没有匹配的音色";
      el.list.append(empty);
    } else if (items.length > max) {
      const more = document.createElement("div");
      more.className = "voice";
      more.style.color = "var(--dim)";
      more.textContent = `…还有 ${items.length - max} 个，继续输入可缩小范围`;
      el.list.append(more);
    }
  }

  /* ---------- 文本统计 ---------- */
  function currentChunkChars() { return Number(el.chunk.value); }

  function refreshStats() {
    const text = el.text.value;
    if (!text.trim()) {
      el.stats.textContent = "";
      el.stats.innerHTML = "<span>0 字</span><span>·</span><span>0 段</span><span>·</span><span>预计 0 秒</span>";
      return;
    }
    const a = analyze(text);
    const chunks = splitText(text, currentChunkChars()).length;
    el.stats.innerHTML =
      `<span>${a.chars.toLocaleString()} 字</span><span>·</span>` +
      `<span>${chunks} 段</span><span>·</span>` +
      `<span>预计 ${formatDuration(a.estSeconds)}</span>` +
      (a.cjk ? `<span>·</span><span>中文 ${a.cjk.toLocaleString()}</span>` : "");
  }

  /* ---------- 日志 ---------- */
  function log(text, bad = false) {
    state.logs += 1;
    el.logCount.textContent = `(${state.logs})`;
    const line = document.createElement("div");
    if (bad) line.className = "bad";
    line.textContent = `${new Date().toLocaleTimeString("zh-CN", { hour12: false })}  ${text}`;
    el.log.append(line);
    el.log.scrollTop = el.log.scrollHeight;
  }

  function setProgress(percent, text) {
    el.bar.style.width = `${percent}%`;
    el.ptext.textContent = text;
  }

  function showNote(target, text, kind) {
    target.className = `note ${kind}`;
    target.textContent = text;
    target.hidden = false;
  }

  /* ---------- 转换 ---------- */
  function filename() {
    const first = (el.text.value.trim().split("\n")[0] || "语音").replace(/[\\/:*?"<>|]/g, "").slice(0, 24);
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    const ts = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
    return `${first}_${ts}.mp3`;
  }

  function releaseResult() {
    if (state.lastUrl) URL.revokeObjectURL(state.lastUrl);
    state.lastUrl = null;
    state.lastBlob = null;
  }

  async function start() {
    const text = el.text.value;
    if (!text.trim()) { showNote(el.rnote, "请先输入要朗读的文字。", "warn"); el.result.hidden = false; return; }

    state.running = true;
    state.controller = new AbortController();
    el.go.disabled = true;
    el.cancel.hidden = false;
    el.result.hidden = true;
    el.rnote.hidden = true;
    releaseResult();
    setProgress(0, "正在切分文本…");
    log(`开始转换：${text.length.toLocaleString()} 字，音色 ${state.voice}`);

    const started = performance.now();
    try {
      const res = await runJob({
        text,
        options: {
          voice: state.voice,
          rate: Number(el.rate.value),
          volume: Number(el.volume.value),
          pitch: Number(el.pitch.value),
          chunkChars: currentChunkChars(),
          concurrency: Number(el.conc.value),
          stripMarkdown: true,
        },
        signal: state.controller.signal,
        onEvent: (ev) => {
          if (ev.type === "start") {
            setProgress(2, `共 ${ev.total} 段，开始合成…`);
            log(`切分为 ${ev.total} 段，合计 ${ev.chars.toLocaleString()} 字`);
          } else if (ev.type === "progress") {
            setProgress(ev.percent, `合成中 ${ev.done}/${ev.total} 段（${ev.percent}%）`);
          } else if (ev.type === "log") {
            log(ev.text, ev.bad);
          } else if (ev.type === "voiceSwitched") {
            state.voice = ev.voice;
            saveSettings();
            renderVoices(el.search.value);
            showNote(el.rnote, `原音色不可用（${ev.reason}），已自动改用「${ev.voice}」。`, "warn");
            el.result.hidden = false;
          } else if (ev.type === "joining") {
            setProgress(99, "正在拼接音频…");
            if (ev.failed) log(`${ev.failed} 段失败，将只在成品中保留成功的部分`, true);
          }
        },
      });

      const elapsed = (performance.now() - started) / 1000;
      setProgress(100, `完成 · 耗时 ${elapsed.toFixed(1)} 秒`);

      state.lastBlob = new Blob([res.bytes], { type: "audio/mpeg" });
      state.lastUrl = URL.createObjectURL(state.lastBlob);
      el.player.src = state.lastUrl;
      el.result.hidden = false;
      el.rstats.innerHTML =
        `<span>时长 ${formatDuration(res.duration)}</span><span>·</span>` +
        `<span>${(res.bytes.length / 1048576).toFixed(2)} MB</span><span>·</span>` +
        `<span>${res.chunks} 段</span><span>·</span>` +
        `<span>${res.frames.toLocaleString()} 帧</span><span>·</span>` +
        `<span>${res.sampleRate / 1000}kHz / ${res.bitrate / 1000}kbps</span>`;

      if (res.failed && !res.switched) {
        showNote(el.rnote, `有 ${res.failed} 段合成失败，成品中不包含这部分内容，可再点一次「开始转换」补齐。`, "warn");
      }
      log(`完成：${formatDuration(res.duration)}，${(res.bytes.length / 1048576).toFixed(2)} MB，耗时 ${elapsed.toFixed(1)} 秒`);
      if (VOICES.dead.includes(state.voice)) {
        showNote(el.rnote, `音色「${state.voice}」已下架，建议换一个。`, "err");
      }
    } catch (e) {
      if (e instanceof Canceled) {
        setProgress(0, "已取消");
        log("已取消");
      } else {
        setProgress(0, "转换失败");
        showNote(el.rnote, `转换失败：${e.message}`, "err");
        el.result.hidden = false;
        log(`失败：${e.message}`, true);
      }
    } finally {
      state.running = false;
      el.go.disabled = false;
      el.cancel.hidden = true;
      state.controller = null;
    }
  }

  /* ---------- 事件绑定 ---------- */
  function bind() {
    el.search.addEventListener("input", () => renderVoices(el.search.value));
    el.text.addEventListener("input", refreshStats);

    for (const [input, label, fmt] of [
      [el.rate, "rate-val", (v) => `${v >= 0 ? "+" : ""}${v}%`],
      [el.volume, "volume-val", (v) => `${v >= 0 ? "+" : ""}${v}%`],
      [el.pitch, "pitch-val", (v) => `${v >= 0 ? "+" : ""}${v}Hz`],
    ]) {
      const out = $(label);
      const sync = () => { out.textContent = fmt(Number(input.value)); };
      input.addEventListener("input", () => { sync(); saveSettings(); });
      sync();
    }
    for (const [input, label] of [[el.chunk, "chunk-val"], [el.conc, "conc-val"]]) {
      const out = $(label);
      const sync = () => { out.textContent = input.value; };
      input.addEventListener("input", () => { sync(); saveSettings(); refreshStats(); });
      sync();
    }

    el.go.addEventListener("click", start);
    el.cancel.addEventListener("click", () => state.controller?.abort());

    $("btn-clear").addEventListener("click", () => {
      el.text.value = "";
      refreshStats();
      el.result.hidden = true;
      releaseResult();
      setProgress(0, "准备就绪");
    });

    $("btn-sample").addEventListener("click", () => {
      el.text.value = [
        "傍晚的风从巷口吹过来，带着一点潮湿的凉意。",
        "",
        "这是城市里最容易被忽略的时刻。路灯还没亮，天色介于白天和夜晚之间，行人的脚步不急不缓。有人刚从写字楼里出来，把一天的疲惫挂在肩上；有人提着菜，正赶回家做一顿热饭。",
        "",
        "我们总以为生活是由那些重要的日子构成的——升职、搬家、告别、重逢。可真正占满生命的，恰恰是这些没有任何标记的黄昏。",
        "",
        "这段音频由「文字转 MP3」生成。它不需要安装任何软件，所有处理都在你的浏览器里完成。",
      ].join("\n");
      refreshStats();
    });

    $("btn-file").addEventListener("click", () => el.file.click());
    el.file.addEventListener("change", () => {
      const f = el.file.files?.[0];
      if (f) readFile(f);
      el.file.value = "";
    });

    const over = (on) => () => el.drop.classList.toggle("over", on);
    el.drop.addEventListener("dragover", (e) => { e.preventDefault(); over(true)(); });
    el.drop.addEventListener("dragleave", over(false));
    el.drop.addEventListener("drop", (e) => {
      e.preventDefault();
      over(false)();
      const f = e.dataTransfer?.files?.[0];
      if (f) readFile(f);
    });

    $("btn-download").addEventListener("click", () => {
      if (!state.lastBlob) return;
      const a = document.createElement("a");
      a.href = state.lastUrl;
      a.download = filename();
      a.click();
      log(`已下载 ${a.download}`);
    });

    $("btn-open").addEventListener("click", () => {
      if (state.lastUrl) window.open(state.lastUrl, "_blank");
    });

    $("btn-about").addEventListener("click", () => $("about").showModal());

    window.addEventListener("beforeunload", (e) => {
      if (state.running) { e.preventDefault(); e.returnValue = ""; }
    });
  }

  function readFile(file) {
    if (file.size > 8 * 1024 * 1024) {
      showNote(el.rnote, "文件超过 8 MB，建议改用本地版处理。", "warn");
      el.result.hidden = false;
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      el.text.value = String(reader.result || "");
      refreshStats();
      log(`已读入文件 ${file.name}（${(file.size / 1024).toFixed(1)} KB）`);
    };
    reader.onerror = () => log(`文件读取失败：${file.name}`, true);
    reader.readAsText(file, "utf-8");
  }

  /* ---------- 启动 ---------- */
  loadSettings();
  renderVoices();
  refreshStats();
  bind();
  el.count.textContent =
    `${VOICES.voices.filter((v) => !v.dead).length} 个音色 · 中文 ${VOICES.voices.filter((v) => !v.dead && v.locale.startsWith("zh")).length} 个`;
  log(`声库已载入（${VOICES.voices.length} 个，实测于 ${VOICES.testedAt}）`);
  window.__uiReady = true;
})();
