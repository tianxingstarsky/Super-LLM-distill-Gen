// Behavior harness for lib/components/review_workspace/index.js.
//
// Runs the real component module against jsdom (already in node_modules) and
// exercises user interactions: clicks, inputs, keyboard shortcuts, server acks.
// Results are printed as one JSON object: {scenario: {ok, observed?, error?}}.
//
// The component file is not a package ("type" is absent at repo root), so the
// source is loaded through a data: ESM URL. That executes the actual file
// content in this realm; the module keeps no globals at import time, so each
// scenario can install a fresh jsdom window before calling it.
import { readFileSync } from 'node:fs';
import jsdom from 'jsdom';

const { JSDOM, VirtualConsole } = jsdom;
const sourceUrl = new URL('../lib/components/review_workspace/index.js', import.meta.url);
const source = readFileSync(sourceUrl, 'utf8');
const componentFn = (await import('data:text/javascript;base64,' + Buffer.from(source, 'utf8').toString('base64'))).default;

// ── tiny assertion helpers (throw with context so pytest can report it) ──────
function fail(message) {
  throw new Error(message);
}
function assert(condition, message) {
  if (!condition) fail(message);
}
function show(value) {
  if (typeof value === 'string') return JSON.stringify(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}
function assertEqual(actual, expected, label) {
  if (!Object.is(actual, expected)) {
    fail(`${label}: expected ${show(expected)}, got ${show(actual)}`);
  }
}
function assertDeepEqual(actual, expected, label) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) fail(`${label}: expected ${b}, got ${a}`);
}

// ── jsdom setup ──────────────────────────────────────────────────────────────
function polyfillScrolling(win) {
  const offsets = new WeakMap();
  Object.defineProperty(win.HTMLElement.prototype, 'scrollTop', {
    configurable: true,
    get() { return offsets.get(this) ?? 0; },
    set(value) { offsets.set(this, Number.isFinite(Number(value)) ? Number(value) : 0); },
  });
  Object.defineProperty(win.HTMLElement.prototype, 'scrollLeft', {
    configurable: true,
    get() { return offsets.get(this) ?? 0; },
    set(value) { offsets.set(this, Number.isFinite(Number(value)) ? Number(value) : 0); },
  });
  win.HTMLElement.prototype.scrollTo = function scrollTo(x, y) {
    const top = typeof y === 'number' ? y : (typeof x === 'number' ? x : 0);
    this.scrollTop = top;
  };
  win.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
}

let currentListenerErrors = [];

function setup() {
  // jsdom reports exceptions thrown inside event listeners via the virtual
  // console instead of rethrowing them from dispatchEvent; collect them so a
  // broken handler cannot masquerade as "the button did nothing".
  const listenerErrors = [];
  currentListenerErrors = listenerErrors;
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => listenerErrors.push(error.detail || error));
  const dom = new JSDOM(
    '<!doctype html><html><body><div id="host"><div id="review-workspace" class="review-workspace"></div></div></body></html>',
    { url: 'http://localhost/', virtualConsole });
  polyfillScrolling(dom.window);
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  const win = dom.window;
  const host = win.document.getElementById('host');
  const calls = [];
  const component = {
    parentElement: host,
    data: null,
    setTriggerValue(type, payload) { calls.push({ type, payload: JSON.parse(JSON.stringify(payload)) }); },
  };
  return {
    dom, win, host, calls, component,
    root: () => host.querySelector('#review-workspace'),
    $: sel => host.querySelector(sel),
    $$: sel => Array.from(host.querySelectorAll(sel)),
    state: () => host.__reviewState,
    render(data) { if (data !== undefined) component.data = data; return componentFn(component); },
    click: el => el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true })),
    input: (el, value) => { el.value = value; el.dispatchEvent(new win.Event('input', { bubbles: true })); },
    change: (el, value) => { el.value = value; el.dispatchEvent(new win.Event('change', { bubbles: true })); },
    key: (el, init) => el.dispatchEvent(new win.KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })),
    flush: () => new Promise(resolve => setTimeout(resolve, 0)),
  };
}

// ── data fixtures mirroring the adapter contract ─────────────────────────────
function messages(assistantText = '答案一', assistantReasoning = '思考一') {
  return [
    { index: 0, role: 'system', content: { text: '系统', html: '<p>系统</p>', editable: false } },
    { index: 1, role: 'user', content: { text: '问题一', html: '<p>问题一</p>', editable: true } },
    { index: 2, role: 'assistant',
      content: { text: assistantText, html: `<p>${assistantText}</p>`, editable: true },
      reasoning_content: { text: assistantReasoning, html: `<p>${assistantReasoning}</p>`, editable: true } },
  ];
}

function makeData(overrides = {}) {
  const base = {
    scope: 'ws-a',
    workspace: 'docs',
    identity: 'alice',
    query: '',
    status: 'pending',
    queue: {
      items: [
        { record_id: 1, sample_id: 's1', instruction: '问题一', decision: null },
        { record_id: 2, sample_id: 's2', instruction: '问题二', decision: null },
      ],
      total: 2, offset: 0, limit: 30, pending: 2, reviewed: 0,
    },
    record: {
      record_id: 1, sample_id: 's1', sample_hash: 'hash-1', decision: null, reason: '',
      messages: messages(),
    },
    suggestion: null,
    notice: null,
    ack: null,
  };
  const data = { ...base, ...overrides };
  data.record = { ...base.record, ...(overrides.record || {}) };
  data.queue = { ...base.queue, ...(overrides.queue || {}) };
  return data;
}

const editButton = (t, index, field) => t.$(`[data-action="edit"][data-index="${index}"][data-field="${field}"]`);
const aiOpenButton = (t, index, field) => t.$(`[data-action="ai-open"][data-index="${index}"][data-field="${field}"]`);
const queueItem = (t, sampleId) => t.$(`[data-action="select"][data-sample="${sampleId}"]`);

// ── scenarios ────────────────────────────────────────────────────────────────
const scenarios = {};

// 1. Switching scope clears local draft/AI instruction/reason and pending.
scenarios.scope_reset = async () => {
  const t = setup();
  t.render(makeData());
  t.click(aiOpenButton(t, 2, 'content'));
  t.input(t.$('[data-input="ai-instruction"]'), 'AI 要求');
  t.click(editButton(t, 1, 'content'));  // changed() is still false here: no guard
  t.input(t.$('textarea.source'), '草稿文本');
  t.input(t.$('[data-input="reason"]'), '判定理由');
  assertEqual(t.state().draft.text, '草稿文本', 'draft captured before scope switch');
  assertEqual(t.state().reason, '判定理由', 'reason captured before scope switch');
  t.$('.conversation').scrollTop = 120;

  t.render(makeData({ scope: 'ws-b', workspace: 'other', identity: 'bob' }));
  const st = t.state();
  assert(st.draft === null, 'scope change must clear draft');
  assert(st.ai === null, 'scope change must clear AI instruction state');
  assertEqual(st.reason, '', 'scope change must clear reason');
  assert(st.pending === null, 'scope change must clear pending');
  assert(t.$('textarea.source') === null, 'editor must be gone after scope change');
  assertEqual(t.$('[data-input="reason"]').value, '', 'reason textarea must be emptied');
  assertEqual(t.$('.conversation').scrollTop, 0, 'scope change must reset conversation scroll to top');
  return {
    draft: st.draft, ai: st.ai, reason: st.reason, pending: st.pending,
    scrollTopAfterScopeChange: t.$('.conversation').scrollTop,
  };
};

// 2. Re-running with identical data must not redraw; a forced redraw keeps the draft.
scenarios.repeat_render_preserves_draft = async () => {
  const t = setup();
  const data = makeData();
  t.render(data);
  t.click(editButton(t, 2, 'content'));
  const firstTextarea = t.$('textarea.source');
  const firstPane = t.$('.conversation');
  assert(firstTextarea, 'editor opened');
  t.input(firstTextarea, '重复渲染草稿');
  t.$('.conversation').scrollTop = 90;

  t.render(data);  // same signature: must be a no-op for the DOM
  const keptSameNode = t.$('textarea.source') === firstTextarea && t.$('.conversation') === firstPane;
  assert(keptSameNode, 'identical render must keep the same textarea and conversation nodes');
  assertEqual(firstTextarea.value, '重复渲染草稿', 'typed draft text survives identical render');
  assertEqual(t.state().draft.text, '重复渲染草稿', 'draft state survives identical render');

  t.render(makeData({ query: 'filter-changed' }));  // signature change forces draw
  const redrawn = t.$('textarea.source');
  assert(redrawn && redrawn !== firstTextarea, 'data change rebuilds the editor');
  assertEqual(redrawn.value, '重复渲染草稿', 'draft text is restored after redraw');
  assertEqual(t.$('.conversation').scrollTop, 90, 'conversation scroll survives redraw');
  return { keptSameNode, draftText: redrawn.value, scrollAfterRedraw: t.$('.conversation').scrollTop };
};

// 3. Save request carries exactly the field payload + base hash; pending blocks repeats.
scenarios.save_request_is_field_only_and_blocks_repeat = async () => {
  const t = setup();
  t.render(makeData());
  t.click(editButton(t, 2, 'content'));
  t.input(t.$('textarea.source'), '修改后的正文');
  const saveButton = t.$('[data-action="save"]');
  t.click(saveButton);

  assertEqual(t.calls.length, 1, 'one save event emitted');
  const { type, payload } = t.calls[0];
  assertEqual(type, 'event', 'trigger type');
  assertDeepEqual(Object.keys(payload).sort(),
    ['action', 'field', 'id', 'index', 'record_id', 'sample_hash', 'text'],
    'save payload is field-only');
  assertEqual(payload.action, 'save', 'action');
  assertEqual(payload.record_id, 1, 'record_id');
  assertEqual(payload.sample_hash, 'hash-1', 'base sample_hash is sent for staleness checks');
  assertEqual(payload.index, 2, 'message index');
  assertEqual(payload.field, 'content', 'field');
  assertEqual(payload.text, '修改后的正文', 'edited text');
  assert(typeof payload.id === 'string' && payload.id.length > 0, 'request id generated');
  assert(t.root().classList.contains('busy'), 'root marked busy while pending');
  assert(saveButton.disabled, 'server buttons disabled while pending');
  t.click(saveButton);  // programmatic repeat attempt
  assertEqual(t.calls.length, 1, 'pending request blocks a repeated save');
  const allControlsDisabled = t.$$('button,input,textarea,select').every(b => b.disabled);
  assert(allControlsDisabled, 'every control is disabled while pending');
  return {
    action: payload.action, keys: Object.keys(payload).sort(), sampleHash: payload.sample_hash,
    recordId: payload.record_id, index: payload.index, field: payload.field, text: payload.text,
    repeatBlocked: t.calls.length === 1, busy: t.root().classList.contains('busy'),
    allControlsDisabled,
  };
};

scenarios.ctrl_s_saves_draft_only = async () => {
  const t = setup();
  t.render(makeData());
  t.click(editButton(t, 1, 'content'));
  const area = t.$('textarea.source');
  t.input(area, 'Ctrl+S 草稿');
  t.key(area, { key: 's', ctrlKey: true });
  assertEqual(t.calls.length, 1, 'Ctrl+S in the source editor saves');
  assertEqual(t.calls[0].payload.action, 'save', 'Ctrl+S action');
  assertEqual(t.calls[0].payload.text, 'Ctrl+S 草稿', 'Ctrl+S text');
  assertEqual(t.calls[0].payload.index, 1, 'Ctrl+S index');

  const t2 = setup();
  t2.render(makeData());
  t2.key(t2.$('.conversation'), { key: 's', ctrlKey: true });
  assertEqual(t2.calls.length, 0, 'Ctrl+S without an open draft must not emit');
  return { action: t.calls[0].payload.action, text: t.calls[0].payload.text,
           noDraftEmissions: t2.calls.length };
};

// 4. Success ack closes the editor, clears the draft, keeps reason and scroll.
scenarios.ack_success_clears_draft_and_keeps_scroll = async () => {
  const t = setup();
  t.render(makeData());
  t.input(t.$('[data-input="reason"]'), '已填写的理由');
  t.click(editButton(t, 2, 'content'));
  t.input(t.$('textarea.source'), '成功前的草稿');
  t.$('.conversation').scrollTop = 180;
  t.click(t.$('[data-action="save"]'));
  const id = t.calls[0].payload.id;

  t.render(makeData({
    record: { sample_hash: 'hash-2', messages: messages('保存后的正文') },
    ack: { id, ok: true },
  }));
  await t.flush();

  assert(t.state().draft === null, 'successful save ack clears draft');
  assert(t.state().pending === null, 'successful save ack clears pending');
  assert(t.state().ai === null, 'successful save ack clears AI state');
  assert(t.$('textarea.source') === null, 'editor closed after ack');
  const newContentShown = t.$$('.field-read').some(el => el.textContent.includes('保存后的正文'));
  assert(newContentShown, 'server-side new content is rendered after ack');
  assertEqual(t.$('[data-input="reason"]').value, '已填写的理由', 'reason survives save ack');
  assertEqual(t.$('.conversation').scrollTop, 180, 'conversation scroll preserved across save ack redraw');
  assert(!t.root().classList.contains('busy'), 'busy cleared after ack');
  return {
    draft: t.state().draft, pending: t.state().pending, editorPresent: t.$('textarea.source') !== null,
    newContentShown, reason: t.$('[data-input="reason"]').value,
    scrollAfterAck: t.$('.conversation').scrollTop,
  };
};

// 5. Failed ack keeps the draft and reason so the user can retry.
scenarios.ack_failure_retains_draft_and_reason = async () => {
  const t = setup();
  t.render(makeData());
  t.input(t.$('[data-input="reason"]'), '失败也要保留的理由');
  t.click(editButton(t, 1, 'content'));
  t.input(t.$('textarea.source'), '失败草稿');
  t.click(t.$('[data-action="save"]'));
  const id = t.calls[0].payload.id;

  t.render(makeData({ ack: { id, ok: false } }));
  await t.flush();

  const st = t.state();
  assert(st.draft && st.draft.text === '失败草稿', 'failed ack retains draft text');
  assertEqual(st.pending, null, 'failed ack clears pending so retry is possible');
  assertEqual(st.reason, '失败也要保留的理由', 'failed ack retains reason');
  const pendingAfterFailure = st.pending;
  assertEqual(t.$('textarea.source').value, '失败草稿', 'failed ack re-renders draft in editor');
  assertEqual(t.$('[data-input="reason"]').value, '失败也要保留的理由', 'reason textarea retained');
  assert(!t.root().classList.contains('busy'), 'busy cleared after failed ack');

  // The adapter refreshes the SAME record_id with new content/hash while the
  // draft is still open (e.g. another save landed). The draft must stay bound
  // to the version the reviewer opened; it must not silently rebase, so the
  // save below keeps the original hash and the backend rejects it as stale.
  const refreshed = messages('服务端新答案', '服务端新思考');
  refreshed[1].content = { text: '服务端新问题', html: '<p>服务端新问题</p>', editable: true };
  t.render(makeData({ record: { sample_hash: 'hash-2', messages: refreshed } }));
  assertEqual(st.draft.sample_hash, 'hash-1', 'draft keeps the hash captured at openEdit');
  assertEqual(st.draft.record_id, 1, 'draft keeps the record_id captured at openEdit');
  assertEqual(st.draft.base, '问题一', 'draft base text is not rebased to the refreshed content');
  assertEqual(t.$('textarea.source').value, '失败草稿', 'refreshed content must not clobber the local draft');

  t.click(t.$('[data-action="save"]'));
  assertEqual(t.calls.length, 2, 'retry after failed ack emits again');
  assert(t.calls[1].payload.id !== id, 'retry uses a fresh request id');
  assertEqual(t.calls[1].payload.sample_hash, 'hash-1', 'save sends the captured original hash, not the refreshed one');
  assertEqual(t.calls[1].payload.record_id, 1, 'save sends the captured original record_id');
  assertEqual(t.calls[1].payload.text, '失败草稿', 'retry resends the retained draft');
  assert(t.calls[1].payload.sample_hash !== t.state().data.record.sample_hash,
    'sent hash differs from the refreshed record, so the backend rejects the stale save');
  return {
    draftText: st.draft.text, reason: st.reason, pendingAfterFailure,
    capturedHash: st.draft.sample_hash, capturedRecordId: st.draft.record_id,
    refreshedHash: t.state().data.record.sample_hash,
    saveHash: t.calls[1].payload.sample_hash, saveRecordId: t.calls[1].payload.record_id,
    retryText: t.calls[1].payload.text, retryUsesFreshId: t.calls[1].payload.id !== id,
  };
};

// 6. While a decision request is pending, repeats and navigation are refused.
scenarios.pending_blocks_repeat_navigation_and_submit_ack = async () => {
  const t = setup();
  t.render(makeData());
  t.input(t.$('[data-input="reason"]'), '提交理由');
  t.click(t.$('[data-action="keep"]'));
  assertEqual(t.calls.length, 1, 'keep submitted once');
  const payload = t.calls[0].payload;
  assertEqual(payload.action, 'submit', 'submit action');
  assertEqual(payload.decision, 'keep', 'decision');
  assertEqual(payload.reason, '提交理由', 'reason');
  const allControlsDisabled = t.$$('button,input,textarea,select').every(b => b.disabled);
  assert(allControlsDisabled, 'every control is disabled while pending so edits cannot be lost');
  const repeatBlocked = (() => {
    t.click(t.$('[data-action="keep"]'));
    t.click(queueItem(t, 's2'));
    return t.calls.length === 1;
  })();
  assert(repeatBlocked, 'repeated decision/selection while pending is refused');
  const pendingNotice = t.$('.statusbar').textContent;
  assert(pendingNotice.includes('正在处理'), 'pending notice shown');

  t.$('.conversation').scrollTop = 100;
  t.render(makeData({
    record: { decision: 'keep', reason: '提交理由' },
    ack: { id: payload.id, ok: true },
  }));
  await t.flush();
  assertEqual(t.state().pending, null, 'submit ack clears pending');
  assertEqual(t.state().reason, '', 'submit ack resets local reason');
  assertEqual(t.$('.conversation').scrollTop, 0, 'submit ack resets conversation scroll to top');
  assert(!t.root().classList.contains('busy'), 'busy cleared after submit ack');
  return {
    submitAction: payload.action, decision: payload.decision, repeatBlocked,
    allControlsDisabled, notice: pendingNotice, pendingAfterAck: t.state().pending,
    reasonAfterAck: t.state().reason, scrollAfterAck: t.$('.conversation').scrollTop,
  };
};

// 7. keep/reject refuse an empty (or whitespace) reason and focus the field.
scenarios.blank_reason_is_refused = async () => {
  const t = setup();
  t.render(makeData());
  t.click(t.$('[data-action="keep"]'));
  assertEqual(t.calls.length, 0, 'no submit without a reason');
  assert(t.$('.statusbar').classList.contains('error'), 'error notice shown');
  assert(t.$('.statusbar').textContent.includes('判定理由'), 'notice explains the missing reason');
  assertEqual(t.win.document.activeElement, t.$('[data-input="reason"]'), 'reason field focused');
  const errorNotice = t.$('.statusbar').className.includes('error');
  const focusedReason = t.win.document.activeElement === t.$('[data-input="reason"]');

  t.input(t.$('[data-input="reason"]'), '   ');
  t.click(t.$('[data-action="reject"]'));
  assertEqual(t.calls.length, 0, 'whitespace-only reason is refused');
  const refusals = t.calls.length;

  t.input(t.$('[data-input="reason"]'), '有依据');
  t.click(t.$('[data-action="reject"]'));
  assertEqual(t.calls.length, 1, 'filled reason submits');
  assertEqual(t.calls[0].payload.decision, 'reject', 'reject decision');
  assertEqual(t.calls[0].payload.reason, '有依据', 'reason text');
  return {
    refusals, errorNotice, focusedReason,
    decisionAfterFill: t.calls[0].payload.decision, emittedReason: t.calls[0].payload.reason,
  };
};

// 8. Alt+Enter family fires only from non-input elements (typing is safe).
scenarios.shortcuts_do_not_submit_while_typing = async () => {
  const t = setup();
  t.render(makeData());
  t.click(aiOpenButton(t, 2, 'content'));
  t.input(t.$('[data-input="ai-instruction"]'), 'AI 输入');
  t.input(t.$('[data-input="reason"]'), '快捷键理由');
  t.key(t.$('[data-input="reason"]'), { key: 'Enter', altKey: true });
  t.key(t.$('[data-input="search"]'), { key: 'Backspace', altKey: true });
  t.key(t.$('[data-input="jump"]'), { key: 'ArrowRight', altKey: true });
  t.key(t.$('[data-input="ai-instruction"]'), { key: 'Enter', altKey: true });
  const inputsEmitted = t.calls.length;
  assertEqual(inputsEmitted, 0, 'alt shortcuts must not fire while an input has focus');

  t.click(t.$('[data-action="ai-close"]'));  // clear the AI panel so a decision is allowed
  t.key(t.$('.conversation'), { key: 'Enter', altKey: true });
  assertEqual(t.calls.length, 1, 'alt+Enter outside inputs submits keep');
  assertEqual(t.calls[0].payload.action, 'submit', 'action');
  assertEqual(t.calls[0].payload.decision, 'keep', 'decision');
  assertEqual(t.calls[0].payload.reason, '快捷键理由', 'reason');

  const t2 = setup();
  t2.render(makeData());
  t2.input(t2.$('[data-input="reason"]'), '驳回理由');
  t2.key(t2.$('.conversation'), { key: 'Backspace', altKey: true });
  assertEqual(t2.calls.length, 1, 'alt+Backspace outside inputs submits reject');
  assertEqual(t2.calls[0].payload.decision, 'reject', 'decision');
  return {
    inputsEmitted, action: t.calls[0].payload.action, decision: t.calls[0].payload.decision,
    reason: t.calls[0].payload.reason, rejectDecision: t2.calls[0].payload.decision,
  };
};

// 9. Guard dialog: switch attempts with unsaved content stay, discard, or save-then-go.
// The window-level outsideClick/outsideKey guards check e.isTrusted; jsdom cannot
// forge isTrusted on dispatched events, so those two outer-navigation paths remain
// for the main browser pass (everything reachable from inside the root is covered here).
scenarios.guard_switch_flow = async () => {
  const t = setup();
  t.render(makeData());
  t.input(t.$('[data-input="reason"]'), '切换前的理由');
  t.click(queueItem(t, 's2'));
  const heldNoEmit = t.calls.length === 0;
  assert(heldNoEmit, 'queue switch is held while dirty');
  assert(!t.$('.guard').hidden, 'guard dialog shown');
  assert(t.$('[data-action="guard-save"]').hidden, 'no save option without a field change');

  t.click(t.$('[data-action="guard-stay"]'));
  assert(t.$('.guard').hidden, 'guard-stay closes the dialog');
  assertEqual(t.state().guardAction, null, 'guard action cleared on stay');
  assertEqual(t.win.document.activeElement, t.$('[data-input="reason"]'),
    'guard-stay refocuses reason when no source editor is open');
  const stayRefocus = t.win.document.activeElement.dataset.input;
  const stayEmits = t.calls.length;

  t.click(queueItem(t, 's2'));
  assert(!t.$('.guard').hidden, 'guard shown again');
  t.click(t.$('[data-action="guard-discard"]'));
  assert(t.$('.guard').hidden, 'guard-discard closes the dialog');
  assertEqual(t.state().reason, '', 'discard resets local reason');
  assertEqual(t.calls.length, 1, 'discard runs the original switch');
  assertEqual(t.calls[0].payload.action, 'select', 'original action');
  assertEqual(t.calls[0].payload.sample_id, 's2', 'original target');
  const discardedAction = t.calls[0].payload.action;
  const discardedTarget = t.calls[0].payload.sample_id;

  // Second phase: changed field + switch -> guard-save chains save then the switch.
  const t2 = setup();
  t2.render(makeData());
  t2.click(editButton(t2, 2, 'content'));
  t2.input(t2.$('textarea.source'), '守卫草稿');
  t2.click(queueItem(t2, 's2'));
  assert(!t2.$('.guard').hidden, 'guard shown for changed draft');
  assert(!t2.$('[data-action="guard-save"]').hidden, 'save option offered when field changed');
  t2.click(t2.$('[data-action="guard-stay"]'));
  const stayRefocusSource = t2.win.document.activeElement === t2.$('textarea.source');
  assert(stayRefocusSource, 'guard-stay refocuses the source editor');
  assertEqual(t2.state().draft.text, '守卫草稿', 'draft survives guard-stay');
  t2.click(queueItem(t2, 's2'));
  assert(!t2.$('.guard').hidden, 'guard shown again after stay');
  t2.click(t2.$('[data-action="guard-save"]'));
  assert(t2.$('.guard').hidden, 'guard closed after choosing save');
  assertEqual(t2.calls.length, 1, 'save emitted first');
  assertEqual(t2.calls[0].payload.action, 'save', 'guard-save emits the field save');
  assertEqual(t2.calls[0].payload.text, '守卫草稿', 'guard-save sends current draft text');
  const saveId = t2.calls[0].payload.id;

  t2.render(makeData({
    record: { sample_hash: 'hash-9', messages: messages('守卫保存后的正文') },
    ack: { id: saveId, ok: true },
  }));
  await t2.flush();
  await t2.flush();
  assertEqual(t2.calls.length, 2, 'guarded switch runs after successful save ack');
  assertEqual(t2.calls[1].payload.action, 'select', 'chained action');
  assertEqual(t2.calls[1].payload.sample_id, 's2', 'chained target');
  assertEqual(t2.state().draft, null, 'draft cleared by save ack');
  return {
    heldNoEmit, stayEmits, stayRefocus, stayRefocusSource,
    discardedAction, discardedTarget,
    chainedActions: t2.calls.map(call => call.payload.action),
    chainedTarget: t2.calls[1].payload.sample_id,
    draftAfterChain: t2.state().draft,
  };
};

// 10. AI request requires an instruction and sends no content.
scenarios.ai_run_requires_instruction = async () => {
  const t = setup();
  t.render(makeData());
  t.click(aiOpenButton(t, 2, 'reasoning_content'));
  assert(t.state().ai, 'AI panel state created');
  assert(t.$('[data-input="ai-instruction"]'), 'instruction input rendered');
  t.click(t.$('[data-action="ai-run"]'));
  const blankRefused = t.calls.length === 0
    && t.$('.statusbar').textContent.includes('修改要求');
  assertEqual(t.calls.length, 0, 'blank AI instruction refused');
  assert(t.$('.statusbar').textContent.includes('修改要求'), 'notice asks for the instruction');

  t.input(t.$('[data-input="ai-instruction"]'), '把思考写得更简洁');
  t.click(t.$('[data-action="ai-run"]'));
  assertEqual(t.calls.length, 1, 'AI request emitted');
  const payload = t.calls[0].payload;
  assertEqual(payload.action, 'ai', 'action');
  assertEqual(payload.index, 2, 'index');
  assertEqual(payload.field, 'reasoning_content', 'field');
  assertEqual(payload.instruction, '把思考写得更简洁', 'instruction');
  assertEqual(payload.sample_hash, 'hash-1', 'base hash included');
  assert(!('text' in payload) && !('messages' in payload), 'AI run does not send the message body');
  assert(t.$('[data-action="ai-run"]').disabled, 'AI run disabled while pending');
  return {
    blankRefused, action: payload.action, field: payload.field, index: payload.index,
    instruction: payload.instruction, sampleHash: payload.sample_hash,
    sendsBody: ('text' in payload) || ('messages' in payload),
    disabledWhilePending: t.$('[data-action="ai-run"]').disabled,
  };
};

// ── runner ───────────────────────────────────────────────────────────────────
const results = {};
const only = process.env.HARNESS_ONLY;
for (const [name, run] of Object.entries(scenarios)) {
  if (only && name !== only) continue;
  try {
    const observed = await run();
    if (currentListenerErrors.length) {
      fail(`uncaught error inside an event listener: ${currentListenerErrors.join(' | ')}`);
    }
    results[name] = { ok: true, observed: observed || {} };
  } catch (error) {
    const listenerNote = currentListenerErrors.length
      ? `\nlistener errors: ${currentListenerErrors.join(' | ')}` : '';
    results[name] = { ok: false, error: `${error && error.message}${listenerNote}\n${error && error.stack}` };
  }
}
console.log(JSON.stringify({ scenarios: results }));
