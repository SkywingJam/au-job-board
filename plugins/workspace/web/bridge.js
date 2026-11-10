(() => {
'use strict';
/* Shared by the workspace sidebar and compare page. The frame runs sandboxed
   (opaque origin) and reaches the board only through the jobs-plugin v1 bridge:
   ready → context → request/response, plus theme/handed/current events.
   All data is untrusted: callers render it with textContent only (see el()). */

const words = {
  en: {
    waiting: 'Workspace is not connected. Reload the page to try again.',
    // errors
    errDisabled: 'The workspace is turned off or needs permission again.',
    errBusy: 'The board is busy. Try again in a moment.',
    errTimeout: 'This took too long. Try again.',
    errTooLarge: 'These jobs are too large to load together.',
    errGeneric: 'Something went wrong. Try again.',
    retry: 'Try again',
    // labels shared by both pages
    vEligible: 'Eligible', vIneligible: 'Ineligible', vUnsure: 'Unsure',
    vSaved: 'To apply', vApplied: 'Applied', vSkipped: 'Skipped', vPriority: '★ Priority',
    missing: 'No longer in results',
    updated: 'Updated since added',
    untitled: 'Untitled job',
    // sidebar
    title: 'Workspace',
    count: '{n}/10', countAria: '{n} of 10 jobs',
    viewing: 'Viewing',
    inWs: 'In workspace', notInWs: 'Not in workspace',
    emptyTitle: 'Nothing here yet',
    emptyBody: 'Collect up to 10 jobs to compare them or hand them to an agent. To add a job:',
    way1: 'Drag a job card onto this panel',
    way2: 'Open a job and choose “Add to workspace”',
    way3: 'Select several jobs in the list and add them together',
    listAria: 'Jobs in the workspace',
    open: 'Open “{x}”',
    remove: 'Remove', removeAria: 'Remove “{x}” from the workspace',
    clearAll: 'Clear all',
    clearAsk: 'Remove all {n} jobs from the workspace?',
    clearYes: 'Remove all', cancel: 'Cancel',
    loading: 'Updating job details…',
    loadError: '{err} Showing the saved titles.',
    removed: 'Removed from the workspace.', cleared: 'Workspace cleared.',
    openFailed: 'This job couldn’t be opened. {err}',
    compare: 'Compare', compareNeed: 'Add 2 or more jobs to compare them.',
    chooseTitle: 'Choose jobs to compare',
    chooseNote: 'Pick 2 to {n}.', chooseTwo: 'Pick 2.',
    chooseSmall: 'Small screens show 2 side by side; the third is hidden there.',
    openCompare: 'Open comparison', back: 'Back',
    agent: 'Hand to agent',
    agentTitle: 'Hand to an agent',
    template: 'What should the agent do?',
    tCompare: 'Compare jobs', tFit: 'Check my fit for one job', tWrite: 'Draft a CV and cover letter',
    jobsFor: 'Jobs to include', fitOne: 'Job to check',
    needTwo: 'Choose at least 2 jobs to compare.', needOne: 'Choose at least one job.',
    instruction: 'Instruction to paste into your agent',
    copy: 'Copy instruction',
    copied: 'Copied. Paste it into your agent.',
    fallback: 'Couldn’t copy automatically. Copy the text from the window that opened.',
    copyFailed: 'Couldn’t copy. Select the instruction above and copy it yourself.',
    tooLong: 'Too long to copy automatically. Select the instruction above and copy it yourself.',
    accessOff: 'An agent can only read these jobs through the Job Board MCP (see docs/mcp.md).',
    override: 'I have set up agent access another way',
    saveFailed: 'This setting couldn’t be saved. {err}',
    helpSummary: 'About the agent skills',
    helpBody: 'The agent follows the jobs-compare and jobs-write skills. They ship in plugins/workspace/skills/ and must be installed into your agent first — see the plugin README.',
    iCompare: 'Use the jobs-compare skill to compare these jobs: {ids}',
    iFit: 'Use the jobs-compare skill to check my fit for this job: {ids}',
    iWrite: 'Use the jobs-write skill to draft a CV and cover letter for these jobs: {ids}',
    idSep: ', ',
    // compare page
    pageTitle: 'Compare jobs',
    openInList: 'Open job', openInListAria: 'Open “{x}”',
    rSalary: 'Salary', rLocation: 'Location', rWorkType: 'Work type', rListed: 'Listed',
    rQualification: 'Eligibility', rEvidence: 'Eligibility evidence', rSkills: 'Skills',
    rLabels: 'Your labels', rJd: 'Job description',
    notStated: 'Not stated', noLabels: '—', noDescription: 'No description.',
    qMachine: '{v} · analysis', qManual: '{v} · manual correction', qUnknown: 'Analysis unknown',
    uniqueLegend: 'Skills marked + appear only in that job.', uniqueSr: ' (only in this job)',
    hiddenThird: 'The third job is hidden on small screens. Use a wider window to see all three.',
    tooFew: 'Choose at least 2 jobs in the workspace to compare them side by side.',
    backList: 'Back to list',
    missingNote: '“{x}” is no longer in results, so it isn’t shown.',
    loadingPage: 'Loading jobs…',
  },
  zh: {
    waiting: '工作区未连接，请刷新页面重试。',
    errDisabled: '工作区已关闭或需要重新授权。',
    errBusy: '面板正忙，请稍后再试。',
    errTimeout: '等待超时，请重试。',
    errTooLarge: '这些岗位内容太多，无法一起加载。',
    errGeneric: '出了点问题，请重试。',
    retry: '重试',
    vEligible: '可投', vIneligible: '不可投', vUnsure: '待定',
    vSaved: '想投', vApplied: '已投', vSkipped: '跳过', vPriority: '★ 优先',
    missing: '已不在结果中',
    updated: '加入后有更新',
    untitled: '无标题岗位',
    title: '工作区',
    count: '{n}/10', countAria: '共 {n} 个岗位，上限 10 个',
    viewing: '正在查看',
    inWs: '已在工作区', notInWs: '不在工作区',
    emptyTitle: '还没有岗位',
    emptyBody: '最多放 10 个岗位，用来对比或交给 agent。添加方式：',
    way1: '把列表里的岗位卡片拖到这里',
    way2: '打开岗位，点「加入工作区」',
    way3: '在列表里多选，一起加入',
    listAria: '工作区里的岗位',
    open: '打开「{x}」',
    remove: '移除', removeAria: '把「{x}」移出工作区',
    clearAll: '全部清空',
    clearAsk: '把 {n} 个岗位全部移出工作区？',
    clearYes: '全部移除', cancel: '取消',
    loading: '正在更新岗位信息…',
    loadError: '{err}下面显示的是保存的标题。',
    removed: '已移出工作区。', cleared: '工作区已清空。',
    openFailed: '无法打开这个岗位。{err}',
    compare: '对比', compareNeed: '放入 2 个或以上岗位才能对比。',
    chooseTitle: '选择要对比的岗位',
    chooseNote: '选 2 到 {n} 个。', chooseTwo: '选 2 个。',
    chooseSmall: '小屏幕上只并排显示 2 个，第 3 个会隐藏。',
    openCompare: '打开对比', back: '返回',
    agent: '交给 agent',
    agentTitle: '交给 agent',
    template: '要 agent 做什么？',
    tCompare: '对比岗位', tFit: '评估一个岗位的匹配度', tWrite: '写 CV 和求职信',
    jobsFor: '包含的岗位', fitOne: '要评估的岗位',
    needTwo: '至少选 2 个岗位才能对比。', needOne: '至少选一个岗位。',
    instruction: '粘贴给 agent 的指令',
    copy: '复制指令',
    copied: '已复制，粘贴给 agent 即可。',
    fallback: '无法自动复制，请在弹出的窗口里复制。',
    copyFailed: '复制失败，请选中上面的指令手动复制。',
    tooLong: '指令太长，无法自动复制，请选中上面的指令手动复制。',
    accessOff: 'agent 需要通过 Job Board MCP 才能读取这些岗位（见 docs/mcp.md）。',
    override: '我已用其他方式让 agent 能读取岗位',
    saveFailed: '这个设置没能保存。{err}',
    helpSummary: '关于 agent 技能',
    helpBody: 'agent 会按 jobs-compare 和 jobs-write 两个技能工作。它们在 plugins/workspace/skills/ 里，需要先安装到你的 agent 中——见插件 README。',
    iCompare: '用 jobs-compare 技能对比这些岗位：{ids}',
    iFit: '用 jobs-compare 技能评估这个岗位的匹配度：{ids}',
    iWrite: '用 jobs-write 技能为这些岗位写 CV 和求职信：{ids}',
    idSep: '、',
    pageTitle: '岗位对比',
    openInList: '打开岗位', openInListAria: '打开「{x}」',
    rSalary: '薪资', rLocation: '地点', rWorkType: '工作类型', rListed: '发布日期',
    rQualification: '可投性', rEvidence: '可投性依据', rSkills: '技能',
    rLabels: '你的标注', rJd: '岗位描述',
    notStated: '未注明', noLabels: '—', noDescription: '没有描述。',
    qMachine: '{v} · 分析结果', qManual: '{v} · 人工纠正', qUnknown: '分析结果未知',
    uniqueLegend: '标 + 的技能只出现在该岗位。', uniqueSr: '（仅此岗位）',
    hiddenThird: '小屏幕上隐藏了第 3 个岗位，换宽一点的窗口可看全部 3 个。',
    tooFew: '在工作区里至少选 2 个岗位才能并排对比。',
    backList: '返回列表',
    missingNote: '「{x}」已不在结果中，未显示。',
    loadingPage: '正在加载岗位…',
  },
};

let lang = 'en', channel = null, serial = 0, context = null, handlers = {}, waitTimer = null;
const pending = new Map();
const MAX_ACTIVE = 8, TIMEOUT_MS = 15000;   // host times out at 14 s; ours is a backstop

const t = (key, args = {}) =>
  String(words[lang][key] ?? words.en[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => String(args[k] ?? ''));

const fail = code => Object.assign(new Error(code), {code});

function request(operation, payload = {}) {
  if (!channel) return Promise.reject(fail('not_ready'));
  if (pending.size >= MAX_ACTIVE) return Promise.reject(fail('page_busy'));
  const id = String(++serial);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(fail('page_timeout')); }, TIMEOUT_MS);
    pending.set(id, {resolve, reject, timer});
    parent.postMessage({type: 'jobs-plugin-request', channel, id, operation, payload}, '*');
  });
}

function errorText(e) {
  const code = String(e?.code || e?.message || '').toLowerCase();
  if (/disabl|permission|grant|revok/.test(code)) return t('errDisabled');
  if (code.includes('busy')) return t('errBusy');
  if (code.includes('timeout')) return t('errTimeout');
  if (code.includes('too_large')) return t('errTooLarge');
  return t('errGeneric');
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'dark' || theme === 'light') root.dataset.theme = theme;
  else delete root.dataset.theme;
}

/* el('button', {class: 'x', 'aria-label': '…', onclick: fn}, 'text', child…)
   Strings always become text nodes; there is no HTML path. */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = String(v);
    else if (k in node && typeof v !== 'string') node[k] = v;
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const str = v => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '').trim();
const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);

/* Stored list from the host: [{uid, title, company, revision, added_at}], max 10. */
function cleanHanded(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set(), out = [];
  for (const h of list) {
    const uid = isObj(h) ? str(h.uid) : '';
    if (!uid || uid.length > 512 || seen.has(uid)) continue;
    seen.add(uid);
    out.push({uid, title: str(h.title), company: str(h.company), revision: str(h.revision), added_at: str(h.added_at)});
    if (out.length >= 10) break;
  }
  return out;
}

/* handed_jobs result → Map uid → record | {uid, missing:true} */
function recordsFrom(result) {
  const map = new Map();
  const items = isObj(result) && Array.isArray(result.items) ? result.items : null;
  if (!items) throw fail('invalid_response');
  for (const r of items) {
    if (!isObj(r) || !str(r.uid)) continue;
    map.set(str(r.uid), r.missing === true ? {uid: str(r.uid), missing: true} : r);
  }
  return map;
}

function labelChips(labels) {
  const out = [];
  if (!isObj(labels)) return out;
  const e = {eligible: 'vEligible', ineligible: 'vIneligible', unsure: 'vUnsure'}[labels.eligibility];
  const a = {saved: 'vSaved', applied: 'vApplied', skipped: 'vSkipped'}[labels.action];
  if (e) out.push({text: t(e), kind: labels.eligibility === 'ineligible' ? 'bad' : labels.eligibility === 'eligible' ? 'good' : ''});
  if (a) out.push({text: t(a), kind: 'accent'});
  if (labels.priority === true || labels.priority === 1) out.push({text: t('vPriority'), kind: 'cue'});
  return out;
}

window.addEventListener('message', e => {
  if (e.source !== parent || !e.data || typeof e.data !== 'object') return;
  const m = e.data;
  if (m.type === 'jobs-plugin-context') {
    if (channel || typeof m.channel !== 'string' || !m.channel || m.channel.length > 256
        || !isObj(m.context) || m.context.plugin_id !== 'workspace') return;
    channel = m.channel; context = m.context;
    clearTimeout(waitTimer);
    lang = context.lang === 'zh' ? 'zh' : 'en';
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    applyTheme(context.theme);
    document.getElementById('waiting')?.setAttribute('hidden', '');
    handlers.context?.(context);
    return;
  }
  if (!channel || m.channel !== channel) return;
  if (m.type === 'jobs-plugin-response') {
    if (typeof m.id !== 'string' || !pending.has(m.id) || typeof m.ok !== 'boolean') return;
    const p = pending.get(m.id); pending.delete(m.id); clearTimeout(p.timer);
    if (m.ok) p.resolve(m.result);
    else p.reject(Object.assign(new Error(String(m.error?.message || 'error')), {code: String(m.error?.code || 'error')}));
  } else if (m.type === 'jobs-plugin-theme' && (m.theme === 'dark' || m.theme === 'light')) {
    applyTheme(m.theme);
  } else if (m.type === 'jobs-plugin-handed') {
    handlers.handed?.(cleanHanded(m.handed));
  } else if (m.type === 'jobs-plugin-current') {
    handlers.current?.(isObj(m.current) ? m.current : null);
  }
});
window.addEventListener('pagehide', () => {
  for (const p of pending.values()) clearTimeout(p.timer);
  pending.clear();
});

function start(h) {
  handlers = h || {};
  // A frame that never gets context stays blank except for one neutral, bilingual line.
  waitTimer = setTimeout(() => {
    const w = document.getElementById('waiting');
    if (w && !channel) { w.textContent = `${words.en.waiting} / ${words.zh.waiting}`; w.hidden = false; }
  }, 6000);
  parent.postMessage({type: 'jobs-plugin-ready', version: 1}, '*');
}

window.WS = {
  start, request, t, el, str, isObj, errorText, cleanHanded, recordsFrom, labelChips,
  get lang() { return lang; },
  get context() { return context; },
};
})();
