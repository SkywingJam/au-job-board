// Sidebar host + per-frame bridges (host API 2, handed-jobs profile): run the real
// plugin_pages.js in a small synthetic DOM. Map page frame and workspace sidebar
// frame run at the same time; every handed operation, the host-owned handed list,
// the host->frame events, copy_text honesty, revocation and the compare page.
// Synthetic data only. Usage: node tools/test_sidebar_bridge.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../jobs/panel_assets/plugin_pages.js', import.meta.url), 'utf8');
let checks = 0;
const ok = (value, label) => { assert.ok(value, label); checks++; };
// Values built inside the vm context have that realm's prototypes: rebuild plain
// arrays/objects here before comparing (DOM stubs keep their identity).
const norm = v => Array.isArray(v) ? Array.from(v, x => norm(x)) : v && typeof v === 'object' && !(v instanceof El)
  ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, norm(x)])) : v;
const eq = (a, b, label) => { assert.deepEqual(norm(a), norm(b), label); checks++; };
const flush = () => new Promise(resolve => setImmediate(resolve));

class El {
  constructor(id = '', parent = null) {
    this.id = id; this.parent = parent; this.dataset = {}; this.attrs = {}; this.listeners = {};
    this.children = []; this.cls = new Set(); this.hidden = false; this.isConnected = true; this.textContent = '';
    const cls = this.cls;
    this.classList = {add: (...c) => c.forEach(x => cls.add(x)), remove: (...c) => c.forEach(x => cls.delete(x)),
      contains: c => cls.has(c), toggle: (c, f) => { const on = f === undefined ? !cls.has(c) : !!f; on ? cls.add(c) : cls.delete(c); return on; }};
  }
  addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
  fire(name, ev = {}) { for (const fn of this.listeners[name] || []) fn(ev); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  insertBefore(child) { child.parent = this; this.children.push(child); return child; }
  querySelector() { return null; }
  remove() { this.isConnected = false; }
  contains(x) { for (let n = x; n; n = n.parent) if (n === this) return true; return false; }
  focus() { env.doc.activeElement = this; }
}
let env;

function makeEnv({context, plugins = {map: 'enabled', workspace: 'enabled'}, store = {}, jobs = {}, prefs = {}} = {}) {
  const e = {listeners: {}, winListeners: {}, toasts: [], fetches: [], opened: [], navigated: [], copied: [],
    dialogs: [], intervals: [], timers: [], plugins, store, jobs, openUid: null, copyOk: true, profile: 'default',
    ignored: false, blockHanded: false, pending: [], pluginsFail: false, prefs};
  env = e;
  const doc = e.doc = {documentElement: {dataset: {}}, activeElement: null};
  const body = e.body = new El('body');
  doc.body = body;
  const main = new El('main'); main.prepend = node => { e.note = node; };
  const pane = e.pane = new El('plugin-sidebar');
  const mk = (id, parent = null) => new El(id, parent);
  const frames = e.frames = {
    map: Object.assign(mk('plugin-display-frame'), {contentWindow: {postMessage: (m, t) => e.mapOut.push({m, t})}}),
    side: Object.assign(mk('plugin-sidebar-frame', pane), {contentWindow: {postMessage: (m, t) => e.sideOut.push({m, t})}}),
  };
  e.mapOut = []; e.sideOut = [];
  frames.map.dataset.pluginSrc = '/plugins/' + context.display + '/frame' + (context.display === 'workspace' ? '?slot=page' : '');
  frames.side.dataset.pluginSrc = '/plugins/workspace/frame?slot=sidebar';
  const ids = e.ids = {
    'plugin-display-frame': context.display !== 'list' ? frames.map : null,
    'plugin-sidebar': pane, 'plugin-sidebar-frame': frames.side,
    'sidebar-toggle': mk('sidebar-toggle'), 'sidebar-count': mk('sidebar-count'), 'sidebar-close': mk('sidebar-close', pane),
    'sidebar-resize': mk('sidebar-resize', pane), 'sidebar-scrim': mk('sidebar-scrim'), 'sidebar-drop': mk('sidebar-drop', pane),
    'copy-modal': Object.assign(mk('copy-modal'), {hidden: true}), 'plugin-detail-back': mk('plugin-detail-back'),
    'back': mk('back'), 'plugin-page-back': mk('plugin-page-back'), 'selbar': null,
  };
  e.scope = null;
  Object.assign(doc, {
    getElementById: id => (id in ids ? ids[id] : null),
    querySelector: sel => sel === 'main' ? main : sel === '#detail [data-label-scope]' ? e.scope
      : sel.split(',').includes('.modal:not([hidden])') ? (ids['copy-modal'].hidden ? null : ids['copy-modal']) : null,
    querySelectorAll: () => [],
    addEventListener: (name, fn, options) => (e.listeners[name] ??= []).push({fn, options}),
    createElement: () => new El(),
  });
  const handedItem = uid => e.jobs[uid] ? {uid, ...e.jobs[uid], revision: e.jobs[uid].revision || 'rev-' + uid} : {uid, missing: true};
  const respond = (status, body) => ({ok: status < 400, status, json: async () => body});
  const fetch = async (url, {signal} = {}) => {
    e.fetches.push(url);
    if (url === '/api/plugins') {
      if (e.pluginsFail) throw new Error('network blip');
      return respond(200, {plugins: Object.entries(e.plugins).map(([id, status]) => ({id, status}))});
    }
    const u = new URL(url, 'http://localhost');
    if (u.pathname.endsWith('/handed')) {
      if (e.handedStatus) return respond(e.handedStatus, {error: 'forbidden'});
      const uids = u.searchParams.getAll('uid');
      const body = e.handedBody ? e.handedBody(uids) : {plugin_id: 'workspace', as_of: 't', generation: 'g', items: uids.map(handedItem)};
      if (e.blockHanded) return new Promise((resolve, reject) => {
        e.pending.push(() => resolve(respond(200, body)));
        signal?.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
      });
      return respond(200, body);
    }
    if (u.pathname.endsWith('/jobs')) return respond(200, {items: []});
    return respond(404, {error: 'not_found'});
  };
  let clock = 1_000_000;
  e.advance = ms => { clock += ms; };
  class FakeDate extends Date { constructor(...a) { a.length ? super(...a) : super(clock); } static now() { return clock; } }
  const storage = {getItem: k => (k in e.store ? e.store[k] : null), setItem: (k, v) => {
    if (e.quotaFull) throw new Error('QuotaExceededError'); e.store[k] = String(v); }, removeItem: k => { delete e.store[k]; }};
  let random = 0;
  const window = e.window = {__PAGE_CONTEXT__: context, scrollY: 0, addEventListener: (name, fn) => { e.winListeners[name] = fn; }};
  vm.runInNewContext(source, {window, document: doc, location: {origin: 'http://localhost', reload() { e.reloaded = true; }},
    URL, URLSearchParams, Uint8Array, AbortController, Element: El, Date: FakeDate,
    crypto: {getRandomValues: b => b.fill(++random)}, fetch, matchMedia: () => ({matches: false, addEventListener() {}}),
    MutationObserver: class { observe() {} }, sessionStorage: storage, localStorage: storage,
    setTimeout: (fn, ms) => { e.timers.push({fn, ms}); return e.timers.length; }, clearTimeout: id => { if (e.timers[id - 1]) e.timers[id - 1].cleared = true; },
    setInterval: fn => { e.intervals.push(fn); return 1; }, clearInterval() {},
    navigatePanel: async url => { e.navigated.push(url); return true; }, closeDetail: () => { e.closed = (e.closed || 0) + 1; },
    openJob: async uid => { e.opened.push(uid); return e.openResult ?? true; }, openUid: () => e.openUid,
    toast: m => e.toasts.push(m), copyPlain: async text => { e.copied.push(text); return e.copyOk; },
    openCopyDialog: (text, back) => { e.dialogs.push({text, back}); ids['copy-modal'].hidden = false; },
    fitLayout: () => { e.fits = (e.fits || 0) + 1; }, sidebarPrefs: () => e.prefs, storeSidebarPrefs: p => Object.assign(e.prefs, p),
    keyProfile: () => e.profile, navKeyIgnored: () => e.ignored, SEL: new Set()});
  e.message = async (frameKey, data) => e.winListeners.message({source: frames[frameKey].contentWindow, data});
  e.ready = async frameKey => { await e.message(frameKey, {type: 'jobs-plugin-ready', version: 1}); return (frameKey === 'map' ? e.mapOut : e.sideOut).at(-1).m; };
  e.ids2 = {map: 0, side: 0};
  e.request = async (frameKey, operation, payload = {}, channel) => {
    const out = frameKey === 'map' ? e.mapOut : e.sideOut;
    const id = String(++e.ids2[frameKey]), start = out.length;
    await e.message(frameKey, {type: 'jobs-plugin-request', channel: channel ?? e.channels[frameKey], id, operation, payload});
    const reply = out.slice(start).findLast(x => x.m.type === 'jobs-plugin-response' && x.m.id === id);
    return reply ? reply.m : null;
  };
  e.channels = {};
  e.key = (key, extra = {}) => { let prevented = false;
    for (const {fn} of e.listeners.keydown || []) fn({key, shiftKey: false, defaultPrevented: prevented, isComposing: false, target: {}, preventDefault() { prevented = true; }, ...extra});
    return prevented; };
  e.host = window.__JOBS_PLUGIN_HOST__;
  return e;
}
const handedStored = e => JSON.parse(e.store['jobs.plugin.handed:workspace'] || '[]');
const ws = {id: 'workspace', label: {zh: '工作区', en: 'Workspace'}, handoff_label: {zh: '加入工作区', en: 'Add to workspace'}, icon: 'workspace'};
const mapCtx = {display: 'map', plugin_id: 'map', lang: 'en', theme: 'light', view: 'all', query_url: '/?view=all&q=Python',
  return_url: '/?view=all&q=Python', pages: [{id: 'map', label: {en: 'Map'}, icon: 'map', switch: true},
    {id: 'workspace', label: {en: 'Compare'}, icon: 'compare', switch: false}], sidebars: [ws], agent_access: 'on'};
const jobs = Object.fromEntries(Array.from({length: 14}, (_, i) => ['seek:' + (i + 1), {title: 'Role ' + (i + 1), company: 'Co ' + (i + 1)}]));

// ---- corrupt / oversized / duplicate stored lists reset to empty ----------------------
for (const bad of ['{bad', JSON.stringify(Array.from({length: 11}, (_, i) => ({uid: 's:' + i, title: '', company: '', revision: '', added_at: ''}))),
  JSON.stringify([{uid: 'a', title: '', company: '', revision: '', added_at: ''}, {uid: 'a', title: '', company: '', revision: '', added_at: ''}]),
  JSON.stringify([{uid: 'a'}]), 'x'.repeat(70000)]) {
  const e = makeEnv({context: mapCtx, store: {'jobs.plugin.handed:workspace': bad}, jobs});
  ok(!('jobs.plugin.handed:workspace' in e.store), 'invalid stored list is reset: ' + bad.slice(0, 20));
  eq(e.ids['sidebar-count'].hidden, true, 'empty count hidden');
}

// ---- map page + workspace sidebar, side by side -----------------------------------------
const e = makeEnv({context: mapCtx, jobs});
eq(e.listeners.keydown[0].options?.capture, true, 'page Esc handler stays first and in the capture phase');
eq(e.frames.side.src, undefined, 'the sidebar frame loads only when first opened');
eq(e.frames.map.src, '/plugins/map/frame', 'the map frame keeps its address');
e.ids['sidebar-toggle'].fire('click');
ok(e.body.classList.contains('sidebar-open'), 'toggle opens the sidebar');
eq(e.ids['sidebar-toggle'].attrs['aria-expanded'], 'true', 'toggle reports expanded');
eq(e.prefs.open, true, 'desktop open state remembered');
eq(e.frames.side.src, '/plugins/workspace/frame?slot=sidebar', 'sidebar frame loaded with slot=sidebar');
eq(e.doc.activeElement, e.frames.side, 'focus moves into the opened sidebar');
const mapCtxMsg = await e.ready('map'), sideCtxMsg = await e.ready('side');
// The sidebar gets a fixed context: nothing about the current view or search.
eq(Object.keys(sideCtxMsg.context).sort(), ['agent_access', 'compare_columns', 'current', 'handed', 'lang', 'plugin_id', 'slot', 'state', 'theme'], 'sidebar context fields');
ok(!JSON.stringify(sideCtxMsg.context).includes('Python'), 'the search text is not sent to the sidebar');
ok('query_url' in mapCtxMsg.context, 'the map still gets the list query');
e.channels = {map: mapCtxMsg.channel, side: sideCtxMsg.channel};
ok(e.channels.map && e.channels.side && e.channels.map !== e.channels.side, 'each frame has its own channel');
eq([mapCtxMsg.context.slot, mapCtxMsg.context.plugin_id, 'handed' in mapCtxMsg.context], ['page', 'map', false], 'map context: page slot, no handed list');
eq([sideCtxMsg.context.slot, sideCtxMsg.context.plugin_id, sideCtxMsg.context.handed, sideCtxMsg.context.agent_access, sideCtxMsg.context.current, sideCtxMsg.context.state],
  ['sidebar', 'workspace', [], 'on', null, null], 'sidebar context: slot, handed, agent_access, current, state');
// Channel isolation and per-profile dispatch.
eq(await e.request('side', 'load', {}, e.channels.map), null, 'a sidebar request on the map channel is ignored');
eq((await e.request('side', 'load')).error.code, 'unsupported_operation', 'sidebar cannot load the map snapshot');
eq((await e.request('side', 'reference', {file: 'x'})).error.code, 'unsupported_operation', 'sidebar cannot read map references');
eq((await e.request('map', 'handed_jobs', {uids: ['seek:1'], detail: false})).error.code, 'unsupported_operation', 'map cannot read handed jobs');
eq((await e.request('map', 'clear_handed')).error.code, 'unsupported_operation', 'map cannot clear the handed list');
eq((await e.request('map', 'load')).ok, true, 'map load still works');
eq((await e.request('side', 'navigate_list')).error.code, 'unsupported_operation', 'navigate_list is a page operation');
eq((await e.request('side', 'dismiss')).result, {closed: false}, 'dismiss does nothing outside the phone sheet');
eq((await e.request('side', 'dismiss', {x: 1})).error.code, 'invalid_request', 'dismiss takes no payload');

// ---- handOff: one host function, honest feedback --------------------------------------
const host = e.window.__JOBS_PLUGIN_HOST__;
let fetchesBefore = e.fetches.length;
eq(await host.handOff(['seek:1']), {added: 1, skipped: 0}, 'one job added');
eq(e.fetches.slice(fetchesBefore).filter(u => u.includes('/handed')), ['/api/plugin-view/workspace/handed?uid=seek%3A1'], '/handed called once for the new uid');
eq(e.toasts.at(-1), 'sidebar.added', 'added toast');
const stored = handedStored(e);
eq([stored.length, stored[0].uid, stored[0].title, stored[0].company, stored[0].revision, typeof stored[0].added_at], [1, 'seek:1', 'Role 1', 'Co 1', 'rev-seek:1', 'string'], 'stored shape');
eq(e.sideOut.at(-1).m.type, 'jobs-plugin-handed', 'sidebar told about the change');
eq(e.sideOut.at(-1).m.channel, e.channels.side, 'event on the sidebar channel');
ok(!e.mapOut.some(x => x.m.type === 'jobs-plugin-handed'), 'the map frame never gets handed events');
eq([e.ids['sidebar-count'].textContent, e.ids['sidebar-count'].hidden], ['1', false], 'toggle shows the count');
fetchesBefore = e.fetches.length;
eq(await host.handOff(['seek:1']), {added: 0, skipped: 1}, 'duplicate not added');
eq([e.toasts.at(-1), e.fetches.length], ['sidebar.already', fetchesBefore], 'already-there toast, no request');
eq(await host.handOff(['seek:2', 'gone:1', 'seek:1']), {added: 1, skipped: 2}, 'missing and duplicate skipped');
eq(e.toasts.at(-1), 'sidebar.some', 'n added, m skipped toast');
ok(!handedStored(e).some(x => x.uid === 'gone:1'), 'missing job not stored');
eq(await host.handOff(['gone:2']), {added: 0, skipped: 1}, 'missing single job');
eq(e.toasts.at(-1), 'sidebar.missing', 'missing toast');
e.handedStatus = 500;
eq((await host.handOff(['seek:3'])).failed, true, 'server failure reported');
eq([e.toasts.at(-1), handedStored(e).length], ['sidebar.failed', 2], 'failure toast, list unchanged');
e.handedStatus = 0; e.handedBody = uids => ({items: uids.map(uid => ({uid: uid + 'x', title: 't'}))});
eq((await host.handOff(['seek:3'])).failed, true, 'mismatched response rejected');
e.handedBody = null;
e.quotaFull = true;
eq((await host.handOff(['seek:3'])).failed, true, 'storage failure reported');
eq(e.toasts.at(-1), 'sidebar.saved_failed', 'storage failure toast');
e.quotaFull = false;
eq(await host.handOff(['seek:3', 'seek:4', 'seek:5', 'seek:6', 'seek:7', 'seek:8', 'seek:9']), {added: 7, skipped: 0}, 'batch added');
eq(e.toasts.at(-1), 'sidebar.added_n', 'batch toast');
eq(await host.handOff(['seek:10', 'seek:11', 'seek:12']), {added: 1, skipped: 2}, 'capacity: only one more fits');
eq(handedStored(e).length, 10, 'never more than ten');
fetchesBefore = e.fetches.length;
eq(await host.handOff(['seek:13']), {added: 0, skipped: 1}, 'full');
eq([e.toasts.at(-1), e.fetches.length], ['sidebar.full', fetchesBefore], 'full toast, no request');
// Concurrent adds are serialized: both read the list after the other wrote.
const e2 = makeEnv({context: mapCtx, jobs});
await Promise.all([e2.window.__JOBS_PLUGIN_HOST__.handOff(['seek:1']), e2.window.__JOBS_PLUGIN_HOST__.handOff(['seek:2'])]);
eq(handedStored(e2).map(x => x.uid), ['seek:1', 'seek:2'], 'quick successive adds both kept');

// ---- operations, happy path and rejection ------------------------------------------------
let r = await e.request('side', 'handed_jobs', {uids: ['seek:1', 'seek:2'], detail: true});
eq([r.ok, r.result.items.map(x => x.uid)], [true, ['seek:1', 'seek:2']], 'handed_jobs returns the /handed response');
ok(e.fetches.at(-1).endsWith('uid=seek%3A1&uid=seek%3A2&detail=1'), 'detail flag forwarded');
for (const [payload, code, label] of [
  [{uids: ['seek:13'], detail: false}, 'not_handed', 'uid not in host list'],
  [{uids: ['seek:1'], detail: 'yes'}, 'invalid_request', 'detail must be boolean'],
  [{uids: ['seek:1', 'seek:1'], detail: false}, 'invalid_request', 'duplicate uids'],
  [{uids: [], detail: false}, 'invalid_request', 'empty list'],
  [{uids: Array.from({length: 11}, (_, i) => 'seek:' + i), detail: false}, 'invalid_request', 'more than ten'],
  [{uids: ['seek:1'], detail: false, extra: 1}, 'invalid_request', 'extra key'],
  [{uids: ['x'.repeat(513)], detail: false}, 'invalid_request', 'uid too long']]) {
  eq((await e.request('side', 'handed_jobs', payload)).error.code, code, 'handed_jobs rejects: ' + label);
}
e.opened.length = 0;
r = await e.request('side', 'open_job', {uid: 'seek:2'});
eq([r.result, e.opened], [{opened: true}, ['seek:2']], 'open_job goes through openJob');
e.openResult = false;
eq((await e.request('side', 'open_job', {uid: 'seek:2'})).result, {opened: false}, 'a guard that keeps the current job is reported');
e.openResult = undefined;
eq((await e.request('side', 'open_job', {uid: 'seek:13'})).error.code, 'not_handed', 'open_job only for handed jobs');
eq((await e.request('side', 'open_job', {uid: 'seek:2', snapshot_id: 's'})).error.code, 'invalid_request', 'map-style open_job payload rejected');
eq(e.opened.length, 2, 'rejected open_job never navigates');
r = await e.request('side', 'copy_text', {text: 'Use the jobs-compare skill on these jobs: seek:1, seek:2'});
eq(r.result, {copied: true, fallback: false}, 'copy_text reports a real copy');
e.copyOk = false;
r = await e.request('side', 'copy_text', {text: 'seek:1'});
eq([r.result, e.dialogs.at(-1).back], [{copied: false, fallback: true}, e.frames.side], 'failed copy opens the manual dialog and says so');
e.ids['copy-modal'].hidden = true;
eq((await e.request('side', 'copy_text', {text: 'x'.repeat(2001)})).error.code, 'invalid_request', 'copy_text length bound');
eq((await e.request('side', 'copy_text', {text: 3})).error.code, 'invalid_request', 'copy_text needs a string');
e.copyOk = true;
r = await e.request('side', 'save_state', {state: {selected: ['seek:1', 'seek:2'], agent_ok: true}});
eq([r.result, JSON.parse(e.store['jobs.plugin.state:workspace']).selected], [{ok: true}, ['seek:1', 'seek:2']], 'save_state stored per plugin');
eq((await e.request('side', 'save_state', {state: {big: 'x'.repeat(5000)}})).error.code, 'state_too_large', 'state over 4 KiB rejected');
eq((await e.request('side', 'save_state', {state: []})).error.code, 'invalid_request', 'state must be an object');
eq((await e.request('side', 'save_state', {selected: []})).error.code, 'invalid_request', 'map-style save_state rejected');
const again = await e.ready('side');
e.channels.side = again.channel; e.ids2.side = 0;
eq([again.context.state.agent_ok, again.context.handed.length], [true, 10], 'saved state and list come back in the context');
r = await e.request('side', 'open_page');
eq([r.result, e.navigated.at(-1)], [{ok: true}, '/?view=all&q=Python&display=workspace'], 'open_page navigates to the compare page with the list query');
eq((await e.request('side', 'open_page', {x: 1})).error.code, 'invalid_request', 'open_page takes no payload');
r = await e.request('side', 'remove_handed', {uids: ['seek:1', 'seek:13']});
eq([r.result.handed.length, handedStored(e).some(x => x.uid === 'seek:1')], [9, false], 'remove_handed');
eq(e.sideOut.at(-1).m.type, 'jobs-plugin-response', 'reply after the handed event');
ok(e.sideOut.some(x => x.m.type === 'jobs-plugin-handed' && x.m.handed.length === 9), 'removal broadcast');
eq((await e.request('side', 'remove_handed', {uids: 'seek:2'})).error.code, 'invalid_request', 'remove_handed needs a list');

// ---- host -> frame events -----------------------------------------------------------------
e.openUid = 'seek:2';
e.scope = Object.assign(new El(), {dataset: {uid: 'seek:2'}, querySelector: sel => sel === 'h2' ? {textContent: 'Role 2'}
  : sel === '[data-copy-text]' ? {dataset: {copyText: 'UID: seek:2\nTitle: Role 2\nCompany: Co 2\nLocation: x\n'}} : null});
const mapBefore = e.mapOut.length;
host.detailOpened('seek:2');
eq(e.sideOut.at(-1).m, {type: 'jobs-plugin-current', channel: e.channels.side, current: {uid: 'seek:2', title: 'Role 2', company: 'Co 2'}}, 'current after openJob');
e.openUid = null;
host.detailClosed();
eq(e.sideOut.at(-1).m.current, null, 'current cleared after close');
eq(e.mapOut.length, mapBefore, 'the map frame gets no current events');
// Cross-tab change.
e.store['jobs.plugin.handed:workspace'] = JSON.stringify([{uid: 'seek:5', title: 'Role 5', company: 'Co 5', revision: 'r', added_at: 't'}]);
e.winListeners.storage({key: 'jobs.plugin.handed:workspace'});
eq([e.sideOut.at(-1).m.type, e.sideOut.at(-1).m.handed.map(x => x.uid), e.ids['sidebar-count'].textContent], ['jobs-plugin-handed', ['seek:5'], '1'], 'storage event syncs the list');
r = await e.request('side', 'clear_handed');
eq([r.result.handed, handedStored(e)], [[], []], 'clear_handed');

// ---- shortcut: open job or focused card, per key profile, guards respected ------------------
e.openUid = 'seek:3';
fetchesBefore = e.fetches.length;
ok(e.key('w'), 'W adds the open job (default profile)');
await flush(); await flush();
eq(handedStored(e).map(x => x.uid), ['seek:3'], 'shortcut used the host handOff');
e.profile = 'gamer';
ok(!e.key('w'), 'gamer profile: W is free');
e.openUid = 'seek:4';
ok(e.key('E'), 'gamer profile: E adds');
await flush(); await flush();
e.ignored = true;
ok(!e.key('e'), 'input / IME / modal guard (navKeyIgnored) wins');
e.ignored = false; e.profile = 'vim';
ok(!e.key('w', {shiftKey: true}), 'Shift+W is not the shortcut');
e.openUid = null;
ok(!e.key('w'), 'nothing open: nothing to add');
eq(handedStored(e).map(x => x.uid), ['seek:3', 'seek:4'], 'shortcut results');

// ---- busy limit and timeout are per frame ---------------------------------------------------
e.blockHanded = true;
const blocked = Array.from({length: 8}, () => e.request('side', 'handed_jobs', {uids: ['seek:3'], detail: false}));
await flush();
eq((await e.request('side', 'handed_jobs', {uids: ['seek:3'], detail: false})).error.code, 'page_busy', 'ninth sidebar request is busy');
eq((await e.request('map', 'load')).ok, true, 'the map frame has its own active set');
e.pending.splice(0).forEach(go => go());
await Promise.all(blocked);
const slow = e.request('side', 'handed_jobs', {uids: ['seek:3'], detail: false});
await flush();
e.timers.filter(t => t.ms === 14000 && !t.cleared).at(-1).fn();
eq((await slow).error.code, 'page_timeout', '14 s timeout');
e.blockHanded = false;

// ---- permission polling: three strikes, revocation cleanup ----------------------------------
e.pluginsFail = true;
for (let i = 0; i < 2; i++) { e.advance(5000); e.intervals[0](); await flush(); await flush(); }
ok(e.frames.side.isConnected, 'two failed permission reads are tolerated');
e.pluginsFail = false; e.advance(5000); e.intervals[0](); await flush(); await flush();
e.blockHanded = true;
const inflight = e.request('side', 'handed_jobs', {uids: ['seek:3'], detail: false});
await flush();
const listBefore = e.store['jobs.plugin.handed:workspace'];
e.plugins.workspace = 'disabled';
e.advance(5000); e.intervals[0](); await flush(); await flush();
eq(await inflight, null, 'revocation cancels in-flight requests without a reply');
ok(!e.frames.side.isConnected, 'sidebar frame removed');
eq([e.ids['sidebar-toggle'].hidden, e.pane.hidden, e.body.classList.contains('sidebar-open'), e.body.classList.contains('has-sidebar')],
  [true, true, false, false], 'toggle and sidebar hidden');
eq(e.store['jobs.plugin.handed:workspace'], listBefore, 'stored list untouched');
eq(e.toasts.at(-1), 'sidebar.closed', 'user told why it closed');
fetchesBefore = e.fetches.length;
eq(await host.handOff(['seek:9']), {added: 0, skipped: 1}, 'no handoff after revocation');
e.openUid = 'seek:9';
ok(!e.key('w'), 'shortcut off after revocation');
eq(e.fetches.slice(fetchesBefore).filter(u => u.includes('/handed')).length, 0, 'no handed request after revocation');
ok(e.frames.map.isConnected, 'the map frame is unaffected');
e.blockHanded = false;
eq((await e.request('map', 'load')).ok, true, 'map keeps working after the sidebar is revoked');

// ---- portrait sheet: Esc closes, open state not remembered -----------------------------------
{
  const p = makeEnv({context: {...mapCtx, display: 'list', plugin_id: null}, jobs});
  p.body.classList.add('portrait');
  p.prefs.open = false;
  p.ids['sidebar-toggle'].fire('click');
  eq([p.body.classList.contains('sidebar-open'), p.prefs.open, p.doc.activeElement], [true, false, p.ids['sidebar-close']], 'sheet opens, focus on its close button, not remembered');
  ok(p.key('Escape'), 'Esc closes the sheet');
  eq([p.body.classList.contains('sidebar-open'), p.doc.activeElement], [false, p.ids['sidebar-toggle']], 'focus back on the toggle');
  // open_job from the sheet closes it so the detail is visible.
  p.ids['sidebar-toggle'].fire('click');
  const ctx = await p.ready('side'); p.channels.side = ctx.channel;
  await p.window.__JOBS_PLUGIN_HOST__.handOff(['seek:1']);
  eq((await p.request('side', 'open_job', {uid: 'seek:1'})).result, {opened: true}, 'open from the sheet');
  eq([p.body.classList.contains('sidebar-open'), p.doc.activeElement], [false, p.ids.back], 'sheet closed, focus on the detail');
  // Remembered open state is restored on desktop load only.
  const d = makeEnv({context: {...mapCtx, display: 'list', plugin_id: null}, jobs});
  eq(d.body.classList.contains('sidebar-open'), false, 'default collapsed');
}
{
  const d = makeEnv({context: {...mapCtx, display: 'list', plugin_id: null}, jobs, prefs: {open: true}});
  eq([d.body.classList.contains('sidebar-open'), d.frames.side.src, d.doc.activeElement], [true, '/plugins/workspace/frame?slot=sidebar', null],
    'remembered desktop open state restored without stealing focus');
}

// ---- map detail overlay + portrait sidebar sheet: Esc closes the sheet first ----------------
{
  const m = makeEnv({context: mapCtx, jobs});
  m.body.classList.add('portrait', 'plugin-detail-open');
  m.ids['sidebar-toggle'].fire('click');
  m.body.classList.add('sidebar-sheet');      // set by panel.js sidebarFit in portrait
  ok(m.key('Escape'), 'Esc handled');
  eq([m.closed, m.body.classList.contains('sidebar-open')], [undefined, false], 'sheet closed, map detail kept open');
}
// ---- compare page (switch:false): page slot, handed profile, back to list --------------------
{
  const c = makeEnv({context: {...mapCtx, display: 'workspace', plugin_id: 'workspace'}, jobs,
    store: {'jobs.plugin.handed:workspace': JSON.stringify([{uid: 'seek:1', title: 'Role 1', company: 'Co 1', revision: 'r', added_at: 't'}])}});
  eq(c.frames.map.src, '/plugins/workspace/frame?slot=page', 'compare frame uses slot=page');
  eq(c.frames.side.src, undefined, 'no sidebar on the compare page');
  eq(c.window.__JOBS_PLUGIN_HOST__, undefined, 'no handoff entry points on the compare page');
  const ctx = await c.ready('map'); c.channels.map = ctx.channel;
  eq(Object.keys(ctx.context).sort(), ['agent_access', 'compare_columns', 'handed', 'lang', 'plugin_id', 'slot', 'state', 'theme'], 'page context fields');
  eq([ctx.context.slot, ctx.context.plugin_id, ctx.context.handed.length, 'current' in ctx.context], ['page', 'workspace', 1, false], 'page context');
  eq((await c.request('map', 'handed_jobs', {uids: ['seek:1'], detail: true})).ok, true, 'page reads handed details');
  eq((await c.request('map', 'clear_handed')).error.code, 'unsupported_operation', 'clear_handed is sidebar-only');
  eq((await c.request('map', 'open_page')).error.code, 'unsupported_operation', 'open_page is sidebar-only');
  eq((await c.request('map', 'load')).error.code, 'unsupported_operation', 'compare page cannot load the list snapshot');
  r = await c.request('map', 'open_job', {uid: 'seek:1'});
  eq([r.result, c.doc.activeElement], [{opened: true}, c.ids['plugin-detail-back']], 'open_job from compare: overlay with a way back');
  // A change made in another tab reaches the compare page too (there is no sidebar here).
  c.store['jobs.plugin.handed:workspace'] = '[]';
  c.winListeners.storage({key: 'jobs.plugin.handed:workspace'});
  eq([c.mapOut.at(-1).m.type, c.mapOut.at(-1).m.handed], ['jobs-plugin-handed', []], 'compare page told about a cross-tab change');
  eq((await c.request('map', 'handed_jobs', {uids: ['seek:1'], detail: true})).error.code, 'not_handed', 'removed job no longer readable');
  eq((await c.request('map', 'dismiss')).error.code, 'unsupported_operation', 'dismiss is sidebar-only');
  r = await c.request('map', 'navigate_list');
  eq([r.result, c.navigated.at(-1)], [{ok: true}, '/?view=all&q=Python&display=list'], 'navigate_list returns to the list');
  c.ids['plugin-page-back'].fire('click');
  eq(c.navigated.at(-1), '/?view=all&q=Python&display=list', 'core back control');
  c.body.classList.remove('plugin-detail-open');
  const n = c.navigated.length;
  ok(c.key('Escape'), 'Esc on the compare page returns to the list');
  eq(c.navigated.length, n + 1, 'Esc navigated once');
  c.ids['copy-modal'].hidden = false;
  ok(!c.key('Escape'), 'Esc is left to an open dialog');
  c.ids['copy-modal'].hidden = true;
  c.body.classList.add('plugin-detail-open');
  c.key('Escape');
  eq([c.closed, c.navigated.length], [1, n + 1], 'with a detail open, Esc closes the detail first');
  c.plugins.workspace = 'disabled'; c.advance(5000); c.intervals[0](); await flush(); await flush();
  ok(!c.frames.map.isConnected && c.note.textContent === 'sidebar.page_closed', 'revoked compare page is removed with a note');
}
console.log(`sidebar bridge synthetic checks passed (${checks} checks)`);
