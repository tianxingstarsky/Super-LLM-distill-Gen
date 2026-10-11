import assert from 'node:assert/strict';
import fs from 'node:fs';
import {JSDOM} from 'jsdom';

const base = new URL('../lib/components/workflow_stream/', import.meta.url);
const html = fs.readFileSync(new URL('index.html', base), 'utf8');
const script = fs.readFileSync(new URL('index.js', base), 'utf8');
const css = fs.readFileSync(new URL('style.css', base), 'utf8');
const results = {};

function page() {
  const dom = new JSDOM(html, {runScripts: 'outside-only', url: 'http://localhost/component'});
  const w = dom.window, d = w.document, events = [];
  const get = id => d.getElementById(id);
  const reader = get('output-reader');
  let scroll = 0, contentHeight = 300, now = 0;
  w.performance.now = () => now;
  Object.defineProperty(reader, 'clientHeight', {get: () => 100});
  Object.defineProperty(reader, 'scrollHeight', {get: () => contentHeight});
  Object.defineProperty(reader, 'scrollTop', {get: () => scroll, set: value => {
    scroll = Math.max(0, Math.min(Number(value), Math.max(0, contentHeight - 100)));
  }});
  get('stream-panel').getBoundingClientRect = () => ({height: 180 + Math.min(contentHeight, 460)});
  w.ResizeObserver = class {constructor(callback) {w.observeResize = callback;} observe() {}};
  w.postMessage = value => events.push(value);
  w.eval(script);
  const render = (rows, extras = {}) => w.dispatchEvent(new w.MessageEvent('message', {
    source: w, data: {type: 'streamlit:render', args: {spec: {
      context: 'run-a:sft', language: 'en', rows, ...extras
    }}}
  }));
  return {w, d, get, reader, events, render,
    setHeight(value) {contentHeight = value;}, advance(ms) {now += ms;}, close() {dom.window.close();}};
}
const row = (id = 'first', extras = {}) => ({id, stage: 'sft', unit: 'sample-01', role: 'generation',
  status: 'active', text: '', reasoning: '', updated_at: '2026-10-07T10:00:01Z',
  started_at: '2026-10-07T10:00:00Z', attempt: 1, run_attempt: 1, ...extras});
const text = p => p.get('answer-output').textContent;
const componentEvents = p => p.events.filter(event => event.type === 'streamlit:setComponentValue').map(event => event.value);
const selectedEvents = p => componentEvents(p).filter(value => !value.receipt);
const receipts = p => componentEvents(p).filter(value => value.receipt === true);
const delta = (id, offset, next_offset, events = [], extras = {}) => ({id, offset, next_offset,
  events, status: 'active', done: false, truncated: false, ...extras});
const token = (text, channel = 'text') => ({channel, text});
function renderDelta(p, rows, selected, value, extras = {}) {
  p.render(rows, {selected_id: selected, delta: value, ack_serial: null, follow_latest: true, ...extras});
}
function scenario(name, operation) {
  const p = page();
  try {operation(p); results[name] = {ok: true};}
  catch (error) {results[name] = {ok: false, error: error.stack};}
  finally {p.close();}
}

scenario('real_delta_appends_without_replacing_output_nodes', p => {
  p.render([row('first', {text: '你', reasoning: '先检查'})]);
  const answer = p.get('answer-output'), node = answer.firstChild;
  const reasoning = p.get('reasoning-output').firstChild;
  const parent = p.get('answer-section');
  let appended = '', replacements = 0;
  const original = node.appendData.bind(node);
  node.appendData = value => {appended += value; original(value);};
  const observer = new p.w.MutationObserver(records => {
    replacements += records.filter(record => record.type === 'childList').length;
  });
  observer.observe(parent, {subtree: true, childList: true});
  p.render([row('first', {text: '你好，世界', reasoning: '先检查来源'})]);
  assert.equal(text(p), '你好，世界');
  assert.equal(appended, '好，世界');
  assert.equal(answer.firstChild, node);
  assert.equal(p.get('reasoning-output').firstChild, reasoning);
  assert.equal(p.get('answer-section'), parent);
  assert.equal(observer.takeRecords().filter(record => record.type === 'childList').length, 0);
  assert.equal(replacements, 0);
  assert.equal(p.get('stream-cursor').hidden, false);
});

scenario('identical_snapshots_do_not_write_text_or_rebuild_tabs', p => {
  const rows = [row('first', {text: 'Already received'}), row('second', {
    text: 'Other request', started_at: '2026-10-07T09:00:00Z'})];
  p.render(rows);
  const button = p.get('request-tabs').firstElementChild;
  const node = p.get('answer-output').firstChild;
  node.appendData = () => {throw new Error('Repeated snapshot appended text');};
  const observer = new p.w.MutationObserver(() => {});
  observer.observe(p.get('stream-panel'), {subtree: true, childList: true, characterData: true});
  p.render(rows);
  assert.equal(observer.takeRecords().length, 0);
  assert.equal(p.get('request-tabs').firstElementChild, button);
});

scenario('full_completed_output_is_immediate_and_not_quality_approval', p => {
  const full = '完整回答。'.repeat(10000);
  p.render([row('complete', {status: 'completed', text: full, reasoning: 'Returned reasoning'})]);
  assert.equal(text(p), full);
  assert.equal(p.get('stream-status').textContent, 'Response received');
  assert.equal(p.get('stream-cursor').hidden, true);
  assert.match(p.get('stream-note').textContent, /quality depends on the node checks/);
  assert.equal(p.get('answer-output').childNodes.length, 1);
});

scenario('manual_request_selection_stays_fixed_until_follow_latest', p => {
  const older = row('older', {text: 'Older completed output', status: 'completed'});
  const active = row('active', {text: 'Current output', started_at: '2026-10-07T10:01:00Z'});
  p.render([active, older]);
  assert.equal(text(p), active.text);
  const tabs = p.get('request-tabs');
  tabs.children[1].click();
  assert.equal(text(p), older.text);
  assert.equal(p.get('follow-request').hidden, false);
  const newest = row('newest', {text: 'Next live output', started_at: '2026-10-07T10:02:00Z'});
  p.render([newest, {...active, text: active.text + ' delta'}, older]);
  assert.equal(text(p), older.text);
  p.get('follow-request').click();
  assert.equal(text(p), newest.text);
  assert.equal(p.get('follow-request').hidden, true);
  // Updates from an older concurrent request must not steal the selected call.
  p.render([{...active, text: 'Older request updated now', updated_at: '2030-01-01'}, newest, older]);
  assert.equal(text(p), newest.text);
});

scenario('scrolling_up_pauses_and_resume_scrolls_to_real_latest_text', p => {
  p.render([row('first', {text: 'Beginning'})]);
  assert.equal(p.reader.scrollTop, 200);
  p.reader.dispatchEvent(new p.w.Event('scroll')); // Programmatic scroll acknowledgement.
  p.reader.scrollTop = 40;
  p.reader.dispatchEvent(new p.w.Event('scroll'));
  assert.equal(p.get('follow-bottom').hidden, false);
  p.setHeight(550);
  p.render([row('first', {text: 'Beginning plus real next token'})]);
  assert.equal(p.reader.scrollTop, 40);
  assert.equal(p.get('follow-bottom').hidden, false);
  p.get('follow-bottom').click();
  assert.equal(p.reader.scrollTop, 450);
  p.reader.dispatchEvent(new p.w.Event('scroll'));
  p.setHeight(600);
  p.render([row('first', {text: 'Beginning plus real next token and more'})]);
  assert.equal(p.reader.scrollTop, 500);
});

scenario('context_switch_clears_old_task_and_node_content', p => {
  p.render([row('first', {text: 'Task A private content', reasoning: 'A reasoning'})]);
  p.reader.scrollTop = 12;
  p.reader.dispatchEvent(new p.w.Event('scroll'));
  p.render([], {context: 'run-b:preference'});
  assert.equal(text(p), '');
  assert.equal(p.get('reasoning-output').textContent, '');
  assert(!p.d.body.textContent.includes('Task A private content'));
  assert.equal(p.get('request-tabs').children.length, 0);
  assert.equal(p.get('stream-empty').hidden, false);
  assert.equal(p.get('follow-request').hidden, true);
  p.render([row('first', {text: 'Task B output'})], {context: 'run-b:preference'});
  assert.equal(text(p), 'Task B output');
});

scenario('reasoning_is_separate_and_user_collapse_survives_deltas', p => {
  p.render([row('first', {reasoning: 'Reasoning begins'})]);
  assert.equal(p.get('reasoning-section').hidden, false);
  assert.equal(p.get('reasoning-section').open, true);
  assert.equal(p.get('answer-section').hidden, true);
  p.get('reasoning-label').click();
  assert.equal(p.get('reasoning-section').open, false);
  p.render([row('first', {reasoning: 'Reasoning begins and continues'})]);
  assert.equal(p.get('reasoning-section').open, false);
  p.render([row('first', {text: 'Answer begins', reasoning: 'Reasoning begins and continues'})]);
  assert.equal(text(p), 'Answer begins');
  assert.equal(p.get('reasoning-output').textContent, 'Reasoning begins and continues');
});

scenario('retry_and_nonprefix_snapshots_never_mix_answers', p => {
  p.render([row('first', {text: 'Old answer prefix'})]);
  const node = p.get('answer-output').firstChild;
  p.render([row('first', {text: 'Corrected snapshot'})]);
  assert.equal(text(p), 'Corrected snapshot');
  assert.equal(p.get('answer-output').firstChild, node);
  p.render([row('retry', {text: 'Fresh retry', attempt: 2, started_at: '2026-10-07T10:01:00Z'}),
    row('first', {text: 'Old answer prefix', status: 'interrupted'})]);
  assert.equal(text(p), 'Fresh retry');
  assert(!text(p).includes('Old answer'));
});

scenario('interrupted_and_partial_status_are_honest', p => {
  p.render([row('first', {status: 'interrupted', text: 'Partial response', truncated: true})]);
  assert.equal(p.get('stream-cursor').hidden, true);
  assert.match(p.get('stream-note').textContent, /unfinished text is not a training sample/);
  assert.match(p.get('stream-note').textContent, /part of the output/);
  assert.equal(text(p), 'Partial response');
  p.render([row('first', {status: 'unrecognized', text: 'Partial response'})]);
  assert.equal(p.get('stream-panel').dataset.status, 'interrupted');
});

scenario('missing_pinned_request_never_shows_a_different_answer', p => {
  const first = row('first', {text: 'First answer'});
  const other = row('other', {text: 'Other answer', status: 'completed'});
  p.render([first, other]);
  p.get('request-tabs').children[1].click();
  p.render([first]);
  assert.equal(text(p), '');
  assert.match(p.get('stream-empty').textContent, /no longer in the preview/);
  assert.equal(p.get('follow-request').hidden, false);
  p.get('follow-request').click();
  assert.equal(text(p), first.text);
});

scenario('untrusted_content_and_foreign_messages_are_inert', p => {
  const unsafe = '<img src=x onerror=alert(1)> & <script>leak()</script>';
  p.render([row(unsafe, {text: unsafe, reasoning: unsafe, unit: unsafe}), row('other')]);
  assert.equal(text(p), unsafe);
  assert.equal(p.d.querySelector('#stream-panel img'), null);
  assert.equal(p.d.querySelector('#stream-panel script'), null);
  assert.equal(p.d.querySelector('#request-tabs img'), null);
  p.w.dispatchEvent(new p.w.MessageEvent('message', {source: {}, data: {
    type: 'streamlit:render', args: {spec: {context: 'evil', rows: [row('evil', {text: 'Foreign replacement'})]}}
  }}));
  assert.equal(text(p), unsafe);
});

scenario('labels_keyboard_navigation_and_frame_height_are_stable', p => {
  const rows = [row('first', {text: '原始中文'}), row('second', {
    text: 'Review answer', role: 'jev', started_at: '2026-10-07T09:00:00Z'})];
  p.render(rows, {language: 'zh', labels: {title: '节点内容', body: '完整正文', review: '独立评审', follow_output: '继续跟随'}});
  assert.equal(p.d.documentElement.lang, 'zh-CN');
  assert.equal(p.get('stream-title').textContent, '节点内容');
  assert.equal(p.get('answer-label').textContent, '完整正文');
  assert.equal(p.get('request-tabs').children[1].textContent.startsWith('独立评审'), true);
  p.get('request-tabs').children[0].dispatchEvent(new p.w.KeyboardEvent('keydown', {key: 'ArrowRight'}));
  assert.equal(text(p), 'Review answer');
  assert.equal(p.get('request-tabs').children[1].getAttribute('aria-selected'), 'true');
  const frames = () => p.events.filter(event => event.type === 'streamlit:setFrameHeight').length;
  const before = frames();
  for (let index = 0; index < 20; index++) p.w.observeResize();
  assert.equal(frames(), before);
  assert(p.events.some(event => event.type === 'streamlit:componentReady'));
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(css, /animation:none!important/);
});

scenario('journal_deltas_accumulate_full_output_in_stable_text_nodes', p => {
  const rows = [row('first')];
  renderDelta(p, rows, 'first', delta('first', 0, 25, [token('先想', 'reasoning')]));
  const answerNode = p.get('answer-output').firstChild;
  const reasoningNode = p.get('reasoning-output').firstChild;
  assert.equal(p.get('reasoning-output').textContent, '先想');
  assert.equal(p.get('reasoning-section').open, true);
  let appended = '';
  const append = answerNode.appendData.bind(answerNode);
  answerNode.appendData = value => {appended += value; append(value);};
  const observer = new p.w.MutationObserver(() => {});
  observer.observe(p.get('output-reader'), {subtree: true, childList: true});
  let offset = 25, full = '';
  for (let part = 0; part < 100; part++) {
    const content = `第${part}段：` + '真实收到的内容。'.repeat(150);
    full += content;
    renderDelta(p, rows, 'first', delta('first', offset, offset + 5000, [token(content)]));
    offset += 5000;
  }
  assert(full.length > 100000);
  assert.equal(text(p), full);
  assert.equal(appended, full);
  assert.equal(p.get('answer-output').firstChild, answerNode);
  assert.equal(p.get('reasoning-output').firstChild, reasoningNode);
  assert.equal(observer.takeRecords().length, 0);
  assert.equal(p.get('stream-cursor').hidden, false);
  renderDelta(p, [row('first', {status: 'completed'})], 'first', delta('first', offset, offset, [], {
    status: 'completed', done: true}));
  assert.equal(text(p), full);
  assert.equal(p.get('stream-status').textContent, 'Response received');
  assert.equal(p.get('stream-cursor').hidden, true);
});

scenario('duplicate_and_old_delta_polls_never_replay_tokens_or_terminal_status', p => {
  const rows = [row()];
  const first = delta('first', 0, 30, [token('Hello')]);
  renderDelta(p, rows, 'first', first);
  renderDelta(p, rows, 'first', first);
  assert.equal(text(p), 'Hello');
  renderDelta(p, rows, 'first', delta('first', 30, 50, [token(' world')]));
  renderDelta(p, rows, 'first', first);
  assert.equal(text(p), 'Hello world');
  renderDelta(p, rows, 'first', delta('first', 50, 50));
  assert.equal(p.get('stream-status').textContent, 'Receiving');
  renderDelta(p, rows, 'first', delta('first', 50, 50, [], {status: 'completed', done: true}));
  renderDelta(p, rows, 'first', delta('first', 30, 50, [token(' world')]));
  assert.equal(text(p), 'Hello world');
  assert.equal(p.get('stream-status').textContent, 'Response received');
  assert.equal(selectedEvents(p).length, 0);
});

scenario('new_mount_with_nonzero_cursor_requests_prefix_and_preserves_manual_selection', p => {
  const rows = [row('first'), row('other')];
  renderDelta(p, rows, 'first', delta('first', 40, 80, [token('suffix only')], {
    status: 'completed', done: true}), {follow_latest: false});
  assert.equal(text(p), '');
  assert.equal(p.get('stream-status').textContent, 'Loading the complete response');
  const reset = selectedEvents(p)[0];
  assert.equal(reset.context, 'run-a:sft');
  assert.equal(reset.request_id, 'first');
  assert.equal(reset.follow_latest, false);
  assert.equal(reset.reset, true);
  assert.equal(typeof reset.serial, 'string');
  assert(reset.serial.length <= 128);
  renderDelta(p, rows, 'first', delta('first', 40, 80, [token('suffix only')]));
  assert.equal(selectedEvents(p).length, 1);
  assert.equal(text(p), '');
  renderDelta(p, rows, 'first', delta('first', 0, 80, [token('prefix and suffix')], {
    status: 'completed', done: true}), {ack_serial: reset.serial, follow_latest: false});
  assert.equal(text(p), 'prefix and suffix');
  assert.equal(p.get('follow-request').hidden, false);
  assert.equal(p.get('stream-status').textContent, 'Response received');
});

scenario('cursor_gaps_overlaps_and_invalid_events_resync_instead_of_mixing', p => {
  const rows = [row()];
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('First prefix')]));
  renderDelta(p, rows, 'first', delta('first', 40, 60, [token('missing middle')]));
  assert.equal(text(p), '');
  let reset = selectedEvents(p).at(-1);
  assert.equal(reset.reset, true);
  renderDelta(p, rows, 'first', delta('first', 0, 60, [token('Restored complete prefix')]), {ack_serial: reset.serial});
  assert.equal(text(p), 'Restored complete prefix');
  renderDelta(p, rows, 'first', delta('first', 50, 70, [token('overlap')]), {ack_serial: reset.serial});
  assert.equal(text(p), '');
  reset = selectedEvents(p).at(-1);
  renderDelta(p, rows, 'first', delta('first', 0, 60, [token('Restored again')]), {ack_serial: reset.serial});
  renderDelta(p, rows, 'first', delta('first', 60, 90, [token('unsafe', 'html')]), {ack_serial: reset.serial});
  assert.equal(text(p), '');
  assert.equal(selectedEvents(p).length, 3);
  assert.equal(p.d.querySelector('#answer-output html'), null);
});

scenario('selection_ack_rejects_late_snapshots_and_follow_latest_can_resume', p => {
  const rows = [row('first'), row('other')];
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('First output')]));
  p.get('request-tabs').children[1].click();
  const selected = selectedEvents(p).at(-1);
  assert.equal(selected.request_id, 'other');
  assert.equal(selected.follow_latest, false);
  assert.equal(text(p), '');
  renderDelta(p, rows, 'first', delta('first', 20, 40, [token('late first')]));
  assert.equal(text(p), '');
  assert.equal(p.get('request-tabs').children[1].getAttribute('aria-selected'), 'true');
  renderDelta(p, rows, 'other', delta('other', 0, 20, [token('Other output')]), {
    ack_serial: selected.serial, follow_latest: false});
  assert.equal(text(p), 'Other output');
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('stale earlier selection')]));
  assert.equal(text(p), 'Other output');
  p.get('follow-request').click();
  const follow = selectedEvents(p).at(-1);
  assert.equal(follow.request_id, null);
  assert.equal(follow.follow_latest, true);
  renderDelta(p, rows, 'other', delta('other', 20, 40, [token('late other')]), {
    ack_serial: selected.serial, follow_latest: false});
  assert.equal(text(p), '');
  renderDelta(p, rows, 'first', delta('first', 0, 40, [token('Latest complete prefix')]), {
    ack_serial: follow.serial});
  assert.equal(text(p), 'Latest complete prefix');
  assert.equal(p.get('follow-request').hidden, true);
});

scenario('switching_away_and_back_reloads_prefix_without_an_old_request_cache', p => {
  const rows = [row('first'), row('other')];
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('First answer')]));
  p.get('request-tabs').children[1].click();
  let selection = selectedEvents(p).at(-1);
  renderDelta(p, rows, 'other', delta('other', 0, 20, [token('Other answer')]), {
    ack_serial: selection.serial, follow_latest: false});
  p.get('request-tabs').children[0].click();
  selection = selectedEvents(p).at(-1);
  assert.equal(text(p), '');
  renderDelta(p, rows, 'first', delta('first', 0, 50, [token('First answer plus new content')]), {
    ack_serial: selection.serial, follow_latest: false});
  assert.equal(text(p), 'First answer plus new content');
});

scenario('context_change_drops_pending_selection_and_reused_id_requires_prefix', p => {
  const rows = [row('first'), row('other')];
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('Task A answer')]));
  p.get('request-tabs').children[1].click();
  const old = selectedEvents(p).at(-1);
  renderDelta(p, [row('first')], 'first', delta('first', 20, 40, [token('Task B suffix')]), {
    context: 'run-b:sft'});
  const reset = selectedEvents(p).at(-1);
  assert.equal(reset.context, 'run-b:sft');
  assert.notEqual(reset.serial, old.serial);
  assert.equal(text(p), '');
  renderDelta(p, [row('first')], 'first', delta('first', 0, 40, [token('Task B full answer')]), {
    context: 'run-b:sft', ack_serial: reset.serial});
  assert.equal(text(p), 'Task B full answer');
  assert(!p.d.body.textContent.includes('Task A answer'));
});

scenario('terminal_metadata_waits_until_all_real_events_have_been_read', p => {
  const rows = [row('first', {status: 'completed'})];
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('Part one')], {status: 'completed'}));
  assert.equal(p.get('stream-status').textContent, 'Loading the complete response');
  assert.equal(text(p), 'Part one');
  assert(!p.get('stream-note').textContent.includes('response is complete'));
  renderDelta(p, rows, 'first', delta('first', 20, 40, [token(' plus final')], {
    status: 'completed', done: true}));
  assert.equal(text(p), 'Part one plus final');
  assert.equal(p.get('stream-status').textContent, 'Response received');
  assert.match(p.get('stream-note').textContent, /quality depends on the node checks/);
});

scenario('legacy_journal_fallback_shows_real_snapshot_and_partial_warning', p => {
  const rows = [row('first', {status: 'completed'})];
  const legacy = {id: 'first', legacy: true, offset: 0, next_offset: 0, events: [],
    text: 'Old real bounded output', reasoning: 'Old reasoning', status: 'completed', done: true, truncated: true};
  renderDelta(p, rows, 'first', legacy);
  const node = p.get('answer-output').firstChild;
  assert.equal(text(p), legacy.text);
  assert.equal(p.get('reasoning-output').textContent, legacy.reasoning);
  assert.match(p.get('stream-note').textContent, /only part of the output/);
  assert.equal(selectedEvents(p).length, 0);
  renderDelta(p, rows, 'first', {...legacy, text: 'Corrected old snapshot'});
  assert.equal(text(p), 'Corrected old snapshot');
  assert.equal(p.get('answer-output').firstChild, node);
  assert.equal(selectedEvents(p).length, 0);
});

scenario('delta_scroll_pause_survives_new_tokens_and_reasoning_toggle', p => {
  const rows = [row()];
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('Reasoning', 'reasoning')]));
  p.get('reasoning-label').click();
  assert.equal(p.get('reasoning-section').open, false);
  p.reader.dispatchEvent(new p.w.Event('scroll'));
  p.reader.scrollTop = 35;
  p.reader.dispatchEvent(new p.w.Event('scroll'));
  p.setHeight(600);
  renderDelta(p, rows, 'first', delta('first', 20, 40, [token('Next token'), token(' continues', 'reasoning')]));
  assert.equal(p.reader.scrollTop, 35);
  assert.equal(p.get('reasoning-section').open, false);
  assert.equal(p.get('follow-bottom').hidden, false);
  p.get('follow-bottom').click();
  assert.equal(p.reader.scrollTop, 500);
  assert.equal(p.get('reasoning-output').textContent, 'Reasoning continues');
});

scenario('missing_delta_request_clears_cursor_and_requests_prefix_when_it_returns', p => {
  const rows = [row('first'), row('other')];
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('First answer')]));
  p.get('request-tabs').children[1].click();
  const choice = selectedEvents(p).at(-1);
  renderDelta(p, rows, 'other', delta('other', 0, 20, [token('Other prefix')]), {
    ack_serial: choice.serial, follow_latest: false});
  renderDelta(p, [row('first')], 'other', null, {ack_serial: choice.serial, follow_latest: false});
  assert.equal(text(p), '');
  assert.match(p.get('stream-empty').textContent, /no longer in the preview/);
  renderDelta(p, rows, 'other', delta('other', 20, 40, [token('new tail')]), {
    ack_serial: choice.serial, follow_latest: false});
  assert.equal(text(p), '');
  const reset = selectedEvents(p).at(-1);
  assert.equal(reset.reset, true);
  renderDelta(p, rows, 'other', delta('other', 0, 40, [token('Other prefix new tail')]), {
    ack_serial: reset.serial, follow_latest: false});
  assert.equal(text(p), 'Other prefix new tail');
});

scenario('consumed_cursor_receipt_is_exact_and_retries_are_throttled_without_replay', p => {
  const rows = [row()];
  const first = delta('first', 0, 20, [token('Real returned text')]);
  renderDelta(p, rows, 'first', first);
  const received = receipts(p)[0];
  assert.deepEqual(JSON.parse(JSON.stringify(received)), {
    context: 'run-a:sft', request_id: 'first', next_offset: 20, receipt: true,
    selection_serial: null, serial: received.serial});
  assert.equal(Number.isSafeInteger(received.next_offset), true);
  assert.equal(text(p), 'Real returned text');
  for (let poll = 0; poll < 7; poll++) {
    p.advance(100);
    renderDelta(p, rows, 'first', first);
  }
  assert.equal(receipts(p).length, 1);
  p.advance(100);
  renderDelta(p, rows, 'first', first);
  assert.equal(receipts(p).length, 2);
  assert.notEqual(receipts(p)[1].serial, received.serial);
  assert.equal(text(p), 'Real returned text');
  renderDelta(p, rows, 'first', delta('first', 20, 40, [token(' plus next')]));
  assert.equal(receipts(p).at(-1).next_offset, 40);
  assert.equal(text(p), 'Real returned text plus next');
  assert.equal(selectedEvents(p).length, 0);
});

scenario('empty_and_terminal_polls_update_status_without_triggering_reruns', p => {
  const rows = [row()];
  for (let poll = 0; poll < 12; poll++) {
    p.advance(1000);
    renderDelta(p, rows, 'first', delta('first', 0, 0));
  }
  assert.equal(componentEvents(p).length, 0, 'waiting for the first token needs no callback');
  assert.equal(p.get('stream-status').textContent, 'Receiving');

  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('Actual answer')]));
  const before = componentEvents(p).length;
  assert.equal(before, 1, 'an advancing data offer is acknowledged');
  for (let poll = 0; poll < 12; poll++) {
    p.advance(1000);
    renderDelta(p, rows, 'first', delta('first', 20, 20));
  }
  assert.equal(componentEvents(p).length, before, 'idle polls cannot create another rerun');
  assert.equal(text(p), 'Actual answer');

  for (let poll = 0; poll < 12; poll++) {
    p.advance(1000);
    renderDelta(p, [row('first', {status: 'completed'})], 'first', delta('first', 20, 20, [], {
      status: 'completed', done: true}));
  }
  assert.equal(componentEvents(p).length, before, 'terminal metadata needs no cursor acknowledgement');
  assert.equal(p.get('stream-status').textContent, 'Response received');
  assert.equal(p.get('stream-cursor').hidden, true);
  assert.equal(text(p), 'Actual answer');
});

scenario('lost_data_receipt_retries_after_idle_poll_without_losing_characters', p => {
  const rows = [row()];
  const first = delta('first', 0, 20, [token('开始🙂'), token('先核对', 'reasoning')]);
  renderDelta(p, rows, 'first', first);
  const initial = receipts(p)[0];
  p.advance(400);
  renderDelta(p, rows, 'first', delta('first', 20, 20));
  assert.equal(receipts(p).length, 1, 'an idle poll neither retries nor discards the pending receipt');
  p.advance(400);
  renderDelta(p, rows, 'first', first);
  assert.equal(receipts(p).length, 2, 'the still-unconfirmed advancing offer retries after 750ms');
  assert.notEqual(receipts(p)[1].serial, initial.serial);
  assert.equal(text(p), '开始🙂', 'the repeated offer cannot replay its characters');
  assert.equal(p.get('reasoning-output').textContent, '先核对');
  renderDelta(p, rows, 'first', delta('first', 20, 40, [token('，继续。'), token('来源。', 'reasoning')]));
  assert.equal(text(p), '开始🙂，继续。');
  assert.equal(p.get('reasoning-output').textContent, '先核对来源。');
  assert.equal(receipts(p).at(-1).next_offset, 40);
  const before = receipts(p).length;
  p.advance(1000);
  renderDelta(p, rows, 'first', delta('first', 40, 40, [], {status: 'completed', done: true}));
  assert.equal(receipts(p).length, before);
  assert.equal(p.get('stream-status').textContent, 'Response received');
  assert.equal(text(p), '开始🙂，继续。');
});

scenario('receipt_does_not_lock_selection_and_uses_the_accepted_selection_epoch', p => {
  const rows = [row('first'), row('other')];
  renderDelta(p, rows, 'first', delta('first', 0, 20, [token('First response')]), {
    ack_serial: 'restored-selection', follow_latest: false});
  assert.equal(receipts(p).at(-1).selection_serial, 'restored-selection');
  p.get('request-tabs').children[1].click();
  const selection = selectedEvents(p).at(-1);
  assert.equal(selection.request_id, 'other');
  const before = receipts(p).length;
  renderDelta(p, rows, 'first', delta('first', 20, 40, [token('stale')]), {
    ack_serial: 'restored-selection', follow_latest: false});
  assert.equal(receipts(p).length, before);
  assert.equal(text(p), '');
  renderDelta(p, rows, 'other', delta('other', 0, 30, [token('Other response')]), {
    ack_serial: selection.serial, follow_latest: false});
  assert.equal(receipts(p).at(-1).selection_serial, selection.serial);
  assert.equal(receipts(p).at(-1).request_id, 'other');
  assert.equal(receipts(p).at(-1).next_offset, 30);
  assert.equal(text(p), 'Other response');
});

scenario('empty_terminal_delta_completes_and_invalid_cursors_never_get_receipts', p => {
  const rows = [row('first', {status: 'completed'})];
  renderDelta(p, rows, 'first', delta('first', 0, 0, [], {status: 'completed', done: true}));
  assert.equal(p.get('stream-status').textContent, 'Response received');
  assert.equal(receipts(p).length, 0, 'a terminal empty cursor does not cause a component rerun');
  const before = receipts(p).length;
  renderDelta(p, rows, 'first', delta('first', Number.MAX_SAFE_INTEGER + 1, Number.MAX_SAFE_INTEGER + 1));
  assert.equal(receipts(p).length, before);
  assert.equal(selectedEvents(p).at(-1).reset, true);
  assert.equal(text(p), '');
});

scenario('reader_height_override_restores_default_without_touching_pinned_content', p => {
  const rows = [row('first', {text: 'Latest answer'}), row('other', {
    text: 'Pinned complete answer', reasoning: 'Pinned reasoning', status: 'completed'})];
  const height = () => p.reader.style.getPropertyValue('--stream-reader-height');
  p.render(rows);
  assert.equal(height(), '', 'standalone readers leave their CSS height defaults intact');
  assert.match(css, /max-height:var\(--stream-reader-height,460px\)/);
  assert.match(css, /max-height:var\(--stream-reader-height,400px\)/);
  p.get('request-tabs').children[1].click();
  if (!p.get('reasoning-section').open) p.get('reasoning-label').click();
  p.get('reasoning-label').click();
  const answerNode = p.get('answer-output').firstChild;
  const reasoningNode = p.get('reasoning-output').firstChild;
  const pinnedTab = p.get('request-tabs').children[1];
  const choices = selectedEvents(p).length;
  function assertPinned() {
    assert.equal(text(p), 'Pinned complete answer');
    assert.equal(p.get('reasoning-output').textContent, 'Pinned reasoning');
    assert.equal(p.get('answer-output').firstChild, answerNode);
    assert.equal(p.get('reasoning-output').firstChild, reasoningNode);
    assert.equal(p.get('request-tabs').children[1], pinnedTab);
    assert.equal(pinnedTab.getAttribute('aria-selected'), 'true');
    assert.equal(p.get('reasoning-section').open, false);
    assert.equal(p.get('follow-request').hidden, false);
    assert.equal(selectedEvents(p).length, choices, 'height does not select or reset a request');
  }
  for (const reader_height of [160, 220, 380, 600]) {
    p.render(rows, {reader_height});
    assert.equal(height(), `${reader_height}px`);
    assertPinned();
  }
  p.render(rows);
  assert.equal(height(), '', 'omitting the option removes the previous override');
  assertPinned();
  for (const reader_height of [null, true, false, 159, 601, 220.5, '220', {}, []]) {
    p.render(rows, {reader_height: 220});
    assert.equal(height(), '220px');
    p.render(rows, {reader_height});
    assert.equal(height(), '', 'invalid height restores the CSS default instead of retaining a stale override');
    assertPinned();
  }
});

scenario('reader_height_changes_preserve_delta_prefix_request_pin_and_receipts', p => {
  const rows = [row('first'), row('other')];
  renderDelta(p, rows, 'first', delta('first', 0, 10, [token('Latest response')]));
  p.get('request-tabs').children[1].click();
  const selection = selectedEvents(p).at(-1);
  const pinnedOptions = {ack_serial: selection.serial, follow_latest: false, reader_height: 220};
  const prefix = delta('other', 0, 20, [token('Pinned prefix'), token('Useful basis', 'reasoning')]);
  renderDelta(p, rows, 'other', prefix, pinnedOptions);
  const answerNode = p.get('answer-output').firstChild;
  const reasoningNode = p.get('reasoning-output').firstChild;
  const pinnedTab = p.get('request-tabs').children[1];
  const choices = selectedEvents(p).length, beforeReceipts = receipts(p).length;
  if (!p.get('reasoning-section').open) p.get('reasoning-label').click();
  p.get('reasoning-label').click();
  p.reader.dispatchEvent(new p.w.Event('scroll')); // Confirm the preceding automatic scroll.
  p.reader.scrollTop = 35;
  p.reader.dispatchEvent(new p.w.Event('scroll'));
  assert.equal(p.get('follow-bottom').hidden, false);

  renderDelta(p, rows, 'other', prefix, {...pinnedOptions, reader_height: 380});
  assert.equal(p.reader.style.getPropertyValue('--stream-reader-height'), '380px');
  assert.equal(text(p), 'Pinned prefix', 'changing height cannot replay a previously consumed prefix');
  assert.equal(receipts(p).length, beforeReceipts, 'a height-only render does not duplicate the cursor receipt');
  assert.equal(selectedEvents(p).length, choices, 'changing height does not reset the request epoch');

  renderDelta(p, rows, 'other', delta('other', 20, 30, [token(' plus next token'),
    token(' continues', 'reasoning')]), {...pinnedOptions, reader_height: 380});
  assert.equal(text(p), 'Pinned prefix plus next token');
  assert.equal(p.get('reasoning-output').textContent, 'Useful basis continues');
  assert.equal(p.get('answer-output').firstChild, answerNode);
  assert.equal(p.get('reasoning-output').firstChild, reasoningNode);
  assert.equal(p.get('request-tabs').children[1], pinnedTab);
  assert.equal(pinnedTab.getAttribute('aria-selected'), 'true');
  assert.equal(p.reader.scrollTop, 35, 'the shorter or wider reader does not resume manual output following');
  assert.equal(p.get('reasoning-section').open, false);
  assert.equal(p.get('follow-request').hidden, false);
  assert.equal(selectedEvents(p).length, choices);
  assert.equal(receipts(p).at(-1).next_offset, 30);
  assert.equal(receipts(p).at(-1).request_id, 'other');
  assert.equal(receipts(p).at(-1).selection_serial, selection.serial);

  renderDelta(p, rows, 'other', delta('other', 30, 30), {
    ack_serial: selection.serial, follow_latest: false});
  assert.equal(p.reader.style.getPropertyValue('--stream-reader-height'), '');
  assert.equal(text(p), 'Pinned prefix plus next token');
  assert.equal(selectedEvents(p).length, choices);
});

const readField = (p, key) => [...p.get('reading-fields').children].find(node => node.dataset.field === key)
  ?.querySelector('.reading-field-text')?.textContent;
function parseCounter(p) {
  const original = p.w.JSON.parse;
  let count = 0;
  p.w.JSON.parse = (...args) => {count++; return original(...args);};
  return () => count;
}

scenario('read_view_is_default_and_raw_roundtrip_keeps_real_tokens', p => {
  const raw = '{"text":"Line one\\nLine two","reason":"Original record"}';
  const rows = [row('first', {status: 'completed', text: raw})];
  p.render(rows);
  assert.equal(p.get('stream-read').textContent, 'Read');
  assert.equal(p.get('stream-raw').textContent, 'Raw');
  assert.equal(p.get('stream-read').getAttribute('aria-pressed'), 'true');
  assert.equal(p.get('read-section').hidden, false);
  assert.equal(p.get('answer-section').hidden, true);
  assert.equal(readField(p, 'text'), 'Line one\nLine two');
  const rawNode = p.get('answer-output').firstChild, beforeEvents = componentEvents(p).length;
  p.get('stream-raw').click();
  assert.equal(p.get('read-section').hidden, true);
  assert.equal(p.get('answer-section').hidden, false);
  assert.equal(text(p), raw);
  p.get('stream-read').click();
  assert.equal(readField(p, 'text'), 'Line one\nLine two');
  assert.equal(p.get('answer-output').firstChild, rawNode);
  assert.equal(componentEvents(p).length, beforeEvents, 'views are local and do not send a receipt');
  p.render(rows, {language: 'zh'});
  assert.equal(p.get('stream-read').textContent, '阅读');
  assert.equal(p.get('stream-raw').textContent, '原始');
});

scenario('complete_cpt_text_and_sft_fields_are_readable_without_losing_reasoning', p => {
  const cpt = {text: '公式 $x=1$。\n章节二', uncertain: ['Footnote'], removed_noise: ['Page header'], reason: 'Kept equations'};
  p.render([row('first', {stage: 'cpt', status: 'completed', text: JSON.stringify(cpt)})]);
  for (const key of Object.keys(cpt)) assert.equal(readField(p, key), Array.isArray(cpt[key]) ? cpt[key].join('\n') : cpt[key]);
  const sft = {question: 'How does this work?', answer: 'It uses the supplied source.', reasoning: 'Compare the evidence.'};
  const raw = JSON.stringify(sft);
  p.render([row('sft', {status: 'completed', text: raw, reasoning: 'Actual API reasoning'})]);
  for (const [key, value] of Object.entries(sft)) assert.equal(readField(p, key), value);
  assert.equal(p.get('reasoning-output').textContent, 'Actual API reasoning');
  assert.equal(readField(p, 'text'), undefined, 'a prior CPT field cannot leak into SFT');
  p.get('stream-raw').click();
  assert.equal(text(p), raw);
});

scenario('judge_scores_and_preservation_are_readable_without_quality_approval', p => {
  const verdict = {scores: {correctness: 5, grounding: 4}, preservation: {source_faithful: true, coverage_complete: false},
    score: 4.5, reason: 'A paragraph is missing.', accepted: false};
  p.render([row('first', {role: 'jev', status: 'completed', text: JSON.stringify(verdict)})]);
  assert.equal(readField(p, 'scores'), 'Correctness: 5\nGrounding: 4');
  assert.equal(readField(p, 'preservation'), 'Faithful to source: Yes\nComplete coverage: No');
  assert.equal(readField(p, 'score'), '4.5');
  assert.equal(readField(p, 'reason'), verdict.reason);
  assert.equal(readField(p, 'accepted'), 'No');
  assert.match(p.get('stream-note').textContent, /quality depends on the node checks/);
  assert.doesNotMatch(p.get('stream-status').textContent, /accepted|approved/i);
});

scenario('cpt_progressive_decoding_waits_for_complete_escapes_without_json_parse', p => {
  const parses = parseCounter(p), rows = [row('first', {stage: 'cpt'})];
  const pieces = ['{"text":"A', '\\', 'nB', '\\', '"C', '\\', '\\D', '\\', 'tE', '","reason":"Still a draft"}'];
  const expected = ['A', 'A', 'A\nB', 'A\nB', 'A\nB"C', 'A\nB"C', 'A\nB"C\\D', 'A\nB"C\\D', 'A\nB"C\\D\tE', 'A\nB"C\\D\tE'];
  let raw = '';
  pieces.forEach((piece, index) => {
    renderDelta(p, rows, 'first', delta('first', index, index + 1, [token(piece)]));
    raw += piece;
    assert.equal(readField(p, 'text'), expected[index]);
    assert.equal(text(p), raw, 'raw tokens retain every backslash and field');
    assert.equal(parses(), 0, 'progressive decoding cannot parse unfinished JSON');
    assert.equal(p.get('stream-status').textContent, 'Receiving');
  });
  const fieldNode = p.get('reading-fields').firstChild.querySelector('.reading-field-text').firstChild;
  renderDelta(p, rows, 'first', delta('first', pieces.length, pieces.length, [], {status: 'completed', done: true}));
  assert.equal(parses(), 1);
  assert.equal(readField(p, 'reason'), 'Still a draft');
  assert.equal(p.get('reading-fields').firstChild.querySelector('.reading-field-text').firstChild, fieldNode);
});

scenario('unicode_progressive_reading_waits_for_complete_surrogate_pairs', p => {
  const rows = [row('first', {stage: 'cpt'})], parses = parseCounter(p);
  let raw = '{"text":"';
  renderDelta(p, rows, 'first', delta('first', 0, 1, [token(raw)]));
  const escaped = '\\u4e2d\\ud83d\\ude0a';
  [...escaped].forEach((char, index) => {
    raw += char;
    renderDelta(p, rows, 'first', delta('first', index + 1, index + 2, [token(char)]));
    const expected = index < 5 ? '' : index < 17 ? '中' : '中😊';
    assert.equal(readField(p, 'text'), expected);
    assert.equal(parses(), 0);
  });
  const rawEmoji = '🚀';
  renderDelta(p, rows, 'first', delta('first', 19, 20, [token(rawEmoji[0])]));
  assert.equal(readField(p, 'text'), '中😊');
  renderDelta(p, rows, 'first', delta('first', 20, 21, [token(rawEmoji[1]), token('"}')]));
  assert.equal(readField(p, 'text'), '中😊🚀');
  renderDelta(p, rows, 'first', delta('first', 21, 21, [], {status: 'completed', done: true}));
  assert.equal(readField(p, 'text'), '中😊🚀');
  assert.equal(parses(), 1);
});

scenario('complex_partial_json_stays_raw_until_full_receipt', p => {
  const parses = parseCounter(p), rows = [row('first')], raw = '{"question":"A?","answer":"B","reasoning":"C"}';
  [...raw].forEach((char, index) => {
    renderDelta(p, rows, 'first', delta('first', index, index + 1, [token(char)]));
    assert.equal(p.get('read-section').hidden, true);
    assert.equal(text(p), raw.slice(0, index + 1));
    assert.equal(parses(), 0);
    assert.doesNotMatch(p.get('stream-panel').textContent, /cannot parse|parse failed|无法解析|解析失败/i);
  });
  assert.match(p.get('answer-label').textContent, /Raw fragment/);
  renderDelta(p, rows, 'first', delta('first', raw.length, raw.length, [], {status: 'completed', done: true}));
  assert.equal(parses(), 1);
  assert.equal(readField(p, 'question'), 'A?');
  assert.equal(readField(p, 'answer'), 'B');
});

scenario('completed_metadata_with_more_events_does_not_parse_a_prefix', p => {
  const parses = parseCounter(p), rows = [row('first', {status: 'completed'})];
  const raw = '{"question":"A?","answer":"B"}';
  renderDelta(p, rows, 'first', delta('first', 0, 1, [token(raw)], {status: 'completed', done: false}));
  assert.equal(p.get('stream-status').textContent, 'Loading the complete response');
  assert.equal(p.get('read-section').hidden, true);
  assert.equal(parses(), 0, 'a balanced prefix is not proof of full receipt');
  renderDelta(p, rows, 'first', delta('first', 1, 2, [token('\n')], {status: 'completed', done: true}));
  assert.equal(parses(), 1);
  assert.equal(readField(p, 'answer'), 'B');
  assert.equal(text(p), raw + '\n');
});

scenario('interrupted_and_truncated_json_never_becomes_a_complete_sample', p => {
  const parses = parseCounter(p), raw = '{"question":"A?","answer":"B"}';
  p.render([row('first', {status: 'interrupted', text: raw})]);
  assert.equal(parses(), 0);
  assert.equal(p.get('read-section').hidden, true);
  assert.match(p.get('stream-note').textContent, /not a training sample/);
  p.render([row('first', {status: 'completed', text: raw, truncated: true})]);
  assert.equal(parses(), 0);
  assert.equal(p.get('read-section').hidden, true);
  assert.match(p.get('stream-note').textContent, /only part/);
  renderDelta(p, [row('first', {status: 'completed'})], 'first', {id: 'first', legacy: true,
    text: raw, reasoning: '', status: 'completed', done: true, truncated: true});
  assert.equal(parses(), 0);
  renderDelta(p, [row('cpt', {stage: 'cpt', status: 'interrupted'})], 'cpt', delta('cpt', 0, 1,
    [token('{"text":"Readable\\nfragment\\')], {status: 'interrupted', done: true}));
  assert.equal(readField(p, 'text'), 'Readable\nfragment');
  assert.equal(parses(), 0);
  assert.match(p.get('stream-note').textContent, /unfinished text is not a training sample/);
});

scenario('fenced_json_preserves_inner_code_fences_and_xss_stays_inert', p => {
  const attack = '<img src=x onerror="window.infected=true"><script>window.infected=true</script>';
  const value = {question: attack, answer: 'Example:\n```python\nprint("hi")\n```', '__proto__': 'not inherited'};
  // Build an own __proto__ key too, rather than the object literal setter.
  const raw = '```json\n' + JSON.stringify({...value, ['__proto__']: 'Prototype text'}) + '\n```';
  p.render([row('first', {status: 'completed', text: raw})]);
  assert.equal(readField(p, 'question'), attack);
  assert.equal(readField(p, 'answer'), value.answer);
  assert.equal(readField(p, '__proto__'), 'Prototype text');
  assert.equal(p.get('reading-fields').querySelector('img,script,svg'), null);
  assert.equal(p.w.infected, undefined);
  assert.equal(text(p), raw);
  p.get('stream-raw').click();
  assert.equal(p.get('answer-output').querySelector('img,script,svg'), null);
  p.render([row('cpt', {stage: 'cpt', text: '```json\n{"text":"A\\nB\\'})]);
  p.get('stream-read').click();
  assert.equal(readField(p, 'text'), 'A\nB');
});

scenario('invalid_json_escapes_and_free_text_remain_visible_without_parse_errors', p => {
  const invalid = '{"text":"Known prefix\\qInvalid"}';
  p.render([row('first', {text: invalid})]);
  assert.equal(p.get('read-section').hidden, true);
  assert.equal(text(p), invalid);
  p.render([row('first', {status: 'completed', text: invalid})]);
  assert.equal(p.get('answer-section').hidden, false);
  assert.equal(text(p), invalid);
  assert.doesNotMatch(p.get('stream-panel').textContent, /cannot parse|parse failed|无法解析|解析失败/i);
  p.render([row('first', {status: 'completed', text: 'Free text with `code` and <markup>.'})]);
  assert.equal(p.get('read-section').hidden, true);
  assert.equal(text(p), 'Free text with `code` and <markup>.');
});

scenario('reading_fields_clear_on_context_switch_and_retry', p => {
  p.render([row('first', {text: '{"text":"Private first'})]);
  assert.equal(readField(p, 'text'), 'Private first');
  p.render([row('first', {text: '{"text":"Retry body'})]);
  assert.equal(readField(p, 'text'), 'Retry body');
  assert.doesNotMatch(p.get('reading-fields').textContent, /Private/);
  p.get('stream-raw').click();
  p.render([row('first')], {context: 'other-run:cpt'});
  assert.equal(p.get('stream-read').getAttribute('aria-pressed'), 'true');
  assert.equal(p.get('reading-fields').textContent, '');
  assert.equal(text(p), '');
  assert.equal(p.get('read-section').hidden, true);
});

scenario('view_switching_preserves_cursor_selection_and_manual_scroll', p => {
  const rows = [row('first'), row('other')];
  renderDelta(p, rows, 'first', delta('first', 0, 1, [token('{"text":"Latest') ]));
  p.get('request-tabs').children[1].click();
  const selection = selectedEvents(p).at(-1), pinned = {ack_serial: selection.serial, follow_latest: false};
  renderDelta(p, rows, 'other', delta('other', 0, 7, [token('{"text":"Pinned')]), pinned);
  p.reader.dispatchEvent(new p.w.Event('scroll'));
  p.reader.scrollTop = 30;
  p.reader.dispatchEvent(new p.w.Event('scroll'));
  const beforeEvents = componentEvents(p).length;
  p.get('stream-raw').click();
  p.get('stream-read').click();
  assert.equal(p.reader.scrollTop, 30);
  assert.equal(p.get('follow-bottom').hidden, false);
  assert.equal(p.get('follow-request').hidden, false);
  assert.equal(componentEvents(p).length, beforeEvents);
  assert.equal(p.get('request-tabs').children[1].getAttribute('aria-selected'), 'true');
  renderDelta(p, rows, 'other', delta('other', 7, 9, [token('\\ncontinued')]), pinned);
  assert.equal(readField(p, 'text'), 'Pinned\ncontinued');
  assert.equal(p.reader.scrollTop, 30);
  assert.equal(receipts(p).at(-1).next_offset, 9);
  assert.equal(receipts(p).at(-1).selection_serial, selection.serial);
  assert.equal(selectedEvents(p).length, 1);
});

scenario('identical_structured_snapshots_do_not_parse_or_rebuild_fields', p => {
  const parses = parseCounter(p), rows = [row('first', {status: 'completed', text: '{"question":"A?","answer":"B"}'})];
  p.render(rows);
  assert.equal(parses(), 1);
  const fields = [...p.get('reading-fields').children], bodyNode = fields[1].querySelector('.reading-field-text').firstChild;
  const observer = new p.w.MutationObserver(() => {});
  observer.observe(p.get('stream-panel'), {subtree: true, childList: true, characterData: true});
  p.render(rows);
  assert.equal(observer.takeRecords().length, 0);
  assert.equal(parses(), 1);
  assert.equal(p.get('reading-fields').children[1], fields[1]);
  assert.equal(fields[1].querySelector('.reading-field-text').firstChild, bodyNode);
});

scenario('manual_history_opens_at_top_while_active_and_latest_keep_following', p => {
  const latest = row('latest', {text: 'New live text', started_at: '2026-10-07T11:00:00Z'});
  const history = row('history', {text: '{"text":"Original beginning'});
  const interrupted = row('interrupted', {status: 'interrupted', text: '{"text":"Interrupted beginning',
    started_at: '2026-10-07T09:00:00Z'});
  p.render([latest, history, interrupted]);
  assert.equal(p.reader.scrollTop, 200, 'latest initially follows the live output');
  const fullHistory = {...history, status: 'completed', text: '{"text":"Original beginning\\nRest of document","reason":"Check result"}'};
  p.render([latest, fullHistory, interrupted]);
  p.get('request-tabs').children[1].click();
  assert.equal(p.reader.scrollTop, 0, 'an existing tab uses the new completed status');
  assert.equal(readField(p, 'text'), 'Original beginning\nRest of document');
  assert.equal(p.get('follow-bottom').hidden, false);
  p.reader.dispatchEvent(new p.w.Event('scroll')); // Acknowledge the explicit scroll to top.
  p.render([{...latest, text: 'New live text grew'}, fullHistory, interrupted]);
  assert.equal(p.reader.scrollTop, 0, 'background output does not move a historical reading');
  assert.equal(text(p), fullHistory.text);
  p.get('request-tabs').children[2].click();
  assert.equal(p.reader.scrollTop, 0, 'interrupted history also starts at the top');
  assert.match(p.get('stream-note').textContent, /not a training sample/);
  p.get('request-tabs').children[0].click();
  assert.equal(p.reader.scrollTop, 200, 'selecting a running request resumes live following');
  p.get('request-tabs').children[1].click();
  p.get('follow-request').click();
  assert.equal(p.reader.scrollTop, 200, 'following latest explicitly resumes automatic scrolling');
  // If the latest request is the same completed row, choose() does not change
  // its id. Follow latest must still override a prior historical reading.
  p.render([fullHistory, interrupted]);
  p.get('request-tabs').children[0].click();
  assert.equal(p.reader.scrollTop, 0);
  p.get('follow-request').click();
  assert.equal(p.reader.scrollTop, 200);

  const rows = [row('latest', {started_at: '2026-10-07T11:00:00Z'}), fullHistory];
  renderDelta(p, rows, 'latest', delta('latest', 0, 1, [token('Streaming')]), {context: 'delta-history:sft'});
  [...p.get('request-tabs').children].find(button => button.dataset.status === 'completed').click();
  const selection = selectedEvents(p).at(-1), pinned = {context: 'delta-history:sft', ack_serial: selection.serial, follow_latest: false};
  const beforeReceipts = receipts(p).length;
  renderDelta(p, rows, 'history', delta('history', 0, 8, [token(fullHistory.text)], {status: 'completed', done: true}), pinned);
  assert.equal(p.reader.scrollTop, 0, 'a terminal delta acknowledgement preserves the selected reading position');
  assert.equal(readField(p, 'text'), 'Original beginning\nRest of document');
  assert.equal(receipts(p).length, beforeReceipts + 1);
  assert.equal(receipts(p).at(-1).next_offset, 8);
  assert.equal(receipts(p).at(-1).selection_serial, selection.serial);
  renderDelta(p, rows, 'history', delta('history', 8, 8, [], {status: 'completed', done: true}), pinned);
  assert.equal(p.reader.scrollTop, 0);
  assert.equal(receipts(p).length, beforeReceipts + 1, 'the reading position does not replay a receipt');
  assert.equal(text(p), fullHistory.text);
});

console.log(JSON.stringify({scenarios: results}));
if (Object.values(results).some(result => !result.ok)) process.exitCode = 1;
