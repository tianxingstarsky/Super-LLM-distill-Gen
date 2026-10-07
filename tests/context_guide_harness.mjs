// Execute the JavaScript emitted by Streamlit AppTest in a small browser model.
// Virtual time makes late widgets and cancelled animation frames deterministic.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createContext, runInContext } from 'node:vm';

const scripts = JSON.parse(readFileSync(0, 'utf8'));
const decoration = 'df-context-guide-target';

function browser() {
  let now = 0;
  let nextId = 1;
  let rafRequests = 0;
  const timers = new Map();
  const observers = new Set();

  function notify(record) {
    for (const observer of observers) {
      const { target, options } = observer;
      if (record.target !== target && !(options.subtree && target.contains(record.target))) continue;
      if (record.type === 'childList' && !options.childList) continue;
      if (record.type === 'attributes' && (!options.attributes ||
          (options.attributeFilter && !options.attributeFilter.includes(record.attributeName)))) continue;
      observer.records.push(record);
    }
  }

  class Element {
    constructor(className = '', attributes = {}) {
      this._className = className;
      this.attributes = attributes;
      this.children = [];
      this.parentElement = null;
      this.scrolls = [];
      this.classList = {
        contains: value => this._className.split(/\s+/).includes(value),
        add: value => {
          if (!this.classList.contains(value)) this.className = `${this._className} ${value}`.trim();
        },
        remove: value => {
          if (this.classList.contains(value)) {
            this.className = this._className.split(/\s+/).filter(item => item !== value).join(' ');
          }
        },
      };
    }
    get className() { return this._className; }
    set className(value) {
      const oldValue = this._className;
      this._className = value;
      notify({ type: 'attributes', target: this, attributeName: 'class', oldValue });
    }
    contains(element) {
      return this === element || this.children.some(child => child.contains(element));
    }
    append(element) {
      if (element.parentElement) element.remove();
      this.children.push(element);
      element.parentElement = this;
      notify({ type: 'childList', target: this, addedNodes: [element], removedNodes: [] });
      return element;
    }
    remove() {
      const parent = this.parentElement;
      if (!parent) return;
      parent.children.splice(parent.children.indexOf(this), 1);
      this.parentElement = null;
      notify({ type: 'childList', target: parent, addedNodes: [], removedNodes: [this] });
    }
    querySelectorAll(selector) {
      const matches = [];
      for (const child of this.children) {
        if (child.matches(selector)) matches.push(child);
        matches.push(...child.querySelectorAll(selector));
      }
      return matches;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    matches(selector) {
      if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
      const classPart = /^\[class\*="([^"]+)"\]$/.exec(selector);
      if (classPart) return this.className.includes(classPart[1]);
      const attribute = /^\[([^=]+)="([^"]+)"\]$/.exec(selector);
      if (attribute) return this.attributes[attribute[1]] === attribute[2];
      throw new Error(`Unsupported DOM selector: ${selector}`);
    }
    scrollIntoView(options) { this.scrolls.push({ ...options }); }
  }

  class MutationObserver {
    constructor(callback) { this.callback = callback; this.records = []; }
    observe(target, options) {
      this.target = target;
      this.options = options;
      observers.add(this);
    }
    disconnect() { observers.delete(this); this.records = []; }
  }

  const body = new Element();
  const main = body.append(new Element('', { 'data-testid': 'stMainBlockContainer' }));
  const document = {
    body,
    contains: element => body.contains(element),
    querySelector: selector => body.matches(selector) ? body : body.querySelector(selector),
    querySelectorAll: selector => [...(body.matches(selector) ? [body] : []), ...body.querySelectorAll(selector)],
  };
  function timer(callback, delay, kind) {
    const id = nextId++;
    timers.set(id, { callback, at: now + delay, kind });
    return id;
  }
  const window = {
    document, MutationObserver,
    requestAnimationFrame: callback => { rafRequests++; return timer(callback, 16, 'frame'); },
    cancelAnimationFrame: id => timers.delete(id),
    setTimeout: (callback, delay = 0) => timer(callback, delay, 'timeout'),
    clearTimeout: id => timers.delete(id),
  };
  window.window = window;
  const context = createContext(window);

  function advance(milliseconds = 100) {
    const until = now + milliseconds;
    for (let iteration = 0; iteration < 1000; iteration++) {
      // MutationObserver batches mutations before the next animation frame.
      let delivered = false;
      for (const observer of [...observers]) {
        if (!observer.records.length) continue;
        const records = observer.records.splice(0);
        observer.callback(records, observer);
        delivered = true;
      }
      if (delivered) continue;
      const due = [...timers.entries()].filter(([, entry]) => entry.at <= until)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!due) { now = until; return; }
      const [id, entry] = due;
      timers.delete(id);
      now = entry.at;
      entry.callback(now);
    }
    throw new Error('Guide did not settle after 1000 mutation/frame deliveries');
  }

  function target(selector, parent = main) {
    const match = /^\[class\*="([^"]+)"\]$/.exec(selector);
    assert.ok(match, `Expected a real widget key selector, received ${selector}`);
    return parent.append(new Element(`stElementContainer ${match[1]}fixture`));
  }
  return {
    main, body, target, advance,
    execute: script => runInContext(script, context, { timeout: 1000 }),
    marked: () => document.querySelectorAll(`.${decoration}`),
    observers: () => observers.size,
    frames: () => [...timers.values()].filter(entry => entry.kind === 'frame').length,
    rafRequests: () => rafRequests,
  };
}

function selectors(script) {
  return JSON.parse(/const selectors = (\[.*?\]);/.exec(script)[1]);
}

function onlyMarked(env, element) {
  assert.deepEqual(env.marked(), [element], 'Only the current real control should be highlighted');
  assert.equal(env.frames(), 0, 'Highlight mutations must settle without a frame loop');
}

const scenarios = {
  late_target_after_retry_window() {
    const env = browser();
    env.execute(scripts.package_export);
    env.advance(6500);
    assert.equal(env.marked().length, 0);
    const widget = env.target(selectors(scripts.package_export)[0]);
    env.advance();
    onlyMarked(env, widget);
    assert.equal(widget.scrolls.length, 1);
    return { appearedAfterMs: 6500, scrolls: widget.scrolls.length };
  },

  react_overwrites_class_on_same_node() {
    const env = browser();
    const widget = env.target(selectors(scripts.package_export)[0]);
    const original = widget.className;
    env.execute(scripts.package_export);
    env.advance();
    widget.className = original;
    env.advance();
    onlyMarked(env, widget);
    assert.equal(widget.scrolls.length, 1, 'Restoring a class must not move the user again');
    const unrelated = env.target('[class*="unrelated-widget"]');
    env.advance();
    const requests = env.rafRequests();
    unrelated.className += ' unrelated-react-update';
    env.advance();
    assert.equal(env.rafRequests(), requests, 'Unrelated class updates should not schedule another frame');
    return { scrolls: widget.scrolls.length, unrelatedFrames: env.rafRequests() - requests };
  },

  fallback_upgrades_and_replaced_target() {
    const env = browser();
    const choices = selectors(scripts.package_export);
    const fallback = env.target(choices[5]);
    env.execute(scripts.package_export);
    env.advance();
    onlyMarked(env, fallback);
    env.advance(6500);
    const primary = env.target(choices[0]);
    env.advance();
    onlyMarked(env, primary);
    assert.equal(primary.scrolls.length, 1, 'A newly available preferred control should be revealed');
    primary.remove();
    const replacement = env.target(choices[0]);
    env.advance();
    onlyMarked(env, replacement);
    assert.equal(replacement.scrolls.length, 0, 'Replacing the same control must preserve reading position');
    return { fallbackScrolls: fallback.scrolls.length, primaryScrolls: primary.scrolls.length,
      replacementScrolls: replacement.scrolls.length };
  },

  same_token_script_rerender_keeps_scroll() {
    const env = browser();
    const widget = env.target(selectors(scripts.package_export)[0]);
    env.execute(scripts.package_export);
    env.advance();
    env.execute(scripts.package_export);
    env.advance();
    onlyMarked(env, widget);
    assert.equal(widget.scrolls.length, 1, 'Streamlit rerendering the same guide step must not scroll twice');
    assert.equal(env.observers(), 1, 'Rerender must replace rather than accumulate observers');
    return { scrolls: widget.scrolls.length, observers: env.observers() };
  },

  change_step_disconnects_old_observer_and_frame() {
    const env = browser();
    const oldWidget = env.target(selectors(scripts.overview_input)[0]);
    const oldClass = oldWidget.className;
    const nextWidget = env.target(selectors(scripts.overview_target)[0]);
    env.execute(scripts.overview_input);
    env.advance();
    oldWidget.className = oldClass;
    env.advance(0);
    assert.equal(env.frames(), 1, 'Fixture must leave an old repair frame pending');
    env.execute(scripts.overview_target);
    assert.equal(env.frames(), 1, 'Changing step must cancel the previous pending frame');
    env.advance();
    onlyMarked(env, nextWidget);
    assert.equal(env.observers(), 1);
    oldWidget.className = `${oldClass} react-updated`;
    env.target(selectors(scripts.overview_input)[0]);
    env.advance(6500);
    onlyMarked(env, nextWidget);
    assert.equal(oldWidget.scrolls.length, 1);
    assert.equal(nextWidget.scrolls.length, 1);
    return { observers: env.observers(), oldScrolls: oldWidget.scrolls.length,
      nextScrolls: nextWidget.scrolls.length };
  },

  exit_cancels_pending_frame_and_future_repairs() {
    const env = browser();
    env.execute(scripts.package_export);
    assert.equal(env.frames(), 1);
    env.execute(scripts.clear);
    assert.equal(env.frames(), 0, 'Exit must cancel the pending initial frame');
    assert.equal(env.observers(), 0);
    const widget = env.target(selectors(scripts.package_export)[0]);
    env.advance(6500);
    assert.equal(env.marked().length, 0, 'Late controls must stay undecorated after exit');
    assert.equal(widget.scrolls.length, 0);
    env.execute(scripts.package_export);
    env.advance();
    onlyMarked(env, widget);
    env.execute(scripts.clear);
    widget.className += ' after-exit';
    env.target(selectors(scripts.package_export)[1]);
    env.advance(6500);
    assert.equal(env.marked().length, 0, 'React updates must not resurrect an exited guide');
    assert.equal(env.observers(), 0);
    assert.equal(env.frames(), 0);
    return { observers: env.observers(), pendingFrames: env.frames(), highlighted: env.marked().length };
  },
};

const results = {};
for (const [name, scenario] of Object.entries(scenarios)) {
  try { results[name] = { ok: true, observed: scenario() }; }
  catch (error) { results[name] = { ok: false, error: error.stack }; }
}
process.stdout.write(JSON.stringify(results));
