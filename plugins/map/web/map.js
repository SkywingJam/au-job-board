(() => {
'use strict';
/* Job map page. Runs in a sandboxed frame and talks to the core page only
   through the jobs-plugin bridge (load / reference / open_job / save_state). */
const $ = id => document.getElementById(id);
const AUSTRALIA = [110, -45, 155, -10];
const TILE_ORIGIN = 'https://tiles.openfreemap.org';

const words = {
  en: {
    loading: 'Loading…',
    inView: '{m} in view · {n} {unit} in total', jobs: 'jobs', source_records: 'records',
    viewTitle: 'Jobs in view', count: '{n} {unit}', emptyView: 'No jobs in this area',
    postcode: 'Postcode {pc}',
    other: 'Other locations ({n})', otherTitle: 'Other locations',
    otherMeta: 'These jobs only name a state or a smaller city, or the place is unclear, so they can’t be shown on the map. State-only jobs are listed for states in view.',
    cityPill: '{x} citywide', allPill: 'All of Australia', allTitle: 'Jobs across Australia', allAria: 'All of Australia: {n} {unit}', cityTitle: 'City only · {x}', cityMeta: '{n} {unit} · only the city is named, no suburb or postcode', cityAria: '{x}: {n} {unit} that only name the city',
    gCity: 'City only · {x}', gState: 'State only · {x}', gAmbiguous: 'Place name is unclear',
    gBoundary: 'Postcode not on the map', gOther: 'No location given',
    cluster: '{n} {unit}, tap to zoom in', marker: 'Postcode {pc}: {n} {unit}',
    empty: 'No jobs in the current results.',
    error: 'The map couldn’t load. Please refresh.', expired: 'The results are out of date. Please refresh.',
    disabled: 'The map is turned off or needs permission again.', refresh: 'Refresh',
    tooLarge: 'Too many jobs to show on the map. Narrow your filters first.',
    fallback: 'The background map couldn’t load. Job locations are still shown.',
    outline: 'This postcode’s outline couldn’t load. Its jobs are still listed.',
    openError: 'This job couldn’t be opened. Please refresh and try again.',
    zoomIn: 'Zoom in', zoomOut: 'Zoom out', reset: 'Show all results', north: 'Point north',
    close: 'Close', grip: 'Expand or collapse the list', sources: 'Map data sources', showList: 'Show these jobs',
  },
  zh: {
    loading: '正在加载…',
    inView: '视野内 {m} 个，共 {n} {unit}', jobs: '个岗位', source_records: '条记录',
    viewTitle: '视野内的岗位', count: '{n} {unit}', emptyView: '区域内没有岗位',
    postcode: '邮编 {pc}',
    other: '其他位置（{n}）', otherTitle: '其他位置',
    otherMeta: '这些岗位只写了州或较小的城市，或地点不明确，无法在地图上标出。只写州的岗位只列出视野内的州。',
    cityPill: '{x} 全市', allPill: '全澳洲', allTitle: '全澳洲的岗位', allAria: '全澳洲：{n} {unit}', cityTitle: '只写城市 · {x}', cityMeta: '{n} {unit} · 只写了城市名，没有具体区或邮编', cityAria: '{x}：只写城市的 {n} {unit}',
    gCity: '只写城市 · {x}', gState: '只写州 · {x}', gAmbiguous: '地点不明确',
    gBoundary: '地图上没有这个邮编', gOther: '未写地点',
    cluster: '{n} {unit}，点击放大', marker: '邮编 {pc}：{n} {unit}',
    empty: '当前结果没有岗位。',
    error: '地图加载失败，请刷新重试。', expired: '结果已过期，请刷新。',
    disabled: '地图已关闭或需要重新授权。', refresh: '刷新',
    tooLarge: '岗位太多，地图无法显示，请先缩小筛选范围。',
    fallback: '底图加载失败，仍可查看岗位位置。',
    outline: '这个邮编的范围加载失败，岗位列表不受影响。',
    openError: '无法打开该岗位，请刷新后重试。',
    zoomIn: '放大', zoomOut: '缩小', reset: '显示全部结果', north: '指北',
    close: '关闭', grip: '展开或收起列表', sources: '地图数据来源', showList: '查看这些岗位',
  },
};

let lang = 'en', channel = null, serial = 0, epoch = 0, disposed = false;
const pending = new Map();
let context = null, snapshot = null, index = null;
let groups = new Map();   // postcode -> {items:[], feature: undefined | null | Feature}
let missing = [];         // jobs that can't be placed on a postcode or a capital
let cityJobs = new Map(); // capital city -> jobs that only name that city (shown as a 'citywide' pill)
let selected = null;      // postcode | 'unmapped' | 'area' (jobs in view)
let map = null, mapFailed = false, styleReady = false, resumed = false, appliedStyle = '';
let openerKey = '';       // the control that opened the sheet; focus returns there on close
let box = AUSTRALIA.slice(), dragging = null, saveTimer = null;
const chunks = new Map(); // reference file -> Promise<Map<postcode, Feature>>

const t = (key, args = {}) => (words[lang][key] || key).replace(/\{(\w+)\}/g, (_, k) => String(args[k] ?? ''));
const unit = () => t(snapshot?.count_unit === 'source_records' ? 'source_records' : 'jobs');

/* ---------- bridge ---------- */
function request(operation, payload = {}) {
  if (!channel || disposed) return Promise.reject(new Error('not ready'));
  if (pending.size >= 8) return Promise.reject(new Error('busy'));
  const id = String(++serial);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('timeout')); }, 15000);
    pending.set(id, {resolve, reject, timer});
    parent.postMessage({type: 'jobs-plugin-request', channel, id, operation, payload}, '*');
  });
}
function rejectPending() {
  for (const p of pending.values()) { clearTimeout(p.timer); p.reject(new Error('superseded')); }
  pending.clear();
}
window.addEventListener('message', e => {
  if (e.source !== parent || !e.data || typeof e.data !== 'object') return;
  const m = e.data;
  if (m.type === 'jobs-plugin-context' && !channel && typeof m.channel === 'string' && m.channel.length > 0
      && m.channel.length <= 256 && m.context?.plugin_id === 'map') {
    channel = m.channel; context = m.context;
    lang = context.lang === 'zh' ? 'zh' : 'en';
    document.documentElement.lang = lang;
    document.body.classList.toggle('dark', context.theme === 'dark');
    labels();
    startMap();          // the map engine loads while the results are fetched
    load();
  } else if (m.type === 'jobs-plugin-theme' && m.channel === channel && (m.theme === 'dark' || m.theme === 'light')) {
    applyTheme(m.theme);
  } else if (m.type === 'jobs-plugin-response' && m.channel === channel && typeof m.id === 'string'
             && pending.has(m.id) && typeof m.ok === 'boolean') {
    const p = pending.get(m.id); pending.delete(m.id); clearTimeout(p.timer);
    m.ok ? p.resolve(m.result) : p.reject(Object.assign(new Error(m.error?.message || 'error'), {code: m.error?.code}));
  }
});
window.addEventListener('pagehide', () => { disposed = true; epoch++; rejectPending(); if (map) map.remove(); });

function saveState() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    if (snapshot) request('save_state', {box: viewport(), area: null, selected,
      collapsed: $('sheet').classList.contains('collapsed')}).catch(() => {});
  }, 300);
}

/* ---------- text ---------- */
function labels() {
  const set = (id, key) => { $(id).setAttribute('aria-label', t(key)); $(id).title = t(key); };
  set('zoom-in', 'zoomIn'); set('zoom-out', 'zoomOut'); set('reset', 'reset'); set('north', 'north');
  set('close', 'close'); set('grip', 'grip'); set('summary', 'showList');
  $('attr-toggle').setAttribute('aria-label', t('sources'));
}
function notice(text, refresh = false) {
  const n = $('notice');
  n.replaceChildren(document.createTextNode(text));
  if (refresh) {
    const b = document.createElement('button');
    b.textContent = t('refresh'); b.addEventListener('click', load);
    n.append(' ', b);
  }
  n.hidden = false;
}
function failure(e) {
  const code = String(e?.code || '').toLowerCase();
  if (code.includes('too_large')) { notice(t('tooLarge')); return; }
  notice(code.includes('expir') ? t('expired')
    : /disabl|permission|grant/.test(code) ? t('disabled') : t('error'), true);
}

/* ---------- data ---------- */
function validIndex(v) { return v && v.schema_version === 1 && v.postcodes && typeof v.postcodes === 'object' && Array.isArray(v.files); }
// Only exact postcodes or uniquely resolved suburbs with a known postcode are placed.
function candidate(item) {
  const f = item?.location_fact, p = f?.postcodes;
  if (!f || !['postcode', 'locality'].includes(f.precision) || f.reason === 'ambiguous_locality'
      || !Array.isArray(p) || p.length !== 1 || !/^\d{4}$/.test(p[0])) return null;
  const geo = index.postcodes[p[0]];
  return geo && Array.isArray(geo.point) && geo.point.length === 2 && geo.point.every(Number.isFinite)
    && typeof geo.file === 'string' ? p[0] : null;
}
async function load() {
  const mine = ++epoch;
  rejectPending();
  snapshot = null; groups = new Map(); missing = []; cityJobs = new Map(); selected = null;
  $('sheet').hidden = true; $('notice').hidden = true;
  $('summary').textContent = t('loading'); $('unmapped').hidden = true;
  renderMap();
  try {
    const [s, i] = await Promise.all([request('load'), request('reference', {file: 'index.json'})]);
    if (mine !== epoch) return false;
    if (!s || !Array.isArray(s.items) || !s.snapshot_id || !['jobs', 'source_records'].includes(s.count_unit)
        || !validIndex(i) || (s.reference_version && s.reference_version !== i.reference_version)) throw new Error('invalid response');
    snapshot = s; index = i;
    for (const item of [...s.items].sort((a, b) => (a.core_order ?? 0) - (b.core_order ?? 0))) {
      const pc = candidate(item);
      if (!pc) {
        const city = cityOf(item);
        if (city) { if (!cityJobs.has(city)) cityJobs.set(city, []); cityJobs.get(city).push(item); }
        else missing.push(item);
        continue;
      }
      if (!groups.has(pc)) groups.set(pc, {items: [], feature: undefined});
      groups.get(pc).items.push(item);
    }
    const resume = context.state;
    if (resume) {
      resumed = true; box = resume.box; selected = resume.selected;
      if (resume.collapsed) sheetSize('collapsed');
      context.state = null;
    } else if (groups.size) {
      box = resultsBox();
    }
    if (map) map.fitBounds(bounds(box), resumed ? {duration: 0, padding: 0, maxZoom: 20} : {duration: 0, padding: 40, maxZoom: 13});
    render();
    if (selected && /^\d{4}$/.test(selected)) outline(selected);
    if (!s.items.length) notice(t('empty'));
    return true;
  } catch (e) {
    if (mine !== epoch) return false;
    failure(e); $('summary').textContent = '';
    return false;
  }
}
// Postcode outlines load on demand (only when a postcode is opened).
function chunk(file) {
  if (!chunks.has(file)) {
    chunks.set(file, request('reference', {file}).then(fc => {
      if (!fc || fc.type !== 'FeatureCollection' || !Array.isArray(fc.features)) throw new Error('invalid geometry');
      return new Map(fc.features.filter(x => x?.type === 'Feature' && ['Polygon', 'MultiPolygon'].includes(x.geometry?.type))
        .map(x => [String(x.properties?.postcode), x]));
    }).catch(e => { chunks.delete(file); throw e; }));
  }
  return chunks.get(file);
}
async function outline(pc) {
  const g = groups.get(pc);
  if (!g || g.feature !== undefined) return;
  const mine = epoch;
  try {
    const features = await chunk(index.postcodes[pc].file);
    if (mine !== epoch) return;
    g.feature = features.get(pc) || null;
    if (!g.feature) notice(t('outline'));
  } catch (_) {
    if (mine !== epoch) return;
    g.feature = null; notice(t('outline'));
  }
  renderMap();
}
// Bounds of the placed results. "Show all results" also includes capital-city markers so
// every placeable job becomes reachable; the first view stays on the postcode results.
function resultsBox(withCities = false) {
  const boxes = [...groups.keys()].map(pc => index.postcodes[pc].bbox).filter(Boolean);
  if (withCities) for (const city of cityJobs.keys()) { const p = cityPoint(city); if (p) boxes.push([p[0], p[1], p[0], p[1]]); }
  if (!boxes.length) return AUSTRALIA.slice();
  const b = [Math.min(...boxes.map(b => b[0])), Math.min(...boxes.map(b => b[1])),
             Math.max(...boxes.map(b => b[2])), Math.max(...boxes.map(b => b[3]))];
  const dx = Math.max(.03, (b[2] - b[0]) * .12), dy = Math.max(.03, (b[3] - b[1]) * .12);
  return [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy];
}
const bounds = b => [[b[0], b[1]], [b[2], b[3]]];
function viewport() {
  if (map) { const b = map.getBounds(); return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]; }
  return box;
}
// "In view" means the postcode's marker is on screen — exactly what the user sees.
function inView() {
  const [w, s, e, n] = viewport();
  return [...groups.keys()].filter(pc => { const [x, y] = index.postcodes[pc].point; return x >= w && x <= e && y >= s && y <= n; });
}

/* ---------- rendering ---------- */
function render() {
  if (!snapshot) return;
  const codes = inView();
  const m = codes.reduce((n, pc) => n + groups.get(pc).items.length, 0);
  $('summary').textContent = t('inView', {m, n: snapshot.total, unit: unit()});
  const others = grouped(missing).reduce((n, [, rows]) => n + rows.length, 0);   // only states in view
  $('unmapped').hidden = !others;
  $('unmapped').textContent = t('other', {n: others});
  renderMap();
  renderSheet(codes);
}
function showSheet(which, opener) {
  cancelLeave();
  selected = which;
  if (opener) openerKey = keyOf(opener);
  sheetSize('half');
  if (/^\d{4}$/.test(which)) outline(which);
  render(); saveState();
  $('sheet-title').focus({preventScroll: true});   // keyboard users land in the list they opened
}
// Markers are rebuilt on every move, so a control is found again by its key.
const keyOf = el => el.id || el.dataset.key || '';
function focusKey(key) {
  const el = key && ($(key) || [...$('markers').children].find(b => b.dataset.key === key));
  if (el && !el.hidden) el.focus({preventScroll: true});
}
function groupOf(item) {
  const f = item.location_fact || {};
  if (f.precision === 'city' && f.city) return t('gCity', {x: f.city});
  if (f.precision === 'state' && Array.isArray(f.states) && f.states.length) return t('gState', {x: f.states.join(' / ')});
  if (f.reason === 'ambiguous_locality') return t('gAmbiguous');
  if (['postcode', 'locality'].includes(f.precision)) return t('gBoundary');
  return t('gOther');
}
// Capital-city metro areas: a citywide pill sits at the CBD postcode's point from the
// bundled index (no hand-entered coordinates). Jobs there name only the city.
const CITY_CBD = {Melbourne: '3000', Sydney: '2000', Brisbane: '4000', Perth: '6000',
                  Adelaide: '5000', Canberra: '2600', Hobart: '7000', Darwin: '0800'};
function cityPoint(city) { return index?.postcodes?.[CITY_CBD[city]]?.point || null; }
function cityOf(item) {
  const f = item?.location_fact;
  return f?.precision === 'city' && CITY_CBD[f.city] && cityPoint(f.city) ? f.city : null;
}
// Rough state extents [west, south, east, north], only to decide which state-only
// groups are relevant to the current view (NSW is two boxes so it doesn't cover Victoria).
const STATE_BOXES = {
  NSW: [[141, -36.2, 153.7, -28.1], [148, -37.6, 150.3, -36.2]], ACT: [[148.7, -35.95, 149.45, -35.1]],
  VIC: [[140.9, -39.2, 150, -34]], QLD: [[137.9, -29.2, 153.6, -9.1]], SA: [[129, -38.1, 141, -26]],
  WA: [[112.9, -35.2, 129, -13.6]], TAS: [[143.8, -43.7, 148.5, -39.5]], NT: [[129, -26, 138, -10.9]],
};
// Viewing almost all of Australia (the view covers at least 70% of its area): show a
// single "all of Australia" pill instead of postcode circles and capital markers.
const NATION = [112, -44, 154, -10];
function nationalView() {
  const [w, s, e, n] = viewport();
  const iw = Math.max(0, Math.min(e, NATION[2]) - Math.max(w, NATION[0]));
  const ih = Math.max(0, Math.min(n, NATION[3]) - Math.max(s, NATION[1]));
  return iw * ih >= .7 * (NATION[2] - NATION[0]) * (NATION[3] - NATION[1]);
}
function stateInView(state) {
  const boxes = STATE_BOXES[state];
  if (!boxes) return true;
  const [w, s, e, n] = viewport();
  return boxes.some(b => b[0] <= e && b[2] >= w && b[1] <= n && b[3] >= s);
}
// State-only groups are listed only for states in view; groups are largest first.
function grouped(rows) {
  const m = new Map();
  for (const r of rows) {
    const f = r.location_fact || {};
    if (f.precision === 'state' && Array.isArray(f.states) && f.states.length && !f.states.some(stateInView)) continue;
    const k = groupOf(r);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(r);
  }
  return [...m].sort((a, b) => b[1].length - a[1].length || a[1][0].core_order - b[1][0].core_order);
}
function renderSheet(codes = inView()) {
  const sheet = $('sheet');
  if (!selected) { sheet.hidden = true; return; }
  let rows, sections;
  if (selected === 'area') {
    rows = codes.flatMap(pc => groups.get(pc).items).sort((a, b) => a.core_order - b.core_order);
    $('sheet-title').textContent = t('viewTitle');
    $('sheet-meta').textContent = rows.length ? t('count', {n: rows.length, unit: unit()}) : t('emptyView');
    sections = [[null, rows]];
  } else if (selected === 'all') {
    rows = [...snapshot.items].sort((a, b) => a.core_order - b.core_order);
    $('sheet-title').textContent = t('allTitle');
    $('sheet-meta').textContent = t('count', {n: rows.length, unit: unit()});
    sections = [[null, rows]];
  } else if (selected.startsWith('city:')) {
    const city = selected.slice(5);
    rows = cityJobs.get(city);
    if (!rows) { selected = null; sheet.hidden = true; return; }
    $('sheet-title').textContent = t('cityTitle', {x: city});
    $('sheet-meta').textContent = t('cityMeta', {n: rows.length, unit: unit()});
    sections = [[null, rows]];
  } else if (selected === 'unmapped') {
    rows = missing;
    $('sheet-title').textContent = t('otherTitle');
    $('sheet-meta').textContent = t('otherMeta');
    sections = grouped(rows);
  } else {
    const group = groups.get(selected);
    if (!group) { selected = null; sheet.hidden = true; return; }
    rows = group.items;
    $('sheet-title').textContent = t('postcode', {pc: selected});
    $('sheet-meta').textContent = t('count', {n: rows.length, unit: unit()});
    sections = [[null, rows]];
  }
  sheet.hidden = false;
  const list = $('jobs');
  const key = selected + '|' + sections.map(([h, part]) => (h || '') + ':' + part.map(r => r.uid).join(',')).join('|');
  if (key === list.dataset.key) return;
  list.dataset.key = key;
  list.replaceChildren();
  list.scrollTop = 0;
  // Large lists (e.g. all of Australia) render in batches as the user scrolls.
  listQueue = sections.flatMap(([heading, part]) => [...(heading ? [{heading, n: part.length}] : []), ...part.map(item => ({item}))]);
  renderMore();
  sheet.style.setProperty('--head-h', headHeight() + 'px');
}
const BATCH = 60;
let listQueue = [];
function renderMore() {
  const list = $('jobs');
  // Keep adding batches until the list overflows its box (or nothing is left).
  do {
    for (const entry of listQueue.splice(0, BATCH)) {
      if (entry.heading) {
        const h = document.createElement('h3');
        h.className = 'group'; h.textContent = entry.heading + ' (' + entry.n + ')';
        list.append(h);
      } else list.append(card(entry.item));
    }
  } while (listQueue.length && list.clientHeight && list.scrollHeight <= list.clientHeight + 200);
}
$('jobs').addEventListener('scroll', () => {
  const list = $('jobs');
  while (listQueue.length && list.scrollTop + list.clientHeight >= list.scrollHeight - 400) renderMore();
}, {passive: true});
function card(item) {
  const el = document.createElement('article');
  el.className = 'job';
  const b = document.createElement('button');
  b.textContent = item.title || 'Untitled';
  b.addEventListener('click', () => openJob(item));
  el.append(b);
  for (const value of [item.company, item.location_raw, item.salary, item.work_type]) {
    if (value != null && typeof value !== 'object' && !/^[\s\-–—]*$/.test(String(value))) {   // skip empty / '-' placeholders
      const d = document.createElement('div'); d.textContent = String(value); el.append(d);
    }
  }
  return el;
}
async function openJob(item, retry = true) {
  try { await request('open_job', {snapshot_id: snapshot.snapshot_id, uid: item.uid}); }
  catch (e) {
    const code = String(e.code || '');
    if (retry && /expir/i.test(code)) {      // the snapshot aged out: reload once and open the same job
      const was = selected;
      if (!await load()) return;             // load() has shown its own failure notice
      if (was) showSheet(was);
      const again = snapshot?.items.find(x => x.uid === item.uid);
      if (again) openJob(again, false); else notice(t('openError'), true);
    } else if (/expir|disabl|permission|grant/i.test(code)) failure(e);
    else notice(t('openError'), true);
  }
}
function size() { return [$('surface').clientWidth || 1, $('surface').clientHeight || 1]; }
function project(point) {
  if (map) { const p = map.project(point); return [p.x, p.y]; }
  const [w, h] = size();
  return [(point[0] - box[0]) / (box[2] - box[0]) * w, (box[3] - point[1]) / (box[3] - box[1]) * h];
}
function pathGeometry(geometry) {
  const polys = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
  return polys.map(poly => poly.map(ring => ring.map((p, i) => {
    const q = project(p); return (i ? 'L' : 'M') + q[0].toFixed(1) + ',' + q[1].toFixed(1);
  }).join(' ') + 'Z').join(' ')).join(' ');
}
function renderMap() {
  const had = $('markers').contains(document.activeElement) ? keyOf(document.activeElement) : '';
  buildMap();
  if (had) focusKey(had);        // the rebuilt marker keeps keyboard focus
}
function buildMap() {
  const markers = $('markers'), svg = $('geometry');
  markers.replaceChildren(); svg.replaceChildren();
  if (!index || !snapshot) return;
  if (nationalView()) {
    const b = document.createElement('button');
    b.className = 'city';
    const icon = document.createElement('span'); icon.className = 'ic'; icon.setAttribute('aria-hidden', 'true');
    const count = document.createElement('small'); count.textContent = String(snapshot.total);
    b.append(icon, document.createTextNode(t('allPill')), count);
    b.setAttribute('aria-label', t('allAria', {n: snapshot.total, unit: unit()}));
    b.setAttribute('aria-pressed', String(selected === 'all'));
    b.dataset.key = 'all'; b.onclick = e => showSheet('all', e.currentTarget);
    b.style.visibility = 'hidden';
    markers.append(b);
    const [W, H] = size(), [x, y] = project([133.5, -26]);
    placePill(b, Math.min(Math.max(x, 0), W), Math.min(Math.max(y, 0), H), []);
    return;
  }
  // Merge markers that would overlap on screen; the selected postcode stays separate.
  const clusters = [];
  for (const [pc, g] of groups) {
    const point = project(index.postcodes[pc].point);
    let c = clusters.find(c => c.pc !== selected && pc !== selected && Math.hypot(c.point[0] - point[0], c.point[1] - point[1]) < 46);
    if (!c) { c = {pc, point, codes: [], count: 0}; clusters.push(c); }
    c.codes.push(pc); c.count += g.items.length;
    if (pc === selected && g.feature) {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', pathGeometry(g.feature.geometry)); path.setAttribute('fill-rule', 'evenodd');
      svg.append(path);
    }
  }
  for (const c of clusters) {
    const b = document.createElement('button');
    b.className = 'marker ' + (c.count >= 20 ? 'l4' : c.count >= 6 ? 'l3' : c.count >= 2 ? 'l2' : 'l1');
    b.textContent = String(c.count);
    b.title = c.codes.join(', '); b.dataset.key = c.codes.join(',');
    b.setAttribute('aria-label', c.codes.length > 1 ? t('cluster', {n: c.count, unit: unit()}) : t('marker', {pc: c.pc, n: c.count, unit: unit()}));
    b.setAttribute('aria-pressed', String(c.codes.length === 1 && c.pc === selected));
    b.style.left = c.point[0] + 'px'; b.style.top = c.point[1] + 'px';
    b.onclick = e => c.codes.length === 1 ? showSheet(c.pc, e.currentTarget) : zoomTo(c.codes);
    markers.append(b);
  }
  const [W, H] = size(), placed = [];
  const pill = (key, label, n, aria, pressed, onclick) => {
    const b = document.createElement('button');
    b.className = 'city';
    const icon = document.createElement('span'); icon.className = 'ic'; icon.setAttribute('aria-hidden', 'true');
    const count = document.createElement('small'); count.textContent = String(n);
    b.append(icon, document.createTextNode(label), count);
    b.setAttribute('aria-label', aria); b.setAttribute('aria-pressed', String(pressed));
    b.dataset.key = key; b.onclick = onclick; b.style.visibility = 'hidden';
    markers.append(b);
    return b;
  };
  for (const [city, items] of cityJobs) {
    const [x, y] = project(cityPoint(city));
    if (x < 0 || y < 0 || x > W || y > H) continue;   // only capitals in view get a pill
    const b = pill('city:' + city, t('cityPill', {x: city}), items.length, t('cityAria', {x: city, n: items.length, unit: unit()}),
                   selected === 'city:' + city, e => showSheet('city:' + city, e.currentTarget));
    placed.push(placePill(b, x, y, clusters, placed));
  }
}
// Place a citywide pill on the nearest spot around the city centre that covers no
// postcode circle (rings of 8 directions, growing outwards). When it ends up away
// from the centre, a thin dashed leader and a dot connect it to the centre.
function placePill(b, x, y, clusters, placed = []) {
  const w = b.offsetWidth, h = b.offsetHeight, [W, H] = size();
  const radius = c => (c.count >= 20 ? 24 : c.count >= 6 ? 20 : c.count >= 2 ? 17 : 15) + 4;
  const hits = (l, tp) => clusters.filter(c => {
    const cx = Math.max(l, Math.min(c.point[0], l + w)), cy = Math.max(tp, Math.min(c.point[1], tp + h));
    return Math.hypot(c.point[0] - cx, c.point[1] - cy) < radius(c);
  }).length + placed.filter(p => l < p.l + p.w + 6 && l + w + 6 > p.l && tp < p.t + p.h + 6 && tp + h + 6 > p.t).length
    + (l < 0 || tp < 0 || l + w > W || tp + h > H ? .5 : 0);
  let best = null;
  for (const d of [26, 60, 100, 150]) {
    for (const a of [-90, 90, 180, 0, -135, -45, 135, 45]) {
      const r = a * Math.PI / 180, cos = Math.cos(r), sin = Math.sin(r);
      const cx = x + cos * (d + w / 2 * Math.abs(cos)), cy = y + sin * (d + h / 2 * Math.abs(sin));
      const spot = {l: cx - w / 2, t: cy - h / 2, d, score: hits(cx - w / 2, cy - h / 2)};
      if (!best || spot.score < best.score) best = spot;
    }
    if (best.score === 0) break;
  }
  b.style.left = best.l + 'px'; b.style.top = best.t + 'px'; b.style.visibility = '';
  if (best.d > 26) {
    const svg = $('geometry'), ns = 'http://www.w3.org/2000/svg';
    const ex = Math.max(best.l, Math.min(x, best.l + w)), ey = Math.max(best.t, Math.min(y, best.t + h));
    const line = document.createElementNS(ns, 'line');
    line.setAttribute('class', 'leader');
    for (const [k, v] of [['x1', x], ['y1', y], ['x2', ex], ['y2', ey]]) line.setAttribute(k, v.toFixed(1));
    const dot = document.createElementNS(ns, 'circle');
    dot.setAttribute('class', 'leader-dot'); dot.setAttribute('cx', x.toFixed(1)); dot.setAttribute('cy', y.toFixed(1)); dot.setAttribute('r', '3.5');
    svg.append(line, dot);
  }
  return {l: best.l, t: best.t, w, h};
}
function zoomTo(codes) {
  const boxes = codes.map(pc => index.postcodes[pc].bbox || [...index.postcodes[pc].point, ...index.postcodes[pc].point]);
  const b = [Math.min(...boxes.map(x => x[0])), Math.min(...boxes.map(x => x[1])),
             Math.max(...boxes.map(x => x[2])), Math.max(...boxes.map(x => x[3]))];
  if (map) { map.fitBounds(bounds(b), {padding: 70, maxZoom: 16, duration: 300}); return; }
  const dx = Math.max(.005, (b[2] - b[0]) * .2), dy = Math.max(.005, (b[3] - b[1]) * .2);
  box = [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy];
  render(); saveState();
}

/* ---------- controls ---------- */
$('summary').onclick = e => { if (snapshot) selected === 'area' ? closeSheet() : showSheet('area', e.currentTarget); };
$('unmapped').onclick = e => showSheet('unmapped', e.currentTarget);
/* ---------- results sheet: sizes, drag (phone) and animated close ---------- */
const phone = () => matchMedia('(max-width: 620px)').matches;
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
let leaveTimer = null, sheetDrag = null, suppressTap = false;
function headHeight() { return $('grip').offsetHeight + $('sheet').querySelector('header').offsetHeight; }
// 'collapsed' (title only) | 'half' (default) | 'full' (almost the whole map)
function sheetSize(size) {
  const el = $('sheet');
  el.classList.toggle('collapsed', size === 'collapsed');
  el.classList.toggle('full', size === 'full');
  $('grip').setAttribute('aria-expanded', String(size !== 'collapsed'));
}
function cancelLeave() { clearTimeout(leaveTimer); leaveTimer = null; $('sheet').classList.remove('leaving'); }
function closeSheet() {
  const el = $('sheet');
  const done = () => {
    const active = document.activeElement, lost = !active || active === document.body || el.contains(active);
    cancelLeave(); selected = null; render(); saveState();
    if (lost) focusKey(openerKey);         // back to the control that opened it, if it is still there
    openerKey = '';
  };
  if (el.hidden || reducedMotion()) { done(); return; }
  if (leaveTimer) return;
  el.classList.add('leaving');
  leaveTimer = setTimeout(done, 200);   // matches the CSS close animation
}
$('close').onclick = closeSheet;
$('grip').onclick = () => {
  if (suppressTap) { suppressTap = false; return; }
  sheetSize($('sheet').classList.contains('collapsed') ? 'half' : 'collapsed'); saveState();
};
function dragStart(e) {
  if (!phone() || e.button > 0 || e.target.closest('#close')) return;
  sheetDrag = {y: e.clientY, h: $('sheet').getBoundingClientRect().height, moved: false};
  e.currentTarget.setPointerCapture(e.pointerId);
}
function dragMove(e) {
  if (!sheetDrag) return;
  const dy = e.clientY - sheetDrag.y;
  if (!sheetDrag.moved && Math.abs(dy) < 5) return;
  if (!sheetDrag.moved) { sheetDrag.moved = true; $('sheet').classList.add('dragging'); }
  const max = $('app').clientHeight - 64;
  sheetDrag.want = sheetDrag.h - dy;   // the size the finger asks for (content may be shorter)
  $('sheet').style.maxHeight = Math.max(24, Math.min(max, sheetDrag.want)) + 'px';
}
function dragEnd() {
  if (!sheetDrag) return;
  const el = $('sheet'), moved = sheetDrag.moved, h = sheetDrag.want;
  sheetDrag = null;
  if (!moved) return;
  suppressTap = true; setTimeout(() => { suppressTap = false; }, 0);
  const head = headHeight(), H = $('app').clientHeight;
  el.classList.remove('dragging'); el.style.maxHeight = '';
  if (h < head - 30) { closeSheet(); return; }          // dragged below the title: close
  const snaps = [['collapsed', head], ['half', H * .55], ['full', H - 64]];
  sheetSize(snaps.reduce((a, b) => Math.abs(b[1] - h) < Math.abs(a[1] - h) ? b : a)[0]);
  saveState();
}
for (const handle of [$('grip'), $('sheet').querySelector('header')]) {
  handle.addEventListener('pointerdown', dragStart);
  handle.addEventListener('pointermove', dragMove);
  handle.addEventListener('pointerup', dragEnd);
  handle.addEventListener('pointercancel', dragEnd);
  handle.addEventListener('lostpointercapture', dragEnd);
}
// Release can land anywhere (the sheet moves under the finger); end the drag on any of them.
for (const type of ['pointerup', 'pointercancel', 'touchend', 'touchcancel']) document.addEventListener(type, dragEnd);
// A mouse released outside this frame sends no pointerup here; finish on the next move with no button held.
document.addEventListener('pointermove', e => { if (sheetDrag) e.buttons === 0 && e.pointerType === 'mouse' ? dragEnd() : dragMove(e); });
function zoom(factor) {
  if (map) { map.zoomTo(map.getZoom() + Math.log2(1 / factor)); return; }
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2, dx = (box[2] - box[0]) * factor / 2, dy = (box[3] - box[1]) * factor / 2;
  box = [cx - dx, cy - dy, cx + dx, cy + dy];
  render(); saveState();
}
$('zoom-in').onclick = () => zoom(.7);
$('zoom-out').onclick = () => zoom(1 / .7);
$('reset').onclick = () => {
  box = groups.size || cityJobs.size ? resultsBox(true) : AUSTRALIA.slice();
  if (map) map.fitBounds(bounds(box), {duration: 300, padding: 30, maxZoom: 13, bearing: 0, pitch: 0});
  render(); saveState();
};
$('north').onclick = () => { if (map) map.easeTo({bearing: 0, pitch: 0, duration: 300}); };
// Attribution: tap the bar to open the full sources; tap it again or anywhere else to close.
function setSources(open) {
  $('attr-full').hidden = !open; $('attr-toggle').setAttribute('aria-expanded', String(open));
}
$('attr-toggle').onclick = () => setSources($('attr-full').hidden);
document.addEventListener('pointerdown', e => {
  if (!$('attr-full').hidden && !e.target.closest('#attribution')) setSources(false);
}, true);

// Pan/zoom for the local fallback when the map engine is unavailable.
$('surface').addEventListener('pointerdown', e => {
  if (map || e.target.closest('button')) return;
  dragging = {x: e.clientX, y: e.clientY, box: [...box]};
  $('surface').setPointerCapture(e.pointerId);
});
$('surface').addEventListener('pointermove', e => {
  if (!dragging) return;
  const [w, h] = size(), b = dragging.box;
  const dx = (e.clientX - dragging.x) / w * (b[2] - b[0]), dy = (e.clientY - dragging.y) / h * (b[3] - b[1]);
  box = [b[0] - dx, b[1] + dy, b[2] - dx, b[3] + dy];
  dragging.moved = true; renderMap();
});
for (const event of ['pointerup', 'pointercancel']) $('surface').addEventListener(event, () => {
  if (dragging?.moved) render();
  dragging = null; saveState();
});
new ResizeObserver(() => render()).observe($('surface'));
// Count markers sit above the map; let trackpad scrolling and pinch over them reach the map.
$('markers').addEventListener('wheel', e => {
  if (!map) return;
  e.preventDefault();
  map.getCanvas().dispatchEvent(new WheelEvent('wheel', {
    deltaX: e.deltaX, deltaY: e.deltaY, deltaZ: e.deltaZ, deltaMode: e.deltaMode,
    clientX: e.clientX, clientY: e.clientY, screenX: e.screenX, screenY: e.screenY,
    ctrlKey: e.ctrlKey, metaKey: e.metaKey, shiftKey: e.shiftKey, altKey: e.altKey, bubbles: true, cancelable: true}));
}, {passive: false});

/* ---------- map engine ---------- */
// Soft colour on the muted base styles: blue water, green parks and woodland.
// The dark style has no park layer of its own, so one is added from the same tiles.
const TINT = {light: {water: '#bcd6ec', green: '#dcebd4'}, dark: {water: '#1c3550', green: '#18241c'}};
function tintStyle() {
  if (!map) return;
  const c = TINT[document.body.classList.contains('dark') ? 'dark' : 'light'];
  const layers = map.getStyle()?.layers || [];
  for (const l of layers) {
    const id = l.id.toLowerCase(), src = String(l['source-layer'] || '');
    if (src === 'water' || src === 'waterway') map.setPaintProperty(l.id, l.type === 'line' ? 'line-color' : 'fill-color', c.water);
    else if (l.type === 'fill' && (src === 'park' || /park|wood|grass/.test(id))) map.setPaintProperty(l.id, 'fill-color', c.green);
  }
  if (!layers.some(l => l['source-layer'] === 'park') && !map.getLayer('jobs-park')) {
    const source = layers.find(l => l['source-layer'])?.source;
    if (source) map.addLayer({id: 'jobs-park', type: 'fill', source, 'source-layer': 'park', paint: {'fill-color': c.green}},
                             layers.find(l => l['source-layer'] === 'water')?.id);
  }
}
function styleUrl() { return TILE_ORIGIN + '/styles/' + (document.body.classList.contains('dark') ? 'dark' : 'positron'); }
function applyTheme(theme) {
  const dark = theme === 'dark';
  if (dark === document.body.classList.contains('dark')) return;
  document.body.classList.toggle('dark', dark);
  if (map && styleReady) useStyle();
}
// A theme change while the first style is still loading is applied when it arrives.
function useStyle() { appliedStyle = styleUrl(); map.setStyle(appliedStyle); }
function fallback() {
  mapFailed = true; $('north').hidden = true;
  if ($('notice').hidden) notice(t('fallback'));
  render();
}
async function startMap() {
  if (map || mapFailed) return;
  try {
    const lib = await import(new URL('/plugins/map/assets/vendor/maplibre-gl.mjs', document.baseURI).href);
    if (disposed) return;
    lib.setWorkerUrl(new URL('/plugins/map/assets/vendor/maplibre-gl-worker.mjs', document.baseURI).href);
    map = new lib.Map({
      container: 'basemap', style: appliedStyle = styleUrl(), bounds: bounds(box), fitBoundsOptions: {padding: snapshot ? 40 : 0, maxZoom: 13},
      attributionControl: false,
      transformRequest: url => {
        const u = new URL(url, location.href);
        if (u.origin !== TILE_ORIGIN) throw new Error('Blocked map resource origin');
        return {url: u.href};
      },
    });
    map.on('move', renderMap);
    map.on('moveend', () => { render(); saveState(); });
    map.on('rotate', () => { $('north-needle').style.transform = `rotate(${-map.getBearing()}deg)`; });
    map.on('style.load', () => { styleReady = true; if (appliedStyle !== styleUrl()) useStyle(); else tintStyle(); });
    // Only a failure before the style loads gives up the background map;
    // a single tile error later keeps it.
    map.on('error', () => {
      if (styleReady || mapFailed) return;
      const broken = map; map = null; broken.remove(); fallback();
    });
    map.on('load', () => render());
    $('north').hidden = false;
    render();
  } catch (e) {
    console.error('Map initialization failed:', e);
    fallback();
  }
}

parent.postMessage({type: 'jobs-plugin-ready', version: 1}, '*');
})();
