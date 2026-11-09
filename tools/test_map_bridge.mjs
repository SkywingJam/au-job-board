// Execute the actual bridge in a minimal synthetic DOM and inspect its messages.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../jobs/panel_assets/plugin_pages.js', import.meta.url), 'utf8');
const handlers = {}, replies = [], calls = [], opened = [];
let resolveLoads = [], blockLoads = false, permitMember = true;
const frameWindow = {postMessage: (message, target) => replies.push({message, target})};
const frameHandlers = {};
const frame = {contentWindow: frameWindow, dataset: {pluginSrc: '/plugins/map/frame'}, isConnected: true, remove() { this.isConnected = false; }, addEventListener: (name, fn) => {frameHandlers[name] = fn;}};
let clock = 1000, pluginsFail = false;
const context = {plugin_id: 'map', display: 'map', lang: 'en', theme: 'light', query_url: '/?view=all&q=Python', return_url: '/?view=all&q=Python'};
const window = {__PAGE_CONTEXT__: context, addEventListener: (name, fn) => {handlers[name] = fn;}};
const document = {documentElement: {dataset: {}}, getElementById: () => frame, querySelectorAll: () => [], querySelector: () => ({prepend() {}}), addEventListener() {}, createElement: () => ({})};
const fetch = async (url, {signal} = {}) => {
  calls.push(url);
  if (url === '/api/plugins' && pluginsFail) throw new Error('network blip');
  if (url === '/api/plugins') return {ok:true, json:async()=>({plugins:[{id:'map',status:'enabled'}]})};
  if (url.includes('/member?') && !permitMember) return {ok:false, json:async()=>({error:{code:'source_not_in_snapshot'}})};
  if (blockLoads && url.includes('/jobs?')) return new Promise((resolve, reject) => {
    resolveLoads.push(() => resolve({ok:true,json:async()=>({items:[]})}));
    signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name:'AbortError'})));
  });
  return {ok:true,json:async()=>({items:[]})};
};
vm.runInNewContext(source, {window, document, location:{origin:'http://localhost'}, URL, URLSearchParams, Uint8Array, AbortController,
  crypto:{getRandomValues: b => b.fill(7)}, fetch, matchMedia:()=>({matches:false,addEventListener(){}}), MutationObserver:class{observe(){}}, sessionStorage:{getItem:()=>null,setItem(){}},
  navigatePanel:async()=>true, openJob:async uid=>{opened.push(uid);return false;},
  setTimeout, clearTimeout, setInterval:()=>0, clearInterval, Date:{now:()=>clock}});
const send = async (data, source = frameWindow) => handlers.message({source, data});
await send({type:'jobs-plugin-ready',version:1}, {});
assert.equal(replies.length, 0);
await send({type:'jobs-plugin-ready',version:1});
assert.equal(replies.length, 1);
assert.equal(replies[0].target, '*');
const channel = replies[0].message.channel;
const req = (id, operation, payload = {}) => ({type:'jobs-plugin-request',channel,id:String(id),operation,payload});
await send(req(1,'load'), {});
await send({...req(1,'load'),channel:'foreign'});
await send({...req(1,'load'),payload:[]});
await send({...req(1,'load'),id:'not-a-number'});
assert.equal(replies.length, 1);
await send(req(1,'unknown'));
assert.equal(replies.at(-1).message.error.code, 'unsupported_operation');
await send(req(1,'load'));
assert.equal(replies.length, 2); // repeated id is ignored
await send(req(2,'load',{extra:true}));
assert.equal(replies.at(-1).message.error.code, 'invalid_request');
await send(req(3,'open_job',{snapshot_id:'s',uid:'synthetic:one',extra:1}));
assert.equal(opened.length, 0);
permitMember = false;
await send(req(4,'open_job',{snapshot_id:'s',uid:'synthetic:one'}));
assert.equal(replies.at(-1).message.error.code, 'source_not_in_snapshot');
assert.equal(opened.length, 0);
permitMember = true;
await send(req(5,'open_job',{snapshot_id:'s',uid:'synthetic:one'}));
assert.deepEqual(opened, ['synthetic:one']);
assert.equal(replies.at(-1).message.result.opened, false); // core guard declined navigation
blockLoads = true;
const pending = Array.from({length:8}, (_, i) => send(req(6+i,'load')));
await new Promise(resolve => setImmediate(resolve));
await send(req(14,'load'));
assert.equal(replies.at(-1).message.error.code, 'page_busy');
assert.equal(resolveLoads.length, 8);
resolveLoads.forEach(resolve=>resolve());
await Promise.all(pending);
// A single failed permission read is tolerated; the map stays open.
blockLoads = false; pluginsFail = true; clock += 5000;
await send(req(15,'load'));
assert.equal(replies.at(-1).message.ok, true);
assert.equal(frame.isConnected, true);
pluginsFail = false; clock += 5000;
const prior = replies.length;
blockLoads = true;
const stale = send(req(16,'load'));
await new Promise(resolve=>setImmediate(resolve));
handlers.pagehide();
await stale;
assert.equal(replies.length, prior);
assert.ok(calls.some(url=>url.includes('/member?')));
// The frame navigating itself (a second load) closes a live bridge.
{
  const handlers2 = {}, frame2 = {...frame, isConnected: true, addEventListener: (name, fn) => {handlers2[name] = fn;}};
  vm.runInNewContext(source, {window:{__PAGE_CONTEXT__: context, addEventListener(){}},
    document:{...document, getElementById: () => frame2}, location:{origin:'http://localhost'}, URL, URLSearchParams,
    Uint8Array, AbortController, crypto:{getRandomValues: b => b.fill(7)}, fetch, matchMedia:()=>({matches:false,addEventListener(){}}), MutationObserver:class{observe(){}},
    sessionStorage:{getItem:()=>null,setItem(){}}, navigatePanel:async()=>true, openJob:async()=>false,
    setTimeout, clearTimeout, setInterval:()=>0, clearInterval, Date:{now:()=>clock}});
  handlers2.load(); assert.equal(frame2.isConnected, true);
  handlers2.load(); assert.equal(frame2.isConnected, false);
}
// Toolbar List/Map links: wired, save the list position only when scrolled, and restore
// it only for the same query.
{
  const run = (ctx, scrollTop, store) => {
    const handlers = {}, navigated = [];
    const link = (mode, href) => ({dataset: {pluginDisplay: mode}, getAttribute: () => href, addEventListener: (n, fn) => { handlers[mode] = fn; }});
    const links = [link('list', '/?view=all&display=list'), link('map', '/?view=all&display=map')];
    const list = {scrollTop, getBoundingClientRect: () => ({top: 0})};
    const card = {dataset: {uid: 'u1'}, getBoundingClientRect: () => ({top: -10, bottom: 50})};
    vm.runInNewContext(source, {window: {__PAGE_CONTEXT__: ctx, scrollY: 0, addEventListener(){}},
      document: {documentElement: {dataset: {}}, getElementById: () => null, addEventListener(){},
        querySelector: sel => sel === '.list' ? list : null,
        querySelectorAll: sel => sel === '[data-plugin-display]' ? links : sel === '.list .card' ? [card] : []},
      location: {origin: 'http://localhost', href: 'http://localhost/?view=all'}, URL, URLSearchParams,
      sessionStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; }},
      navigatePanel: async url => { navigated.push(url); }, matchMedia: () => ({matches: false, addEventListener(){}}), MutationObserver: class{observe(){}}});
    return {handlers, navigated};
  };
  const click = {preventDefault(){}, button: 0};
  const listCtx = {display: 'list', plugin_id: null, query_url: '/?view=all', return_url: '/?view=all'};
  const mapCtx = {display: 'map', plugin_id: 'map', query_url: '/?view=all', return_url: '/?view=all'};
  let store = {};
  let r = run(listCtx, 0, store); r.handlers.map(click);
  assert.deepEqual(r.navigated, ['/?view=all&display=map']);
  assert.equal(store['jobs.plugin.return-position'], undefined, 'at the top nothing is saved');
  r = run(listCtx, 900, store); r.handlers.map(click);
  assert.equal(JSON.parse(store['jobs.plugin.return-position']).uid, 'u1');
  r = run(mapCtx, 0, store); r.handlers.list(click);
  assert.deepEqual(r.navigated, ['/?view=all&display=list']);
  assert.equal(JSON.parse(store['jobs.result.position']).top, 900, 'same query restores');
  delete store['jobs.result.position'];
  r = run({...mapCtx, query_url: '/?view=all&q=x', return_url: '/?view=all&q=x'}, 0, store); r.handlers.list(click);
  assert.equal(store['jobs.result.position'], undefined, 'a new search starts at the top');
}
// Esc over the map detail: registered in the capture phase (before panel.js closes
// its own layers) and skipped while any of those layers is still open.
{
  const listeners = [], open = new Set(), classes = new Set(['plugin-detail-open']);
  let closed = 0;
  const doc = {documentElement: {dataset: {}}, getElementById: () => frame, querySelectorAll: () => [],
    querySelector: sel => sel === 'main' ? {prepend(){}} : sel.split(',').some(part => open.has(part.trim())) ? {} : null,
    body: {classList: {contains: name => classes.has(name)}},
    addEventListener: (name, fn, options) => listeners.push({name, fn, options})};
  vm.runInNewContext(source, {window: {__PAGE_CONTEXT__: context, addEventListener(){}}, document: doc, location: {origin: 'http://localhost'},
    URL, URLSearchParams, Uint8Array, AbortController, crypto: {getRandomValues: b => b.fill(7)}, fetch,
    matchMedia: () => ({matches: false, addEventListener(){}}), MutationObserver: class{observe(){}}, sessionStorage: {getItem: () => null, setItem(){}},
    Element: class{}, closeDetail: () => { closed++; }, navigatePanel: async () => true, openJob: async () => false,
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval, Date: {now: () => clock}});
  const esc = listeners.find(l => l.name === 'keydown');
  assert.ok(esc, 'Esc handler registered');
  assert.equal(esc.options?.capture, true, 'registered in the capture phase');
  const press = () => esc.fn({key: 'Escape', defaultPrevented: false, isComposing: false, target: {}, preventDefault(){}});
  press(); assert.equal(closed, 1, 'Esc closes the detail when nothing else is open');
  for (const layer of ['#detail.label-open', '#drawer:not([hidden])', '#display-pop:not([hidden])']) {
    open.clear(); open.add(layer); press();
    assert.equal(closed, 1, layer + ' open: Esc is left to its own handler');
  }
  open.clear(); open.add('#more-panel:not([hidden])'); press();
  assert.equal(closed, 2, 'an inline (landscape) more panel does not block');
  classes.add('portrait'); press();
  assert.equal(closed, 2, 'the portrait more sheet closes first');
}
console.log('map bridge synthetic checks passed (incl. list/map switch and list position)');
console.log('map bridge synthetic checks passed (incl. transient permission failure and frame self-navigation)');
