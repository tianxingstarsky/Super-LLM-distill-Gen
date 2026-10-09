/* Append real model journal events without replacing output nodes or scroll state. */
(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const ui = Object.fromEntries([
    "stream-panel", "stream-title", "stream-status", "request-tabs", "stream-meta",
    "output-reader", "reasoning-section", "reasoning-label", "reasoning-output",
    "answer-section", "answer-label", "answer-output", "stream-cursor", "stream-empty",
    "stream-note", "follow-request", "follow-bottom"
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
    lastReceipt: null};
  const defaults = {
    zh: {title: "模型输出", active: "正在接收", completed: "接收完成", interrupted: "已中断，可重试",
      generation: "生成", jev: "评审", request: "请求", tabs: "当前节点的模型请求", reader: "模型已返回的内容",
      answer: "正文", reasoning: "推理内容", waiting: "等待首段正文…", empty: "当前节点尚未收到模型输出。",
      loading: "正在载入完整响应", syncing: "正在同步请求内容…",
      missing: "此请求已不在当前预览中。可切回最新输出。", follow_latest: "跟随最新", following_latest: "正在跟随最新",
      follow_bottom: "回到最新内容 ↓", pending_note: "实时接收的内容尚未完成校验。",
      completed_note: "响应已完整接收；样本是否合格以节点质检结果为准。",
      interrupted_note: "此请求已中断；未完成片段不能作为训练样本。",
      truncated_note: "当前预览仅展示部分响应内容。", chars: "正文", reasoning_chars: "推理", unit: "字符"},
    en: {title: "Model output", active: "Receiving", completed: "Response received", interrupted: "Interrupted · retry available",
      generation: "Generate", jev: "Review", request: "Request", tabs: "Model requests in this node", reader: "Received model output",
      answer: "Response", reasoning: "Reasoning", waiting: "Waiting for response text…", empty: "No model output for this node yet.",
      loading: "Loading the complete response", syncing: "Syncing request output…",
      missing: "This request is no longer in the preview. Follow the latest output to continue.", follow_latest: "Follow latest", following_latest: "Following latest",
      follow_bottom: "Go to latest text ↓", pending_note: "Live output has not completed validation.",
      completed_note: "The response is complete. Sample quality depends on the node checks.",
      interrupted_note: "This request was interrupted. Its unfinished text is not a training sample.",
      truncated_note: "This preview contains only part of the output.",
      chars: "Text", reasoning_chars: "Reasoning", unit: "characters"}
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
      const name = `${role} · ${row.unit || row.id.slice(0, 8)}`;
      setText(button, name);
      button.title = `${name} · ${label(row.status)}`;
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
    if (delta.next_offset === state.offset) sendReceipt();
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
    setText(ui["follow-request"], label(state.pinned ? "follow_latest" : "following_latest"));
    ui["follow-request"].hidden = !state.pinned;
    setText(ui["follow-bottom"], label("follow_bottom"));
    updateTabs();
    if (!row) {
      if (state.deltaMode) resetBuffer();
      else {
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
      patchText(answerText, row.text);
      patchText(reasoningText, row.reasoning);
    }
    const receivedText = answerText.data, receivedReasoning = reasoningText.data;
    const counts = `${label("chars")} ${state.deltaMode ? receivedText.length : row.text_chars ?? receivedText.length} · ${label("reasoning_chars")} ${state.deltaMode ? receivedReasoning.length : row.reasoning_chars ?? receivedReasoning.length} ${label("unit")}`;
    setText(ui["stream-meta"], `${role} · ${row.unit || row.id.slice(0, 8)} · ${counts}`);
    ui["answer-section"].hidden = !receivedText;
    ui["reasoning-section"].hidden = !receivedReasoning;
    if (!state.reasoningTouched) ui["reasoning-section"].open = !receivedText;
    ui["stream-cursor"].hidden = status !== "active" || !receivedText;
    ui["stream-empty"].hidden = Boolean(receivedText);
    setText(ui["stream-empty"], label(state.syncing ? "syncing" : "waiting"));
    const note = label(status === "completed" ? "completed_note" :
      status === "interrupted" ? "interrupted_note" : status === "loading" ? "loading" : "pending_note");
    const truncated = state.deltaMode ? state.deltaTruncated : row.truncated;
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
    choose(latestRow()?.id || null);
    if (state.deltaMode) {
      resetBuffer();
      sendSelection(null, true);
    }
    display();
  });
  ui["follow-bottom"].addEventListener("click", () => { state.followBottom = true; scrollLatest(); });
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
