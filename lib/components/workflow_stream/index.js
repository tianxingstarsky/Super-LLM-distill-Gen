/* Append real model journal events without replacing output nodes or scroll state. */
(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const ui = Object.fromEntries([
    "stream-panel", "stream-title", "stream-status", "request-tabs", "stream-meta",
    "output-reader", "reasoning-section", "reasoning-label", "reasoning-output",
    "answer-section", "answer-label", "answer-output", "stream-cursor", "stream-empty",
    "stream-note", "follow-request", "follow-bottom", "stream-view", "stream-read", "stream-raw",
    "read-section", "reading-fields", "read-cursor"
  ].map(id => [id, byId(id)]));
  const answerText = document.createTextNode("");
  const reasoningText = document.createTextNode("");
  ui["answer-output"].append(answerText);
  ui["reasoning-output"].append(reasoningText);
  const state = {context: null, rows: [], selected: null, pinned: false, followBottom: true,
    labels: {}, language: "zh", seen: new Map(), nextSeen: 0, tabs: new Map(), height: 0,
    reasoningTouched: false, programmaticScroll: null, deltaMode: false, offset: 0,
    deltaReady: false, deltaStatus: null, deltaDone: false, deltaTruncated: false,
    syncing: false, pendingAck: null, acceptedAck: null, serial: 0, deltaLegacy: false,
    lastReceipt: null, view: "read", fields: new Map(), readCache: null, decoder: null};
  const defaults = {
    zh: {title: "模型输出", active: "正在接收", completed: "接收完成", interrupted: "已中断，可重试",
      generation: "生成", jev: "评审", request: "请求", tabs: "当前节点的模型请求", reader: "模型已返回的内容",
      answer: "正文", reasoning: "推理内容", waiting: "等待首段正文…", empty: "当前节点尚未收到模型输出。",
      loading: "正在载入完整响应", syncing: "正在同步请求内容…",
      missing: "此请求已不在当前预览中。可切回最新输出。", follow_latest: "跟随最新", following_latest: "正在跟随最新",
      follow_bottom: "回到最新内容 ↓", pending_note: "实时接收的内容尚未完成校验。",
      completed_note: "响应已完整接收；样本是否合格以节点质检结果为准。",
      interrupted_note: "此请求已中断；未完成片段不能作为训练样本。",
      truncated_note: "当前预览仅展示部分响应内容。", chars: "正文", reasoning_chars: "推理", unit: "字符",
      read: "阅读", raw: "原始", view: "输出显示方式", fragment: "原始片段 · 完整接收后自动排版"},
    en: {title: "Model output", active: "Receiving", completed: "Response received", interrupted: "Interrupted · retry available",
      generation: "Generate", jev: "Review", request: "Request", tabs: "Model requests in this node", reader: "Received model output",
      answer: "Response", reasoning: "Reasoning", waiting: "Waiting for response text…", empty: "No model output for this node yet.",
      loading: "Loading the complete response", syncing: "Syncing request output…",
      missing: "This request is no longer in the preview. Follow the latest output to continue.", follow_latest: "Follow latest", following_latest: "Following latest",
      follow_bottom: "Go to latest text ↓", pending_note: "Live output has not completed validation.",
      completed_note: "The response is complete. Sample quality depends on the node checks.",
      interrupted_note: "This request was interrupted. Its unfinished text is not a training sample.",
      truncated_note: "This preview contains only part of the output.",
      chars: "Text", reasoning_chars: "Reasoning", unit: "characters", read: "Read", raw: "Raw", view: "Output view",
      fragment: "Raw fragment · formatted after full receipt"}
  };
  const aliases = {jev: "review", answer: "body", follow_bottom: "follow_output",
    truncated_note: "truncated", pending_note: "preview_note"};
  const label = key => typeof state.labels[key] === "string" ? state.labels[key] :
    typeof state.labels[aliases[key]] === "string" ? state.labels[aliases[key]] : defaults[state.language][key] || key;
  const setText = (node, value) => { if (node.textContent !== value) node.textContent = value; };
  const post = (type, data = {}) => parent.postMessage({isStreamlitMessage: true, type, ...data}, "*");
  function frameHeight() {
    const height = Math.ceil(ui["stream-panel"].getBoundingClientRect().height);
    if (height > 0 && height !== state.height) {
      state.height = height;
      post("streamlit:setFrameHeight", {height});
    }
  }
  function patchText(node, value) {
    if (node.data === value) return false;
    if (value.startsWith(node.data)) node.appendData(value.slice(node.data.length));
    else node.data = value; // A retry or corrected snapshot must never concatenate two answers.
    return true;
  }
  const fieldNames = {
    zh: {text: "正文", question: "问题", answer: "答案", reasoning: "推理内容", reasoning_content: "推理内容",
      score: "评分", scores: "各项评分", preservation: "内容保留检查", reason: "说明", rationale: "评审说明",
      explanation: "说明", instruction: "指令", input: "输入", output: "输出", messages: "对话",
      role: "角色", content: "内容", chosen: "优选回答", rejected: "对照回答", prompt: "提示词",
      accepted: "评审通过", passed: "评审通过", grounded: "有来源依据", uncertain: "待确认内容",
      removed_noise: "清除的噪声", source_faithful: "忠于来源", coverage_complete: "覆盖完整",
      numbers_preserved: "数值保留", formulas_preserved: "公式保留", correctness: "正确性", grounding: "依据",
      quality: "质量", relevance: "相关性", completeness: "完整性", safety: "安全性"},
    en: {text: "Text", question: "Question", answer: "Answer", reasoning: "Reasoning", reasoning_content: "Reasoning",
      score: "Score", scores: "Scores", preservation: "Preservation checks", reason: "Reason", rationale: "Review notes",
      explanation: "Explanation", instruction: "Instruction", input: "Input", output: "Output", messages: "Conversation",
      role: "Role", content: "Content", chosen: "Preferred answer", rejected: "Comparison answer", prompt: "Prompt",
      accepted: "Review passed", passed: "Review passed", grounded: "Supported by sources", uncertain: "Uncertain content",
      removed_noise: "Removed noise", source_faithful: "Faithful to source", coverage_complete: "Complete coverage",
      numbers_preserved: "Numbers preserved", formulas_preserved: "Formulas preserved", correctness: "Correctness", grounding: "Grounding",
      quality: "Quality", relevance: "Relevance", completeness: "Completeness", safety: "Safety"}
  };
  const fieldLabel = key => Object.hasOwn(fieldNames[state.language], key) ?
    fieldNames[state.language][key] : key.replace(/_/g, " ");
  function fieldValue(value) {
    if (typeof value === "string") return value;
    if (value === null) return state.language === "zh" ? "无" : "None";
    if (typeof value === "boolean") return state.language === "zh" ? value ? "是" : "否" : value ? "Yes" : "No";
    if (typeof value !== "object") return String(value);
    if (Array.isArray(value) && value.every(item => typeof item === "string")) return value.join("\n");
    if (!Array.isArray(value) && Object.values(value).every(item => item === null || typeof item !== "object"))
      return Object.entries(value).map(([key, item]) => `${fieldLabel(key)}: ${fieldValue(item)}`).join("\n");
    return JSON.stringify(value, null, 2);
  }
  function resetReading() {
    state.readCache = state.decoder = null;
    for (const field of state.fields.values()) field.root.remove();
    state.fields.clear();
    ui["read-section"].hidden = true;
    ui["read-cursor"].hidden = true;
  }
  function showFields(entries) {
    const keys = new Set(entries.map(([key]) => key));
    for (const [key, field] of state.fields) if (!keys.has(key)) {
      field.root.remove();
      state.fields.delete(key);
    }
    let previous = null;
    for (const [key, value] of entries) {
      let field = state.fields.get(key);
      if (!field) {
        const root = document.createElement("div"), heading = document.createElement("div"), body = document.createElement("div");
        root.className = "reading-field";
        root.dataset.field = key;
        heading.className = "reading-field-label";
        body.className = "reading-field-text response-text";
        const text = document.createTextNode("");
        body.append(text);
        root.append(heading, body);
        field = {root, heading, text};
        state.fields.set(key, field);
      }
      const expected = previous ? previous.nextSibling : ui["reading-fields"].firstChild;
      if (expected !== field.root) ui["reading-fields"].insertBefore(field.root, expected);
      previous = field.root;
      setText(field.heading, fieldLabel(key));
      patchText(field.text, fieldValue(value));
    }
  }
  // Only a first, root-level text field has an unambiguous incremental reading.
  // Decode complete JSON string characters; never invent a pending escape or brace.
  function progressiveText(source) {
    let decoder = state.decoder;
    if (!decoder) {
      const prefix = /^(?:\s*```(?:json)?[ \t]*\r?\n)?\s*\{\s*"text"\s*:\s*"/i.exec(source);
      if (!prefix) return null;
      decoder = state.decoder = {position: prefix[0].length, text: "", closed: false, invalid: false};
    }
    if (decoder.invalid) return null;
    const escapes = {'"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t'};
    let position = decoder.position, suffix = "";
    while (!decoder.closed && position < source.length) {
      const char = source[position];
      if (char === '"') {decoder.closed = true; position++; break;}
      if (char.charCodeAt(0) < 0x20) {decoder.invalid = true; return null;}
      if (char === "\\") {
        if (position + 1 >= source.length) break;
        const code = source[position + 1];
        if (code === "u") {
          const digits = source.slice(position + 2, position + 6);
          if (!/^[\da-f]*$/i.test(digits)) {decoder.invalid = true; return null;}
          if (digits.length < 4) break;
          const point = parseInt(digits, 16);
          if (point >= 0xd800 && point <= 0xdbff) {
            // A lone high surrogate is not yet a complete displayed character.
            if (source.length < position + 12) break;
            const low = source.slice(position + 6, position + 12);
            if (!/^\\u[\da-f]{4}$/i.test(low)) {decoder.invalid = true; return null;}
            const lowPoint = parseInt(low.slice(2), 16);
            if (lowPoint < 0xdc00 || lowPoint > 0xdfff) {decoder.invalid = true; return null;}
            suffix += String.fromCharCode(point, lowPoint);
            position += 12;
          } else if (point >= 0xdc00 && point <= 0xdfff) {decoder.invalid = true; return null;}
          else {suffix += String.fromCharCode(point); position += 6;}
        } else if (Object.hasOwn(escapes, code)) {suffix += escapes[code]; position += 2;}
        else {decoder.invalid = true; return null;}
      } else {
        const point = char.charCodeAt(0);
        if (point >= 0xd800 && point <= 0xdbff) {
          if (position + 1 >= source.length) break;
          const low = source.charCodeAt(position + 1);
          if (low < 0xdc00 || low > 0xdfff) {decoder.invalid = true; return null;}
          suffix += source.slice(position, position + 2);
          position += 2;
        } else if (point >= 0xdc00 && point <= 0xdfff) {decoder.invalid = true; return null;}
        else {suffix += char; position++;}
      }
    }
    decoder.position = position;
    decoder.text += suffix;
    return decoder.text;
  }
  function readingEntries(source, complete) {
    if (state.readCache?.source === source && state.readCache.complete === complete)
      return state.readCache.entries;
    let entries = null;
    if (complete) {
      // Remove only one full outer JSON fence, never a code fence inside a string.
      const wrapper = /^\s*```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```\s*$/i.exec(source);
      try {
        const value = JSON.parse(wrapper ? wrapper[1] : source);
        if (value && typeof value === "object" && !Array.isArray(value)) entries = Object.entries(value);
        else if (Array.isArray(value)) entries = [["output", value]];
        else if (typeof value === "string") entries = [["text", value]];
      } catch { /* Free text and malformed final responses remain available as raw text. */ }
    } else {
      const text = progressiveText(source);
      if (text !== null) entries = [["text", text]];
    }
    state.readCache = {source, complete, entries};
    return entries;
  }
  const bottom = () => Math.max(0, ui["output-reader"].scrollHeight - ui["output-reader"].clientHeight);
  function scrollLatest() {
    const reader = ui["output-reader"];
    state.programmaticScroll = bottom();
    reader.scrollTop = state.programmaticScroll;
    ui["follow-bottom"].hidden = true;
  }
  function latestRow() {
    const active = state.rows.filter(row => row.status === "active");
    return (active.length ? active : state.rows).reduce((latest, row) => {
      const rank = value => Date.parse(value.started_at || "") || state.seen.get(value.id) || 0;
      return !latest || rank(row) > rank(latest) ? row : latest;
    }, null);
  }
  function updateTabs() {
    const container = ui["request-tabs"];
    const ids = new Set(state.rows.map(row => row.id));
    for (const [id, button] of state.tabs) if (!ids.has(id)) {
      button.remove();
      state.tabs.delete(id);
    }
    for (const row of state.rows) {
      let button = state.tabs.get(row.id);
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.className = "request-tab";
        button.setAttribute("role", "tab");
        button.addEventListener("click", () => {
          state.pinned = true;
          choose(row.id);
          // A historical response opens at its beginning. Look up its current
          // status because a tab can outlive the request's active state.
          state.followBottom = state.rows.find(value => value.id === row.id)?.status === "active";
          if (!state.followBottom) {
            state.programmaticScroll = 0;
            ui["output-reader"].scrollTop = 0;
          }
          if (state.deltaMode) {
            resetBuffer();
            sendSelection(row.id, false);
          }
          display();
        });
        button.addEventListener("keydown", event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const buttons = [...container.children];
          const index = buttons.indexOf(button);
          const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
            (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
          buttons[next]?.focus();
          buttons[next]?.click();
        });
        state.tabs.set(row.id, button);
        container.append(button);
      }
      const role = label(row.role === "generation" || row.role === "jev" ? row.role : "request");
      const name = `${role} · ${state.rows.indexOf(row) + 1}`;
      setText(button, name);
      button.title = `${name} · ${label(row.status)} · ${row.unit || row.id}`;
      button.dataset.status = row.status;
      button.setAttribute("aria-selected", String(row.id === state.selected));
      button.tabIndex = row.id === state.selected ? 0 : -1;
    }
    container.hidden = state.rows.length < 2;
    container.setAttribute("aria-label", label("tabs"));
  }
  function choose(id) {
    if (state.selected === id) return;
    state.selected = id;
    state.followBottom = true;
    state.reasoningTouched = false;
    state.programmaticScroll = null;
    resetBuffer();
    ui["output-reader"].scrollTop = 0;
  }
  function resetBuffer() {
    resetReading();
    patchText(answerText, "");
    patchText(reasoningText, "");
    state.offset = 0;
    state.deltaReady = false;
    state.deltaStatus = null;
    state.deltaDone = false;
    state.deltaTruncated = false;
    state.deltaLegacy = false;
    state.lastReceipt = null;
    state.syncing = state.deltaMode;
  }
  function sendSelection(requestId, followLatest, reset = false) {
    const serial = `${Date.now()}-${++state.serial}`;
    state.pendingAck = serial;
    state.syncing = true;
    post("streamlit:setComponentValue", {dataType: "json", value: {
      context: state.context, request_id: requestId, follow_latest: followLatest,
      ...(reset ? {reset: true} : {}), serial
    }});
  }
  function sendReceipt() {
    if (!Number.isSafeInteger(state.offset) || state.offset < 0 || !state.deltaReady) return;
    const now = performance.now();
    const previous = state.lastReceipt;
    // Retry a lost value without making identical polls trigger an immediate rerun loop.
    if (previous && previous.id === state.selected && previous.offset === state.offset &&
        previous.status === state.deltaStatus && previous.done === state.deltaDone &&
        now - previous.at < 750) return;
    state.lastReceipt = {id: state.selected, offset: state.offset, status: state.deltaStatus,
      done: state.deltaDone, at: now};
    post("streamlit:setComponentValue", {dataType: "json", value: {
      context: state.context, request_id: state.selected, next_offset: state.offset,
      receipt: true, selection_serial: state.acceptedAck,
      serial: `${Date.now()}-${++state.serial}`
    }});
  }
  function resync() {
    resetBuffer();
    if (state.pendingAck === null) sendSelection(state.selected, !state.pinned, true);
  }
  function applyDelta(delta) {
    if (!delta || delta.id !== state.selected || state.pendingAck !== null) return;
    // Old runs can lack a journal. Their honest bounded snapshot is not an event cursor.
    if (delta.legacy === true) {
      if (typeof delta.text !== "string" || typeof delta.reasoning !== "string") return;
      if (!delta.text.startsWith(answerText.data)) resetReading();
      patchText(answerText, delta.text);
      patchText(reasoningText, delta.reasoning);
      state.deltaLegacy = state.deltaReady = true;
      state.offset = 0;
      state.syncing = false;
      state.deltaStatus = ["active", "completed", "interrupted"].includes(delta.status) ?
        delta.status : "interrupted";
      state.deltaDone = delta.done === true;
      state.deltaTruncated = delta.truncated === true;
      return;
    }
    if (state.deltaLegacy) resetBuffer();
    const valid = Number.isSafeInteger(delta.offset) && delta.offset >= 0 &&
      Number.isSafeInteger(delta.next_offset) && delta.next_offset >= delta.offset &&
      Array.isArray(delta.events) && delta.events.every(event => event &&
        ["text", "reasoning"].includes(event.channel) && typeof event.text === "string") &&
      !(delta.events.length && delta.next_offset === delta.offset);
    if (!valid || (!state.deltaReady && delta.offset !== 0) || delta.offset > state.offset ||
        (delta.offset < state.offset && delta.next_offset > state.offset)) {
      resync();
      return;
    }
    // A repeated or older poll has already been appended. Never replay it.
    if (delta.offset === state.offset) {
      for (const event of delta.events)
        (event.channel === "text" ? answerText : reasoningText).appendData(event.text);
      state.offset = delta.next_offset;
      state.deltaReady = true;
      state.syncing = false;
    }
    // Terminal status can arrive at the same cursor with no extra token.
    if (delta.next_offset === state.offset && (!state.deltaDone || delta.done === true)) {
      state.deltaStatus = ["active", "completed", "interrupted"].includes(delta.status) ?
        delta.status : "interrupted";
      state.deltaDone = delta.done === true;
      state.deltaTruncated = delta.truncated === true;
    }
    // Only an advancing offer needs confirmation. Empty polls and terminal
    // metadata already describe the consumed cursor; posting a fresh value
    // for them would trigger another immediate Streamlit fragment rerun.
    // Repeated advancing offers still retry a lost receipt after 750ms.
    if (delta.next_offset === state.offset && delta.next_offset > delta.offset) sendReceipt();
  }
  function display() {
    const reader = ui["output-reader"];
    const scrollBefore = reader.scrollTop;
    const row = state.rows.find(value => value.id === state.selected);
    setText(ui["stream-title"], label("title"));
    ui["stream-panel"].setAttribute("aria-label", label("title"));
    reader.setAttribute("aria-label", label("reader"));
    setText(ui["reasoning-label"], label("reasoning"));
    setText(ui["answer-label"], label("answer"));
    setText(ui["stream-read"], label("read"));
    setText(ui["stream-raw"], label("raw"));
    ui["stream-view"].setAttribute("aria-label", label("view"));
    ui["stream-read"].setAttribute("aria-pressed", String(state.view === "read"));
    ui["stream-raw"].setAttribute("aria-pressed", String(state.view === "raw"));
    ui["stream-panel"].dataset.view = state.view;
    setText(ui["follow-request"], label(state.pinned ? "follow_latest" : "following_latest"));
    ui["follow-request"].hidden = !state.pinned;
    setText(ui["follow-bottom"], label("follow_bottom"));
    updateTabs();
    if (!row) {
      if (state.deltaMode) resetBuffer();
      else {
        resetReading();
        patchText(answerText, "");
        patchText(reasoningText, "");
      }
      ui["answer-section"].hidden = ui["reasoning-section"].hidden = true;
      ui["stream-cursor"].hidden = true;
      ui["stream-empty"].hidden = false;
      ui["stream-panel"].dataset.status = "empty";
      setText(ui["stream-status"], "");
      setText(ui["stream-meta"], "");
      setText(ui["stream-empty"], label(state.pinned ? "missing" : "empty"));
      setText(ui["stream-note"], "");
      ui["follow-bottom"].hidden = true;
      frameHeight();
      return;
    }
    const status = state.deltaMode ? state.syncing ? "loading" :
      (state.deltaStatus || row.status) === "completed" && !state.deltaDone ? "loading" :
        state.deltaStatus || row.status : row.status;
    ui["stream-panel"].dataset.status = status;
    setText(ui["stream-status"], label(status));
    const role = label(row.role === "generation" || row.role === "jev" ? row.role : "request");
    if (!state.deltaMode) {
      if (!row.text.startsWith(answerText.data)) resetReading();
      patchText(answerText, row.text);
      patchText(reasoningText, row.reasoning);
    }
    const receivedText = answerText.data, receivedReasoning = reasoningText.data;
    const counts = `${label("chars")} ${state.deltaMode ? receivedText.length : row.text_chars ?? receivedText.length} · ${label("reasoning_chars")} ${state.deltaMode ? receivedReasoning.length : row.reasoning_chars ?? receivedReasoning.length} ${label("unit")}`;
    setText(ui["stream-meta"], `${role} · ${state.rows.indexOf(row) + 1} · ${counts}`);
    ui["stream-meta"].title = row.unit || row.id;
    const truncated = state.deltaMode ? state.deltaTruncated : row.truncated;
    const complete = status === "completed" && !truncated && (!state.deltaMode || state.deltaDone);
    const entries = state.view === "read" && receivedText ? readingEntries(receivedText, complete) : null;
    const structured = Boolean(entries?.length);
    if (structured) showFields(entries);
    ui["read-section"].hidden = !structured;
    ui["read-cursor"].hidden = !structured || status !== "active";
    ui["answer-section"].hidden = !receivedText || structured;
    if (state.view === "read" && !structured && !complete && /^\s*(?:\{|\[|```)/.test(receivedText))
      setText(ui["answer-label"], label("fragment"));
    ui["reasoning-section"].hidden = !receivedReasoning;
    if (!state.reasoningTouched) ui["reasoning-section"].open = !receivedText;
    ui["stream-cursor"].hidden = structured || status !== "active" || !receivedText;
    ui["stream-empty"].hidden = Boolean(receivedText);
    setText(ui["stream-empty"], label(state.syncing ? "syncing" : "waiting"));
    const note = label(status === "completed" ? "completed_note" :
      status === "interrupted" ? "interrupted_note" : status === "loading" ? "loading" : "pending_note");
    setText(ui["stream-note"], note + (truncated ? " " + label("truncated_note") : ""));
    if (state.followBottom) scrollLatest();
    else {
      reader.scrollTop = scrollBefore;
      ui["follow-bottom"].hidden = bottom() <= reader.scrollTop + 12;
    }
    frameHeight();
  }
  function render(spec) {
    if (!spec || typeof spec.context !== "string" || !spec.context) return;
    // A native node inspector can reserve a shorter reading viewport without
    // changing stream selection, cursor receipts, or the default standalone UI.
    const readerHeight = Number.isSafeInteger(spec.reader_height) &&
      spec.reader_height >= 160 && spec.reader_height <= 600 ? `${spec.reader_height}px` : "";
    const readerStyle = ui["output-reader"].style;
    if (readerStyle.getPropertyValue("--stream-reader-height") !== readerHeight) {
      if (readerHeight) readerStyle.setProperty("--stream-reader-height", readerHeight);
      else readerStyle.removeProperty("--stream-reader-height");
    }
    const newContext = spec.context !== state.context;
    if (newContext) {
      state.context = spec.context;
      state.pinned = false;
      state.selected = null;
      state.followBottom = true;
      state.reasoningTouched = false;
      state.view = "read";
      state.seen.clear();
      state.nextSeen = 0;
      state.programmaticScroll = null;
      state.pendingAck = state.acceptedAck = null;
      resetBuffer();
      ui["output-reader"].scrollTop = 0;
    }
    const deltaMode = Object.hasOwn(spec, "selected_id") || Object.hasOwn(spec, "delta");
    if (deltaMode !== state.deltaMode) {
      state.deltaMode = deltaMode;
      state.pendingAck = state.acceptedAck = null;
      resetBuffer();
    }
    state.language = spec.language === "en" ? "en" : "zh";
    document.documentElement.lang = state.language === "en" ? "en" : "zh-CN";
    state.labels = spec.labels && typeof spec.labels === "object" ? spec.labels : {};
    const ids = new Set();
    state.rows = (Array.isArray(spec.rows) ? spec.rows : []).filter(row => {
      if (!row || typeof row.id !== "string" || !row.id || ids.has(row.id) ||
          (!state.deltaMode && (typeof row.text !== "string" || typeof row.reasoning !== "string"))) return false;
      ids.add(row.id);
      return true;
    }).map(row => ({...row, text: typeof row.text === "string" ? row.text : "",
      reasoning: typeof row.reasoning === "string" ? row.reasoning : "",
      status: ["active", "completed", "interrupted"].includes(row.status) ? row.status : "interrupted"}));
    for (const id of state.seen.keys()) if (!ids.has(id)) state.seen.delete(id);
    for (const row of [...state.rows].reverse()) if (!state.seen.has(row.id))
      state.seen.set(row.id, ++state.nextSeen);
    if (state.deltaMode) {
      let accepted = true;
      const ack = spec.ack_serial ?? null;
      if (state.pendingAck !== null) {
        if (ack === state.pendingAck) {
          state.acceptedAck = ack;
          state.pendingAck = null;
        } else accepted = false;
      } else if (state.acceptedAck !== null && ack !== state.acceptedAck) accepted = false;
      if (accepted) {
        if (state.acceptedAck === null && typeof ack === "string") state.acceptedAck = ack;
        if (typeof spec.follow_latest === "boolean") state.pinned = !spec.follow_latest;
        const selected = typeof spec.selected_id === "string" ? spec.selected_id : null;
        choose(selected);
        applyDelta(spec.delta);
      }
    } else if (!state.pinned) choose(latestRow()?.id || null);
    display();
  }
  ui["follow-request"].addEventListener("click", () => {
    state.pinned = false;
    state.followBottom = true;
    choose(latestRow()?.id || null);
    if (state.deltaMode) {
      resetBuffer();
      sendSelection(null, true);
    }
    display();
  });
  ui["follow-bottom"].addEventListener("click", () => { state.followBottom = true; scrollLatest(); });
  for (const view of ["read", "raw"]) ui[`stream-${view}`].addEventListener("click", () => {
    state.view = view;
    // This is a local display preference. It must not change request selection
    // or send another receipt for already consumed model events.
    display();
  });
  ui["output-reader"].addEventListener("scroll", () => {
    const current = ui["output-reader"].scrollTop;
    if (state.programmaticScroll !== null && Math.abs(current - state.programmaticScroll) < 2) {
      state.programmaticScroll = null;
      return;
    }
    state.programmaticScroll = null;
    state.followBottom = bottom() - current < 12;
    if (!state.followBottom) state.reasoningTouched = true;
    ui["follow-bottom"].hidden = state.followBottom;
  });
  ui["reasoning-section"].addEventListener("click", event => {
    if (event.target.closest("summary")) state.reasoningTouched = true;
  });
  ui["reasoning-section"].addEventListener("toggle", () => {
    if (state.followBottom) scrollLatest();
    frameHeight();
  });
  window.addEventListener("message", event => {
    if (event.source === parent && event.data?.type === "streamlit:render") render(event.data.args?.spec);
  });
  new ResizeObserver(frameHeight).observe(ui["stream-panel"]);
  post("streamlit:componentReady", {apiVersion: 1});
})();
