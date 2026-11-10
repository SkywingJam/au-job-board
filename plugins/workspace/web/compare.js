(() => {
'use strict';
/* Workspace compare page: up to 3 jobs side by side (≥900px), 2 stacked blocks on
   narrow screens. Read-only; all job text is rendered with textContent. */
const {t, el, str, isObj} = WS;
const $ = id => document.getElementById(id);
const wideQuery = matchMedia('(min-width: 900px)');

let handed = [], state = {}, selection = [], records = null, epoch = 0, ready = false;

const CUES_EN = {'保密等级': 'Clearance level', '保密审查': 'Security clearance', '公民/PR': 'Citizen / PR',
  '工作权': 'Work rights', '警察检查': 'Police check', '原住民专属': 'Identified position', '驾照': 'Driver’s licence'};

// Stored descriptions are often Markdown. Show them as readable plain text: drop
// emphasis markers and backslash escapes, turn list markers into bullets. Text only.
function plainText(text) {
  return text.replace(/\\([\\`*_{}\[\]()#+\-.!&|<>~])/g, '$1')
    .replace(/(\*\*|__)(?=\S)([^\n]*?\S)\1/g, '$2')
    .replace(/^[ \t]*[*+-][ \t]+/gm, '• ')
    .replace(/^[ \t]*#{1,6}[ \t]+/gm, '')
    .replace(/\n{3,}/g, '\n\n');
}
function setStatus(text, retry = null, tone = '') {
  const s = $('status');
  s.className = 'status' + (tone ? ' ' + tone : '');
  s.replaceChildren();
  if (!text) return;
  s.append(text);
  if (retry) s.append(' ', el('button', {class: 'link-btn', type: 'button', onclick: retry}, t('retry')));
}

/* Saved selection (from the sidebar) limited to jobs still in the workspace;
   without one, the first three jobs in the workspace. */
function pickSelection() {
  const ids = handed.map(h => h.uid);
  const saved = Array.isArray(state.compare) ? state.compare.map(str).filter(u => ids.includes(u)) : [];
  return [...new Set(saved.length >= 2 ? saved : ids)].slice(0, 3);
}

async function load() {
  const mine = ++epoch;
  selection = pickSelection();
  records = null;
  if (selection.length < 2) { layout(); return; }
  $('columns').replaceChildren();
  $('empty').hidden = true;
  setStatus(t('loadingPage'), null, 'muted');
  try {
    const result = await WS.request('handed_jobs', {uids: selection, detail: true});
    if (mine !== epoch) return;
    records = WS.recordsFrom(result);
    setStatus('');
  } catch (e) {
    if (mine !== epoch) return;
    setStatus(WS.errorText(e), load, 'error');
    return;
  }
  layout();
}

const notStated = () => el('span', {class: 'none'}, t('notStated'));
const dash = () => el('span', {class: 'none'}, t('noLabels'));
const text = v => (str(v) ? el('span', {}, str(v)) : notStated());
const skillKey = s => str(s).toLowerCase().replace(/\s+/g, ' ');
const skillsOf = r => (Array.isArray(r.skills) ? r.skills.map(str).filter(Boolean) : []);

function qualificationText(q) {
  if (!isObj(q) || !['eligible', 'ineligible', 'unsure'].includes(q.effective)) return t('qUnknown');
  const v = t({eligible: 'vEligible', ineligible: 'vIneligible', unsure: 'vUnsure'}[q.effective]);
  return t(q.corrected === true ? 'qManual' : 'qMachine', {v});
}
function evidenceNode(ev) {
  if (!isObj(ev)) return dash();
  const quote = str(ev.snippet) || str(ev.matched_text);
  const cue = str(ev.cue);
  if (!quote && !cue) return dash();
  return el('div', {},
    cue ? el('span', {class: 'chip cue'}, WS.lang === 'en' ? (CUES_EN[cue] || cue) : cue) : null,
    quote ? el('blockquote', {class: 'quote'}, quote) : null);
}
function skillsNode(r, others) {
  const list = skillsOf(r);
  if (!list.length) return notStated();
  return el('ul', {class: 'skills'}, ...list.map(s => {
    const unique = !others.has(skillKey(s));
    return el('li', {class: 'chip' + (unique ? ' unique' : '')},
      unique ? el('span', {'aria-hidden': 'true'}, '+ ') : null, s,
      unique ? el('span', {class: 'sr-only'}, t('uniqueSr')) : null);
  }));
}
function labelsNode(labels) {
  const chips = WS.labelChips(labels);
  return chips.length ? el('div', {class: 'chips'}, ...chips.map(c => el('span', {class: 'chip ' + c.kind}, c.text))) : dash();
}
function listedText(v) {
  const s = str(v);
  return /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : s;
}

function column(r, index, others, wide) {
  const stored = handed.find(h => h.uid === r.uid);
  const title = str(r.title) || stored?.title || t('untitled');
  const company = str(r.company) || stored?.company || '';
  const updated = stored?.revision && str(r.revision) && str(r.revision) !== stored.revision;
  const headId = 'col-title-' + index;
  const rows = [
    ['rSalary', text(r.salary)],
    ['rLocation', text(r.location_raw)],
    ['rWorkType', text(r.work_type)],
    ['rListed', text(listedText(r.listing_date))],
    ['rQualification', el('span', {}, qualificationText(r.qualification))],
    ['rEvidence', evidenceNode(r.eligibility_evidence)],
    ['rSkills', skillsNode(r, others)],
    ['rLabels', labelsNode(r.human_labels)],
  ];
  const description = typeof r.description === 'string' && r.description.trim() ? plainText(r.description) : '';
  return el('section', {class: 'col', 'aria-labelledby': headId, tabindex: wide ? '0' : null, 'data-uid': r.uid},
    el('header', {class: 'col-head', 'data-row': 'head'},
      el('h2', {id: headId}, title),
      company ? el('p', {class: 'muted'}, company) : null,
      updated ? el('span', {class: 'chip cue'}, t('updated')) : null,
      el('button', {class: 'btn small', type: 'button', 'aria-label': t('openInListAria', {x: title}),
        onclick: () => openJob(r.uid)}, t('openInList'))),
    el('dl', {class: 'summary'}, ...rows.map(([key, value], i) =>
      el('div', {class: 'srow', 'data-row': String(i)}, el('dt', {}, t(key)), el('dd', {}, value)))),
    el('h3', {class: 'jd-title'}, t('rJd')),
    el('div', {class: 'jd', tabindex: wide ? null : '0', role: wide ? null : 'region', 'aria-label': t('rJd') + ' · ' + title},
      description || t('noDescription')));
}

function layout() {
  if (!ready) return;
  const wide = wideQuery.matches;
  document.body.classList.toggle('wide', wide);
  const notes = [];
  const usable = [];
  for (const uid of selection) {
    const r = records?.get(uid);
    if (!r) continue;
    if (r.missing) notes.push(t('missingNote', {x: handed.find(h => h.uid === uid)?.title || t('untitled')}));
    else usable.push(r);
  }
  const shown = usable.slice(0, wide ? 3 : 2);
  if (usable.length > shown.length) notes.push(t('hiddenThird'));
  $('notes').replaceChildren(...notes.map(n => el('li', {}, n)));
  if (records === null && selection.length >= 2) return;    // still loading or failed
  if (shown.length < 2) {
    $('columns').replaceChildren();
    $('legend').textContent = '';
    $('empty-text').textContent = t('tooFew');
    $('empty').hidden = false;
    return;
  }
  $('empty').hidden = true;
  $('legend').textContent = t('uniqueLegend');
  const keysets = shown.map(r => new Set(skillsOf(r).map(skillKey)));
  const cols = shown.map((r, i) => {
    const others = new Set();
    keysets.forEach((set, j) => { if (j !== i) for (const k of set) others.add(k); });
    return column(r, i, others, wide);
  });
  $('columns').style.setProperty('--cols', String(shown.length));
  $('columns').replaceChildren(...cols);
  align();
}

/* Headers and summary rows line up across the columns: each gets the height of its tallest cell. */
function align() {
  const rows = [...document.querySelectorAll('.col-head, .srow')];
  for (const row of rows) row.style.minHeight = '';
  if (!document.body.classList.contains('wide')) return;
  const tallest = new Map();
  for (const row of rows) tallest.set(row.dataset.row, Math.max(tallest.get(row.dataset.row) || 0, row.getBoundingClientRect().height));
  for (const row of rows) row.style.minHeight = tallest.get(row.dataset.row) + 'px';
}

async function openJob(uid) {
  try { await WS.request('open_job', {uid}); }
  catch (e) { setStatus(WS.errorText(e), null, 'error'); }
}

// Key presses inside this frame never reach the host page, so Esc (the host's "back to list" key) is forwarded here.
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || e.isComposing || e.defaultPrevented || !ready) return;
  e.preventDefault();
  WS.request('navigate_list', {}).catch(err => setStatus(WS.errorText(err), null, 'error'));
});

let resizeTimer = null;
wideQuery.addEventListener('change', () => layout());
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(align, 120); });

WS.start({
  context(ctx) {
    handed = WS.cleanHanded(ctx.handed);
    state = isObj(ctx.state) ? ctx.state : {};
    ready = true;
    $('page-title').textContent = t('pageTitle');
    $('back-list').textContent = t('backList');
    $('back-list').addEventListener('click', () => WS.request('navigate_list', {}).catch(e => setStatus(WS.errorText(e), null, 'error')));
    document.title = t('pageTitle');
    $('app').hidden = false;
    load();
  },
  handed(list) {
    const before = selection.join('\n');
    handed = list;
    // A shown job was removed elsewhere: reload with what is still in the workspace.
    if (pickSelection().join('\n') !== before) load(); else layout();
  },
});
})();
