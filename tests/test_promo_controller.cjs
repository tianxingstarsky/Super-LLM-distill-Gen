// Run with: node --test tests/test_promo_controller.cjs
// Exercise the shipped controller with deterministic browser/audio boundaries.
// Rendering is stubbed; no alternative implementation of the controller is used.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createContext, runInContext } = require('node:vm');
const { test } = require('node:test');

const promoRoot = path.resolve(__dirname, '../assets/promo');
const film = readFileSync(path.join(promoRoot, 'film.mjs'), 'utf8');
function sourceBetween(start, end) {
  const left = film.indexOf(start);
  const right = film.indexOf(end, left);
  assert(left >= 0 && right > left, `Controller source boundaries changed: ${start}`);
  return film.slice(left, right);
}
const controllerSource = sourceBetween('let cues=[]', 'function animateStage(') +
  sourceBetween('function stopSource(', 'async function initialize(') + `
  globalThis.controller = {play, pause, seek, tickFrame, recordFilm, timeNow,
    state: () => ({ready, playing, playPending, offset, recording, recorder,
      audioBuffer, gainNode, audioDestination})};
  ready = true;
`;

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

async function harness() {
  const timeline = await import(pathToFileURL(path.join(promoRoot, 'timeline.mjs')).href);
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', disabled: false, hidden: false, value: '',
      attributes: {}, listeners: new Map(), dataset: {},
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(name, callback) { this.listeners.set(name, callback); },
    });
    return elements.get(id);
  }
  const chapters = timeline.SCENES.map(scene => ({
    ...element(`chapter-${scene.start}`), dataset: { time: String(scene.start) },
  }));
  const documentListeners = new Map();
  const document = {
    hidden: false, activeElement: { tagName: 'BODY' },
    querySelectorAll: () => chapters,
    addEventListener: (name, callback) => documentListeners.set(name, callback),
    createElement: () => ({ click() { downloads++; } }),
  };
  const calls = { fetch: [], draw: [], sources: [], gains: [], recorders: [],
    streams: [], contexts: [], decode: 0, frames: 0, uploads: 0, fills: [], texts: [] };
  const behavior = { fetch: null, resumes: [], constructorError: null, startError: null };
  let downloads = 0;
  class AudioContext {
    constructor() { this.currentTime = 100; this.destination = {}; calls.contexts.push(this); }
    async decodeAudioData() { calls.decode++; return { duration: timeline.DURATION }; }
    resume() {
      const next = behavior.resumes.shift();
      return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
    }
    createGain() {
      const node = { gain: { value: 1 }, connections: [], connect(to) { this.connections.push(to); } };
      calls.gains.push(node); return node;
    }
    createMediaStreamDestination() { return { stream: { getAudioTracks: () => [{ kind: 'audio' }] } }; }
    createBufferSource() {
      const node = { starts: [], stops: 0, disconnects: 0,
        connect(to) { this.connectedTo = to; },
        start(...args) { this.starts.push(args); },
        stop() { this.stops++; }, disconnect() { this.disconnects++; } };
      calls.sources.push(node); return node;
    }
  }
  const canvas = {
    dataset: {},
    captureStream(fps) {
      const video = { kind: 'video', stops: 0, stop() { this.stops++; } };
      const stream = { fps, video, added: [], addTrack(track) { this.added.push(track); },
        getVideoTracks: () => [video], getTracks() { return [video, ...this.added]; } };
      calls.streams.push(stream); return stream;
    },
  };
  class MediaRecorder {
    static isTypeSupported() { return true; }
    constructor(stream, options) {
      if (behavior.constructorError) throw behavior.constructorError;
      this.stream = stream; this.options = options;
      this.state = 'inactive'; this.stops = 0; calls.recorders.push(this); }
    start(timeslice) {
      if (behavior.startError) throw behavior.startError;
      this.timeslice = timeslice; this.state = 'recording';
    }
    pause() { this.state = 'paused'; }
    resume() { this.state = 'recording'; }
    stop() {
      this.stops++; this.state = 'inactive';
      this.ondataavailable?.({ data: new Blob(['recorded frame']) });
      this.stopped = Promise.resolve(this.onstop?.());
    }
  }
  let roundedPath;
  const ctx = {
    save() {}, restore() {}, beginPath() { roundedPath = null; },
    roundRect(x, y, width, height) { roundedPath = { x, y, width, height }; },
    fill() { if (roundedPath) calls.fills.push({ ...roundedPath }); },
    stroke() {}, fillRect(x, y, width, height) { calls.fills.push({ x, y, width, height }); },
    fillText(value, x, y) { calls.texts.push({ value, x, y }); },
    measureText: value => ({ width: String(value).length * 30 }),
  };
  const sandbox = {
    $: element, document, window: { MediaRecorder, addEventListener() {} },
    AudioContext, MediaRecorder, canvas, Blob, console,
    DURATION: timeline.DURATION, clamp: timeline.clamp, SCENES: timeline.SCENES,
    ease: timeline.ease, cueAt: timeline.cueAt, W: 1920, H: 1080, FONT: 'Promo', ctx,
    brandMark() {}, tracked() {}, line() {}, chip() { return 100; }, iconCheck() {}, proofShot() {},
    panel: (x, y, width, height, draw) => draw(), project: () => [0, 0],
    draw: time => calls.draw.push(time),
    renderer: { dispose() {} }, performance: { now: () => 1000 },
    requestAnimationFrame: () => { calls.frames++; }, setTimeout() {},
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
    fetch: async (url, options) => {
      calls.fetch.push({ url, options });
      if (url === '/__recording') {
        calls.uploads++; return { ok: true, json: async () => ({ saved: true }) };
      }
      if (behavior.fetch) return behavior.fetch(url, options);
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
    },
  };
  const context = createContext(sandbox);
  runInContext(controllerSource, context, { filename: 'film.mjs:controller' });
  return { ...sandbox.controller, calls, behavior, canvas, chapters, document,
    element, get downloads() { return downloads; },
    dispatch: name => documentListeners.get(name)(),
    evaluate: source => runInContext(source, context),
    renderOverlay(index, time) {
      runInContext(sourceBetween('function rr(', 'function tracked(') +
        sourceBetween('function overlay(', 'function draw('), context);
      runInContext(`overlay(${index}, ${time})`, context);
    },
  };
}

test('pause cancels a play waiting for audio preparation', async () => {
  const h = await harness();
  const gate = deferred();
  h.behavior.fetch = () => gate.promise;
  const pending = h.play(12);
  h.pause();
  gate.resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(1) });
  await pending;
  assert.equal(h.state().playing, false);
  assert.equal(h.state().playPending, false);
  assert.equal(h.calls.sources.length, 0);
  assert.equal(h.element('play').attributes['aria-label'], '播放');
});

test('overlapping first plays share audio setup and only the latest starts', async () => {
  const h = await harness();
  const gate = deferred();
  h.behavior.fetch = () => gate.promise;
  const earlier = h.play(5);
  const latest = h.play(25);
  assert.equal(h.calls.fetch.length, 1, 'Concurrent initial plays must share one audio load');
  gate.resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(1) });
  await Promise.all([earlier, latest]);
  assert.equal(h.calls.decode, 1);
  assert.equal(h.calls.gains.length, 1);
  assert.equal(h.calls.sources.length, 1);
  assert.deepEqual(h.calls.sources[0].starts[0], [0, 25]);
  assert.equal(h.calls.sources[0].connectedTo, h.state().gainNode);
  assert.equal(h.state().playing, true);
});

test('seeking during pending playback starts at the requested chapter', async () => {
  const h = await harness();
  const gate = deferred();
  h.behavior.fetch = () => gate.promise;
  const pending = h.play(5);
  h.seek(35);
  gate.resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(1) });
  await pending;
  await flush();
  assert.equal(h.calls.sources.length, 1);
  assert.deepEqual(h.calls.sources[0].starts[0], [0, 35]);
  assert.equal(h.state().offset, 35);
});

test('seeking to the end stops playback without restarting from zero', async () => {
  const h = await harness();
  await h.play(15);
  h.seek(60);
  await flush();
  assert.equal(h.state().playing, false);
  assert.equal(h.state().offset, 60);
  assert.equal(h.calls.sources.length, 1);
  assert.equal(h.calls.sources[0].stops, 1);
  assert.equal(h.calls.draw.at(-1), 60);
  assert.equal(h.element('seek').value, 60);
  assert.equal(h.element('time').value, '01:00 / 01:00');
});

test('pause preserves elapsed audio time and explicit play resumes it', async () => {
  const h = await harness();
  await h.play(9);
  h.calls.contexts[0].currentTime += 4.25;
  h.pause();
  assert.equal(h.state().offset, 13.25);
  assert.equal(h.calls.sources[0].disconnects, 1);
  await h.play();
  assert.deepEqual(h.calls.sources[1].starts[0], [0, 13.25]);
});

test('natural completion keeps the final frame and ends the audio source', async () => {
  const h = await harness();
  await h.play(58);
  h.calls.contexts[0].currentTime += 3;
  h.tickFrame();
  assert.equal(h.state().playing, false);
  assert.equal(h.state().offset, 60);
  assert.equal(h.calls.sources[0].stops, 1);
  assert.equal(h.calls.draw.at(-1), 60);
  assert.equal(h.element('time').value, '01:00 / 01:00');
  assert.match(h.element('status').textContent, /播放完成/);
});

test('recording locks controls before audio is ready and rejects duplicate starts', async () => {
  const h = await harness();
  const gate = deferred();
  h.behavior.fetch = () => gate.promise;
  const first = h.recordFilm();
  await h.recordFilm();
  assert.equal(h.state().recording, true);
  for (const id of ['start', 'play', 'replay', 'seek', 'mute', 'captions', 'record']) {
    assert.equal(h.element(id).disabled, true, `${id} must be locked before awaiting audio`);
  }
  assert(h.chapters.every(chapter => chapter.disabled));
  assert.equal(h.calls.fetch.length, 1);
  gate.resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(1) });
  await first;
  assert.equal(h.calls.recorders.length, 1);
  assert.equal(h.calls.streams[0].fps, 30);
  assert.equal(h.calls.streams[0].added[0].kind, 'audio');
  assert.deepEqual(h.calls.sources[0].starts[0], [0, 0]);
  assert.equal(h.state().playing, true);
});

test('completed recording saves one blob and stops its video capture track', async () => {
  const h = await harness();
  await h.recordFilm();
  h.calls.contexts[0].currentTime += 61;
  h.tickFrame();
  await h.calls.recorders[0].stopped;
  assert.equal(h.state().recording, false);
  assert.equal(h.calls.streams[0].video.stops, 1);
  assert.equal(h.calls.uploads, 1);
  assert.equal(h.canvas.dataset.recording, 'saved');
  assert.equal(h.element('record').disabled, false);
});

test('failed audio preparation releases controls and permits retry', async () => {
  const h = await harness();
  h.behavior.fetch = async () => { throw new Error('missing audio'); };
  await h.recordFilm();
  assert.equal(h.state().recording, false);
  assert.equal(h.element('record').disabled, false);
  assert.match(h.element('status').textContent, /录制失败.*missing audio/);
  h.behavior.fetch = null;
  await h.recordFilm();
  assert.equal(h.calls.recorders.length, 1);
  assert.equal(h.state().playing, true);
});

test('failed playback after recorder start cleans up without exporting a partial film', async () => {
  const h = await harness();
  h.behavior.resumes = [undefined, new Error('audio device unavailable')];
  await h.recordFilm();
  await flush();
  assert.equal(h.state().recording, false);
  assert.equal(h.state().playing, false);
  assert.equal(h.element('record').disabled, false);
  assert.equal(h.calls.recorders[0].state, 'inactive');
  assert.equal(h.calls.streams[0].video.stops, 1);
  assert.equal(h.calls.uploads, 0);
  assert.equal(h.downloads, 0);
  assert.match(h.element('status').textContent, /录制失败/);
});

for (const failure of ['constructorError', 'startError']) {
  test(`recorder ${failure === 'constructorError' ? 'constructor' : 'start'} exception stops capture and restores controls`, async () => {
    const h = await harness();
    h.behavior[failure] = new Error('unsupported capture configuration');
    await h.recordFilm();
    await flush();
    assert.equal(h.state().recording, false);
    assert.equal(h.state().playing, false);
    assert.equal(h.calls.streams.length, 1);
    assert.equal(h.calls.streams[0].video.stops, 1);
    for (const id of ['start', 'play', 'replay', 'seek', 'mute', 'captions', 'record']) {
      assert.equal(h.element(id).disabled, false, `${id} must be restored after ${failure}`);
    }
    assert(h.chapters.every(chapter => !chapter.disabled));
    assert.equal(h.element('record').textContent, '录制 WebM');
    assert.equal(h.canvas.dataset.recording, 'failed');
    assert.equal(h.calls.uploads, 0);
    assert.equal(h.downloads, 0);
    assert.match(h.element('status').textContent, /录制失败.*unsupported capture configuration/);
  });
}

for (const [scene, time, theme] of [[0, 3, 'light'], [4, 40, 'dark']]) {
  test(`${theme} scene paints caption text without a subtitle background plate`, async () => {
    const h = await harness();
    h.evaluate('cues = [{start: 0, end: 60, text: "透明字幕验证"}]; captionsEnabled = true;');
    h.renderOverlay(scene, time);
    const caption = h.calls.texts.find(command => command.value === '透明字幕验证');
    assert(caption, 'The active cue must still be painted into the exported canvas');
    assert.equal(caption.x, 960);
    assert(caption.y > 900 && caption.y < 1020, 'Caption belongs within the lower video safe area');
    const coversCaption = rectangle => rectangle.x <= caption.x &&
      rectangle.x + rectangle.width >= caption.x && rectangle.y <= caption.y &&
      rectangle.y + rectangle.height >= caption.y - 30;
    assert.equal(h.calls.fills.some(coversCaption), false,
      'Caption text must be composited over the scene without a filled rectangle or rounded plate');
  });
}

test('hiding and returning to a recording resumes audio from the preserved offset', async () => {
  const h = await harness();
  await h.recordFilm();
  h.calls.contexts[0].currentTime += 15;
  h.document.hidden = true;
  h.dispatch('visibilitychange');
  assert.equal(h.calls.recorders[0].state, 'paused');
  assert.equal(h.state().playing, false);
  assert.equal(h.state().offset, 15);
  h.document.hidden = false;
  h.dispatch('visibilitychange');
  await flush();
  assert.equal(h.calls.recorders[0].state, 'recording');
  assert.equal(h.state().playing, true);
  assert.deepEqual(h.calls.sources[1].starts[0], [0, 15]);
});
