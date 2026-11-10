// Offline frame test for the workspace plugin (sidebar + compare page).
// A mock host page implements bridge protocol v1 around the sandboxed frame.
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve, extname} from 'node:path';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); }
catch { playwright = require('/opt/homebrew/lib/node_modules/@playwright/cli/node_modules/playwright'); }
const root = resolve('plugins/workspace/web');

const harness = `<!doctype html><meta charset="utf-8"><iframe id="frame" sandbox="allow-scripts" style="width:340px;height:760px;border:0"></iframe><script>
const q=new URLSearchParams(location.search);
frame.style.width=q.get('w')||'340px';
frame.src='/plugins/workspace/frame?slot='+(q.get('slot')||'sidebar');
window.calls=[];window.readies=[];window.cfg=null;window.ready=false;window.mode={};window.held=[];
window.hostList=[];window.db={};window.saved=null;window.copyResult={copied:true,fallback:false};
const post=m=>frame.contentWindow.postMessage(m,'*');
window.send=(type,extra)=>post({type,channel:'tok',...extra});
window.respond=(m,ok,result)=>post({type:'jobs-plugin-response',channel:'tok',id:m.id,ok,...(ok?{result}:{error:{code:result,message:result}})});
function trySend(){if(ready&&cfg){post({type:'jobs-plugin-context',channel:'tok',context:{plugin_id:'workspace',slot:q.get('slot')==='page'?'page':'sidebar',lang:'en',theme:'light',agent_access:'on',current:null,state:null,...cfg,handed:hostList}});cfg=null;}}
window.start=c=>{hostList=c.handed||[];db=c.db||{};delete c.db;cfg=c;trySend();};
const DETAIL=['description','skills','tags','qualification','eligibility_evidence'];
function host(m){
  const p=m.payload||{};
  if(m.operation==='handed_jobs'){const items=p.uids.map(u=>{if(!db[u])return{uid:u,missing:true};const r={...db[u]};if(!p.detail)for(const k of DETAIL)delete r[k];return r;});respond(m,true,{plugin_id:'workspace',as_of:'now',generation:1,items});}
  else if(m.operation==='remove_handed'){hostList=hostList.filter(h=>!p.uids.includes(h.uid));respond(m,true,{handed:hostList});setTimeout(()=>send('jobs-plugin-handed',{handed:hostList}),20);}
  else if(m.operation==='clear_handed'){hostList=[];respond(m,true,{handed:[]});setTimeout(()=>send('jobs-plugin-handed',{handed:[]}),20);}
  else if(m.operation==='save_state'){saved=p.state;respond(m,true,{ok:true});}
  else if(m.operation==='copy_text')respond(m,true,copyResult);
  else if(m.operation==='open_job')respond(m,true,{opened:true});
  else respond(m,true,{});
}
window.addEventListener('message',e=>{if(e.source!==frame.contentWindow)return;const m=e.data;
  if(m.type==='jobs-plugin-ready'){readies.push(m);ready=true;trySend();return;}
  if(m.type!=='jobs-plugin-request'||m.channel!=='tok')return;calls.push(m);
  const md=mode[m.operation];if(md==='hold'){held.push(m);return;}if(md){respond(m,false,md);return;}host(m);});
</script>`;

const types = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'};
let origin = '';
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); res.end(harness); return; }
    let rel = '';
    if (url.pathname === '/plugins/workspace/frame') {
      rel = url.searchParams.get('slot') === 'page' ? 'compare.html' : 'index.html';
      // Same shape as the host: only the plugin's own assets, inline styles, no inline scripts, no network.
      res.setHeader('Content-Security-Policy', `sandbox allow-scripts; default-src 'none'; script-src ${origin}/plugins/workspace/assets/; style-src ${origin}/plugins/workspace/assets/ 'unsafe-inline'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'`);
    } else if (url.pathname.startsWith('/plugins/workspace/assets/')) rel = url.pathname.slice('/plugins/workspace/assets/'.length);
    if (!rel || rel.includes('..')) throw Error();
    const file = resolve(root, rel);
    if (!file.startsWith(root + '/')) throw Error();
    res.setHeader('Content-Type', types[extname(file)] || 'application/octet-stream');
    res.end(await readFile(file));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
origin = `http://127.0.0.1:${server.address().port}`;

const HOSTILE = '<img src=x onerror="parent.pwned=1">Engineer';
const stored = (uid, title, company = 'Acme', revision = 'r1') => ({uid, title, company, revision, added_at: '2026-11-01T00:00:00Z'});
const rec = (uid, extra = {}) => ({uid, group_id: uid, title: 'Job ' + uid, company: 'Acme', location_raw: 'Melbourne VIC 3000',
  location_fact: null, salary: '$100k', work_type: 'Full time', listing_date: '2026-11-01T03:00:00Z', total_score: 5,
  priority: false, core_order: 0, panel_url: '/', revision: 'r1', human_labels: {eligibility: null, action: null, priority: false},
  description: 'Line one\nLine two', skills: ['Python', 'SQL'], tags: [],
  qualification: {analysis: 'eligible', effective: 'eligible', corrected: false}, eligibility_evidence: null, ...extra});

let browser;
const consoleErrors = [];
// WORKSPACE_SHOTS=<dir> saves screenshots for a visual check.
const shot = async (page, name) => { if (process.env.WORKSPACE_SHOTS) await page.screenshot({path: resolve(process.env.WORKSPACE_SHOTS, name + '.png')}); };
try {
  browser = await playwright.chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH || '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser', args: ['--no-sandbox']});
  async function open({slot = 'sidebar', width = 340, viewport = {width: 1200, height: 860}, screen = {width: 1440, height: 900}} = {}) {
    const context = await browser.newContext({viewport, screen, reducedMotion: 'reduce'});
    const page = await context.newPage();
    page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', e => consoleErrors.push(String(e)));
    page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('/favicon.ico')) consoleErrors.push('HTTP ' + r.status() + ' ' + r.url()); });
    await page.goto(`${origin}/?slot=${slot}&w=${width}px`);
    await page.waitForFunction(() => window.ready);
    return {page, context, frame: page.frameLocator('#frame')};
  }
  const ops = (page, op) => page.evaluate(op => calls.filter(c => c.operation === op).map(c => c.payload), op);
  const text = (frame, sel) => frame.locator(sel).textContent();

  /* ======================= Sidebar ======================= */
  {
    const {page, context, frame} = await open();
    const fr = page.frames().find(f => f.url().includes('/plugins/workspace/frame'));
    assert.deepEqual(await page.evaluate(() => readies), [{type: 'jobs-plugin-ready', version: 1}]);
    // Messages not from the parent, or for another plugin, are ignored.
    await fr.evaluate(() => window.postMessage({type: 'jobs-plugin-context', channel: 'evil', context: {plugin_id: 'workspace', lang: 'en', handed: []}}, '*'));
    await page.evaluate(() => frame.contentWindow.postMessage({type: 'jobs-plugin-context', channel: 'x', context: {plugin_id: 'map', lang: 'en'}}, '*'));
    await page.waitForTimeout(80);
    assert.ok(await frame.locator('#app').isHidden(), 'no context accepted from a non-parent source or for another plugin');
    assert.equal(await page.evaluate(() => calls.length), 0);

    const handed = [stored('seek:1', 'Stored one'), stored('seek:2', HOSTILE), stored('seek:3', 'Gone job', 'OldCo'), stored('seek:4', 'Changed', 'Acme', 'old')];
    const db = {'seek:1': rec('seek:1', {title: 'Data analyst', human_labels: {eligibility: 'eligible', action: 'applied', priority: true}}),
      'seek:2': rec('seek:2', {title: HOSTILE, company: '<b>Bold</b> Co', salary: '', location_raw: ''}),
      'seek:4': rec('seek:4', {title: 'Changed', revision: 'new'})};
    await page.evaluate(([handed, db]) => start({handed, db, current: {uid: 'seek:1', title: 'Data analyst', company: 'Acme'}}), [handed, db]);
    await frame.locator('#app').waitFor();
    await page.waitForFunction(() => calls.some(c => c.operation === 'handed_jobs'));
    await frame.locator('.item-meta .chip.warn').waitFor();
    assert.deepEqual((await ops(page, 'handed_jobs'))[0], {uids: ['seek:1', 'seek:2', 'seek:3', 'seek:4'], detail: false});
    assert.ok(await page.evaluate(() => calls.every((c, i) => c.id === String(i + 1))), 'monotonic decimal request ids');
    assert.equal(await text(frame, '#count'), '4/10');
    assert.deepEqual(await frame.locator('.item-title').allTextContents(), ['Data analyst', HOSTILE, 'Gone job', 'Changed']);
    assert.equal(await frame.locator('img').count(), 0, 'hostile title stays text');
    assert.equal(await frame.locator('b').count(), 0, 'hostile company stays text');
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    const item = n => frame.locator('.item').nth(n);
    assert.equal(await item(2).locator('.chip.warn').textContent(), 'No longer in results');
    assert.equal(await item(2).locator('button.item-title').count(), 0, 'missing job is not a link');
    assert.equal(await item(2).locator('.item-company').textContent(), 'OldCo', 'stored company kept for a missing job');
    assert.equal(await item(3).locator('.chip.cue').textContent(), 'Updated since added');
    assert.equal(await item(0).locator('.chip', {hasText: 'Updated since added'}).count(), 0, 'same revision: no update flag');
    assert.deepEqual(await item(0).locator('.chip').allTextContents(), ['Eligible', 'Applied', '★ Priority']);
    assert.equal(await item(0).locator('.item-meta > span').first().textContent(), 'Melbourne VIC 3000 · $100k');
    // Currently viewed job, then a live update to one that is not in the workspace.
    assert.equal(await text(frame, '#current-state'), 'In workspace');
    await shot(page, 'sidebar-list');
    await page.evaluate(() => send('jobs-plugin-current', {current: {uid: 'seek:99', title: 'Other <i>job</i>'}}));
    await page.waitForFunction(() => frame.contentDocument === null || true);
    await frame.locator('#current-state', {hasText: 'Not in workspace'}).waitFor();
    assert.equal(await text(frame, '#current-title'), 'Other <i>job</i>');
    await page.evaluate(() => frame.contentWindow.postMessage({type: 'jobs-plugin-current', channel: 'wrong', current: null}, '*'));
    await page.waitForTimeout(50);
    assert.ok(await frame.locator('#current').isVisible(), 'wrong channel ignored');
    // Theme: live event; wrong channel ignored.
    await page.evaluate(() => frame.contentWindow.postMessage({type: 'jobs-plugin-theme', channel: 'wrong', theme: 'dark'}, '*'));
    await page.evaluate(() => send('jobs-plugin-theme', {theme: 'dark'}));
    await frame.locator('html[data-theme=dark]').waitFor();
    assert.equal(await page.evaluate(() => calls.filter(c => c.operation === 'handed_jobs').length), 1);
    // Open a job by its title.
    await item(0).locator('button.item-title').click();
    await page.waitForFunction(() => calls.some(c => c.operation === 'open_job'));
    assert.deepEqual((await ops(page, 'open_job'))[0], {uid: 'seek:1'});
    // Open failure is reported.
    await page.evaluate(() => { mode.open_job = 'page_busy'; });
    await item(0).locator('button.item-title').click();
    await frame.getByText('This job couldn’t be opened. The board is busy. Try again in a moment.').waitFor();
    await page.evaluate(() => { mode = {}; });

    // Compare: 3 present of 4 → choose view, default first 3 (wide screen).
    assert.ok(await frame.locator('#compare').isEnabled());
    await frame.locator('#compare').click();
    assert.equal(await frame.locator('#choose-title').evaluate(n => n === document.activeElement), true, 'focus moves to the panel title');
    const picks = frame.locator('#choose-list input');
    assert.equal(await picks.count(), 3, 'missing jobs cannot be compared');
    assert.deepEqual(await Promise.all([0, 1, 2].map(i => picks.nth(i).isChecked())), [true, true, true]);
    await picks.nth(1).uncheck();
    assert.ok(await frame.locator('#open-compare').isEnabled());
    await picks.nth(0).uncheck();
    assert.ok(await frame.locator('#open-compare').isDisabled(), 'needs 2');
    await picks.nth(1).check(); await picks.nth(0).check();
    await frame.locator('#open-compare').click();
    await page.waitForFunction(() => calls.some(c => c.operation === 'open_page'));
    assert.deepEqual(await page.evaluate(() => saved), {compare: ['seek:1', 'seek:2', 'seek:4'], v: 1});
    const order = await page.evaluate(() => calls.map(c => c.operation).slice(-2));
    assert.deepEqual(order, ['save_state', 'open_page'], 'selection saved before opening');
    // Escape returns to the list with focus on the Compare button.
    await frame.locator('#choose-title').press('Escape');
    assert.ok(await frame.locator('#list-view').isVisible());
    assert.equal(await frame.locator('#compare').evaluate(n => n === document.activeElement), true);

    // Hand to agent (access on).
    assert.ok(await frame.locator('#access').isHidden());
    await frame.locator('#agent').click();
    assert.equal(await text(frame, '#status'), '', 'old messages do not follow into another view');
    const instr = () => frame.locator('#instruction').inputValue();
    assert.equal(await instr(), 'Use the jobs-compare skill to compare these jobs: seek:1, seek:2, seek:4');
    await shot(page, 'sidebar-agent');
    await frame.locator('input[name=template][value=fit]').check();
    assert.equal(await instr(), 'Use the jobs-compare skill to check my fit for this job: seek:1');
    await frame.locator('input[name=fit-job]').nth(2).check();
    assert.equal(await instr(), 'Use the jobs-compare skill to check my fit for this job: seek:4');
    await frame.locator('input[name=template][value=write]').check();
    assert.equal(await instr(), 'Use the jobs-write skill to draft a CV and cover letter for these jobs: seek:1, seek:2, seek:4');
    await frame.locator('#agent-jobs input').nth(1).uncheck();
    assert.equal(await instr(), 'Use the jobs-write skill to draft a CV and cover letter for these jobs: seek:1, seek:4');
    await frame.locator('input[name=template][value=compare]').check();
    await frame.locator('#agent-jobs input').nth(0).uncheck();
    assert.equal(await instr(), '');
    assert.ok(await frame.locator('#copy').isDisabled());
    assert.equal(await text(frame, '#agent-hint'), 'Choose at least 2 jobs to compare.');
    await frame.locator('#agent-jobs input').nth(0).check();
    // Copy results: copied / manual-copy window / failed.
    await frame.locator('#copy').click();
    await frame.locator('#agent-hint', {hasText: 'Copied. Paste it into your agent.'}).waitFor();
    assert.deepEqual((await ops(page, 'copy_text')).at(-1), {text: 'Use the jobs-compare skill to compare these jobs: seek:1, seek:4'});
    await page.evaluate(() => { copyResult = {copied: false, fallback: true}; });
    await frame.locator('#copy').click();
    await frame.locator('#agent-hint', {hasText: 'Couldn’t copy automatically. Copy the text from the window that opened.'}).waitFor();
    await page.evaluate(() => { copyResult = {copied: false, fallback: false}; });
    await frame.locator('#copy').click();
    await frame.locator('#agent-hint', {hasText: 'Couldn’t copy. Select the instruction above and copy it yourself.'}).waitFor();
    assert.equal(await frame.locator('#instruction').evaluate(n => n === document.activeElement), true, 'failed copy selects the text');
    await page.evaluate(() => { mode.copy_text = 'page_timeout'; });
    await frame.locator('#copy').click();
    await frame.locator('#agent-hint', {hasText: 'Couldn’t copy. Select'}).waitFor();
    await page.evaluate(() => { mode = {}; });
    await frame.locator('#agent-back').click();

    // Remove: request, re-render from the returned list, then the host event refreshes.
    const before = (await ops(page, 'handed_jobs')).length;
    await item(1).locator('.remove').click();
    await page.waitForFunction(() => calls.some(c => c.operation === 'remove_handed'));
    assert.deepEqual((await ops(page, 'remove_handed'))[0], {uids: ['seek:2']});
    await frame.getByText('Removed from the workspace.').waitFor();
    assert.deepEqual(await frame.locator('.item-title').allTextContents(), ['Data analyst', 'Gone job', 'Changed']);
    assert.equal(await text(frame, '#count'), '3/10');
    assert.equal(await frame.locator('.item').nth(1).locator('.remove').evaluate(n => n === document.activeElement), true, 'focus stays in the list');
    await page.waitForFunction(n => calls.filter(c => c.operation === 'handed_jobs').length > n, before);
    assert.deepEqual((await ops(page, 'handed_jobs')).at(-1), {uids: ['seek:1', 'seek:3', 'seek:4'], detail: false});
    await page.waitForTimeout(60);
    assert.equal(await text(frame, '#status'), 'Removed from the workspace.', 'the follow-up refresh keeps the confirmation');
    // Remove failure keeps the list.
    await page.evaluate(() => { mode.remove_handed = 'plugin_disabled_or_permission_required'; });
    await frame.locator('.item').nth(0).locator('.remove').click();
    await frame.getByText('The workspace is turned off or needs permission again.').waitFor();
    assert.equal(await frame.locator('.item').count(), 3);
    await page.evaluate(() => { mode = {}; });
    // Clear all: in-page confirm, cancel, then confirm.
    await frame.locator('#clear').click();
    assert.equal(await text(frame, '#confirm-text'), 'Remove all 3 jobs from the workspace?');
    await frame.locator('#confirm-no').press('Escape');
    assert.ok(await frame.locator('#confirm').isHidden());
    assert.equal(await frame.locator('#clear').evaluate(n => n === document.activeElement), true);
    assert.equal((await ops(page, 'clear_handed')).length, 0);
    await frame.locator('#clear').click();
    await frame.locator('#confirm-yes').click();
    await frame.locator('#empty').waitFor();
    assert.deepEqual((await ops(page, 'clear_handed'))[0], {});
    await page.waitForTimeout(60);
    assert.equal(await text(frame, '#status'), 'Workspace cleared.');
    assert.equal(await text(frame, '#count'), '0/10');
    assert.deepEqual(await frame.locator('#empty li').allTextContents(),
      ['Drag a job card onto this panel', 'Open a job and choose “Add to workspace”', 'Select several jobs in the list and add them together']);
    assert.ok(await frame.locator('#compare').isDisabled());
    assert.ok(await frame.locator('#agent').isDisabled());
    assert.ok(await frame.locator('#clear').isHidden());
    await shot(page, 'sidebar-empty');
    // A handed event re-renders and refreshes; wrong channel is ignored.
    await page.evaluate(() => frame.contentWindow.postMessage({type: 'jobs-plugin-handed', channel: 'wrong', handed: [{uid: 'seek:1', title: 'X'}]}, '*'));
    await page.waitForTimeout(50);
    assert.equal(await frame.locator('.item').count(), 0);
    await page.evaluate(() => { hostList = [{uid: 'seek:1', title: 'Back again', company: 'Acme', revision: 'r1'}]; send('jobs-plugin-handed', {handed: hostList}); });
    await frame.locator('.item-title', {hasText: 'Data analyst'}).waitFor();
    assert.ok(await frame.locator('#compare').isDisabled(), 'one job cannot be compared');
    assert.equal(await text(frame, '#compare-hint'), 'Add 2 or more jobs to compare them.');
    await context.close();
  }

  /* ---- read errors keep stored titles; retry; narrow screen limit; agent access off; zh ---- */
  {
    const {page, context, frame} = await open({viewport: {width: 600, height: 860}, screen: {width: 600, height: 900}});
    const handed = [stored('seek:1', 'Saved one'), stored('seek:2', 'Saved two'), stored('seek:3', 'Saved three')];
    await page.evaluate(h => { mode.handed_jobs = 'page_timeout'; start({handed: h, db: {}, agent_access: 'off', theme: 'dark', compare_columns: 2}); }, handed);
    await frame.getByText('This took too long. Try again. Showing the saved titles.').waitFor();
    assert.equal(await frame.locator('html').getAttribute('data-theme'), 'dark');
    assert.deepEqual(await frame.locator('.item-title').allTextContents(), ['Saved one', 'Saved two', 'Saved three']);
    assert.ok(await frame.locator('#compare').isEnabled(), 'unknown is not treated as missing');
    await page.evaluate(() => { mode = {}; db = {'seek:1': {uid: 'seek:1', title: 'Fresh one', revision: 'r1'}, 'seek:2': {uid: 'seek:2'}, 'seek:3': {uid: 'seek:3', revision: 'r1'}}; });
    await frame.getByRole('button', {name: 'Try again'}).click();
    await frame.locator('.item-title', {hasText: 'Fresh one'}).waitFor();
    assert.equal(await frame.locator('#status').textContent(), '');
    assert.deepEqual(await frame.locator('.item-title').allTextContents(), ['Fresh one', 'Saved two', 'Saved three'], 'sparse records fall back to stored text');
    // Narrow (host hint): at most 2 can be picked.
    await frame.locator('#compare').click();
    assert.equal(await text(frame, '#choose-note'), 'Pick 2.');
    const picks = frame.locator('#choose-list input');
    assert.deepEqual([await picks.nth(0).isChecked(), await picks.nth(1).isChecked(), await picks.nth(2).isChecked()], [true, true, false]);
    assert.ok(await picks.nth(2).isDisabled());
    await frame.locator('#choose-back').click();
    // Agent access off: visible, disabled, explained; override persists and enables.
    assert.ok(await frame.locator('#agent').isVisible());
    assert.ok(await frame.locator('#agent').isDisabled());
    assert.equal(await text(frame, '#access-note'), 'An agent can only read these jobs through the Job Board MCP (see docs/mcp.md).');
    assert.equal(await frame.locator('#agent').getAttribute('aria-describedby'), 'access-note');
    await shot(page, 'sidebar-dark-access-off');
    await frame.getByLabel('I have set up agent access another way').check();
    assert.ok(await frame.locator('#agent').isEnabled());
    await page.waitForFunction(() => saved);
    assert.deepEqual(await page.evaluate(() => saved), {agent_override: true, v: 1});
    assert.equal(await frame.locator('details.help summary').textContent(), 'About the agent skills');
    await frame.locator('details.help summary').click();
    assert.match(await text(frame, '#help-body'), /plugins\/workspace\/skills\/.*plugin README/);
    // save_state failure is reported.
    await page.evaluate(() => { mode.save_state = 'page_busy'; });
    await frame.getByLabel('I have set up agent access another way').uncheck();
    await frame.getByText('This setting couldn’t be saved. The board is busy. Try again in a moment.').waitFor();
    await context.close();
  }
  {
    const {page, context, frame} = await open();
    const handed = [stored('seek:1', '一'), stored('seek:2', '二')];
    await page.evaluate(h => start({handed: h, db: {'seek:1': {uid: 'seek:1'}, 'seek:2': {uid: 'seek:2'}}, lang: 'zh', agent_access: 'off', state: {agent_override: true}}), handed);
    await frame.locator('#app').waitFor();
    assert.equal(await frame.locator('html').getAttribute('lang'), 'zh-CN');
    assert.ok(await frame.getByLabel('我已用其他方式让 agent 能读取岗位').isChecked(), 'override restored from saved state');
    assert.ok(await frame.locator('#agent').isEnabled());
    await frame.locator('#agent').click();
    const instr = () => frame.locator('#instruction').inputValue();
    assert.equal(await instr(), '用 jobs-compare 技能对比这些岗位：seek:1、seek:2');
    await frame.locator('input[name=template][value=fit]').check();
    assert.equal(await instr(), '用 jobs-compare 技能评估这个岗位的匹配度：seek:1');
    await frame.locator('input[name=template][value=write]').check();
    assert.equal(await instr(), '用 jobs-write 技能为这些岗位写 CV 和求职信：seek:1、seek:2');
    assert.equal(await text(frame, '#copy'), '复制指令');
    await context.close();
  }
  /* ---- a frame that never gets context stays blank apart from one neutral line ---- */
  {
    const {page, context, frame} = await open();
    await frame.locator('#waiting').waitFor({timeout: 9000});
    assert.ok(await frame.locator('#app').isHidden());
    assert.match(await text(frame, '#waiting'), /Workspace is not connected.*工作区未连接/);
    assert.equal(await page.evaluate(() => calls.length), 0);
    await context.close();
  }

  /* ======================= Compare page ======================= */
  const three = [stored('seek:1', 'One'), stored('seek:2', 'Two', 'Acme', 'old'), stored('seek:3', 'Three'), stored('seek:4', 'Four')];
  const pageDb = {
    'seek:1': rec('seek:1', {title: 'Analyst', skills: ['Python', 'SQL', 'Tableau'], description: 'First line\n\nThird <script>x</script>',
      eligibility_evidence: {rule_id: 'pr', rule_kind: 'cue', matched_text: 'PR', snippet: 'Must hold Australian PR', cue: '公民/PR'},
      human_labels: {eligibility: 'ineligible', action: 'saved', priority: false},
      qualification: {analysis: 'eligible', effective: 'ineligible', corrected: true}}),
    'seek:2': rec('seek:2', {title: HOSTILE, skills: ['python', 'Excel'], salary: null, work_type: undefined, listing_date: null, revision: 'new',
      qualification: null}),
    'seek:3': rec('seek:3', {title: 'Third', skills: ['SQL'], description: ''}),
  };
  {
    const {page, context, frame} = await open({slot: 'page', width: '1200', viewport: {width: 1240, height: 860}});
    await page.evaluate(([h, db]) => start({handed: h, db, slot: 'page', state: {compare: ['seek:1', 'seek:2', 'seek:3'], v: 1}}), [three, pageDb]);
    await frame.locator('.col').nth(2).waitFor();
    assert.deepEqual((await ops(page, 'handed_jobs'))[0], {uids: ['seek:1', 'seek:2', 'seek:3'], detail: true});
    assert.equal(await frame.locator('.col').count(), 3);
    await shot(page, 'compare-wide');
    assert.equal(await frame.locator('img').count(), 0);
    assert.equal(await frame.locator('.col h2').nth(1).textContent(), HOSTILE);
    assert.equal(await frame.locator('.jd script').count(), 0);
    assert.equal(await frame.locator('.jd').nth(0).textContent(), 'First line\n\nThird <script>x</script>');
    assert.equal(await frame.locator('.jd').nth(0).evaluate(n => getComputedStyle(n).whiteSpace), 'pre-wrap');
    assert.equal(await frame.locator('.jd').nth(2).textContent(), 'No description.');
    // Same rows, same order, in every column; missing values say so.
    const rowLabels = ['Salary', 'Location', 'Work type', 'Listed', 'Eligibility', 'Eligibility evidence', 'Skills', 'Your labels'];
    for (let i = 0; i < 3; i++) assert.deepEqual(await frame.locator('.col').nth(i).locator('dt').allTextContents(), rowLabels);
    const dd = (c, r) => frame.locator('.col').nth(c).locator('dd').nth(r).textContent();
    assert.equal(await dd(1, 0), 'Not stated'); assert.equal(await dd(1, 2), 'Not stated'); assert.equal(await dd(1, 3), 'Not stated');
    assert.equal(await dd(0, 3), '2026-11-01');
    assert.equal(await dd(0, 4), 'Ineligible · manual correction');
    assert.equal(await dd(2, 4), 'Eligible · analysis');
    assert.equal(await dd(1, 4), 'Analysis unknown');
    assert.equal(await dd(0, 5), 'Citizen / PRMust hold Australian PR');
    assert.equal(await dd(1, 5), '—');
    assert.equal(await dd(0, 7), 'IneligibleTo apply');
    assert.equal(await dd(1, 7), '—');
    // Unique skills (case-insensitive across the shown columns).
    const uniq = c => frame.locator('.col').nth(c).locator('.skills .unique').allTextContents();
    assert.deepEqual(await uniq(0), ['+ Tableau (only in this job)']);
    assert.deepEqual(await uniq(1), ['+ Excel (only in this job)']);
    assert.deepEqual(await uniq(2), []);
    // Rows line up across columns.
    const tops = await frame.locator('.srow[data-row="7"]').evaluateAll(ns => ns.map(n => Math.round(n.getBoundingClientRect().top)));
    assert.equal(new Set(tops).size, 1, 'label rows aligned: ' + tops);
    // Columns scroll independently and are keyboard-focusable; header is sticky.
    assert.equal(await frame.locator('.col').nth(0).evaluate(n => getComputedStyle(n).overflowY), 'auto');
    assert.equal(await frame.locator('.col').nth(0).getAttribute('tabindex'), '0');
    assert.equal(await frame.locator('.col-head').nth(0).evaluate(n => getComputedStyle(n).position), 'sticky');
    assert.equal(await frame.locator('.col').nth(1).locator('.chip.cue').textContent(), 'Updated since added');
    assert.equal(await frame.locator('.col').nth(0).locator('.col-head .chip.cue').count(), 0);
    assert.equal(await frame.locator('input,textarea,select,[contenteditable]').count(), 0, 'read-only');
    await frame.locator('.col').nth(2).getByRole('button', {name: 'Open “Third” in the list'}).click();
    await page.waitForFunction(() => calls.some(c => c.operation === 'open_job'));
    assert.deepEqual((await ops(page, 'open_job'))[0], {uid: 'seek:3'});
    await frame.locator('.col').nth(0).press('Escape');
    await page.waitForFunction(() => calls.some(c => c.operation === 'navigate_list'));
    assert.deepEqual((await ops(page, 'navigate_list'))[0], {}, 'Esc inside the frame returns to the list');
    // Narrow: first 2 stacked, the third hidden with a note; uniqueness among the shown two.
    await page.setViewportSize({width: 700, height: 860});
    await page.evaluate(() => { frame.style.width = '680px'; });
    await frame.locator('.notes li', {hasText: 'third job is hidden'}).waitFor();
    assert.equal(await frame.locator('.col').count(), 2);
    await shot(page, 'compare-narrow');
    assert.deepEqual(await uniq(0), ['+ SQL (only in this job)', '+ Tableau (only in this job)']);
    const [a, b] = await frame.locator('.col').evaluateAll(ns => ns.map(n => n.getBoundingClientRect()));
    assert.ok(b.top >= a.bottom, 'stacked blocks');
    assert.equal(await frame.locator('.jd').nth(0).getAttribute('tabindex'), '0', 'narrow: description scrolls on its own');
    assert.equal((await ops(page, 'handed_jobs')).length, 1, 'no refetch on resize');
    // A shown job removed elsewhere → reload with what is left.
    await page.evaluate(() => { hostList = hostList.filter(h => h.uid !== 'seek:3'); send('jobs-plugin-handed', {handed: hostList}); });
    await page.waitForFunction(() => calls.filter(c => c.operation === 'handed_jobs').length === 2);
    assert.deepEqual((await ops(page, 'handed_jobs'))[1], {uids: ['seek:1', 'seek:2'], detail: true});
    await frame.locator('.notes li').waitFor({state: 'detached'});
    await context.close();
  }
  {
    // Missing job leaves fewer than 2 → empty state with a way back.
    const {page, context, frame} = await open({slot: 'page', width: '1200', viewport: {width: 1240, height: 860}});
    await page.evaluate(([h, db]) => start({handed: h, db, slot: 'page', state: {compare: ['seek:1', 'seek:4']}}), [three, pageDb]);
    await frame.locator('#empty').waitFor();
    assert.equal(await text(frame, '#empty-text'), 'Choose at least 2 jobs in the workspace to compare them side by side.');
    assert.equal(await frame.locator('.notes li').textContent(), '“Four” is no longer in results, so it isn’t shown.');
    await frame.getByRole('button', {name: 'Back to list'}).click();
    await page.waitForFunction(() => calls.some(c => c.operation === 'navigate_list'));
    assert.deepEqual((await ops(page, 'navigate_list'))[0], {});
    await context.close();
  }
  {
    // No saved selection → first three in the workspace; errors with retry; zh labels.
    const {page, context, frame} = await open({slot: 'page', width: '1200', viewport: {width: 1240, height: 860}});
    await page.evaluate(([h, db]) => { mode.handed_jobs = 'plugin_disabled_or_permission_required'; start({handed: h, db, slot: 'page', lang: 'zh'}); }, [three, pageDb]);
    await frame.getByText('工作区已关闭或需要重新授权。').waitFor();
    assert.deepEqual((await ops(page, 'handed_jobs'))[0], {uids: ['seek:1', 'seek:2', 'seek:3'], detail: true});
    assert.equal(await frame.locator('.col').count(), 0);
    assert.ok(await frame.locator('#empty').isHidden(), 'an error is not shown as "too few"');
    await page.evaluate(() => { mode = {}; });
    await frame.getByRole('button', {name: '重试'}).click();
    await frame.locator('.col').nth(2).waitFor();
    assert.deepEqual(await frame.locator('.col').nth(0).locator('dt').allTextContents(), ['薪资', '地点', '工作类型', '发布日期', '可投性', '可投性依据', '技能', '你的标注']);
    assert.equal(await frame.locator('.col').nth(0).locator('dd').nth(5).textContent(), '公民/PRMust hold Australian PR');
    // Only one workspace job → empty state without a request.
    await context.close();
  }
  {
    const {page, context, frame} = await open({slot: 'page', width: '1200', viewport: {width: 1240, height: 860}});
    await page.evaluate(() => start({handed: [{uid: 'seek:1', title: 'Solo'}], db: {}, slot: 'page'}));
    await frame.locator('#empty').waitFor();
    assert.equal((await ops(page, 'handed_jobs')).length, 0);
    await context.close();
  }
  const cspErrors = consoleErrors.filter(x => /Content Security Policy|Refused/.test(x));
  assert.deepEqual(cspErrors, [], 'no CSP violations');
  assert.deepEqual(consoleErrors.filter(x => !x.startsWith('Failed to load resource')), [], 'no console errors or failed requests');
  console.log('PASS: workspace sidebar (handshake, source/channel/plugin filtering, monotonic ids, missing/updated/labels, hostile text, current job, theme, open/remove/clear with confirm and focus, compare selection limits and save order, agent instructions en/zh for all templates, copy copied/fallback/failed, agent access off + persisted override, read errors keep stored titles + retry, no-context blank) and compare page (detail fetch, 3 columns aligned rows, not-stated values, unique skills, updated flag, read-only, open in list, narrow 2 stacked + hidden-third note, reload on removal, missing → empty state + back, error + retry, zh)');
} finally {
  await browser?.close();
  await new Promise(r => server.close(r));
}
