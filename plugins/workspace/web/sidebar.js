(() => {
'use strict';
/* Workspace sidebar. The host owns the list of jobs; this frame shows it, asks the host
   to remove/clear/open, builds compare selections and agent instructions. */
const {t, el, str, isObj} = WS;
const $ = id => document.getElementById(id);

let handed = [];                 // host list [{uid,title,company,revision,added_at}]
let records = new Map();         // uid → summary record | {uid, missing:true}
let loading = false, loadError = null, epoch = 0, loadingTimer = null;
let current = null, state = {}, view = 'list', opener = null;
let chooseSel = [];
let template = 'compare', agentSel = [], fitUid = '';
let ready = false, statusKind = '';   // 'load' = about the job details read; '' = a one-off message

/* ---------- helpers ---------- */
const isMissing = uid => records.get(uid)?.missing === true;
const present = () => handed.filter(h => !isMissing(h.uid));
const presentUids = () => present().map(h => h.uid);
const titleOf = h => str(records.get(h.uid)?.title) || h.title || t('untitled');
const accessOn = () => WS.context?.agent_access === 'on';
const agentAllowed = () => accessOn() || state.agent_override === true;
// The compare page shows 3 columns at ≥900px; decide by the device screen this sidebar is on.
function compareLimit() {
  const hint = Number(WS.context?.compare_columns);
  if (hint === 2 || hint === 3) return hint;
  return (window.screen?.width || 0) >= 900 ? 3 : 2;
}

function setStatus(text, retry = null, tone = '', kind = '') {
  statusKind = text ? kind : '';
  const s = $('status');
  s.className = 'status' + (tone ? ' ' + tone : '');
  s.replaceChildren();
  if (!text) return;
  s.append(text);
  if (retry) s.append(' ', el('button', {class: 'link-btn', type: 'button', onclick: retry}, t('retry')));
}

// Rejects unless the host confirms the state was stored.
async function save(patch) {
  state = {...state, ...patch, v: 1};
  const result = await WS.request('save_state', {state});
  if (result?.ok !== true) throw Object.assign(new Error('state not stored'), {code: 'state_not_stored'});
}

/* ---------- data ---------- */
async function refresh() {
  const mine = ++epoch;
  clearTimeout(loadingTimer);
  if (!handed.length) { loading = false; loadError = null; if (statusKind === 'load') setStatus(''); render(); return; }
  loading = true; loadError = null;
  // Only say "updating" if it takes a moment; the stored titles are already shown.
  loadingTimer = setTimeout(() => { if (mine === epoch && loading) setStatus(t('loading'), null, 'muted', 'load'); }, 400);
  try {
    const result = await WS.request('handed_jobs', {uids: handed.map(h => h.uid), detail: false});
    if (mine !== epoch) return;
    records = WS.recordsFrom(result);
    loading = false; clearTimeout(loadingTimer);
    if (statusKind === 'load') setStatus('');
  } catch (e) {
    if (mine !== epoch) return;
    loading = false; clearTimeout(loadingTimer);
    loadError = e;
    setStatus(t('loadError', {err: WS.errorText(e)}), refresh, 'error', 'load');
  }
  render();
}

function applyHanded(list, fetch) {
  handed = list;
  const keep = new Set(handed.map(h => h.uid));
  for (const uid of [...records.keys()]) if (!keep.has(uid)) records.delete(uid);
  chooseSel = chooseSel.filter(u => keep.has(u));
  agentSel = agentSel.filter(u => keep.has(u));
  if (fitUid && !keep.has(fitUid)) fitUid = '';
  if (!handed.length && view !== 'list') showView('list');
  render();
  if (fetch) refresh();
}

/* ---------- list ---------- */
function itemNode(h, index) {
  const r = records.get(h.uid);
  const rec = r && !r.missing ? r : null;
  const title = titleOf(h);
  const company = str(rec?.company) || h.company;
  const titleNode = r?.missing
    ? el('span', {class: 'item-title'}, title)
    : el('button', {class: 'item-title link', type: 'button', 'data-focus': 'open:' + h.uid,
        title: t('open', {x: title}), onclick: () => openJob(h.uid)}, title);
  const meta = [str(rec?.location_raw), str(rec?.salary)].filter(Boolean).join(' · ');
  const chips = [];
  if (r?.missing) chips.push(el('span', {class: 'chip warn'}, t('missing')));
  else if (rec && str(rec.revision) && h.revision && str(rec.revision) !== h.revision)
    chips.push(el('span', {class: 'chip cue'}, t('updated')));
  for (const c of WS.labelChips(rec?.human_labels)) chips.push(el('span', {class: 'chip ' + c.kind}, c.text));
  return el('li', {class: 'item' + (r?.missing ? ' is-missing' : ''), 'data-uid': h.uid},
    el('div', {class: 'item-main'},
      titleNode,
      company ? el('div', {class: 'item-company'}, company) : null,
      el('div', {class: 'item-meta'}, meta ? el('span', {}, meta) : null, ...chips)),
    el('button', {class: 'link-btn remove', type: 'button', 'data-focus': 'remove:' + h.uid,
      'aria-label': t('removeAria', {x: title}), onclick: () => remove(h.uid, index)}, t('remove')));
}

function render() {
  if (!ready) return;
  const focusKey = document.activeElement?.dataset?.focus;
  $('title').textContent = t('title');
  $('count').textContent = t('count', {n: handed.length});
  $('count').setAttribute('aria-label', t('countAria', {n: handed.length}));
  $('clear').hidden = !handed.length || view !== 'list' || !$('confirm').hidden;
  $('list').replaceChildren(...handed.map(itemNode));
  $('empty').hidden = handed.length > 0;
  renderCurrent();
  renderFoot();
  if (view === 'choose') renderChoose();
  if (view === 'agent') renderAgent();
  if (focusKey) document.querySelector(`[data-focus="${CSS.escape(focusKey)}"]`)?.focus();
}

function renderCurrent() {
  const box = $('current');
  const uid = str(current?.uid);
  box.hidden = !uid;
  if (!uid) return;
  const inside = handed.some(h => h.uid === uid);
  $('current-label').textContent = t('viewing');
  $('current-title').textContent = str(current.title) || t('untitled');
  $('current-state').textContent = inside ? t('inWs') : t('notInWs');
  $('current-state').className = 'chip ' + (inside ? 'good' : 'muted');
}

function renderFoot() {
  const n = present().length;
  $('foot-list').hidden = view !== 'list';
  $('foot-choose').hidden = view !== 'choose';
  $('foot-agent').hidden = view !== 'agent';
  $('compare').disabled = n < 2;
  $('compare-hint').hidden = n >= 2 || view !== 'list';
  if (n < 2) $('compare').setAttribute('aria-describedby', 'compare-hint');
  else $('compare').removeAttribute('aria-describedby');
  const allowed = agentAllowed();
  $('agent').disabled = !allowed || n < 1;
  $('access').hidden = accessOn() || view !== 'list';
  $('override').checked = state.agent_override === true;
  if (!accessOn()) $('agent').setAttribute('aria-describedby', 'access-note');
  else $('agent').removeAttribute('aria-describedby');
}

/* ---------- actions ---------- */
async function openJob(uid) {
  try { await WS.request('open_job', {uid}); }
  catch (e) { setStatus(t('openFailed', {err: WS.errorText(e)}), null, 'error'); }
}

async function remove(uid, index) {
  try {
    const result = await WS.request('remove_handed', {uids: [uid]});
    applyHanded(WS.cleanHanded(result?.handed), false);
    setStatus(t('removed'));
    // Keep the keyboard user in place: next item, else previous, else the panel title.
    const next = handed[Math.min(index, handed.length - 1)];
    const target = next && document.querySelector(`[data-focus="${CSS.escape('remove:' + next.uid)}"]`);
    (target || $('title')).focus?.();
  } catch (e) { setStatus(WS.errorText(e), null, 'error'); }
}

function askClear() {
  $('confirm-text').textContent = t('clearAsk', {n: handed.length});
  $('confirm').hidden = false; $('clear').hidden = true;
  $('confirm-no').focus();
}
function closeConfirm(focusClear = true) {
  $('confirm').hidden = true;
  $('clear').hidden = !handed.length || view !== 'list';
  if (focusClear && !$('clear').hidden) $('clear').focus();
}
async function clearAll() {
  try {
    const result = await WS.request('clear_handed', {});
    closeConfirm(false);
    applyHanded(WS.cleanHanded(result?.handed), false);
    setStatus(t('cleared'));
    $('title').focus();
  } catch (e) { closeConfirm(false); setStatus(WS.errorText(e), null, 'error'); }
}

/* ---------- views ---------- */
function showView(next, from = null) {
  view = next;
  $('list-view').hidden = next !== 'list';
  $('choose-view').hidden = next !== 'choose';
  $('agent-view').hidden = next !== 'agent';
  if (next !== 'list') closeConfirm(false);
  if (statusKind !== 'load') setStatus('');   // one-off messages belong to the view they came from
  if (from) opener = from;
  render();
  if (next === 'choose') $('choose-title').focus();
  else if (next === 'agent') $('agent-title').focus();
  else if (opener) { (opener.disabled ? $('title') : opener).focus(); opener = null; }
}

function openChoose() {
  const limit = compareLimit(), ids = presentUids();
  const saved = Array.isArray(state.compare) ? state.compare.filter(u => ids.includes(u)).slice(0, limit) : [];
  chooseSel = saved.length >= 2 ? saved : ids.slice(0, limit);
  showView('choose', $('compare'));
}
function renderChoose() {
  const limit = compareLimit();
  $('choose-title').textContent = t('chooseTitle');
  $('choose-legend').textContent = t('chooseTitle');
  $('choose-note').textContent = limit === 3 ? t('chooseNote', {n: 3}) + ' ' + t('chooseSmall') : t('chooseTwo');
  const full = chooseSel.length >= limit;
  $('choose-list').replaceChildren(...present().map(h => {
    const on = chooseSel.includes(h.uid);
    return el('label', {class: 'check'},
      el('input', {type: 'checkbox', checked: on, disabled: !on && full, 'data-focus': 'pick:' + h.uid,
        onchange: e => {
          chooseSel = e.target.checked ? [...chooseSel, h.uid].slice(0, limit) : chooseSel.filter(u => u !== h.uid);
          // keep the list order, not the click order
          chooseSel = presentUids().filter(u => chooseSel.includes(u));
          render();
        }}),
      ' ', el('span', {}, titleOf(h)));
  }));
  $('open-compare').disabled = chooseSel.length < 2;
}
async function openCompare() {
  const sel = chooseSel.slice(0, compareLimit());
  if (sel.length < 2) return;
  try {
    // The compare page reads this selection; without it it would show other jobs.
    try { await save({compare: sel}); }
    catch (e) { setStatus(t('saveFailed', {err: WS.errorText(e)}), null, 'error'); return; }
    await WS.request('open_page', {});
  } catch (e) { setStatus(WS.errorText(e), null, 'error'); }
}

function openAgent() {
  if ($('agent').disabled) return;
  const ids = presentUids();
  if (!agentSel.length) agentSel = ids.slice();
  if (!fitUid || !ids.includes(fitUid)) {
    const cur = str(current?.uid);
    fitUid = ids.includes(cur) ? cur : ids[0] || '';
  }
  if (['compare', 'fit', 'write'].includes(state.template)) template = state.template;
  showView('agent', $('agent'));
}
function instructionUids() {
  const ids = presentUids();
  if (template === 'fit') return ids.includes(fitUid) ? [fitUid] : [];
  return ids.filter(u => agentSel.includes(u));
}
function instructionText(uids) {
  const key = {compare: 'iCompare', fit: 'iFit', write: 'iWrite'}[template];
  return t(key, {ids: uids.join(t('idSep'))});
}
function renderAgent() {
  $('agent-title').textContent = t('agentTitle');
  $('template-legend').textContent = t('template');
  $('templates').replaceChildren(...[['compare', 'tCompare'], ['fit', 'tFit'], ['write', 'tWrite']].map(([v, k]) =>
    el('label', {class: 'check'},
      el('input', {type: 'radio', name: 'template', value: v, checked: template === v, 'data-focus': 'tpl:' + v,
        onchange: () => { template = v; setHint(''); save({template: v}).catch(() => {}); render(); }}),
      ' ', el('span', {}, t(k)))));
  const fit = template === 'fit';
  $('agent-jobs-legend').textContent = fit ? t('fitOne') : t('jobsFor');
  $('agent-jobs').replaceChildren(...present().map(h => el('label', {class: 'check'},
    el('input', fit
      ? {type: 'radio', name: 'fit-job', checked: fitUid === h.uid, 'data-focus': 'fit:' + h.uid,
          onchange: () => { fitUid = h.uid; setHint(''); render(); }}
      : {type: 'checkbox', checked: agentSel.includes(h.uid), 'data-focus': 'job:' + h.uid,
          onchange: e => { agentSel = e.target.checked ? [...agentSel, h.uid] : agentSel.filter(u => u !== h.uid); setHint(''); render(); }}),
    ' ', el('span', {}, titleOf(h)))));
  const uids = instructionUids();
  const need = template === 'compare' ? 2 : 1;
  const ok = uids.length >= need;
  $('instruction-label').textContent = t('instruction');
  $('instruction').value = ok ? instructionText(uids) : '';
  $('copy').disabled = !ok;
  if (!ok) setHint(template === 'compare' ? t('needTwo') : t('needOne'));
  else if ($('agent-hint').dataset.kind === 'need') setHint('');
}
function setHint(text, kind = text ? 'need' : '') {
  $('agent-hint').textContent = text;
  $('agent-hint').dataset.kind = kind;
}
async function copy() {
  const text = $('instruction').value;
  if (!text) return;
  const manual = msg => { setHint(msg, 'copy'); $('instruction').focus(); $('instruction').select(); };
  if (text.length > 2000) { manual(t('tooLong')); return; }
  setHint('', '');
  try {
    const r = await WS.request('copy_text', {text});
    if (r?.copied === true) setHint(t('copied'), 'copy');
    else if (r?.fallback === true) setHint(t('fallback'), 'copy');
    else manual(t('copyFailed'));
  } catch (e) { manual(t('copyFailed')); }
}

async function toggleOverride(e) {
  const on = e.target.checked;
  state = {...state, agent_override: on};
  renderFoot();
  try { await save({agent_override: on}); }
  catch (err) { setStatus(t('saveFailed', {err: WS.errorText(err)}), null, 'error'); }
}

/* ---------- static text and wiring ---------- */
function labels() {
  const text = (id, key) => { $(id).textContent = t(key); };
  text('clear', 'clearAll'); text('confirm-yes', 'clearYes'); text('confirm-no', 'cancel');
  text('empty-title', 'emptyTitle'); text('empty-body', 'emptyBody');
  text('way1', 'way1'); text('way2', 'way2'); text('way3', 'way3');
  text('compare', 'compare'); text('agent', 'agent'); text('compare-hint', 'compareNeed');
  text('open-compare', 'openCompare'); text('choose-back', 'back');
  text('copy', 'copy'); text('agent-back', 'back');
  text('access-note', 'accessOff'); text('override-label', 'override');
  text('help-summary', 'helpSummary'); text('help-body', 'helpBody');
  $('list').setAttribute('aria-label', t('listAria'));
  document.title = t('title');
}

function wire() {
  $('clear').addEventListener('click', askClear);
  $('confirm-yes').addEventListener('click', clearAll);
  $('confirm-no').addEventListener('click', () => closeConfirm());
  $('compare').addEventListener('click', openChoose);
  $('agent').addEventListener('click', openAgent);
  $('choose-back').addEventListener('click', () => showView('list'));
  $('agent-back').addEventListener('click', () => showView('list'));
  $('open-compare').addEventListener('click', openCompare);
  $('copy').addEventListener('click', copy);
  $('override').addEventListener('change', toggleOverride);
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || e.isComposing) return;
    if (!$('confirm').hidden) { e.preventDefault(); closeConfirm(); }
    else if (view !== 'list') { e.preventDefault(); showView('list'); }
    // Keys pressed in here never reach the board: ask it to close the phone sheet.
    else WS.request('dismiss', {}).catch(() => {});
  });
}

WS.start({
  context(ctx) {
    handed = WS.cleanHanded(ctx.handed);
    current = isObj(ctx.current) ? ctx.current : null;
    state = isObj(ctx.state) ? {...ctx.state} : {};
    ready = true;
    labels(); wire();
    $('app').hidden = false;
    render();
    refresh();
  },
  handed(list) { if (ready) applyHanded(list, true); },
  current(c) { current = c; if (ready) renderCurrent(); },
});
})();
