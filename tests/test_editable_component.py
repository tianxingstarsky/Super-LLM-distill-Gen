"""editable_chat 自定义组件前端回归测试（无浏览器）。

lib/components/editable_chat/index.html 的新行为：
  1. 重复收到同 messages 签名的 streamlit:render 不得重置未保存草稿；
  2. 保存只回写 contenteditable="true" 且 data-dirty="true" 的字段，
     未改动的字段（缺失/null 值/结构化 list 内容）原样保留；
  3. 非字符串 content 只读展示，绝不写回成字符串；
  4. 「重置改动」清空 dirty、恢复原文并禁用按钮。

做法：用 node（本机已安装）在 vm 里跑组件内联 <script>，配一个最小 DOM 假实现
（innerHTML 解析、dataset、querySelector(All)、closest、事件分发、postMessage 记录），
全程无浏览器、无网络；只读取被测 index.html，不在 tmp 之外写任何文件。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPONENT_HTML = ROOT / "lib" / "components" / "editable_chat" / "index.html"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node 不可用，跳过可编辑组件 DOM 回归")

SCENARIOS = (
    "boot: componentReady + setFrameHeight posted",
    "same-signature render keeps draft; changed render resets",
    "save writes only dirty editable fields; absent/null/list metadata kept",
    "non-string content rendered readonly; dirty force cannot write it",
    "reset clears dirty and restores original text",
)

# 最小 DOM 假实现 + 场景驱动。只依赖 node 内置 fs/vm；读取 index.html 路径由 argv 传入。
HARNESS_JS = r'''
'use strict';
const fs = require('fs');
const vm = require('vm');

const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf8');
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) {
  console.error('no inline <script> found in ' + htmlPath);
  process.exit(2);
}
const componentScript = match[1];

function decodeEntities(s) {
  return s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
}

function parseAttrs(src) {
  const attrs = {};
  const re = /([\w-]+)="([^"]*)"/g;
  let m;
  while ((m = re.exec(src))) attrs[m[1]] = m[2];
  return attrs;
}

function parseNodes(htmlStr) {
  const out = [];
  const stack = [out];
  const re = /<(\/?)([a-zA-Z0-9]+)((?:\s+[\w-]+="[^"]*")*)\s*(\/?)>|([^<]+)/g;
  let m;
  while ((m = re.exec(htmlStr))) {
    if (m[5] !== undefined) {
      const text = decodeEntities(m[5]);
      if (text) stack[stack.length - 1].push({ textNode: true, textContent: text, parentNode: null, children: [] });
    } else if (m[1] === '/') {
      stack.pop();
    } else {
      const node = new El(m[2]);
      const attrs = parseAttrs(m[3] || '');
      for (const key of Object.keys(attrs)) {
        if (key.indexOf('data-') === 0) node.dataset[key.slice(5)] = attrs[key];
        else if (key === 'class') { node.attrs[key] = attrs[key]; node.className = attrs[key]; }
        else node.attrs[key] = attrs[key];
      }
      stack[stack.length - 1].push(node);
      if (!m[4]) stack.push(node.children);
    }
  }
  return out;
}

class El {
  constructor(tag) {
    this.tag = String(tag).toLowerCase();
    this.children = [];
    this.parentNode = null;
    this.attrs = {};
    this.ownText = '';
    this.handlers = {};
    this.disabled = false;
    this.scrollHeight = 42;
    this._classList = [];
    const el = this;
    this.dataset = new Proxy({}, {
      get(t, key) { return key in t ? t[key] : undefined; },
      set(t, key, value) { t[key] = String(value); el.attrs['data-' + key] = String(value); return true; },
    });
  }
  get tagName() { return this.tag.toUpperCase(); }
  get className() { return this._classList.join(' '); }
  set className(v) { this._classList = String(v).split(/\s+/).filter(Boolean); }
  get classList() { return this._classList; }
  get isContentEditable() { return this.attrs.contenteditable === 'true'; }
  get textContent() {
    if (this.children.length === 0) return this.ownText;
    return this.children.map(function (c) { return c.textContent; }).join('');
  }
  set textContent(v) { this.children = []; this.ownText = String(v); }
  get innerText() { return this.textContent; }
  set innerText(v) { this.children = []; this.ownText = String(v); }
  get innerHTML() { return ''; }
  set innerHTML(v) {
    this.children = [];
    this.ownText = '';
    if (v === '' || v === null || v === undefined) return;
    for (const node of parseNodes(String(v))) {
      node.parentNode = this;
      this.children.push(node);
    }
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); }
  removeEventListener() {}
  matches(selector) {
    const parts = selector.match(/\[[^\]]+\]|\.[\w-]+|[a-zA-Z][\w-]*/g) || [];
    for (const part of parts) {
      if (part.charAt(0) === '[') {
        const m = part.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
        if (!m) return false;
        const name = m[1];
        if (!(name in this.attrs)) return false;
        if (m[2] !== undefined && String(this.attrs[name]) !== m[2]) return false;
      } else if (part.charAt(0) === '.') {
        if (this._classList.indexOf(part.slice(1)) === -1) return false;
      } else if (this.tag !== part.toLowerCase()) {
        return false;
      }
    }
    return true;
  }
  closest(selector) {
    let node = this;
    while (node && node.matches) {
      if (node.matches(selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  querySelectorAll(selector) {
    const found = [];
    const walk = function (node) {
      for (const child of node.children) {
        if (child.matches && child.matches(selector)) found.push(child);
        if (child.children) walk(child);
      }
    };
    walk(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { this.focused = true; }
  scrollIntoView() {}
  fire(type, extra) {
    const event = Object.assign({
      type: type, target: this, currentTarget: this,
      preventDefault: function () {}, stopPropagation: function () {},
    }, extra || {});
    for (const fn of this.handlers[type] || []) fn(event);
    return event;
  }
}

function makeEnv() {
  const byId = {};
  const windowListeners = {};
  const posted = [];

  const windowFake = {
    parent: { postMessage: function (msg) { posted.push(msg); } },
    addEventListener: function (type, fn) { (windowListeners[type] = windowListeners[type] || []).push(fn); },
    removeEventListener: function () {},
    getSelection: function () { return { selectAllChildren: function () {}, collapseToEnd: function () {} }; },
    clipboardData: { getData: function () { return ''; } },
  };

  const documentFake = {
    body: new El('body'),
    activeElement: null,
    getElementById: function (id) { return byId[id] || null; },
    createElement: function (tag) { return new El(tag); },
    execCommand: function () { return true; },
    getSelection: function () { return windowFake.getSelection(); },
  };

  for (const id of ['root', 'save', 'reset', 'cancel', 'status']) byId[id] = new El('div');

  const sandbox = {
    window: windowFake, document: documentFake, console: console,
    setTimeout: setTimeout, clearTimeout: clearTimeout,
  };
  vm.createContext(sandbox);
  vm.runInContext(componentScript, sandbox, { filename: 'editable_chat_inline.js' });

  function fireWindow(type, event) {
    for (const fn of windowListeners[type] || []) fn(event);
  }

  return {
    posted: posted,
    root: byId.root,
    save: byId.save,
    reset: byId.reset,
    cancel: byId.cancel,
    status: byId.status,
    render: function (messages, focusIndex) {
      fireWindow('message', { data: { type: 'streamlit:render', args: {
        messages: messages, focus_index: focusIndex === undefined ? -1 : focusIndex } } });
    },
    bubble: function (index, field) {
      return byId.root.querySelector('[data-i="' + index + '"][data-field="' + field + '"]');
    },
    lastSubmitted: function () {
      const found = posted.filter(function (p) { return p.type === 'streamlit:setComponentValue'; });
      return found.length ? found[found.length - 1] : null;
    },
  };
}

const results = [];
function check(name, fn) {
  try { fn(); results.push({ name: name, ok: true }); }
  catch (error) { results.push({ name: name, ok: false, error: String((error && error.stack) || error) }); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function deepEqual(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

check('boot: componentReady + setFrameHeight posted', function () {
  const env = makeEnv();
  assert(env.posted.some(function (p) { return p.type === 'streamlit:componentReady' && p.apiVersion === 1; }), 'componentReady missing');
  assert(env.posted.some(function (p) { return p.type === 'streamlit:setFrameHeight'; }), 'setFrameHeight missing');
});

check('same-signature render keeps draft; changed render resets', function () {
  const env = makeEnv();
  env.render([{ role: 'assistant', content: 'hello' }]);
  const bubble = env.bubble(0, 'content');
  assert(bubble, 'content bubble missing');
  assert(bubble.attrs.contenteditable === 'true', 'string content must be editable');
  bubble.innerText = 'edited draft';
  env.root.fire('input', { target: bubble });
  assert(env.save.disabled === false, 'save should be enabled after input');
  assert(env.reset.disabled === false, 'reset should be enabled after input');
  assert(bubble.dataset.dirty === 'true', 'field should be marked dirty');
  assert(env.status.textContent === '有未保存的改动', 'dirty status text mismatch');

  // identical messages payload (fresh objects, same signature) => no re-render, draft kept
  env.render([{ role: 'assistant', content: 'hello' }]);
  assert(env.bubble(0, 'content') === bubble, 'same-signature render must not rebuild DOM');
  assert(bubble.innerText === 'edited draft', 'draft text lost on same-signature render');
  assert(env.save.disabled === false, 'save got disabled by same-signature render');
  assert(env.status.textContent === '有未保存的改动', 'dirty status lost on same-signature render');

  // changed messages => real re-render, draft reset to server state
  env.render([{ role: 'assistant', content: 'server changed' }]);
  const fresh = env.bubble(0, 'content');
  assert(fresh && fresh !== bubble, 'changed messages must rebuild DOM');
  assert(fresh.innerText === 'server changed', 're-rendered text mismatch');
  assert(env.save.disabled === true, 'save must be disabled after re-render');
  assert(env.reset.disabled === true, 'reset must be disabled after re-render');
  assert(env.status.textContent === '', 'status must be cleared after re-render');
});

check('save writes only dirty editable fields; absent/null/list metadata kept', function () {
  const env = makeEnv();
  const messages = [
    { role: 'assistant', content: '答', reasoning_content: '想', toolCalls: [{ id: 'c1', name: 'Bash', input: { command: 'ls' } }] },
    { role: 'user', content: '问' },
    { role: 'assistant', content: null, reasoning_content: null },
    { role: 'user', content: [{ type: 'text', text: '看图' }, { type: 'image_url', image_url: { url: 'data:image/png;base64,AA' } }], meta: { keep: true } },
    { role: 'tool', content: 'tool out', toolCallId: 'c1', isError: true },
  ];
  env.render(messages);
  const c0 = env.bubble(0, 'content');
  c0.innerText = '答（改）';
  env.root.fire('input', { target: c0 });
  const r0 = env.bubble(0, 'reasoning_content');
  r0.innerText = '想（改）';
  env.root.fire('input', { target: r0 });
  env.save.fire('click');

  const submitted = env.lastSubmitted();
  assert(submitted, 'no setComponentValue posted on save');
  assert(submitted.dataType === 'json' && typeof submitted.value.nonce === 'number', 'value envelope mismatch');
  const out = submitted.value.messages;
  assert(out.length === 5, 'message count changed');
  assert(out[0].content === '答（改）', 'edited content not written');
  assert(out[0].reasoning_content === '想（改）', 'edited reasoning not written');
  assert(deepEqual(out[0].toolCalls, messages[0].toolCalls), 'untouched toolCalls metadata lost');
  assert(!('reasoning_content' in out[1]), 'absent field must stay absent');
  assert(deepEqual(out[1], messages[1]), 'untouched text message must round-trip unchanged');
  assert(out[2].content === null, 'null content must stay null when untouched');
  assert(out[2].reasoning_content === null, 'null reasoning must stay null');
  assert(deepEqual(out[3].content, messages[3].content), 'list content must stay structured');
  assert(out[3].meta.keep === true, 'dict metadata lost');
  assert(out[4].isError === true && out[4].toolCallId === 'c1', 'tool metadata lost');
  assert(out !== messages && out[3].content !== messages[3].content, 'payload must be a deep copy');
});

check('non-string content rendered readonly; dirty force cannot write it', function () {
  const env = makeEnv();
  const messages = [
    { role: 'user', content: { text: '结构化', n: 1 } },
    { role: 'assistant', content: [{ type: 'text', text: 'x' }] },
    { role: 'assistant', content: 123 },
    { role: 'assistant', content: true },
    { role: 'assistant', content: 'plain' },
  ];
  env.render(messages);
  for (const index of [0, 1, 2, 3]) {
    const bubble = env.bubble(index, 'content');
    assert(bubble.attrs.contenteditable === 'false', 'index ' + index + ' must be readonly');
    const hint = bubble.parentNode.querySelector('.hint');
    assert(hint && hint.textContent.indexOf('只读') !== -1, 'index ' + index + ' hint missing');
  }
  const shown = env.bubble(0, 'content').innerText;
  assert(shown.indexOf('"n": 1') !== -1, 'structured content should be shown as JSON text');
  assert(env.bubble(4, 'content').attrs.contenteditable === 'true', 'plain string stays editable');

  // input on readonly must not arm save
  const ro = env.bubble(0, 'content');
  env.root.fire('input', { target: ro });
  assert(env.save.disabled === true, 'readonly input must not enable save');

  // even force-marked dirty, readonly fields are never written by the selector
  ro.dataset.dirty = 'true';
  env.save.fire('click');
  const out = env.lastSubmitted().value.messages;
  assert(typeof out[0].content === 'object' && out[0].content !== null && out[0].content.n === 1,
    'readonly structured content must be preserved as-is');
  assert(deepEqual(out[1].content, messages[1].content), 'readonly list must be preserved as-is');
  assert(out[2].content === 123, 'readonly number must be preserved as-is');
  assert(out[3].content === true, 'readonly bool must be preserved as-is');
});

check('reset clears dirty and restores original text', function () {
  const env = makeEnv();
  env.render([{ role: 'assistant', content: 'orig', reasoning_content: 'think' }]);
  const bubble = env.bubble(0, 'content');
  bubble.innerText = 'changed';
  env.root.fire('input', { target: bubble });
  const reason = env.bubble(0, 'reasoning_content');
  reason.innerText = 'changed think';
  env.root.fire('input', { target: reason });
  assert(env.save.disabled === false, 'save should be enabled before reset');
  const submitsBefore = env.posted.filter(function (p) { return p.type === 'streamlit:setComponentValue'; }).length;

  env.reset.fire('click');
  const after = env.bubble(0, 'content');
  assert(after.innerText === 'orig', 'reset must restore original text');
  assert(env.bubble(0, 'reasoning_content').innerText === 'think', 'reset must restore reasoning');
  assert(env.save.disabled === true, 'save must be disabled after reset');
  assert(env.reset.disabled === true, 'reset must be disabled after reset');
  assert(env.status.textContent === '', 'status must be cleared after reset');
  assert(after.dataset.dirty === undefined, 'dirty flag must be cleared after reset');
  const submitsAfter = env.posted.filter(function (p) { return p.type === 'streamlit:setComponentValue'; }).length;
  assert(submitsAfter === submitsBefore, 'reset must not submit any value');

  // after reset, same-signature server render still does not rebuild
  env.render([{ role: 'assistant', content: 'orig', reasoning_content: 'think' }]);
  assert(env.bubble(0, 'content') === after, 'same-signature render after reset must not rebuild');
});

const failures = results.filter(function (r) { return !r.ok; });
process.stdout.write(JSON.stringify({ total: results.length, failed: failures.length, results: results }, null, 2) + '\n');
process.exitCode = failures.length ? 1 : 0;
'''


@pytest.fixture(scope="module")
def dom_report(tmp_path_factory):
    """把内嵌 harness 写入本测试自己的 tmp 目录，用 node 跑一次并返回场景报告。"""
    assert COMPONENT_HTML.is_file(), f"组件文件不存在：{COMPONENT_HTML}"
    tmp_dir = tmp_path_factory.mktemp("editable_chat_dom")
    harness = tmp_dir / "dom_harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    env = {**os.environ, "TEMP": str(tmp_dir), "TMP": str(tmp_dir)}
    proc = subprocess.run(
        [NODE, str(harness), str(COMPONENT_HTML)],
        cwd=str(tmp_dir), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.stdout.strip(), f"node harness 无输出\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert proc.stdout.lstrip().startswith("{"), f"node harness 输出不是 JSON\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    report = json.loads(proc.stdout)
    assert proc.returncode in (0, 1), f"node harness 异常退出 {proc.returncode}\nstderr: {proc.stderr}"
    return report


def test_harness_ran_all_scenarios(dom_report):
    assert dom_report["total"] == len(SCENARIOS), dom_report
    assert {r["name"] for r in dom_report["results"]} == set(SCENARIOS), dom_report


@pytest.mark.parametrize("name", SCENARIOS)
def test_component_regression(dom_report, name):
    matches = [r for r in dom_report["results"] if r["name"] == name]
    assert matches, f"场景缺失：{name!r}；实际: {[r['name'] for r in dom_report['results']]}"
    result = matches[0]
    assert result["ok"], f"场景失败：{name}\n{result.get('error', '')}"
