// Run with: node --test tests/test_promo_bootstrap.cjs
// Execute the shipped bootstrap, replacing only its browser module-load boundary.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { createContext, runInContext } = require('node:vm');
const { test } = require('node:test');

const promoRoot = path.resolve(__dirname, '../assets/promo');
const source = readFileSync(path.join(promoRoot, 'bootstrap.mjs'), 'utf8');
assert.equal(source.split("import('./film.mjs')").length, 2, 'Stub exactly one module-load boundary');
const executable = source.replace("import('./film.mjs')", 'loadFilm()')
  .replace('void startPromo();', 'globalThis.startPromo = startPromo;');

function harness(loadFilm) {
  const elements = new Map();
  for (const id of ['film', 'loading', 'start', 'record', 'finished-film', 'status', 'transport', 'chapters']) {
    elements.set(id, { hidden: id === 'finished-film', textContent: '' });
  }
  const sourceElement = { dataset: { src: 'shujian-cube-promo.mp4' }, src: '' };
  const video = elements.get('finished-film');
  video.listeners = new Map();
  video.querySelector = () => sourceElement;
  video.addEventListener = (type, callback) => video.listeners.set(type, callback);
  video.load = () => { video.loads = (video.loads || 0) + 1; };
  const warnings = [];
  const context = createContext({
    document: {
      getElementById: id => elements.get(id),
      querySelector: selector => elements.get(selector.slice(1)),
    },
    loadFilm,
    console: { warn: (...values) => warnings.push(values) },
  });
  runInContext(executable, context, { filename: 'bootstrap.mjs' });
  return { elements, sourceElement, video, warnings, start: context.startPromo };
}

for (const [label, loadFilm] of [
  ['a rendering dependency fails to load', async () => { throw new Error('Failed to fetch module'); }],
  ['WebGL fails before film initialization', async () => { throw new Error('Error creating WebGL context'); }],
  ['film asset initialization fails', async () => ({ filmReady: Promise.reject(new Error('Missing image')) })],
]) {
  test(`native film becomes playable when ${label}`, async () => {
    const h = harness(loadFilm);
    await h.start();
    for (const id of ['film', 'loading', 'start', 'transport', 'chapters', 'record']) {
      assert.equal(h.elements.get(id).hidden, true, `${id} must not cover or control the native video`);
    }
    assert.equal(h.video.hidden, false);
    assert.equal(h.video.loads, 1);
    assert.equal(h.video.preload, 'metadata');
    assert.equal(h.sourceElement.src, 'shujian-cube-promo.mp4');
    assert.match(h.elements.get('status').textContent, /已切换到 MP4 成片/);
    assert.equal(h.warnings.length, 1);
    h.video.listeners.get('error')();
    assert.match(h.elements.get('status').textContent, /MP4 成片暂时无法载入/);
  });
}

test('a successful 3D initialization keeps its player and does not load a second soundtrack', async () => {
  const h = harness(async () => ({ filmReady: Promise.resolve() }));
  await h.start();
  assert.equal(h.elements.get('film').hidden, false);
  assert.equal(h.video.hidden, true);
  assert.equal(h.video.loads, undefined);
  assert.equal(h.sourceElement.src, '');
  assert.equal(h.warnings.length, 0);
});

test('the ordinary player exposes a native playback route before scripts initialize', () => {
  const html = readFileSync(path.join(promoRoot, 'index.html'), 'utf8');
  assert.match(html, /href="watch\.html"[^>]*>播放 MP4 成片/);
  assert.match(html, /id="mp4"[^>]*href="shujian-cube-promo\.mp4"[^>]*download>/);
  const watch = readFileSync(path.join(promoRoot, 'watch.html'), 'utf8');
  assert.match(watch, /<video[^>]*controls[^>]*poster="shujian-cube-promo-poster\.png"/);
  assert.match(watch, /<source src="shujian-cube-promo\.mp4" type="video\/mp4">/);
  assert.doesNotMatch(watch, /<script/);
});
