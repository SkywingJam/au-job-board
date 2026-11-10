// Plugin sidebar layout math in panel.js (fitLayout + sidebarFit), offline: docked
// beside the detail when list + detail minimums still fit, floating when not, bottom
// sheet in portrait, nothing at all without a sidebar; repeated fits never oscillate.
// Synthetic DOM stub only. Usage: node tools/test_sidebar_layout.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../jobs/panel_assets/panel.js', import.meta.url), 'utf8');
let checks = 0;
const eq = (a, b, label) => { assert.deepEqual(a, b, label); checks++; };

function load({width, sidebar = true, open = false, map = false, stored = {}}) {
  const style = new Map(), cls = new Set(map ? ['plugin-display'] : []);
  if (sidebar) cls.add('has-sidebar');
  if (open) cls.add('sidebar-open');
  const classList = {add: (...c) => c.forEach(x => cls.add(x)), remove: (...c) => c.forEach(x => cls.delete(x)),
    contains: c => cls.has(c), toggle: (c, f) => { const on = f === undefined ? !cls.has(c) : !!f; on ? cls.add(c) : cls.delete(c); return on; }};
  const root = {dataset: {}, style: {setProperty: (k, v) => style.set(k, v), getPropertyValue: k => style.get(k) || ''}};
  const px = k => parseFloat(style.get(k) || '0') || 0;
  // main is the full width; the list layout loses main's padding and the docked reserve.
  const main = {get clientWidth() { return state.width; }};
  const layout = {get clientWidth() { return state.width - (map ? 0 : 40) - (cls.has('sidebar-docked') ? px('--sb-reserve') : 0); }};
  const attrs = {};
  const ids = {gutter: {offsetWidth: map ? 0 : 14, setAttribute() {}}, 'plugin-sidebar': sidebar ? {} : null,
    'sidebar-resize': {setAttribute: (k, v) => { attrs[k] = v; }}};
  const state = {width};
  const local = new Map(Object.entries(stored).map(([k, v]) => [k, JSON.stringify(v)]));
  const document = {documentElement: root, body: {classList}, cookie: '', hidden: false,
    getElementById: id => ids[id] ?? null, querySelector: s => s === '.layout' ? layout : s === 'main' ? main : null,
    querySelectorAll: () => [], addEventListener() {}, createElement: () => ({})};
  const sandbox = {document, window: null, localStorage: {getItem: k => local.get(k) ?? null, setItem: (k, v) => local.set(k, v)},
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}}, matchMedia: () => ({matches: false, addEventListener() {}}),
    getComputedStyle: () => ({getPropertyValue: k => style.get(k) || ''}), setTimeout, clearTimeout, console, location: {hash: ''},
    addEventListener() {}, history: {replaceState() {}}};
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source + ';globalThis.__T__={fitLayout,setSidebarWidth,sidebarResizeKeys};', sandbox);
  const snap = () => ({mode: ['sidebar-docked', 'sidebar-float', 'sidebar-sheet', 'portrait'].filter(c => cls.has(c)),
    reserve: px('--sb-reserve'), sb: px('--sb-w'), left: px('--left')});
  return {api: sandbox.__T__, cls, state, snap, local, attrs, fit() { sandbox.__T__.fitLayout(); return snap(); }};
}

// No sidebar on the page: layout unchanged, no sidebar classes or reserve.
{
  const t = load({width: 1600, sidebar: false});
  eq(t.fit(), {mode: [], reserve: 0, sb: 0, left: 600}, 'without a sidebar fitLayout behaves as before');
}
// Closed sidebar: nothing reserved.
eq(load({width: 1600}).fit(), {mode: [], reserve: 0, sb: 0, left: 600}, 'collapsed by default reserves nothing');
// Wide: docked at the default width, list keeps its stored/default left width.
{
  const t = load({width: 1600, open: true});
  const first = t.fit();
  eq(first, {mode: ['sidebar-docked'], reserve: 352, sb: 340, left: 600}, 'wide: docked 340px + 12px gap');
  eq(t.fit(), first, 'a second fit is stable');
  eq(t.attrs['aria-valuenow'], '340', 'resize handle reports its width');
}
// Medium: the sidebar narrows so list (300) + detail (520) still fit.
{
  const t = load({width: 1200, open: true});
  eq(t.fit(), {mode: ['sidebar-docked'], reserve: 326, sb: 314, left: 300}, 'medium: docked, narrowed to the room left');
  eq(t.fit(), {mode: ['sidebar-docked'], reserve: 326, sb: 314, left: 300}, 'stable');
}
// Too little room for a docked column: it floats over the detail; the split is untouched.
{
  const t = load({width: 1150, open: true});
  eq(t.fit(), {mode: ['sidebar-float'], reserve: 0, sb: 340, left: 576}, 'narrow desktop: floating sidebar');
  eq(t.fit().mode, ['sidebar-float'], 'stable');
}
// Portrait: bottom sheet; the open sidebar never pushes the layout into portrait by itself.
{
  const t = load({width: 800, open: true});
  eq(t.fit().mode, ['sidebar-sheet', 'portrait'], 'portrait: sheet');
  t.state.width = 1600;
  eq(t.fit().mode, ['sidebar-docked'], 'back to desktop: docked again');
  t.state.width = 900;   // 900-40-14 = 846 >= 820: still desktop even though docking would leave too little
  eq(t.fit().mode, ['sidebar-float'], 'no portrait flip-flop caused by the sidebar');
  eq(t.fit().mode, ['sidebar-float'], 'stable');
}
// Map display: the map needs 420px; no gap.
{
  const t = load({width: 1200, open: true, map: true});
  eq(t.fit(), {mode: ['sidebar-docked'], reserve: 340, sb: 340, left: 600}, 'map: docked beside the map');
}
// Remembered width is clamped; keyboard resize stores the new width.
{
  const t = load({width: 1600, open: true, stored: {'jobs.plugin.sidebar': {open: true, width: 9999}}});
  eq(t.fit().sb, 560, 'stored width clamped to the maximum');
  t.api.sidebarResizeKeys({key: 'Home', shiftKey: false, preventDefault() {}});
  eq([t.snap().sb, JSON.parse(t.local.get('jobs.plugin.sidebar')).width], [280, 280], 'Home: minimum width, stored');
  t.api.sidebarResizeKeys({key: 'ArrowLeft', shiftKey: false, preventDefault() {}});
  eq(t.snap().sb, 304, 'ArrowLeft widens');
  t.local.set('jobs.plugin.sidebar', '{corrupt');
  eq(t.fit().sb, 340, 'corrupt stored prefs fall back to the default width');
}
console.log(`sidebar layout synthetic checks passed (${checks} checks)`);
