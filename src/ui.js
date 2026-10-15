/* 界面逻辑：把引擎、切分器、声库串成一个可用的页面 */

(function () {
  const $ = (id) => document.getElementById(id);
  const LS_KEY = "tts-web-settings-v1";

  const el = {
    count: $("voice-count"), search: $("voice-search"), list: $("voices"),
    filters: $("voice-filters"), shown: $("voice-shown"), presets: $("presets"),
    more: $("voice-more"),
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
    filter: "all",
    limit: 26,          // 不搜索时先列这么多，可点「显示更多」继续加
  };

  /* ---------- 设置持久化 ---------- */
  const fmtSigned = (v, unit) => `${v >= 0 ? "+" : ""}${Math.round(v)}${unit}`;

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
  /** 排序：中文（含方言）→ 多语言 → 其他语言，组内按名称排 */
  const zhFirst = (v) => {
    const c = classify(v);
    return c === "zh" || c === "dialect" ? 0 : c === "multi" ? 1 : 2;
  };

  const DIALECT_LOCALES = ["zh-CN-liaoning", "zh-CN-shaanxi", "zh-HK"];
  /** 音色归类：中文 / 方言 / 多语言 / 其他语言
   *  注意：官方多语言音色的 locale 是 en-US / fr-FR 这类真实语言码，
   *  只能靠 id 里的 MultilingualNeural 识别，不能看 locale。 */
  function classify(v) {
    if (/Multilingual/i.test(v.id)) return "multi";
    if (DIALECT_LOCALES.includes(v.locale)) return "dialect";
    if (v.locale.startsWith("zh")) return "zh";
    return "other";
  }

  const aliveVoices = () => VOICES.voices.filter((v) => !v.dead);

  function visibleVoices(keyword) {
    const kw = keyword.trim().toLowerCase();
    return aliveVoices().filter((v) => {
      if (state.filter !== "all" && classify(v) !== state.filter) return false;
      if (!kw) return true;
      return (v.id + " " + v.name + " " + v.localeName + " " + v.locale + " " +
        (v.gender === "Female" ? "女声 female" : "男声 male") +
        (v.extra ? " 扩展 extra" : "") + " " + (v.note || "")).toLowerCase().includes(kw);
    }).sort((a, b) => zhFirst(a) - zhFirst(b) || a.name.localeCompare(b.name, "zh"));
  }

  function renderVoices(keyword = "") {
    const items = visibleVoices(keyword);
    const max = state.limit;

    el.list.replaceChildren();
    for (const v of items.slice(0, max)) {
      const row = document.createElement("div");
      row.className = "voice" + (v.id === state.voice ? " on" : "");
      row.title = v.note ? `${v.id}\n${v.note}` : v.id;
      const name = document.createElement("span");
      name.className = "vname";
      name.textContent = v.name;
      if (v.extra) {
        const tag = document.createElement("b");
        tag.className = "vtag";
        tag.textContent = "扩展";
        name.append(" ", tag);
      }
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
    }

    // 「显示更多」放在滚动区外面，否则藏在 26 条之后根本看不见
    const rest = items.length - Math.min(max, items.length);
    el.more.hidden = rest <= 0;
    if (rest > 0) {
      el.more.textContent = `显示更多（还有 ${rest} 个）`;
      el.more.onclick = () => {
        state.limit += 80;
        renderVoices(el.search.value);
      };
    }

    el.shown.textContent = items.length
      ? `已显示 ${Math.min(max, items.length)} / ${items.length} 个`
      : "无匹配";
  }

  /* ---------- 一键风格 ----------
   * 服务端不接受 SSML 的情感风格（mstts:express-as 会被判为非法 SSML），
   * 所以"风格"只能由「换音色 + 调语速/音调」组合出来。
   *
   * 注意：这里的数值必须是滑块步长(5)的整数倍，否则滑块会静默取整，
   * 按钮上标注的参数就和实际生效的不一致了。
   */
  const PRESETS = [
    { name: "深夜电台", voice: "zh-CN-YunjianNeural", rate: -15, volume: -5, pitch: -10 },
    { name: "纪录片解说", voice: "zh-CN-YunjianNeural", rate: -10, volume: 5, pitch: 0 },
    { name: "新闻播报", voice: "zh-CN-YunyangNeural", rate: 5, volume: 5, pitch: 0 },
    { name: "睡前故事", voice: "zh-CN-XiaoxiaoNeural", rate: -15, volume: 0, pitch: -5 },
    { name: "温柔女声", voice: "zh-CN-XiaoyiNeural", rate: -10, volume: 0, pitch: -5 },
    { name: "活力带货", voice: "zh-CN-XiaoxuanNeural", rate: 20, volume: 5, pitch: 5 },
    { name: "童声朗读", voice: "zh-CN-YunxiaNeural", rate: -5, volume: 0, pitch: 10 },
    { name: "粤语旁白", voice: "zh-HK-WanLungNeural", rate: -5, volume: 0, pitch: -5 },
  ];

  function renderPresets() {
    const known = new Set(aliveVoices().map((v) => v.id));
    el.presets.replaceChildren();
    for (const p of PRESETS) {
      if (!known.has(p.voice)) continue;              // 预设音色若被下架就自动隐藏
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = p.name;
      b.title = `音色 ${p.voice}，语速 ${p.rate >= 0 ? "+" : ""}${p.rate}%，` +
        `音量 ${p.volume >= 0 ? "+" : ""}${p.volume}%，音调 ${p.pitch >= 0 ? "+" : ""}${p.pitch}Hz`;
      b.addEventListener("click", () => applyPreset(p));
      el.presets.append(b);
    }
  }

  function applyPreset(p) {
    state.voice = p.voice;
    el.rate.value = String(p.rate);
    el.volume.value = String(p.volume);
    el.pitch.value = String(p.pitch);
    syncOutputs();
    saveSettings();
    state.limit = 26;
    renderVoices(el.search.value);
    log(`已套用风格「${p.name}」：${p.voice} · 语速 ${fmtSigned(p.rate, "%")} · 音调 ${fmtSigned(p.pitch, "Hz")}`);
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
  /** 滑块数值 → 标签文案。抽出来是为了「一键风格」也能刷新标签 */
  const OUTPUTS = [];

  function syncOutputs() {
    for (const { input, out, fmt } of OUTPUTS) out.textContent = fmt(Number(input.value));
  }

  function bind() {
    el.search.addEventListener("input", () => {
      state.limit = 26;              // 换关键词重新收起列表
      renderVoices(el.search.value);
    });
    el.text.addEventListener("input", refreshStats);

    el.filters.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-filter]");
      if (!btn) return;
      state.filter = btn.dataset.filter;
      state.limit = 26;
      for (const b of el.filters.querySelectorAll(".chip")) b.classList.toggle("on", b === btn);
      renderVoices(el.search.value);
    });

    for (const [input, label, fmt] of [
      [el.rate, "rate-val", (v) => fmtSigned(v, "%")],
      [el.volume, "volume-val", (v) => fmtSigned(v, "%")],
      [el.pitch, "pitch-val", (v) => fmtSigned(v, "Hz")],
    ]) {
      const out = $(label);
      OUTPUTS.push({ input, out, fmt });
      input.addEventListener("input", () => { syncOutputs(); saveSettings(); });
    }
    syncOutputs();

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
  renderPresets();

  const alive = aliveVoices();
  const zhList = alive.filter((v) => v.locale.startsWith("zh"));
  const extraList = alive.filter((v) => v.extra);
  el.count.textContent = `${alive.length} 个音色 · 中文 ${zhList.length} 个`;
  el.count.title =
    `官方声库 ${alive.length - extraList.length} 个` +
    (extraList.length ? ` ＋ 实测可用扩展 ${extraList.length} 个` : "") +
    `，覆盖 ${new Set(alive.map((v) => v.locale)).size} 种语言与地区。` +
    `点击「中文 / 方言 / 多语言」可按类别筛选。`;
  log(`声库已载入：共 ${alive.length} 个音色（中文 ${zhList.length} 个），官方清单实测于 ${VOICES.testedAt}`);
  if (extraList.length) {
    log(`其中 ${extraList.length} 个不在官方清单里但实测可用：${extraList.map((v) => `${v.name}(${v.id})`).join("、")}`);
  }
  window.__uiReady = true;
})();
