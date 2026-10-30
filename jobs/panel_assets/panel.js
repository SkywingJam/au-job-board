const LABELS = window.__LABELS__ || {};
function elementLocation(){return '';}
function detailScope(){return document.querySelector('#detail [data-label-scope]');}
function scopeLabel(uid){return LABELS[uid]||{};}
function adoptScopeLabel(uid,_id,field,value,snapshot={}){
  // A response is a snapshot taken by one field's write, not a fresh read of
  // concurrent note/status writes. Adopt only the field it acknowledges.
  const label={...scopeLabel(uid),[field]:value};
  const metadata=field==='action' ? ['action_at','applied_at'] : field==='eligibility'
    ? ['ineligible_reason','eligibility_source','eligibility_at','eligibility_analysis'] : [];
  metadata.forEach(key=>{
    if(Object.prototype.hasOwnProperty.call(snapshot,key))label[key]=snapshot[key];
  });
  LABELS[uid]=label;
  return label;
}
// Server-rendered detail is a fresh read of the whole group label.
function replaceScopeLabel(uid,label){LABELS[uid]=label||{};}
function inScope(){return true;}
/* 界面文案：服务端按当前语言注入（i18n.js_strings）。数据（备注、技能名）不经过这里。 */
const I18N = window.__I18N__ || {};
function T(key, vars){
  let s = I18N[key] || key;
  if(vars) for(const k in vars) s = s.split('{'+k+'}').join(vars[k]);
  return s;
}
/* ---- 动效 ---------------------------------------------------------------
   原则：动效只用来交代「东西从哪来、到哪去」，不拖慢操作 —— 时长 120–280ms，
   只动 opacity / transform（不触发重排）。出现：先去掉 hidden 再播入场；
   消失：先播离场，播完才设 hidden（否则 display:none 会把动画直接截掉）。
   系统「减少动态效果」打开时，CSS 把时长压到接近 0，这里也直接跳过等待。 */
const REDUCED = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : {matches:false};
function shown(el){ return !!el && !el.hidden && !el.classList.contains('leaving'); }
function cancelMotion(el){
  if(el && el._motionCancel) el._motionCancel();
}
function reveal(el){
  if(!el) return;
  cancelMotion(el);
  el.hidden=false;
  el.classList.remove('leaving','entering');
  if(REDUCED.matches) return;
  let timer;
  const cleanup=()=>{
    clearTimeout(timer); el.removeEventListener('animationend',onEnd);
    el.classList.remove('entering'); el._motionCancel=null;
  };
  const onEnd=ev=>{ if(ev.target===el) cleanup(); };
  el._motionCancel=cleanup;
  void el.offsetWidth;
  el.classList.add('entering');
  el.addEventListener('animationend',onEnd);
  timer=setTimeout(cleanup,360);
}
function conceal(el, done){
  if(!el || el.hidden){ if(done) done(); return; }
  cancelMotion(el);
  el.classList.remove('entering');
  let timer, finished=false;
  const cleanup=()=>{
    clearTimeout(timer); el.removeEventListener('animationend',onEnd);
    el.classList.remove('leaving'); el._motionCancel=null;
  };
  const fin=()=>{
    if(finished) return;
    finished=true; cleanup(); el.hidden=true; if(done) done();
  };
  const onEnd=ev=>{ if(ev.target===el) fin(); };
  el._motionCancel=()=>{ finished=true; cleanup(); };
  if(REDUCED.matches){ fin(); return; }
  el.classList.add('leaving');
  el.addEventListener('animationend',onEnd);
  timer=setTimeout(fin,360);
}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');
 clearTimeout(window._tt);window._tt=setTimeout(()=>t.classList.remove('show'),1400);}
/* 处理状态：想投 / 已投 / 跳过 才算处理；星标独立。卡片、详情状态行与竖屏底栏
   读取整组 UID 的合并状态，与服务端 _card_state / _state_text 同口径。 */
const TONE={eligible:'good',ineligible:'bad',saved:'want',applied:'good',skipped:'mid'};
function stateText(s){
  let text=s.action ? T('state.done',{status:T('value.action.'+s.action)}) : T('state.open');
  if(s.priority) text+=' · '+T('state.star');
  return text;
}
function refreshDot(uid,id=''){
  const s=scopeLabel(uid,id);
  document.querySelectorAll('.prog[data-prog-uid="'+uid+'"]').forEach(el=>{
    if(!inScope(el,id))return;
    const v=s.action;
    el.hidden=!v;
    el.className='prog'+(v ? ' '+(TONE[v]||'mid') : '');
    el.textContent=v ? T('value.action.'+v) : '';
    el.title=v ? T('field.action')+T('js.colon')+T('value.action.'+v) : '';
  });
  document.querySelectorAll('.card-star[data-star-uid="'+uid+'"]').forEach(el=>{ if(inScope(el,id))el.hidden=!s.priority; });
  document.querySelectorAll('[data-state-uid="'+uid+'"]').forEach(el=>{ if(inScope(el,id))el.textContent=stateText(s); });
  if(!document.querySelector('#detail [data-uid="'+uid+'"]')) return;
  document.querySelectorAll('#detail .label-sum').forEach(el=>{ if(inScope(el,id))el.textContent=stateText(s); });
}
/* 资格纠错摘要：当前有效结果 · 来源（服务端 _qualification_summary 同口径） */
function qualificationSummary(q){
  if(!q.effective) return T('qualification.sum_unknown');
  return T(q.corrected?'qualification.sum_manual':'qualification.sum_machine',
           {value:T('value.eligibility.'+q.effective)});
}
function qualificationSourceKey(q){
  if(q.corrected) return 'qualification.manual';
  if(q.analysis==='ineligible') return 'qualification.machine_excluded';
  return q.analysis ? 'qualification.machine' : 'qualification.unknown';
}
/* 写入失败：服务端的 write_failed / invalid_request 是代码，不直接把 JSON 摆给人看；
   其他文本（例如校验说明）照原样附在后面。状态保持原样，可以直接重试。 */
function labelFailure(e){
  let msg=String(e && e.message || '');
  try{ const d=JSON.parse(msg); if(d && d.error) msg=T('js.label_retry'); }catch(_){}
  return T('js.label_failed')+msg;
}
function labelToast(field,data){
  if(field==='action') return data.value ? T('js.action_set',{status:T('value.action.'+data.value)}) : T('js.action_cleared');
  if(field==='priority') return T(data.value ? 'js.star_on' : 'js.star_off');
  if(field==='eligibility'){
    if(data.value==='ineligible') return T('js.miss_hint');
    return data.value ? T('js.correction_eligible') : T('js.correction_cleared');
  }
  return data.value==null ? T('js.unlabelled') : T('js.labelled')+' · '+T('field.'+field);
}
/* 卡片上的「人工：可投/不可投」只提示与分析结果相反的人工纠正（服务端 _correction_tag 同口径） */
function refreshCorrectionTag(uid,q,id=''){
  document.querySelectorAll('.card[data-uid="'+uid+'"] .tags').forEach(tags=>{
    if(!inScope(tags,id))return;
    let tag=tags.querySelector('.tag.corr');
    if(!q || !q.corrected || !q.analysis || q.effective===q.analysis){ if(tag) tag.remove(); return; }
    if(!tag){
      tag=document.createElement('span'); tag.className='tag corr';
      const before=tags.querySelector('.tag.salary,.tag.raw,.skills,.card-state');
      tags.insertBefore(tag,before||null);
    }
    tag.textContent=T('card.corrected',{value:T('value.eligibility.'+q.effective)});
    tag.title=T('qualification.manual');
  });
}
function detailHash(uid){
  return '#'+encodeURIComponent(uid);
}
function readDetailHash(){
  const raw=location.hash.slice(1), cut=raw.indexOf('&location_id=');
  try{return {uid:decodeURIComponent(cut<0?raw:raw.slice(0,cut))};}catch(_){return {uid:''};}
}
async function openJob(uid){
  if(OPEN_UID && (OPEN_UID!==uid || NOTE_REQUESTS.size || LABEL_WRITES.size || hasDetailDraft())){
    if(!await allowDetailNavigation()){
      history.replaceState(null,"",location.pathname+location.search+detailHash(OPEN_UID));
      return false;
    }
    if(UPDATE_PENDING){applyLatestResults(detailHash(uid));return false;}
  }
  OPEN_UID=uid;
  document.querySelectorAll('.card').forEach(c=>c.classList.toggle('sel',c.dataset.uid===uid));
  history.replaceState(null,'',detailHash(uid));
  const pane=document.getElementById('detail');
  const seq=(OPEN_SEQ+=1);
  const mobileOpening=document.body.classList.contains('portrait') &&
    (!pane.classList.contains('open') || pane.classList.contains('leaving'));
  // 竖屏重新入场前清掉上一条详情；已显示的详情仍等 160ms 才显示骨架屏。
  const slow=mobileOpening ? null : setTimeout(()=>{ if(seq===OPEN_SEQ) showSkeleton(pane); },160);
  if(mobileOpening){
    showSkeleton(pane);
    pane.scrollTop=0;
    pane.classList.remove('label-open');
    pane.classList.add('open'); pane.classList.remove('leaving');
    reveal(pane);                                   // 竖屏：详情从右侧滑入
  }
  try{
    const params=new URLSearchParams({uid});
    const r=await fetch('/api/job?'+params);
    if(!r.ok) throw new Error(await r.text());
    const d=await r.json();
    if(seq!==OPEN_SEQ) return;                      // 连点时只认最后一次
    clearTimeout(slow);
    pane.innerHTML=d.html;
    const scope=detailScope();
    if(scope){
      try{replaceScopeLabel(uid,JSON.parse(scope.dataset.scopeLabel));}catch(_){}
      history.replaceState(null,'',detailHash(uid));
    }
    pane.querySelectorAll('.note input').forEach(noteSync);
    pane.classList.remove('label-open');            // 标注抽屉属于上一条，换条即收起
    pane.classList.add('open');
    pane.scrollTop=0;
    syncDetailNav();
    swapIn(pane);
    return true;
  }catch(e){
    clearTimeout(slow);
    if(seq!==OPEN_SEQ) return;
    pane.innerHTML='';
    const d=document.createElement('div'); d.className='placeholder';
    d.textContent=T('js.load_failed')+e.message; pane.appendChild(d);
    return false;
  }
}
let OPEN_SEQ=0, OPEN_UID=null;
const NOTE_REQUESTS=new Set();
const NOTE_WRITES=new Map();
const LABEL_WRITES=new Map();
function showSkeleton(pane){
  pane.innerHTML='<div class="skeleton" aria-busy="true" aria-label="'+T('js.loading')+'">'
    +'<i class="sk-t"></i><i class="sk-m"></i><i class="sk-chips"></i><i class="sk-box"></i>'
    +'<i class="sk-l"></i><i class="sk-l"></i><i class="sk-l short"></i></div>';
}
/* 详情换内容：各块依次轻微上浮淡入（只在桌面右栏；竖屏已经有整屏滑入） */
function swapIn(pane){
  if(REDUCED.matches) return;
  pane.classList.remove('swap'); void pane.offsetWidth; pane.classList.add('swap');
}
async function closeDetail(){
  if(!await allowDetailNavigation()){
    if(OPEN_UID)history.replaceState(null,"",location.pathname+location.search+detailHash(OPEN_UID));
    return;
  }
  OPEN_UID=null;
  history.replaceState(null,"",location.pathname+location.search);
  if(UPDATE_PENDING){applyLatestResults("");return;}
  OPEN_SEQ+=1; // Ignore pending responses and skeleton timers after closing.
  const pane=document.getElementById('detail');
  pane.classList.remove('label-open');
  if(!document.body.classList.contains('portrait') || REDUCED.matches){ pane.classList.remove('open'); return; }
  conceal(pane, ()=>{ pane.hidden=false; pane.classList.remove('open'); });
}
function labelButtons(uid,id){
  return Array.from(document.querySelectorAll('[data-uid="'+uid+'"][data-field]')).filter(b=>id===undefined || inScope(b,id));
}
async function setLabel(uid,field,value,btn){
  if(LABEL_WRITES.has(uid)) return LABEL_WRITES.get(uid);
  const id=elementLocation(btn);
  const wasOn=btn.classList.contains('on');
  const correction=Array.from(document.querySelectorAll('[data-qualification-uid="'+uid+'"]')).find(el=>inScope(el,id));
  if(field==='eligibility' && wasOn && correction && correction.dataset.corrected!=='true') return;
  const target=wasOn ? null : (field==='priority' ? true : value);
  labelButtons(uid).forEach(b=>{b.disabled=true;b.setAttribute('aria-busy','true');});
  // Publish the lock before any await, including a pending note save.
  let release;
  const locked=new Promise(resolve=>{release=resolve;});
  LABEL_WRITES.set(uid,locked);
  let saved=false;
  try{
    if(NOTE_WRITES.has(uid)) await NOTE_WRITES.get(uid);
    const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({uid:uid,field:field,value:target})});
    if(!r.ok) throw new Error(await r.text());
    const data=await r.json();
    const savedLabel=adoptScopeLabel(uid,id,field,data.value,data.label||{});
    if(data.qualification && correction){
      correction.dataset.analysis=data.qualification.analysis||'';
      correction.dataset.corrected=String(data.qualification.corrected);
      const source=correction.querySelector('[data-qualification-source]');
      if(source)source.textContent=T(qualificationSourceKey(data.qualification));
      const sum=correction.querySelector('[data-qualification-summary]');
      if(sum)sum.textContent=qualificationSummary(data.qualification);
      const was=correction.querySelector('[data-qualification-analysis]');
      if(was)was.textContent=(data.qualification.corrected && data.qualification.analysis)
        ? T('qualification.analysis_was',{value:T('value.eligibility.'+data.qualification.analysis)}) : '';
    }
    labelButtons(uid,id).filter(b=>b.dataset.field===field).forEach(b=>{
      const chosen=field==='eligibility' && data.qualification ? data.qualification.effective : data.value;
      const on=field==='priority' ? !!chosen : b.dataset.value===chosen;
      b.classList.toggle('on',on); b.setAttribute('aria-pressed',on?'true':'false');
    });
    // 只有推荐视图把本次处理过的卡片变淡（与服务端 _card 的 since 口径一致）
    if(window.__VIEW__==='recommend') document.querySelectorAll('.card[data-uid="'+uid+'"]').forEach(c=>{
      if(inScope(c,id))c.classList.toggle('done',!!savedLabel.action);
    });
    if(field==='eligibility') refreshCorrectionTag(uid,data.qualification,id);
    refreshDot(uid,id);refreshStats();
    saved=true;
    toast(labelToast(field,data));
  }catch(e){toast(labelFailure(e));}
  finally{
    LABEL_WRITES.delete(uid);
    labelButtons(uid).forEach(b=>{b.disabled=false;b.removeAttribute('aria-busy');});
    release(saved);
  }
}
/* ---- 顶部统计 -----------------------------------------------------------
   写入成功后向只读接口 /api/stats 重拉，按 data-stat 键回填。**不在前端加减**：
   统计按单条记录计，列表按去重组合并显示，自己算会和后端口径对不上。
   连续标注时只认最后一次请求；失败就保留原数字（宁可旧，不写错）。 */
let STATS_SEQ=0;
async function refreshStats(){
  if(!document.querySelector('[data-stat]')) return;
  const seq=(STATS_SEQ+=1);
  try{
    const r=await fetch('/api/stats',{cache:'no-store',credentials:'same-origin'});
    if(!r.ok) return;
    const d=await r.json();
    if(seq!==STATS_SEQ || !d || typeof d!=='object') return;
    document.querySelectorAll('[data-stat]').forEach(el=>{
      const v=d[el.dataset.stat];
      if(Number.isSafeInteger(v) && v>=0) el.textContent=String(v);
    });
  }catch(e){}
}
/* ---- 上一条 / 下一条 + 快捷键 -------------------------------------------
   顺序是**页面载入时冻结**的卡片顺序：标注不会让卡片消失或换位，所以刚标完
   的那条仍占着位置，「下一条」不会跳过一条（推荐视图本来就按 since 冻结）。
   切换一律走 openJob：它先过 allowDetailNavigation（等备注写完、草稿先确认），
   有待应用的新结果时整页载入并把目标 uid 带进 hash。
   正在等保存/确认时再按键直接忽略 —— 不排队，免得一口气跳过几条没看过的。 */
let NAV_LIST=null, NAV_GATES=0;
function navList(){
  if(!NAV_LIST) NAV_LIST=Array.from(document.querySelectorAll('.list .card')).map(c=>c.dataset.uid);
  return NAV_LIST;
}
function syncDetailNav(){
  const pane=document.getElementById('detail'); if(!pane) return;
  const list=navList(), i=OPEN_UID ? list.indexOf(OPEN_UID) : -1;
  pane.querySelectorAll('.stepbtn').forEach(b=>{ b.disabled = i<0 || !list[i+parseInt(b.dataset.step,10)]; });
  pane.querySelectorAll('.d-pos').forEach(el=>{ el.textContent = i<0 ? '' : T('js.detail_pos',{n:i+1,total:list.length}); });
  syncKeyHints();
}
async function stepDetail(delta){
  if(NAV_GATES || DETAIL_LEAVING || UPDATE_APPLYING) return;
  const list=navList(); if(!list.length) return;
  let target;
  if(!OPEN_UID) target = delta>0 ? list[0] : list[list.length-1];
  else {
    const i=list.indexOf(OPEN_UID);
    if(i<0){ toast(T('js.detail_not_in_list')); return; }   // 从别处打开的岗位不在本页列表里
    target=list[i+delta];
  }
  if(!target){ toast(T(delta>0 ? 'js.detail_last' : 'js.detail_first')); return; }
  await openJob(target);
  if(OPEN_UID!==target || document.body.classList.contains('portrait')) return;
  const card=Array.from(document.querySelectorAll('.list .card')).find(c=>c.dataset.uid===target);
  if(card){
    if(card.scrollIntoView) card.scrollIntoView({block:'nearest'});
    if(card.focus) card.focus({preventScroll:true});
  }
}
/* 按键方案存在 cookie jobs_keys（服务端 i18n.KEY_PROFILES 同表，按钮上的提示据此渲染）。
   每次按键现读 cookie：设置页刚改过，已打开的面板也只认新方案的键。 */
const KEY_PROFILES={default:['ArrowLeft','ArrowRight'],vim:['k','j'],gamer:['a','d']};
const KEY_GLYPHS={default:'← / →',vim:'K / J',gamer:'A / D'};
// 按钮提示：[上一条, 下一条] 的 [显示字形, aria-keyshortcuts]，与服务端 KEY_PROFILES 同值
const KEY_HINTS={default:[['←','ArrowLeft'],['→','ArrowRight']],vim:[['K','K'],['J','J']],gamer:[['A','A'],['D','D']]};
function keyProfile(){
  const m=/(?:^|;\s*)jobs_keys=([^;]*)/.exec(document.cookie||'');
  const v=m ? m[1].trim() : '';
  return Object.prototype.hasOwnProperty.call(KEY_PROFILES,v) ? v : 'default';
}
function navKeyStep(ev){
  const raw=String(ev.key||''), key=raw.length===1 ? raw.toLowerCase() : raw;
  const [prev,next]=KEY_PROFILES[keyProfile()];
  const step=key===next ? 1 : key===prev ? -1 : 0;
  if(!step || !key.startsWith('Arrow')) return step;
  // 方向键配 Shift 是扩选；已选中文字时方向键收起选区 / 移动光标：都留给浏览器
  return ev.shiftKey || hasTextSelection() ? 0 : step;
}
function hasTextSelection(){
  const sel=document.getSelection ? document.getSelection() : null;
  return !!(sel && sel.rangeCount && !sel.isCollapsed && String(sel).length);
}
/* 已打开的页面在别处改了方案（设置页、另一个标签）后，按钮提示随当前 cookie 重写，
   与实际响应的键保持一致。 */
function syncKeyHints(){
  const hints=KEY_HINTS[keyProfile()];
  document.querySelectorAll('#detail .stepbtn').forEach(b=>{
    const [glyph,aria]=hints[parseInt(b.dataset.step,10)>0 ? 1 : 0];
    if(b.getAttribute('aria-keyshortcuts')===aria) return;
    b.setAttribute('aria-keyshortcuts',aria);
    const name=b.getAttribute('aria-label')||'';
    b.title=name+' ('+glyph+')';
    const kbd=b.querySelector && b.querySelector('kbd'); if(kbd) kbd.textContent=glyph;
  });
}
document.addEventListener('visibilitychange',()=>{ if(!document.hidden) syncKeyHints(); });
window.addEventListener('pageshow',syncKeyHints);
window.addEventListener('focus',syncKeyHints);
function navKeyIgnored(ev){
  if(ev.defaultPrevented || ev.repeat || ev.ctrlKey || ev.metaKey || ev.altKey || imeComposing(ev)) return true;
  for(const el of [ev.target, document.activeElement]){
    if(!el || !el.tagName) continue;
    if(el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return true;
    // 自带方向键语义的控件（分栏拖条等）
    if(el.closest && el.closest('[role="separator"],[role="slider"],[role="tablist"],[role="listbox"],[role="menu"],[role="radiogroup"],[role="dialog"]'))return true;
    if(el.closest && el.closest('.choicepick,.skillpick,.geo-control')) return true;
  }
  // 抽屉、筛选面板和对话浮层开着时按键属于它们
  const detail=document.getElementById('detail');
  return shown(document.getElementById('drawer')) || shown(document.getElementById('more-panel'))
    || !!document.querySelector('.choicepanel.open,.skillpanel.open,.geo-suggest-panel.open')
    || !!(detail && detail.classList.contains('label-open'))
    || shown(document.getElementById('update-confirm'))
    || shown(document.getElementById('copy-modal'))
    || shown(document.getElementById('display-pop'));
}
document.addEventListener('keydown',ev=>{
  const step=navKeyStep(ev);
  if(!step || !document.getElementById('detail') || navKeyIgnored(ev)) return;
  ev.preventDefault();
  document.querySelector('.list')?.classList.add('keyboard-nav');
  stepDetail(step);
});
document.addEventListener('pointermove',()=>{
  document.querySelector('.list')?.classList.remove('keyboard-nav');
});
/* ---- 竖屏：标注抽屉 -----------------------------------------------------
   竖屏先读正文：「做标记 + 备注」这一块（同一组节点，不复制）变成底部抽屉，
   由底栏「标注 ▴」打开。收起时让抽屉里的焦点失焦 —— 备注照常走 noteBlur →
   保存队列；没保存成的草稿仍留在输入框里，离开时照样拦截。 */
function toggleLabelSheet(force){
  const pane=document.getElementById('detail'), sheet=document.getElementById('label-sheet');
  if(!pane || !sheet) return;
  const open = force===undefined ? !pane.classList.contains('label-open') : !!force;
  if(!open && document.activeElement && sheet.contains(document.activeElement)) document.activeElement.blur();
  pane.classList.toggle('label-open',open);
  pane.querySelectorAll('.label-toggle').forEach(b=>b.setAttribute('aria-expanded',open?'true':'false'));
  if(open && sheet.focus) sheet.focus({preventScroll:true});
}
function setNote(input){
  const uid=input.dataset.uid;
  // Returning to the loaded display while a write is pending also restores
  // its canonical identity (a translated default is not custom English text).
  const reverted=input._notePending && input.value===input.dataset.noteLoaded;
  const request={payload:reverted ? input.dataset.noteStored : noteCanonicalize(input),display:input.value,
    uid,version:(input._noteSaveVersion||0)+1};
  input._noteSaveVersion=request.version;
  input._notePending=request;
  const previous=NOTE_WRITES.get(uid);
  const task=previous ? previous.then(()=>saveNote(input,request)) : saveNote(input,request);
  NOTE_WRITES.set(uid,task);
  NOTE_REQUESTS.add(task);
  task.finally(()=>{
    if(input._notePending===request)delete input._notePending;
    NOTE_REQUESTS.delete(task);
    if(NOTE_WRITES.get(uid)===task)NOTE_WRITES.delete(uid);
  });
  return task;
}
async function saveNote(input,request){
  const uid=request.uid;
  // 落库前把界面显示值映射回 canonical：默认标签用中文原文，自定义内容原样，
  // 服务端仍走 labels.normalize_note 做最终规范化。显示值本身不入库。
  const payload=request.payload;
  try{
    const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({uid:uid,field:'note',value:payload||null})});
    if(!r.ok) throw new Error(await r.text());
    // 服务端回传 canonical；LABELS 存它，输入框按当前语言显示。
    const d=await r.json();
    const saved=d.value||'';
    if(input._noteSaveVersion===request.version && input.value===request.display){
      noteAdopt(input,saved);
    }else{
      // Keep the newer draft/identity map, but remember what is actually saved.
      input.dataset.noteStored=saved;
      input.dataset.noteLoaded=noteTagTranslate(saved);
    }
    adoptScopeLabel(uid,'','note',saved||null); refreshDot(uid);
    refreshStats();                 // 只有备注的新标注也算「已标注」
    toast(T('js.note_saved'));
  }catch(e){toast(T('js.note_failed')+e.message);}
}
/* ---- 备注候选 ----------------------------------------------------------
   备注是自由文本，但人的判断往往反复落在少数几类上。所以给它一个候选表：
   默认词表 + 库里已经写过的说法。**打字自动过滤，不匹配就照原样存成
   自定义标签** —— 不强制枚举，只是让用词慢慢收敛。

   **「；」是标签分隔符**，不是普通标点。这一点决定了全部行为：

     过滤  只看**最后一段**（最后一个「；」之后的内容），
           否则选了「搬迁」之后再打字，整串「搬迁；需」会把候选全过滤没。
     去重  只看**整段**：已经收尾的标签不再出现在候选里。
     点选  自动补尾随「；」，把这一段收尾，于是"已选中"和"正在输入"
           在文本上就区分得开。
     手动  手敲「；」和点候选完全等效 —— 用户加了分号，程序必须认。
------------------------------------------------------------------------ */
const NOTE_TAGS = window.__NOTE_TAGS__ || [];
/* 默认备注标签的显示名（canonical -> 当前语言），由服务端 i18n.note_tag_labels
   注入。自定义标签不在表里，按原文显示与保存。 */
const NOTE_TAG_LABELS = window.__NOTE_TAG_LABELS__ || {};
/* 备注分隔符：存储契约是后台 canonical 全角「；」（jobs/labels.py NOTE_SEP）。
   界面按当前语言显示：中文「；」，英文「;」。转换只发生在输入框的显示边界，
   且只替换分隔字符本身 —— 不清洗、不折叠、不补尾。 */
const NOTE_SEP_STORE = '\uff1b';
const NOTE_SEP_DISPLAY = I18N['js.note_sep'] || NOTE_SEP_STORE;
/* 英文相邻完整标签之间显示「; 」：只在边界补一个空格（结尾的分隔符后不补、
   已有空白不叠加、不产生空标签）。存储与保存仍是 canonical，且 NOTE_SEP_DISPLAY
   保持单字符，结尾分隔符判断不受影响。与 Python i18n.display_note 同口径。 */
const NOTE_GAP = I18N['js.note_gap'] || '';
function noteGapBefore(next){ return (NOTE_GAP && next && !/^\s/.test(next)) ? NOTE_GAP : ''; }
function noteJoinGap(prefix){ return prefix + ((NOTE_GAP && /[\uff1b;]$/.test(prefix)) ? NOTE_GAP : ''); }
function noteToDisplay(v){ return String(v==null?'':v).replace(/[\uff1b;]/g, NOTE_SEP_DISPLAY); }
function noteToStore(v){ return String(v==null?'':v).replace(/[\uff1b;]/g, NOTE_SEP_STORE); }
/* 只认译表自身的字符串条目：constructor / toString / __proto__ /
   hasOwnProperty 这类原型属性不是翻译，自定义词保持字面值。 */
function noteTagDisplay(tag){
  if(!Object.prototype.hasOwnProperty.call(NOTE_TAG_LABELS, tag)) return tag;
  const text=NOTE_TAG_LABELS[tag];
  return (typeof text==='string' && text) ? text : tag;
}
/* 把 canonical 备注转成当前语言的显示串：完整默认标签换译名，分隔符换本语言，
   其余原样。与 Python i18n.display_note 同口径（数据来源同一份）。 */
function noteTagTranslate(v){
  const parts=String(v==null?'':v).split(/([\uff1b;])/);
  for(let i=0;i<parts.length;i+=2){
    const seg=parts[i], tag=seg.trim();
    if(!tag) continue;
    const display=noteTagDisplay(tag);
    if(display!==tag){
      const at=seg.indexOf(tag);
      if(at>=0) parts[i]=seg.slice(0,at)+display+seg.slice(at+tag.length);
    }
  }
  for(let i=1;i<parts.length;i+=2) parts[i]=NOTE_SEP_DISPLAY+noteGapBefore(parts[i+1]);
  return parts.join('');
}
/* 输入框内存的片段身份：与当前分段一一对应的 {display, canonical} 或 null。
   只为区分「已载入/已点选的默认标签」与自定义内容；不落库、不加 schema。 */
function noteReconcile(input){
  const old=Array.isArray(input._noteIds)?input._noteIds:[];
  const segs=noteSegments(input.value);
  const ids=[]; let j=0;
  for(const seg of segs){
    const key=seg.trim(); let hit=null;
    for(let k=j;k<old.length;k++){
      if(old[k] && old[k].display===key){ hit=old[k]; j=k+1; break; }
    }
    ids.push(hit);
  }
  input._noteIds=ids;
  return ids;
}
function noteCanonicalSegments(input){
  const ids=noteReconcile(input);
  return noteSegments(input.value).map((s,i)=>{
    const key=s.trim(), id=ids[i];
    return (id && id.display===key) ? id.canonical : key;
  });
}
function noteCanonicalize(input){ return noteCanonicalSegments(input).join(NOTE_SEP_STORE); }
/* 用 canonical 原值设置输入框：显示值按当前语言渲染，同时重建身份对应关系。 */
function noteAdopt(input,canonical){
  const shown=noteTagTranslate(canonical);
  if(shown!==input.value) input.value=shown;
  input.dataset.noteStored=canonical;
  const segs=noteSegments(shown), cs=noteSegments(canonical);
  input._noteIds=segs.map((s,i)=>({display:s.trim(), canonical:(cs[i]!==undefined?cs[i]:s).trim()}));
  input.dataset.noteLoaded=shown;
  return shown;
}
function noteSync(input){
  // 服务端已按当前语言渲染显示值；data-note-stored 保留 canonical 原值，
  // 载入时用它建立身份映射，并记下「未编辑基线」。
  const stored=input.dataset.noteStored;
  noteAdopt(input,(stored===undefined||stored===null)?(input.value||''):stored);
}
function noteMenu(){return document.getElementById('note-menu');}
function noteClose(){const m=noteMenu(); if(m) m.classList.remove('open');}
function noteBlur(input){
  input.dataset.typing='';
  noteClose();
  // 显示转换（译名/全角 <-> 半角）不是用户编辑：值没变就不写库、不产生事件。
  const baseline=input._notePending ? input._notePending.display : input.dataset.noteLoaded;
  if(baseline!==undefined && input.value===baseline) return;
  setNote(input);
}
function noteSegments(v){ return String(v||'').split(/[\uff1b;]/); }
function noteCurrent(v){ const p=noteSegments(v); return p[p.length-1]; }
function noteClosed(v){
  return noteSegments(v).slice(0,-1).map(s=>s.trim()).filter(Boolean);
}
/* typed=true 表示是「打字」触发的，此时用输入内容过滤；
   点一下只是聚焦时（typed=false）显示**全部**候选 —— 否则已经写了备注的岗位
   一聚焦就只剩「无匹配候选」，等于候选表白做。 */
/* 下拉菜单定位。可见区域必须按 visualViewport 算：iOS 弹键盘时 layout viewport
   不变（window.innerHeight 也不变），屏幕下沿已经被键盘盖住；用 innerHeight 算
   「下方够不够」会以为够，菜单就排到键盘背后。
   **坐标系只有一个：layout viewport。** getBoundingClientRect() 和 position:fixed
   的 top/left 在本机浏览器中以它为原点；visualViewport.offsetLeft/Top 是
   「可见窗口的左上角在 layout viewport 里的位置」。所以 offset 只用来划定可见范围
   [offset, offset+size]，**不能从要写进 fixed 的 top/left 里再减一次**：减了之后
   菜单被整体上推 offset，与输入框的间隙恰好多出 offset，键盘弹出、页面被推上去
   （offsetTop 很大）时菜单就跑出屏幕上沿。桌面无键盘、未缩放时 offset 恒为 0，
   行为与原先相同。两边都放不下时压缩高度并贴着输入框，不盖住输入框。 */
function placeMenu(input,menu,opts){
  opts=opts||{};
  const gap=opts.gap||4;
  const minWidth=opts.minWidth||200;
  const vv=window.visualViewport;
  const ox=vv?vv.offsetLeft:0, oy=vv?vv.offsetTop:0;
  const viewW=vv?vv.width:window.innerWidth;
  const viewH=vv?vv.height:window.innerHeight;
  const visLeft=ox, visTop=oy, visRight=ox+viewW, visBottom=oy+viewH;
  const r=input.getBoundingClientRect();
  const width=Math.min(Math.max(minWidth,r.width),viewW-8);
  menu.style.left=Math.max(visLeft+4,Math.min(r.left,visRight-4-width))+'px';
  menu.style.width=width+'px';
  menu.style.maxHeight='';                 // 恢复 CSS 上限，才能量到真实高度
  menu.classList.add('open');              // 先显示才能量高度
  const h=menu.offsetHeight;
  const below=visBottom-r.bottom-gap;
  const above=r.top-visTop-gap;
  if(h<=below){                            // 下方够：放下面
    menu.style.top=(r.bottom+gap)+'px';
  } else if(h<=above){                     // 上方够：放上面
    menu.style.top=(r.top-h-gap)+'px';
  } else if(below>=above){                 // 两边都不够：挑大的一侧并压缩高度（与屏幕边缘也留 gap）
    const avail=Math.max(0,below-gap);
    menu.style.maxHeight=avail+'px';
    menu.style.top=(r.bottom+gap)+'px';
  } else {
    const avail=Math.max(0,above-gap);
    menu.style.maxHeight=avail+'px';
    menu.style.top=(r.top-avail-gap)+'px';
  }
}
function noteRender(input,typed){
  if(input.dataset.noteLoaded===undefined) input.dataset.noteLoaded=input.value;
  noteReconcile(input);
  if(typed) input.dataset.typing='1';
  const m=noteMenu(); if(!m) return;
  const cur=input.value||'';
  const seg=noteCurrent(cur);                    // 只有最后一段参与过滤
  // 去重按 canonical 身份（不看译名）：英文默认「Prioritize」与自定义
  // 「Prioritize」是两条不同的 canonical，都不会被对方隐藏。
  const used=noteCanonicalSegments(input).slice(0,-1).filter(Boolean);
  const q=input.dataset.typing==='1' ? seg.trim().toLowerCase() : '';
  // **不截断**：候选表本身是可滚动的（max-height + overflow-y），
  // 截断到十几个等于让它没法滚，用户就看不到后面的词了。
  const items=NOTE_TAGS.filter(t=>used.indexOf(t)<0 &&
    (!q||noteTagDisplay(t).toLowerCase().indexOf(q)>=0)).slice(0,400);
  // 用 Map 计重复：候选人可能是 __proto__ 这类词，普通对象作键会撞原型。
  const displayCount=new Map();
  items.forEach(t=>{ const dn=noteTagDisplay(t); displayCount.set(dn,(displayCount.get(dn)||0)+1); });
  m.innerHTML=''; m.dataset.hl='-1'; m.scrollTop=0;
  if(!items.length){
    const d=document.createElement('div'); d.className='note-empty';
    d.textContent = q ? T('js.note_no_match') : T('js.note_no_more');
    m.appendChild(d);
  } else {
    items.forEach(t=>{
      const dn=noteTagDisplay(t);
      const d=document.createElement('div'); d.className='note-item';
      // 同显冲突（默认「优先投递」与自定义「Prioritize」在英文都显示 Prioritize）：
      // 给默认项补一个 canonical 提示；选择值走 dataset，不用 textContent。
      d.textContent = (displayCount.get(dn)>1 && t!==dn) ? (dn+'  ·  '+t) : dn;
      d.dataset.tag=t; d.dataset.display=dn;
      // mousedown + preventDefault：不让输入框失焦，否则 blur 会先把半截内容存下去
      d.addEventListener('mousedown',ev=>{ev.preventDefault(); notePick(input,t,dn);});
      m.appendChild(d);
    });
    if(items.length>7){
      const d=document.createElement('div'); d.className='note-more';
      d.textContent=T('js.note_count',{n:items.length});
      m.appendChild(d);
    }
  }
  placeMenu(input,m,{minWidth:200});
}
function notePick(input,canonical,display){
  if(display===undefined) display=noteTagDisplay(canonical);
  let cur=input.value||'';
  const origSegs=noteSegments(cur);
  const seg=noteCurrent(cur);
  const replace=seg.trim()!=='' && input.dataset.typing==='1';
  if(replace){
    // 正在打「需」，点了「需驾照」—— 换掉这半截，别拼成「需；需驾照」
    cur = noteJoinGap(cur.slice(0, cur.length-seg.length)) + display;
  } else {
    // 上一段还没被分隔符收尾（旧数据就是这样，例如「搬迁-1；技术栈模糊」），
    // 先补当前语言的分隔符再追加，免得两段粘成一个词
    if(seg.trim()!=='') cur = cur + NOTE_SEP_DISPLAY;
    cur = noteJoinGap(cur) + display;
  }
  // 收尾：两种分隔符都算已收尾，不能再补一个重复的
  if(!/[\uff1b;]$/.test(cur)) cur = cur + NOTE_SEP_DISPLAY;
  input.value = cur;
  // 选择携带 canonical 身份（不是元素 textContent）：先把旧身份按文本对齐，
  // 再把刚选中的这一格标成它的 canonical + 显示名。
  const ids=noteReconcile(input).slice();
  const idx = replace ? origSegs.length-1 : (seg.trim()==='' ? origSegs.length-1 : origSegs.length);
  ids[idx]={display:display.trim(), canonical:canonical};
  input._noteIds=ids;
  // 选完立刻收起。留在屏幕上会挡住输入框，而下一步往往是接着敲下一个词；
  // 想连着选第二个，再点一下输入框就会重新展开（且已排除选过的）。
  input.focus(); noteClose();
}
/* ---- 输入法（IME）组字：备注框与搜索框共用 ----------------------------
   同一个坑踩了两次（先备注、后搜索），所以抽出来共用 —— 以后再加输入框
   就别再抄一遍了。

   中文输入法下用回车确认英文（打 "test" 再回车）时，keydown 一样会带着
   Enter 进来；此时 preventDefault + blur 会和输入法上屏打架，导致重复
   提交。Safari 确认组字那次回车 isComposing 是 false，所以还要用
   **组字结束的时间戳**再兜一层。

   分工：**keydown** 在组字期间一律不处理；但 **input 事件不能一律跳过** ——
   有些输入法（例如 macOS 中文）提交后不再补发非组字的 input，跳过会让
   候选表停在组字前的状态。所以 input 照常处理，只是不排定时器。 */
const IME_COMPOSED = new WeakMap();
function imeComposing(ev){ return !!(ev.isComposing || ev.keyCode===229); }
function imeStart(el){ el.dataset.composing='1'; }
function imeEnd(el){ delete el.dataset.composing; IME_COMPOSED.set(el, Date.now()); }
function imeBusy(el){ return el.dataset.composing==='1'; }
function imeJustComposed(el, ms){ return Date.now()-(IME_COMPOSED.get(el)||0) < (ms||120); }

function noteTyping(ev){
  // **组字期间也要重绘**，不能 return。见上方说明。
  noteRender(ev.target,true);
}
function noteComposed(input){
  imeEnd(input);
  noteRender(input,true);
  // 有些浏览器 compositionend 早于 value 落定，补一次延后重绘兜底。
  // **必须确认输入框还在焦点上** —— 否则用户上屏后立刻回车/点走，blur 收起菜单，
  // 这个 0ms 回调又把菜单重新弹开。
  setTimeout(()=>{ if(document.activeElement===input) noteRender(input,true); },0);
}
function noteKeys(ev,input){
  if(imeComposing(ev)) return;      // 交给输入法处理这一次按键
  const m=noteMenu();
  const open = m && m.classList.contains('open');
  if(ev.key==='Escape'){ noteClose(); return; }
  if(ev.key==='Enter'){
    // Safari 等浏览器确认组字的那次回车 isComposing 是 false，用时间戳再兜一层，
    // 免得「刚把 test 上屏」被当成「要保存」
    if(imeBusy(input) || imeJustComposed(input)) return;
    const i=open ? parseInt(m.dataset.hl||'-1',10) : -1;
    if(i>=0){ const el=m.querySelectorAll('.note-item')[i];
      if(el) notePick(input, el.dataset.tag!==undefined?el.dataset.tag:el.textContent,
                      el.dataset.display!==undefined?el.dataset.display:el.textContent); return; }
    noteClose(); input.blur(); return;                     // 没有高亮项 -> 存原文
  }
  if(open && (ev.key==='ArrowDown' || ev.key==='ArrowUp')){
    ev.preventDefault();                                   // 别让光标跟着跑
    const els=m.querySelectorAll('.note-item'); if(!els.length) return;
    let i=parseInt(m.dataset.hl||'-1',10);
    i = ev.key==='ArrowDown' ? (i+1)%els.length : (i<=0?els.length-1:i-1);
    m.dataset.hl=i;
    els.forEach((el,n)=>el.classList.toggle('sel',n===i));
    els[i].scrollIntoView({block:'nearest'});              // 长列表要跟着滚
  }
}
/* ---- 左右分栏拖动条 ----------------------------------------------------
   为什么要有它：`ch` 取决于字体，固定上限很难拍准，实际往往比看着窄得多，
   读者看到的是「正文很早就断、右边一片空白」。「多长算好读」本来就不是
   一个能拍准的数，所以改成给一个**能拖的边界**：拖动条写 --left，宽度记在
   localStorage，正文本身不再设上限。

   竖屏判断也在这里：**看右栏实际宽度，不看窗口宽度** —— 分栏能拖之后，
   窗口宽不等于右栏宽（窗口很宽但右栏拖窄了，一样读不了 JD）。
   右栏放不下 MIN_RIGHT 时切 body.portrait，详情走整屏覆盖那套。 */
const LEFT_KEY='jobs.panel.left';
const MIN_LEFT=300;     // 左栏再窄卡片就没法看了
const MIN_RIGHT=520;    // 右栏低于此宽度 -> 竖屏 UI（同时也是拖动的下限）
const DEFAULT_LEFT=600; // 上次定稿的左栏宽度，作为首次打开的默认值
function layoutEl(){return document.querySelector('.layout');}
function readStoredLeft(){
  try{const v=parseFloat(localStorage.getItem(LEFT_KEY)||''); return isFinite(v)?v:null;}
  catch(e){return null;}   // 隐私模式等情况下 localStorage 会抛
}
function storeLeft(px){ try{localStorage.setItem(LEFT_KEY,String(Math.round(px)));}catch(e){} }
function gutterWidth(){const g=document.getElementById('gutter'); return g?g.offsetWidth:14;}
/* 右栏「应该」有多宽 = 布局宽 - 左栏 - 拖动条。刻意不用 .detail 的宽度：
   竖屏时它是 position:fixed、宽度等于视口，拿它来判断会自己抖（刚切过去
   就发现「够宽了」再切回来）。 */
function paneWidths(){
  const l=layoutEl(); if(!l) return null;
  const left=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--left'))||DEFAULT_LEFT;
  return {avail:l.clientWidth-gutterWidth(), left:left, right:l.clientWidth-gutterWidth()-left};
}
function setLeft(px){
  document.documentElement.style.setProperty('--left',Math.round(px)+'px');
  const g=document.getElementById('gutter');
  if(g) g.setAttribute('aria-valuenow',String(Math.round(px)));
}
/* 把当前宽度收敛到合法范围，并决定是不是竖屏。窗口缩放、拖动结束都要调它。 */
function fitLayout(){
  const l=layoutEl(); if(!l) return;
  const avail=l.clientWidth-gutterWidth();
  if(avail < MIN_LEFT+MIN_RIGHT){
    document.body.classList.add('portrait');
    placeFilters(true);
    if(shown(document.getElementById('more-panel'))) filterDraftBegin();
    placeUpdate(true);
    return;
  }
  document.body.classList.remove('portrait');
  const more=document.getElementById('more-panel');
  if(more && !hasHiddenMore(more) && shown(more)){
    cancelMotion(more);
    more.hidden=true;
    more.classList.remove('entering','leaving');
    moreButtons().forEach(b=>b.setAttribute('aria-expanded','false'));
    clearMoreRestore();
  }
  filterDraftDiscard();             // 桌面即改即生效：没应用的抽屉草稿作废
  placeFilters(false);
  placeUpdate(false);
  closeDrawer();
  const want=readStoredLeft() ?? Math.min(DEFAULT_LEFT,avail-MIN_RIGHT);
  setLeft(Math.min(Math.max(want,MIN_LEFT),avail-MIN_RIGHT));
}
function resetSplit(){ storeLeft(DEFAULT_LEFT); fitLayout(); }
function startDrag(ev){
  const l=layoutEl();
  if(!l || document.body.classList.contains('portrait')) return;
  ev.preventDefault();
  const g=document.getElementById('gutter');
  const rect=l.getBoundingClientRect();
  g.classList.add('dragging'); document.body.classList.add('resizing');
  let last=null;
  const move=e=>{
    const avail=l.clientWidth-gutterWidth();
    last=Math.min(Math.max(e.clientX-rect.left,MIN_LEFT),avail-MIN_RIGHT);
    setLeft(last);
  };
  const up=()=>{
    g.classList.remove('dragging'); document.body.classList.remove('resizing');
    document.removeEventListener('pointermove',move);
    document.removeEventListener('pointerup',up);
    document.removeEventListener('pointercancel',up);
    if(last!==null) storeLeft(last);
    fitLayout();          // 拖到窗口边缘等情况下再收一次边界
  };
  document.addEventListener('pointermove',move);
  document.addEventListener('pointerup',up);
  document.addEventListener('pointercancel',up);
}
/* 键盘也能调（role=separator 的标准做法）：左右各 24px，Home/End 到两端 */
function gutterKeys(ev){
  const step=ev.shiftKey?96:24;
  const m=paneWidths(); if(!m) return;
  let px=null;
  if(ev.key==='ArrowLeft') px=m.left-step;
  else if(ev.key==='ArrowRight') px=m.left+step;
  else if(ev.key==='Home') px=MIN_LEFT;
  else if(ev.key==='End') px=m.avail-MIN_RIGHT;
  if(px===null) return;
  ev.preventDefault();
  px=Math.min(Math.max(px,MIN_LEFT),m.avail-MIN_RIGHT);
  setLeft(px); storeLeft(px);
}
/* 搜索。**已经挪到服务端**，和筛选走同一套机制。
   原来是纯前端、只搜当前页 —— 那样「先筛出 40 条、再在 40 条里搜」，
   和筛选、翻页不是同一套口径，翻页后搜索结果还会变。
   现在它就是一个普通的 data-fkey，和下拉框走同一条路；只是输入框不能
   每敲一个字就跳一次页面，所以加 380ms 防抖 + 回车立即跳。 */
let Q_TIMER=null;
function searchTyping(ev){
  const el=ev.target;
  // 组字中、或刚提交（时间戳窗口内）：只更新，不排定时器。
  // **也不要清掉 compositionend 已经排上的那个** —— 否则组字确认后反倒搜不了。
  if(imeComposing(ev) || imeBusy(el) || imeJustComposed(el)) return;
  clearTimeout(Q_TIMER);
  // 竖屏筛选抽屉开着：搜索词也是草稿，只更新「应用（N 项）」
  if(filterBatching()){ filterChanged(); return; }
  Q_TIMER=setTimeout(applyFilter,380);
}
function searchStart(ev){ clearTimeout(Q_TIMER); imeStart(ev.target); }
function searchEnd(ev){
  imeEnd(ev.target);
  clearTimeout(Q_TIMER);
  if(filterBatching()){ filterChanged(); return; }
  Q_TIMER=setTimeout(applyFilter,380);
}
function searchKeys(ev){
  // 组字确认的那次回车交给输入法，绝不提交搜索（旧版就是漏了这一步）
  if(imeComposing(ev) || imeBusy(ev.target) || imeJustComposed(ev.target)) return;
  if(ev.key==='Enter'){ clearTimeout(Q_TIMER); ev.preventDefault(); applyFilter(); }
}

/* ---- 技能筛选器 --------------------------------------------------------
   **多选 = 任一命中**。默认一个都不勾、面板收起 —— 相当一部分岗位没有任何技术词，
   默认开启会静默藏掉它们（和项目里「只排序不排除」同一条原则）。
   想取交集就用搜索词：搜索词之间本来就是 AND。 */
function skillToggle(ev){
  ev.preventDefault();
  choicePanelClose(null,false);
  geoPanelClose();
  document.getElementById('skill-panel').classList.toggle('open');
}
function skillPanelClose(){
  const p=document.getElementById('skill-panel');
  if(p) p.classList.remove('open');
}
/* ---- 单选自绘筛选器 ----------------------------------------------------
   与技能筛选共用按钮、弹出层和键盘语义，但一次只保留一个值。 */
function choicePanelClose(except,focusPicker){
  document.querySelectorAll('.choicepanel.open').forEach(p=>{
    const picker=p.closest('.choicepick');
    if(except && picker===except) return;
    p.classList.remove('open'); p.hidden=true;
    const trigger=picker && picker.querySelector('[data-choice-trigger]');
    if(trigger) trigger.setAttribute('aria-expanded','false');
    if(focusPicker && picker===focusPicker && trigger) trigger.focus({preventScroll:true});
  });
}
function choiceSetValue(trigger,value){
  const picker=trigger.closest('.choicepick'); if(!picker) return;
  const text=String(value==null?'':value);
  const options=Array.from(picker.querySelectorAll('.choiceopt'));
  const selected=options.find(option=>(option.dataset.choiceValue||'')===text);
  trigger.value=text;
  const label=selected?.querySelector('.choice-label')?.textContent||text;
  const shown=trigger.querySelector('.v'); if(shown) shown.textContent=label;
  trigger.classList.toggle('on',trigger.dataset.fkey!=='sort' && !!text);
  options.forEach(option=>{
    const on=(option.dataset.choiceValue||'')===text;
    option.classList.toggle('sel',on);
    option.setAttribute('aria-selected',on?'true':'false');
  });
}
function choiceOpen(trigger,focusOption){
  const picker=trigger.closest('.choicepick'); if(!picker) return;
  skillPanelClose();
  geoPanelClose();
  choicePanelClose(picker,false);
  const panel=picker.querySelector('.choicepanel');
  if(!panel) return;
  panel.hidden=false; panel.classList.add('open');
  trigger.setAttribute('aria-expanded','true');
  if(focusOption){
    const options=Array.from(panel.querySelectorAll('.choiceopt')).filter(option=>!option.hidden);
    const selected=options.find(option=>option.classList.contains('sel'))||options[0];
    if(selected && selected.focus) selected.focus({preventScroll:true});
  }
}
function choiceToggle(ev,trigger){
  ev.preventDefault(); ev.stopPropagation();
  const panel=trigger.closest('.choicepick')?.querySelector('.choicepanel');
  if(panel && panel.classList.contains('open')) choicePanelClose(null,trigger.closest('.choicepick'));
  else choiceOpen(trigger,false);
}
function choicePick(option){
  const picker=option.closest('.choicepick'); if(!picker) return;
  const trigger=picker.querySelector('[data-choice-trigger]');
  choiceSetValue(trigger,option.dataset.choiceValue||'');
  choicePanelClose(null,picker);
  filterChanged();
}
function choiceTriggerKeys(ev,trigger){
  if(ev.key==='Escape'){
    if(trigger.getAttribute('aria-expanded')==='true'){ev.preventDefault();ev.stopPropagation();choicePanelClose(null,trigger.closest('.choicepick'));}
    return;
  }
  if(!['ArrowDown','ArrowUp','Enter',' '].includes(ev.key)) return;
  ev.preventDefault(); ev.stopPropagation();
  choiceOpen(trigger,true);
}
function choiceOptionKeys(ev,option){
  const picker=option.closest('.choicepick'); if(!picker) return;
  const options=Array.from(picker.querySelectorAll('.choiceopt'));
  const i=options.indexOf(option);
  if(ev.key==='Escape'){
    ev.preventDefault(); ev.stopPropagation();
    choicePanelClose(null,picker);
  }else if(ev.key==='Enter'||ev.key===' '){
    ev.preventDefault(); ev.stopPropagation(); choicePick(option);
  }else if(['ArrowDown','ArrowUp','Home','End'].includes(ev.key)){
    ev.preventDefault(); ev.stopPropagation();
    const next=ev.key==='Home'?0:ev.key==='End'?options.length-1:
      (i+(ev.key==='ArrowDown'?1:-1)+options.length)%options.length;
    options[next]?.focus({preventScroll:true});
  }
}
/* 注意有两个东西别搞混：
   #skill-panel 是筛选栏的「技术栈多选」；#skill-menu 是详情里的技能候选。
   失焦要关的是后者 —— 之前 skillBlur 调的是 skillPanelClose()，关错了对象，
   所以详情候选点走之后一直留着。 */
function skillMenuClose(){
  const m=skillMenu();
  if(m) m.classList.remove('open');
}

/* ---- 详情页的技能词表编辑 ----------------------------------------------
   复用备注候选表那套形态（候选可滚、点选生效），但语义不同：
   点候选 = 把输入框里的词作为该技能的**别名**；回车 / 首行 = **新增**一个技能。
   落库前由 skills.apply_edit 校验（同名、重复、超长都会被打回）。

   **候选不按输入过滤**：要设成别名的词与目标技能天然不同名（aws ↔ Amazon Web
   Services），按输入筛会把目标筛掉。所以排序是唯一的"怎么找"：
     首行   固定「＋ 新增技能」；词已是某技能的名称/别名时换成不可点的提示
     可能是 首字母缩写、名称/别名互相包含、词重叠 —— 忽略大小写、空格、标点
     全部   完整字母表（含上一组的项），每行技能名 + 灰字别名
   列表显示展示名，写入的 target 一律是规范名。 */
const SKILLS = window.__SKILLS__ || [];
/* token（后端 canonical_tokens 的小写键）-> 规范名，与 apply_edit 的判定同一份数据 */
const SKILL_OWNERS = new Map(window.__SKILL_OWNERS__ || []);
/* 与后端 skills._clean 同口径：Python str.split() 的空白字符集合，折叠成单个空格 */
const PY_SPACE = /[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/;
function skillClean(v){ return String(v==null?'':v).split(PY_SPACE).filter(Boolean).join(' '); }
/* 已占用：apply_edit 用 canonical_tokens().get(token.lower())，这里同样只做 toLowerCase */
function skillOwner(token){
  const t=skillClean(token);
  return t ? (SKILL_OWNERS.get(t.toLowerCase())||null) : null;
}
function skillLabel(s){ return s.display_name||s.name; }
function skillCompare(a,b){
  const x=skillLabel(a).toLowerCase(), y=skillLabel(b).toLowerCase();
  return x<y ? -1 : x>y ? 1 : 0;
}
/* 相似度只决定「可能是」组的顺序，不隐藏任何项 */
function skillNorm(v){ return String(v||'').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,''); }
function skillWords(v){ return String(v||'').toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(w=>w.length>1); }
function skillInitials(v){
  return String(v||'').split(/[^\p{L}\p{N}]+/u).filter(Boolean).map(w=>w[0]).join('').toLowerCase();
}
function skillSimilarity(token,s){
  const q=skillNorm(token), qi=skillInitials(token), qw=skillWords(token);
  if(!q) return 0;
  let best=0;
  for(const name of [s.name, ...(s.aliases||[])]){
    const n=skillNorm(name); if(!n) continue;
    const ni=skillInitials(name);
    if((q.length>1 && ni.length>1 && ni===q) || (n.length>1 && qi.length>1 && qi===n)) best=Math.max(best,90);
    const short=q.length<=n.length ? q : n;
    if(short.length>1 && (n.includes(q) || q.includes(n))) best=Math.max(best,70);
    const nw=skillWords(name);
    if(qw.some(w=>nw.includes(w))) best=Math.max(best,50);
  }
  return best;
}
function skillInput(){ return document.getElementById('skill-q'); }
function skillMenu(){ return document.getElementById('skill-menu'); }
/* 候选行：kind = add | taken | hint | group | alias；alias 行带规范名 target */
function skillRows(value){
  const token=skillClean(value);
  const owner=skillOwner(token);
  const rows=[];
  if(!token) rows.push({kind:'hint', label:T('js.skill_type_first')});
  else if(owner){
    const hit=SKILLS.find(s=>s.name===owner);
    rows.push({kind:'taken', label:T('js.skill_taken',{token:token,name:hit?skillLabel(hit):owner})});
  } else rows.push({kind:'add', label:T('js.skill_add',{token:token})});
  const off=!token || !!owner;
  const alias=s=>{
    const shown=skillLabel(s).toLowerCase();
    const extra=[s.name, ...(s.aliases||[])].filter((a,i,all)=>a && String(a).toLowerCase()!==shown
      && all.findIndex(b=>String(b).toLowerCase()===String(a).toLowerCase())===i);
    return {kind:'alias', label:skillLabel(s), extra:extra, target:s.name, off:off};
  };
  if(!off){
    const like=SKILLS.map(s=>[skillSimilarity(token,s),s]).filter(x=>x[0]>0)
      .sort((a,b)=>(b[0]-a[0]) || skillCompare(a[1],b[1])).slice(0,5);
    if(like.length){
      rows.push({kind:'group', label:T('js.skill_group_like')});
      like.forEach(x=>rows.push(alias(x[1])));
    }
  }
  if(SKILLS.length){
    rows.push({kind:'group', label:T('js.skill_group_all')});
    SKILLS.slice().sort(skillCompare).forEach(s=>rows.push(alias(s)));
  }
  return {token:token, owner:owner, rows:rows};
}
function skillRender(){
  const input=skillInput(); if(!input) return;
  const m=skillMenu(); if(!m) return;
  const list=skillRows(input.value);
  m.innerHTML=''; m.scrollTop=0;
  list.rows.slice(0,500).forEach(r=>{
    const d=document.createElement('div');
    if(r.kind==='group' || r.kind==='hint' || r.kind==='taken'){
      d.className = r.kind==='group' ? 'note-group' : (r.kind==='taken' ? 'note-empty note-warn' : 'note-empty');
      d.textContent=r.label; m.appendChild(d); return;
    }
    d.className='note-item'+(r.kind==='add' ? ' note-add' : '')+(r.off ? ' off' : '');
    d.textContent=r.label;
    if(r.extra && r.extra.length){
      const a=document.createElement('span'); a.className='note-alias'; a.textContent=r.extra.join(' · ');
      d.appendChild(a);
    }
    d.dataset.kind=r.kind; d.dataset.target=r.target||'';
    // mousedown + preventDefault：和备注候选一样，别让输入框失焦导致菜单先被收掉
    d.addEventListener('mousedown',ev=>{
      ev.preventDefault();
      if(r.off){ toast(list.rows[0].label); return; }   // 先输入 / 已被占用
      skillPick(r.kind, r.target||'');
    });
    m.appendChild(d);
  });
  placeMenu(input,m,{minWidth:280});
}
let SKILL_SAVING=false, SKILL_REQUEST=null, SKILL_NAV_WAITING=false;
function skillStatus(text){
  const s=document.getElementById('skill-status');
  if(s) s.textContent=text||'';
}
/* 失焦收起候选。点候选用的是 mousedown+preventDefault，不会触发 blur，
   所以这里只会被「点到别处 / Tab 走」触发。 */
function skillBlur(){ skillMenuClose(); }
function skillPick(kind,target){
  if(SKILL_REQUEST)return SKILL_REQUEST;
  const task=saveSkill(kind,target);
  SKILL_REQUEST=task;
  task.finally(()=>{SKILL_SAVING=false;SKILL_REQUEST=null;});
  return task;
}
async function saveSkill(kind,target){
  if(SKILL_SAVING) return;          // 防重复提交：请求还没回来时连点不能再发一次
  const input=skillInput();
  if(!input) return;
  const token=(input.value||'').trim();
  if(!token) return;
  SKILL_SAVING=true;
  // **立刻反馈**：收起候选、清空输入、置灰并写状态。以前这些都要等 fetch
  // 回来才做，中间几百毫秒界面毫无变化，看起来就像"静默发送了一条消息"。
  skillMenuClose();
  input.value='';
  input.disabled=true;
  skillStatus(T('js.skill_saving'));
  const targetSkill=SKILLS.find(s=>s.name===target);
  const verb = kind==='add' ? T('js.skill_added',{token:token}) : T('js.skill_aliased',{token:token,name:targetSkill?.display_name||target});
  try{
    const r=await fetch('/api/skills',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:token,action:(kind==='add'?'add':'alias'),target:target})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    skillStatus(T('js.skill_recalc'));
    toast(verb);
    // 重算 + 整页重载要点时间；先让状态和 toast 露个面，否则一刷新就看不见了
    if(!SKILL_NAV_WAITING)setTimeout(()=>location.reload(), 300);
  }catch(e){
    SKILL_SAVING=false;
    input.disabled=false;
    skillStatus('');
    input.value=token;              // 被校验打回的词还给用户，别让他重打
    toast(T('js.skill_failed')+e.message);
    skillRender();
  }
}
function skillKeys(ev){
  if(imeComposing(ev) || imeBusy(ev.target) || imeJustComposed(ev.target)) return;
  if(ev.key==='Escape'){ skillMenuClose(); return; }
  if(ev.key==='Enter'){
    ev.preventDefault();
    const list=skillRows(ev.target.value);
    if(list.owner){ toast(list.rows[0].label); return; }       // 已占用：提前提示，不发请求
    skillPick('add','');
  }
}
/* 每个视图各记各的筛选条件。
   存在 localStorage 的 {view: "q=python&interest=want"} 里。
   切视图时先套上目标视图记住的那套参数 —— 否则在「全部」里筛完，
   切到别处再切回来就白筛了。**URL 仍然是当前页唯一的真相**，
   localStorage 只在「点导航切换视图」这一件事上补一层记忆。 */
const FILTER_KEY='jobs.panel.filters';
function filterStore(){
  try{ return JSON.parse(localStorage.getItem(FILTER_KEY)||'{}')||{}; }catch(e){ return {}; }
}
function writeFilterStore(st){
  try{ localStorage.setItem(FILTER_KEY,JSON.stringify(st)); }catch(e){}
}
/* 当前控件上的筛选参数（不含 view / offset / since —— 那些不是筛选条件） */
function viewParams(){
  // 一个 key 可能有多个值（技能多选、将来的多选下拉都用 append）。
  // 用 set() 会把同名的 checkbox 互相覆盖，只剩最后一个。
  const p=new URLSearchParams();
  document.querySelectorAll('[data-fkey]').forEach(el=>{
    const k=el.dataset.fkey;
    if(el.type==='checkbox'){ if(el.checked) p.append(k, el.value||'1'); return; }
    if(el.tagName==='SELECT' && el.multiple){
      for(const o of el.selectedOptions) if(o.value) p.append(k,o.value);
      return;
    }
    const value=k==='geo' && !filterBatching() ? urlValue(el) : el.value;
    if(value) p.append(k, value);
  });
  return p;
}
/* 所有筛选控件共用这一套：data-fkey -> URL 参数。
   **不要给排序另起状态管理**，否则 URL 里会有两个真相。
   竖屏筛选抽屉开着时，任何防抖 / 直接调用都只更新草稿；收起抽屉时统一提交。 */
function applyFilter(){
  clearTimeout(Q_TIMER);
  if(filterBatching()){ filterChanged(); return; }
  commitFilter();
}
function commitFilter(){
  clearTimeout(Q_TIMER);
  const view=window.__VIEW__||'recommend';
  const p=viewParams();
  const st=filterStore();
  const s=p.toString();
  if(s) st[view]=s; else delete st[view];
  writeFilterStore(st);
  p.set('view',view);
  navigatePanel('/'+ (p.toString()?'?'+p.toString():''));
}
/* 清除筛选 = 回到该视图的默认口径，并忘掉记住的那套。
   移动端先复位草稿，仍需点“应用”提交。 */
function clearFilter(){
  if(filterBatching()){ filterDraftBegin(); filterControls().forEach(resetEl); filterChanged(); return; }
  const view=window.__VIEW__||'recommend';
  try{ sessionStorage.removeItem(MORE_KEY); }catch(e){}
  const st=filterStore(); delete st[view]; writeFilterStore(st);
  navigatePanel('/?view='+encodeURIComponent(view));
}
/* 点导航切换视图：套上那个视图自己记住的筛选条件。
   修饰键 / 中键点仍然走原生链接（新标签打开），所以 href 是真的能用。 */
function gotoView(view,ev){
  if(ev && (ev.metaKey||ev.ctrlKey||ev.shiftKey||ev.altKey)) return true;
  if(ev) ev.preventDefault();
  const s=filterStore()[view]||'';
  navigatePanel('/?view='+encodeURIComponent(view)+(s?'&'+s:''));
  return false;
}
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.dots').forEach(d=>refreshDot(d.dataset.uid));
  const h=readDetailHash();
  if(h.uid && document.getElementById("detail")) openJob(h.uid,h.id);
  fitLayout();          // 应用记住的分栏宽度 / 决定是不是竖屏
  // 搜索框：请求回来之后焦点会丢，光标也回到开头。带着 q 重新聚焦、光标置尾，
  // 否则用户打字打到一半就被打断，没法连着敲。
  // **修好 IME 之后这一步才是安全的**：组字期间不再触发跳转，
  // 就不会在「刚载入就聚焦」时把输入法拽进组字态。
  const qi=document.querySelector('input[data-fkey="q"]');
  if(qi && qi.value){ qi.focus(); qi.setSelectionRange(qi.value.length,qi.value.length); }
  // 候选表内按下鼠标不要抢焦点：否则点滚动条 / 空白处会让输入框失焦，
  // blur 直接把菜单收掉，滚动条等于点不动。
  const nm=noteMenu();
  if(nm) nm.addEventListener('mousedown',ev=>ev.preventDefault());
  // 「更多筛选」在筛选改动后（整页跳转）保持展开，方便连着设几个条件
  try{ if(sessionStorage.getItem(MORE_KEY)==='1') toggleMore(true); }catch(e){}
  syncThemeUi();
  const cb=document.querySelector('.ctxbar');
  if(cb) cb.addEventListener('click',()=>{ if(document.body.classList.contains('portrait')) cb.classList.toggle('expanded'); });
});
// 技能多选面板：点面板外面就收起。
document.addEventListener('click',ev=>{
  const w=document.getElementById('skillpick');
  const p=document.getElementById('skill-panel');
  if(p && p.classList.contains('open') && w && !w.contains(ev.target))
    p.classList.remove('open');
  if(!(ev.target.closest && ev.target.closest('.choicepick'))) choicePanelClose(null,false);
  if(!(ev.target.closest && ev.target.closest('.geo-control'))) geoPanelClose();
});
// 技能筛选面板在失焦时也要收起（Tab 走、焦点落到别处）。面板**内部**移动
// 焦点不算 —— 勾选项之间来回点会一直触发 focusout，那样面板就选不成了。
document.addEventListener('focusout',ev=>{
  const w=document.getElementById('skillpick');
  if(w && w.contains(ev.target) && !(ev.relatedTarget && w.contains(ev.relatedTarget)))
    skillPanelClose();
  const choice=ev.target.closest && ev.target.closest('.choicepick');
  if(choice && !(ev.relatedTarget && choice.contains(ev.relatedTarget)))
    choicePanelClose(null,false);
  const geo=ev.target.closest && ev.target.closest('.geo-control');
  if(geo && !(ev.relatedTarget && geo.contains(ev.relatedTarget)))
    geoPanelClose();
},true);
// 详情栏内部滚动时，fixed 定位的候选表会留在原地，直接收起来最省事。
// **但候选表自己滚动时不能收** —— 否则滚轮一动窗口就没了，等于不可滚动。
/* iOS 聚焦输入框时浏览器会把页面滚上来让输入框可见。那次滚动不是用户在翻页，
   不能据此把刚打开的候选收掉 —— 之前就是它造成「闪一下就没」。焦点还在关联
   输入框上就重新定位，焦点走了才收起。 */
function syncMenusOnScroll(){
  const active=document.activeElement;
  const si=skillInput(), sm=document.getElementById('skill-menu');
  if(sm && sm.classList.contains('open')){
    if(si && active===si) placeMenu(si,sm,{minWidth:280});
    else skillMenuClose();
  }
  const ni=document.querySelector('.note input'), nm=noteMenu();
  if(nm && nm.classList.contains('open')){
    if(ni && active===ni) placeMenu(ni,nm,{minWidth:200});
    else noteClose();
  }
  skillPanelClose();          // 技术栈多选在顶栏，任何页面滚动都该收起
  choicePanelClose(null,false);
  geoPanelClose();
}
document.addEventListener('scroll',ev=>{
  // 菜单自己滚动时不能收，否则滚轮一动面板就没了。技能候选 / 技术栈多选
  // 面板也必须豁免 —— 之前只判了 #note-menu，所以技术栈多选根本滚不动。
  const t=ev.target instanceof Node ? ev.target : null;
  const menus=[noteMenu(), document.getElementById('skill-menu'),
               document.getElementById('skill-panel'),
               ...document.querySelectorAll('.choicepanel,.geo-suggest-panel')].filter(Boolean);
  if(t && menus.some(el=>el===t || el.contains(t))) return;
  syncMenusOnScroll();
},true);
// The note input moves while its sheet slides in. Focus can arrive before
// that movement ends, without a scroll/viewport event at its final position.
document.addEventListener('transitionend',ev=>{
  if(ev.target===document.getElementById('label-sheet') && ev.propertyName==='transform'){
    syncMenusOnScroll();
  }
});
window.addEventListener('resize',()=>{
  skillMenuClose(); noteClose(); skillPanelClose(); choicePanelClose(null,false); geoPanelClose(); fitLayout();
});
// iOS 键盘：只有 visualViewport 会变。它一变大变小就重排菜单，否则菜单会停在
// 键盘背后或旧位置上。
if(window.visualViewport){
  window.visualViewport.addEventListener('resize',syncMenusOnScroll);
  window.visualViewport.addEventListener('scroll',syncMenusOnScroll);
}

/* ---- 卡片键盘可达 ---------------------------------------------------- */
function cardKeys(ev,uid){
  if(ev.key==='Enter' || ev.key===' '){ ev.preventDefault(); openJob(uid); }
}

/* ---- 「更多筛选」与竖屏底部抽屉 ----------------------------------------
   常驻筛选在工具栏；其余收进 #more-panel。竖屏时把常驻筛选和排序**挪进**
   同一个面板（底部抽屉）—— 是移动 DOM 节点，不是复制一份：每个 data-fkey
   控件只能有一份，否则 viewParams() 会把同一个参数拼两次。 */
const MORE_KEY='jobs.panel.more';
function moreButtons(){ return document.querySelectorAll('[aria-controls="more-panel"]'); }
function hasHiddenMore(p){ return !!(p && String(p.dataset.keys||'').trim()); }
function clearMoreRestore(){
  document.body.classList.remove('sheet-open','sheet-closing');
  try{ sessionStorage.removeItem(MORE_KEY); }catch(e){}
}
function toggleMore(force){
  const p=document.getElementById('more-panel');
  // 推荐页在桌面没有“更多筛选”：即使上一页留下的 sessionStorage 要求恢复，
  // 也只在竖屏（主筛选会移入底部抽屉）允许打开。空面板绝不能留在桌面上。
  if(!hasHiddenMore(p) && !document.body.classList.contains('portrait')){
    clearMoreRestore();
    return;
  }
  if(!p){ clearMoreRestore(); return; }
  const open = (force===undefined) ? !shown(p) : !!force;
  if(open===shown(p)) return;
  moreButtons().forEach(b=>b.setAttribute('aria-expanded',open?'true':'false'));
  if(open && document.body.classList.contains('portrait')) filterDraftBegin();
  if(!open){ choicePanelClose(null,false); geoPanelClose(); skillPanelClose(); filterDraftDiscard(); }
  if(open){ document.body.classList.remove('sheet-closing'); document.body.classList.add('sheet-open'); reveal(p); }
  else {
    document.body.classList.add('sheet-closing');
    conceal(p, ()=>document.body.classList.remove('sheet-open','sheet-closing'));
  }
  try{ if(open) sessionStorage.setItem(MORE_KEY,'1'); else sessionStorage.removeItem(MORE_KEY); }catch(e){}
  // 草稿期间推迟的新结果：丢弃草稿后没开着岗位就立刻载入
  if(!open && UPDATE_PENDING && !OPEN_UID) applyLatestResults();
}
function placeFilters(portrait){
  const slot=document.getElementById('sheet-slot'); if(!slot) return;
  [['primary-filters','primary-home'],['tail-filters','tail-home']].forEach(([id,home])=>{
    const el=document.getElementById(id), anchor=document.getElementById(home);
    if(!el || !anchor) return;
    if(portrait){ if(el.parentNode!==slot) slot.appendChild(el); }
    else if(el.previousElementSibling!==anchor){ anchor.after(el); }
  });
}
/* 「更新岗位」只有一个按钮（#update-start，状态机与禁用逻辑都挂在它上面）。
   竖屏时顶栏隐藏，把整个小部件**挪进**抽屉里「上次抓取」旁边；横屏挪回顶栏。
   和 placeFilters 一样是移动节点，不复制。 */
function placeUpdate(portrait){
  const w=document.getElementById('update-widget');
  const slot=document.getElementById('drawer-update-slot'), home=document.getElementById('update-home');
  if(!w || !slot || !home) return;
  if(portrait){ if(w.parentNode!==slot) slot.appendChild(w); }
  else if(home.nextElementSibling!==w){ home.after(w); }
}
/* 移除单个筛选：把对应控件复位，再走同一个 applyFilter（URL 仍是唯一真相） */
function resetControl(key){
  document.querySelectorAll('[data-fkey="'+key+'"]').forEach(el=>{
    if(el.type==='checkbox') el.checked=false;
    else if(el.closest && el.closest('.choicepick'))
      choiceSetValue(el,el.closest('.choicepick').dataset.choiceDefault||'');
    else el.value='';
    if(key==='geo' && !filterBatching())el.defaultValue='';
  });
}
/* 复位成默认页渲染出的样子：下拉选「任意」（空值）项，没有空值项（排序）就选第一项 */
function resetEl(el){
  if(el.type==='checkbox'){ el.checked=false; return; }
  const picker=el.closest && el.closest('.choicepick');
  if(picker){ choiceSetValue(el,picker.dataset.choiceDefault||''); return; }
  const opts=el.tagName==='SELECT' && el.options ? Array.from(el.options) : [];
  if(!opts.length){ el.value=''; if(el.dataset.fkey==='geo' && !filterBatching())el.defaultValue=''; return; }
  if(el.multiple){ opts.forEach(o=>{ o.selected=false; }); return; }
  const i=opts.findIndex(o=>o.value==='');
  el.selectedIndex = i<0 ? 0 : i;
}
function removeFilter(key){
  resetControl(key);
  // 先让这枚小标签收起来，再跳转 —— 否则点了像没反应、页面突然整个换掉
  const chip=[...document.querySelectorAll('.achip button')]
    .find(b=>(b.getAttribute('onclick')||'').indexOf("'"+key+"'")>=0);
  if(chip && !REDUCED.matches){ chip.parentElement.classList.add('leaving'); setTimeout(applyFilter,140); }
  else applyFilter();
}
function clearHidden(){
  const p=document.getElementById('more-panel'); if(!p) return;
  (p.dataset.keys||'').split(' ').filter(Boolean).forEach(resetControl);
  filterChanged();
}
/* ---- 竖屏筛选抽屉：改完一次「应用」 --------------------------------------
   桌面即改即生效。竖屏抽屉里改动只留在控件上，「应用」才调一次 applyFilter，
   其余一切照旧 —— **不另建筛选状态**：草稿就是控件本身，URL 仍是唯一真相。
   打开抽屉时记下各控件的值（= 由 URL 渲染出的状态）；关闭 / 取消 / 切回桌面
   就按快照还原，等于丢弃草稿。 */
let FILTER_DRAFT=null;
function filterControls(){ return Array.from(document.querySelectorAll('[data-fkey]')); }
function filterBatching(){
  return document.body.classList.contains('portrait') && shown(document.getElementById('more-panel'));
}
/* 搜索框还没提交的字（380ms 防抖内）不算 URL 状态：基线与快照用渲染时的值，
   这样它计入「应用（N 项）」，丢弃草稿时也一起还原。 */
function urlValue(el){
  return ['q','geo'].includes(el.dataset.fkey) && typeof el.defaultValue==='string' ? el.defaultValue : el.value;
}
function filterDraftBegin(){
  if(FILTER_DRAFT) return;
  clearTimeout(Q_TIMER);              // 抽屉打开前排上的搜索跳转作废，搜索词进草稿
  const base=viewParams();
  for(const key of ['q','geo']){
    const input=filterControls().find(el=>el.dataset.fkey===key);
    if(input){base.delete(key);if(urlValue(input))base.append(key,urlValue(input));}
  }
  FILTER_DRAFT={base, snap:filterControls().map(el=>[el,
    el.type==='checkbox' ? el.checked : (el.multiple ? Array.from(el.options).map(o=>o.selected) : urlValue(el))])};
  syncFilterDraft();
}
function filterDraftDiscard(){
  const draft=FILTER_DRAFT; if(!draft) return;
  FILTER_DRAFT=null;
  draft.snap.forEach(([el,v])=>{
    if(el.type==='checkbox') el.checked=v;
    else if(Array.isArray(v)) Array.from(el.options).forEach((o,i)=>{ o.selected=!!v[i]; });
    else if(el.closest && el.closest('.choicepick')) choiceSetValue(el,v);
    else el.value=v;
  });
  syncFilterDraft();
}
/* 草稿里与 URL 不同的参数个数（同一个键的多值按集合比较） */
function filterDraftCount(){
  if(!FILTER_DRAFT) return 0;
  const a=FILTER_DRAFT.base, b=viewParams();
  const keys=new Set([...a.keys(), ...b.keys()]);
  let n=0;
  keys.forEach(k=>{
    const x=a.getAll(k).sort().join('\n'), y=b.getAll(k).sort().join('\n');
    if(x!==y) n+=1;
  });
  return n;
}
/* 控件外观跟着草稿走（ctl.on、技能多选的摘要、底部按钮计数）。 */
function syncFilterDraft(){
  filterControls().forEach(el=>{
    const box=el.closest ? el.closest('.ctl') : null;
    if(box && el.dataset.fkey!=='sort' && el.dataset.fkey!=='q')
      box.classList.toggle('on', el.type==='checkbox' ? el.checked : !!el.value);
  });
  const pick=document.getElementById('skillpick');
  if(pick){
    const names=Array.from(pick.querySelectorAll('input[type=checkbox]')).filter(c=>c.checked)
      .map(c=>{ const t=c.nextElementSibling; return t ? t.textContent : c.value; });
    const btn=pick.querySelector('button.ctl'), v=pick.querySelector('.v');
    if(btn) btn.classList.toggle('on',names.length>0);
    if(v) v.textContent=names.length ? names.join(', ') : T('filter.any');
  }
  const done=document.getElementById('more-done');
  if(done){
    const n=filterDraftCount();
    done.textContent = n ? T('js.filter_apply',{n}) : T('filter.done');
  }
}
function filterChanged(){
  if(!filterBatching()){ applyFilter(); return; }
  filterDraftBegin();
  syncFilterDraft();
}
function geoPanel(){ return document.getElementById('geo-suggest-panel'); }
function geoPanelClose(){
  const p=geoPanel(); if(!p || !p.classList.contains('open')) return;
  p.classList.remove('open'); p.hidden=true;
  const input=document.querySelector('input[data-fkey="geo"]');
  if(input) input.setAttribute('aria-expanded','false');
}
function geoSuggest(input,showAll){
  const p=geoPanel(); if(!p || !input) return;
  const query=String(input.value||'').trim().toLowerCase();
  let shown=0;
  p.querySelectorAll('.geoopt').forEach(option=>{
    const match=!!query && String(option.dataset.geoValue||'').toLowerCase().includes(query);
    option.hidden=!showAll && !match;
    if(!option.hidden) shown++;
  });
  const open=showAll || (!!query && shown>0);
  p.classList.toggle('open',open); p.hidden=!open;
  input.setAttribute('aria-expanded',open?'true':'false');
}
function geoFocus(input){
  skillPanelClose(); choicePanelClose(null,false); geoSuggest(input,true);
}
function geoInput(input){
  geoSuggest(input,false);
  geoTyping();
}
function geoOptionPick(ev,option){
  ev.preventDefault();
  const input=option.closest('.geo-control')?.querySelector('input[data-fkey="geo"]');
  if(!input) return;
  input.value=option.dataset.geoValue||'';
  input.focus({preventScroll:true}); geoPanelClose(); geoTyping();
}
function geoOptionKeys(ev,option){
  const panel=option.closest('.geo-suggest-panel'); if(!panel) return;
  const options=Array.from(panel.querySelectorAll('.geoopt')).filter(item=>!item.hidden);
  const i=options.indexOf(option);
  if(ev.key==='Escape'){
    ev.preventDefault(); ev.stopPropagation();
    const input=document.querySelector('input[data-fkey="geo"]');
    input?.focus({preventScroll:true}); geoPanelClose();
  }else if(ev.key==='Enter'||ev.key===' '){
    ev.preventDefault(); ev.stopPropagation(); geoOptionPick(ev,option);
  }else if(['ArrowDown','ArrowUp','Home','End'].includes(ev.key)){
    ev.preventDefault(); ev.stopPropagation();
    const next=ev.key==='Home'?0:ev.key==='End'?options.length-1:
      (i+(ev.key==='ArrowDown'?1:-1)+options.length)%options.length;
    options[next]?.focus({preventScroll:true});
  }
}
function geoTyping(){
  if(filterBatching()){filterDraftBegin();syncFilterDraft();}
}
function applyGeo(){
  const input=document.querySelector('[data-fkey="geo"]');
  if(!input || imeBusy(input) || imeJustComposed(input))return;
  if(!filterBatching())input.defaultValue=input.value;
  filterChanged();
}
function geoKeys(ev){
  if(imeComposing(ev) || imeBusy(ev.target) || imeJustComposed(ev.target))return;
  if(ev.key==='Escape'){ev.preventDefault();ev.stopPropagation();geoPanelClose();return;}
  if(ev.key==='ArrowDown'||ev.key==='ArrowUp'){
    ev.preventDefault();ev.stopPropagation();
    const panel=geoPanel();
    if(!panel || !panel.classList.contains('open'))
      geoSuggest(ev.target,!String(ev.target.value||'').trim());
    const options=panel?Array.from(panel.querySelectorAll('.geoopt')).filter(item=>!item.hidden):[];
    if(options.length) options[ev.key==='ArrowUp'?options.length-1:0].focus({preventScroll:true});
    return;
  }
  if(ev.key!=='Enter')return;
  ev.preventDefault();geoPanelClose();applyGeo();
}
/* 抽屉底部按钮：有改动 =「应用」（一次跳转，跳完不再自动展开）；没改动 = 收起 */
function moreDone(){
  if(filterBatching() && filterDraftCount()){
    try{ sessionStorage.removeItem(MORE_KEY); }catch(e){}
    commitFilter();
    return;
  }
  toggleMore(false);
}

/* ---- 汉堡菜单（竖屏）------------------------------------------------- */
function openDrawer(){
  const d=document.getElementById('drawer'); if(!d) return;
  reveal(d);
  document.querySelectorAll('[aria-controls="drawer"]').forEach(b=>b.setAttribute('aria-expanded','true'));
  const first=d.querySelector('.dv'); if(first) first.focus();
}
function closeDrawer(){
  const d=document.getElementById('drawer'); if(!shown(d)) return;
  conceal(d);
  document.querySelectorAll('[aria-controls="drawer"]').forEach(b=>b.setAttribute('aria-expanded','false'));
}
document.addEventListener('keydown',ev=>{
  if(ev.key!=='Escape') return;
  const pane=document.getElementById('detail');
  if(pane && pane.classList.contains('label-open')){ toggleLabelSheet(false); return; }
  closeDrawer();
  const p=document.getElementById('more-panel');
  if(shown(p) && document.body.classList.contains('portrait')) toggleMore(false);
});

/* ---- 语言与外观偏好（cookie，服务端据此渲染）-------------------------- */
function setPref(name,value){
  const base='; path=/; SameSite=Lax';
  if(value) document.cookie=name+'='+value+'; max-age=31536000'+base;
  else document.cookie=name+'=; max-age=0'+base;
}
function setKeys(profile){
  if(!Object.prototype.hasOwnProperty.call(KEY_PROFILES,profile)) profile='default';
  setPref('jobs_keys', profile==='default' ? '' : profile);
  document.querySelectorAll('[data-keys-choice]').forEach(b=>{
    const on=b.dataset.keysChoice===profile;
    b.classList.toggle('on',on); b.setAttribute('aria-pressed',on?'true':'false');
  });
  toast(T('js.keys_saved',{keys:KEY_GLYPHS[profile]}));
}
function setLang(lang){ setPref('jobs_lang', lang==='auto' ? '' : lang); location.reload(); }
function effectiveTheme(){
  const t=document.documentElement.dataset.theme;
  if(t) return t;
  return (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
}
function setTheme(theme){
  // 切换外观时颜色渐变过去，而不是整页一闪；只在切换那一下开过渡，平时不开
  const root=document.documentElement;
  if(!REDUCED.matches){
    root.classList.add('theme-anim');
    clearTimeout(window._themeT); window._themeT=setTimeout(()=>root.classList.remove('theme-anim'),420);
  }
  if(theme==='auto'){ setPref('jobs_theme',''); delete document.documentElement.dataset.theme; }
  else { setPref('jobs_theme',theme); document.documentElement.dataset.theme=theme; }
  syncThemeUi();
}
function syncThemeUi(){
  const choice=document.documentElement.dataset.theme||'auto';
  document.querySelectorAll('[data-theme-choice]').forEach(b=>{
    const on=b.dataset.themeChoice===choice;
    b.classList.toggle('on',on); b.setAttribute('aria-pressed',on?'true':'false');
  });
}
if(window.matchMedia){
  const mq=matchMedia('(prefers-color-scheme: dark)');
  if(mq.addEventListener) mq.addEventListener('change',syncThemeUi);
}

/* ---- 顶栏「显示」菜单 ------------------------------------------------
   语言 + 外观的弹出层（披露式按钮，不是 ARIA menu：里面是普通的切换按钮）。
   点外面 / Esc / 焦点离开都会收起；Esc 把焦点还给「显示」按钮。 */
function displayParts(){ return [document.getElementById('display-btn'), document.getElementById('display-pop')]; }
function toggleDisplay(force){
  const [btn,pop]=displayParts(); if(!btn || !pop) return;
  const open=(force===undefined) ? pop.hidden : !!force;
  if(open===!pop.hidden) return;
  pop.hidden=!open;
  btn.setAttribute('aria-expanded',open?'true':'false');
  if(open){ const on=pop.querySelector('button.on')||pop.querySelector('button'); if(on) on.focus(); }
}
document.addEventListener('click',ev=>{
  const box=document.getElementById('display-menu');
  if(box && ev.target instanceof Node && !box.contains(ev.target)) toggleDisplay(false);
});
document.addEventListener('focusout',ev=>{
  const box=document.getElementById('display-menu');
  if(!box || !box.contains(ev.target)) return;
  if(ev.relatedTarget && box.contains(ev.relatedTarget)) return;
  if(ev.relatedTarget) toggleDisplay(false);
},true);
document.addEventListener('keydown',ev=>{
  if(ev.key!=='Escape') return;
  const [btn,pop]=displayParts();
  if(pop && !pop.hidden){ toggleDisplay(false); if(btn) btn.focus(); }
});

/* ---- 详情：复制岗位 ----------------------------------------------------
   文本由服务端从存储字段生成（UID、标题、公司、地点、链接、完整 JD），放在按钮
   转义后的 data-copy-text 属性里；这里不读渲染后的正文 DOM。先试 Clipboard API；HTTP 等非安全上下文
   或被拒时退到 execCommand；仍不行就打开手动复制框（已全选）。只读，不改岗位和标注；
   不移动页面选区，焦点回到按钮。 */
let COPYING=false;
function copySource(btn){
  const el=btn && btn.dataset && btn.dataset.copyText!==undefined ? btn : document.querySelector('#detail [data-copy-text]');
  const v=el && el.dataset ? el.dataset.copyText : undefined;
  return typeof v==='string' && v ? v : null;
}
function legacyCopy(text){
  if(!document.execCommand) return false;
  const sel=document.getSelection ? document.getSelection() : null;
  const ranges=[]; if(sel) for(let i=0;i<sel.rangeCount;i++) ranges.push(sel.getRangeAt(i));
  const active=document.activeElement;
  const ta=document.createElement('textarea');
  ta.value=text; ta.readOnly=true; ta.tabIndex=-1; ta.setAttribute('aria-hidden','true');
  ta.style.cssText='position:fixed;top:0;left:-9999px;width:1px;height:1px;opacity:0';
  document.body.appendChild(ta);
  let ok=false;
  try{ ta.focus({preventScroll:true}); ta.select(); ok=!!document.execCommand('copy'); }catch(e){ ok=false; }
  ta.remove();
  if(sel){ sel.removeAllRanges(); ranges.forEach(r=>sel.addRange(r)); }
  if(active && active!==document.body && active.focus) active.focus({preventScroll:true});
  return ok;
}
async function copyJob(btn){
  if(COPYING) return;
  const text=copySource(btn);
  if(text===null){ toast(T('js.copy_missing')); return; }
  COPYING=true;
  try{
    let ok=false;
    const clip=typeof navigator!=='undefined' && navigator.clipboard;
    if(clip && clip.writeText && window.isSecureContext){
      try{ await clip.writeText(text); ok=true; }catch(e){ ok=false; }
    }
    if(!ok) ok=legacyCopy(text);
    if(ok){ toast(T('js.copy_done')); copiedMark(btn); }
    else openCopyDialog(text,btn);
  }finally{ COPYING=false; }
}
/* 成功也在按钮上短暂标一下：同一时刻的其它提示（例如失焦保存备注）可能顶掉 toast */
function copiedMark(btn){
  if(!btn || !btn.classList) return;
  if(btn.dataset.label===undefined) btn.dataset.label=btn.textContent;
  btn.textContent=T('js.copy_done_short'); btn.classList.add('copied');
  clearTimeout(btn._copiedT);
  btn._copiedT=setTimeout(()=>{ btn.textContent=btn.dataset.label; btn.classList.remove('copied'); },1600);
}
let COPY_RETURN=null;
function copyParts(){
  const box=document.getElementById('copy-modal');
  return box ? [box, box.querySelector('#copy-modal-text')] : [null,null];
}
function selectCopyText(){
  const [,ta]=copyParts(); if(!ta) return;
  ta.focus({preventScroll:true}); ta.select(); ta.scrollTop=0;
}
function openCopyDialog(text,btn){
  const [box,ta]=copyParts(); if(!box || !ta){ toast(T('js.copy_missing')); return; }
  COPY_RETURN=btn||null;
  ta.value=text;
  reveal(box);
  selectCopyText();
}
function closeCopyDialog(){
  const [box]=copyParts(); if(!box || !shown(box)) return;
  conceal(box);
  const back=COPY_RETURN; COPY_RETURN=null;
  if(back && back.isConnected && back.focus) back.focus({preventScroll:true});
}
document.addEventListener('DOMContentLoaded',()=>{
  const [box]=copyParts(); if(!box || !box.querySelector) return;
  const pick=box.querySelector('[data-copy-select]'), close=box.querySelector('[data-copy-close]');
  if(pick) pick.addEventListener('click',selectCopyText);
  if(close) close.addEventListener('click',closeCopyDialog);
  box.addEventListener('click',ev=>{ if(ev.target===box) closeCopyDialog(); });
  box.addEventListener('keydown',ev=>{
    if(ev.key==='Escape'){ ev.preventDefault(); ev.stopPropagation(); closeCopyDialog(); return; }
    if(ev.key!=='Tab') return;
    const items=[box.querySelector('#copy-modal-text'),pick,close].filter(Boolean);
    const i=items.indexOf(document.activeElement);
    ev.preventDefault();
    items[(i + (ev.shiftKey ? items.length-1 : 1) + items.length) % items.length].focus();
  });
});

/* ---- 详情：技能编辑的展开 ---------------------------------------------- */
function toggleSkillEdit(btn){
  const box=document.getElementById('skill-edit'); if(!box) return;
  const open=!shown(box);
  if(open) reveal(box); else conceal(box);
  btn.setAttribute('aria-expanded',open?'true':'false');
  if(open){ const i=skillInput(); if(i) i.focus(); }
}

fitLayout();   // 脚本在 body 末尾，元素已就绪，先跑一次减少首帧跳动

/* Manual updates never reload the page: unsaved notes stay in the editor.
   Only enum codes and numeric counts are displayed from runtime status. */
let UPDATE_TIMER=null, UPDATE_REQUESTING=false, UPDATE_POLLING=false, UPDATE_STOPPED=false, UPDATE_REVISION=0;
let UPDATE_REJECTION=null, UPDATE_LAST_STARTED=null;
const UPDATE_TEXT_PREFIX='js.update_', UPDATE_STAGE_PREFIX='js.update_stage_', FETCH_TEXT_PREFIX='set.fetch_';
const UPDATE_STATES=['idle','running','success','partial','error','busy','timeout','interrupted','disabled','unknown'];
const UPDATE_CONFIG_ERRORS=['config_invalid','config_unavailable','reload_required','no_sources','demo_disabled','storage_unavailable'];
function updateNode(tag, cls, text){
  const node=document.createElement(tag);
  if(cls) node.className=cls;
  if(text!==undefined) node.textContent=text;
  return node;
}
async function updateJson(url, options){
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),10000);
  try{
    const response=await fetch(url,{...options,signal:controller.signal,cache:'no-store',credentials:'same-origin'});
    const data=await response.json();
    if(!data || typeof data!=='object' || Array.isArray(data)) throw new Error('invalid response');
    // A conflicting start still returns the authoritative running/busy state.
    if(!response.ok && !data.state) throw new Error('unavailable');
    return data;
  }finally{clearTimeout(timer);}
}
function updateTime(value){
  if(!value) return '';
  const date=new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(document.documentElement.lang||undefined);
}
let UPDATE_BASELINE=window.__RESULT_GENERATION__||'', UPDATE_PENDING=null, UPDATE_APPLYING=false;
let UPDATE_OWN_TASK=null;
function hasDetailDraft(){
  return Array.from(document.querySelectorAll('.note input')).some(el=>el.dataset.noteLoaded!==undefined && el.value!==el.dataset.noteLoaded) ||
    !!(skillInput() && skillInput().value.trim());
}
async function allowDetailNavigation(){
  NAV_GATES+=1;
  // Retain results even if a label finishes and releases its lock while notes
  // or skills are still saving. A failed label must keep this navigation here.
  const enteringLabels=Array.from(LABEL_WRITES.values());
  try{
    if(SKILL_REQUEST){
      SKILL_NAV_WAITING=true;
      await SKILL_REQUEST;
      SKILL_NAV_WAITING=false;
    }
    if(NOTE_REQUESTS.size) await Promise.allSettled(Array.from(NOTE_REQUESTS));
    if(enteringLabels.length || LABEL_WRITES.size){
      const saved=await Promise.all(enteringLabels.concat(Array.from(LABEL_WRITES.values())));
      if(saved.some(ok=>!ok))return false;
    }
    return !hasDetailDraft() || window.confirm(T('js.unsaved_leave'));
  }finally{NAV_GATES-=1;}
}
function rememberListPosition(){
  const list=document.querySelector('.list');if(!list)return;
  const cards=Array.from(list.querySelectorAll('.card'));
  const boundary=list.getBoundingClientRect().top;
  const anchor=cards.find(el=>el.getBoundingClientRect().bottom>boundary);
  try{sessionStorage.setItem('jobs.result.position',JSON.stringify({url:location.pathname+location.search,
    uid:anchor?anchor.dataset.uid:null,delta:anchor?anchor.getBoundingClientRect().top-boundary:0,
    top:list.scrollTop,windowY:window.scrollY}));}catch(e){}
}
function applyLatestResults(hash){
  if(!UPDATE_PENDING || UPDATE_APPLYING || document.hidden || NOTE_REQUESTS.size || LABEL_WRITES.size || SKILL_SAVING || (hash===undefined && (hasDetailDraft() || (FILTER_DRAFT && filterDraftCount()))))return;
  UPDATE_APPLYING=true;
  UPDATE_BASELINE=UPDATE_PENDING;UPDATE_PENDING=null;
  rememberListPosition();
  if(hash!==undefined)DETAIL_LEAVING=true;
  // assign forces a new document even when only the hash would otherwise change.
  const url=new URL(location.href);url.hash=hash===undefined?location.hash:hash;
  if(url.href===location.href) location.reload();
  else {history.replaceState(null,'',url.href);location.reload();}
}
function isRunStale(data){
  const seconds=(Date.now()-new Date(data.finished_at).getTime())/1000;
  return data.state!=='running' && !!data.finished_at && Number.isFinite(seconds)
    && seconds>=window.__STALE_AFTER_DAYS__*86400;
}
function relativeRunTime(data){
  const stamp=data.finished_at;
  if(data.state==='running') return T('js.update_running',{stage:T('js.update_stage_'+(data.stage||'wait'))});
  if(!stamp)return T('run.never');
  const seconds=Math.max(0,(Date.now()-new Date(stamp).getTime())/1000);
  if(!Number.isFinite(seconds)) return '';
  const unit=seconds<3600?'minutes':seconds<86400?'hours':'days';
  const n=Math.max(1,Math.floor(seconds/(unit==='minutes'?60:unit==='hours'?3600:86400)));
  let text=T('run.ago',{ago:T('ago.'+unit,{n})});
  if(isRunStale(data))text+=T('run.stale');
  if(data.state!=='success'){
    const short='run.state_'+data.state;
    text+=' · '+(Object.prototype.hasOwnProperty.call(I18N,short) ? T(short) : T('js.update_'+data.state));
  }
  return text;
}
function renderUpdateState(data, fromPoll=false){
  const started=data.started_at||null;
  if(fromPoll && UPDATE_REJECTION){
    if(data.state!=='running' && started===UPDATE_REJECTION.priorStarted) data=UPDATE_REJECTION.data;
    else UPDATE_REJECTION=null;
  }
  if(fromPoll) UPDATE_LAST_STARTED=started;
  const button=document.getElementById('update-start'),status=document.getElementById('update-status');
  if(!button || !status)return;
  const settings=document.body.classList.contains('settings');
  if(settings){
    const generation=data.generation||'';
    if(window.__SETTINGS_GENERATION__===undefined)window.__SETTINGS_GENERATION__=generation;
    else if(generation && generation!==window.__SETTINGS_GENERATION__){
      window.__SETTINGS_GENERATION__=generation;
      document.dispatchEvent(new CustomEvent('job-results-updated'));
    }
  }
  const state=UPDATE_STATES.includes(data.state)?data.state:'disabled';
  const stage=['fetch','details','analyze','report'].includes(data.stage)?data.stage:'wait';
  let message=T('js.update_'+state,{stage:T('js.update_stage_'+stage)});
  if(UPDATE_CONFIG_ERRORS.includes(data.error))message=T('js.update_'+data.error);
  if(data.generation && data.generation!==UPDATE_BASELINE && !settings) UPDATE_PENDING=data.generation;
  // Only the tab which initiated this precise attempt receives completion feedback.
  if(UPDATE_OWN_TASK && data.task_id===UPDATE_OWN_TASK && ['success','partial','error','timeout','interrupted'].includes(state)){
    toast(message);UPDATE_OWN_TASK=null;
  }
  status.textContent=settings?message:(UPDATE_PENDING?T('js.update_pending'):UPDATE_CONFIG_ERRORS.includes(data.error)?message:'');
  button.disabled=state==='running'||state==='disabled'||UPDATE_REQUESTING;
  button.setAttribute('aria-busy',state==='running'?'true':'false');
  document.querySelectorAll('[data-run-time]').forEach(el=>{
    const stale=isRunStale(data);
    el.classList.toggle('stale',stale);
    el.dataset.runTime=data.finished_at||'';el.dataset.runState=data.state||'idle';
    el.textContent='';
    if(stale){const dot=document.createElement('span');dot.className='dot bad';dot.setAttribute('aria-hidden','true');el.appendChild(dot);}
    const text=document.createElement('span');text.textContent=relativeRunTime(data);el.appendChild(text);
  });
  const meta=[];
  const task=document.getElementById('update-task');
  if(task){task.textContent=data.task_id?T('js.update_task',{id:data.task_id}):'';
    if(task.parentElement)task.parentElement.hidden=!data.task_id;}
  if(data.trigger)meta.push(T('js.update_trigger',{source:T('js.update_trigger_'+data.trigger)}));
  const startedTime=updateTime(data.started_at),finished=updateTime(data.finished_at);
  if(startedTime)meta.push(T('js.update_started',{time:startedTime}));
  if(finished)meta.push(T('js.update_finished',{time:finished}));
  if(data.counts && (startedTime || finished)){
    const counts={};for(const k of ['seen','inserted','updated','details_fetched'])counts[k]=Number.isSafeInteger(data.counts[k])&&data.counts[k]>=0?data.counts[k]:0;
    meta.push(T('js.update_counts',counts));
  }
  const names={seek:'SEEK',indeed:'Indeed',linkedin:'LinkedIn'};
  const failed=Array.isArray(data.failed_sources)?data.failed_sources.filter(s=>Object.hasOwn(names,s)):[];
  if(failed.length)meta.push(T('js.update_failed_sources',{sources:failed.map(s=>names[s]).join(', ')}));
  const detail=document.getElementById('update-meta');if(detail)detail.textContent=meta.join(' · ');
  if(UPDATE_PENDING && !OPEN_UID)applyLatestResults();
}

function updateConnectionLost(){
  const button=document.getElementById('update-start');
  if(!button) return;
  button.disabled=true;button.setAttribute('aria-busy','false');
  document.getElementById('update-status').textContent=T('js.update_unavailable');
}
function scheduleUpdatePoll(delay){
  clearTimeout(UPDATE_TIMER);
  if(!UPDATE_STOPPED) UPDATE_TIMER=setTimeout(pollUpdateState,delay);
}
async function pollUpdateState(){
  if(!document.getElementById('update-start') || UPDATE_STOPPED) return;
  if(document.hidden){scheduleUpdatePoll(5000);return;}
  if(UPDATE_POLLING || UPDATE_REQUESTING){scheduleUpdatePoll(1000);return;}
  UPDATE_POLLING=true;
  const revision=UPDATE_REVISION;
  let delay=10000;
  try{
    const data=await updateJson('/api/update-state');
    if(UPDATE_REQUESTING || revision!==UPDATE_REVISION) return;
    renderUpdateState(data,true);
    delay=data.state==='running'?1500:10000;
  }catch(e){if(!UPDATE_REQUESTING && revision===UPDATE_REVISION) updateConnectionLost();}
  finally{UPDATE_POLLING=false;scheduleUpdatePoll(delay);}
}
async function startJobUpdate(){
  if(UPDATE_REQUESTING) return;
  UPDATE_REQUESTING=true;UPDATE_REVISION++;UPDATE_REJECTION=null;clearTimeout(UPDATE_TIMER);
  const button=document.getElementById('update-start');
  button.disabled=true;button.setAttribute('aria-busy','true');
  document.getElementById('update-status').textContent=T('js.update_starting');
  try{
    const data=await updateJson('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(data.accepted===false && data.state!=='running'){
      UPDATE_REJECTION={data,priorStarted:UPDATE_LAST_STARTED};
    }
    if(data.accepted===true)UPDATE_OWN_TASK=data.task_id;
    UPDATE_REQUESTING=false;renderUpdateState(data);
  }catch(e){updateConnectionLost();}
  finally{UPDATE_REQUESTING=false;scheduleUpdatePoll(1500);}
}
/* 「更新岗位」会联网、要几分钟，所以先确认。确认窗只是挡在 startJobUpdate 前面：
   按钮的禁用、请求和轮询仍走原来的状态机。确认时按钮若已被禁用（例如别的
   页面刚开始更新），就不再发起。 */
function updateConfirmEl(){ return document.getElementById('update-confirm'); }
function requestJobUpdate(){
  const box=updateConfirmEl();
  if(!box){ startJobUpdate(); return; }
  reveal(box);
  const cancel=box.querySelector('[data-update-cancel]'); if(cancel) cancel.focus();
}
function closeUpdateConfirm(start){
  const box=updateConfirmEl(); if(!box || !shown(box)) return;
  if(start){   // 更新按钮运行期间会禁用、接不住焦点：先把焦点移到可见的状态文字，再收起对话框
    const status=document.getElementById('update-status');
    if(status && status.focus) status.focus();
  }
  conceal(box);
  const button=document.getElementById('update-start');
  if(start && button && !button.disabled) startJobUpdate();
  else if(button && !button.disabled && button.getClientRects && button.getClientRects().length) button.focus();
}
document.addEventListener('DOMContentLoaded',()=>{
  const button=document.getElementById('update-start');
  if(button){button.addEventListener('click',requestJobUpdate);pollUpdateState();}
  const box=updateConfirmEl();
  if(!box || !box.querySelector) return;
  const ok=box.querySelector('[data-update-ok]'), cancel=box.querySelector('[data-update-cancel]');
  if(ok) ok.addEventListener('click',()=>closeUpdateConfirm(true));
  if(cancel) cancel.addEventListener('click',()=>closeUpdateConfirm(false));
  box.addEventListener('click',ev=>{ if(ev.target===box) closeUpdateConfirm(false); });
  box.addEventListener('keydown',ev=>{
    if(ev.key==='Escape'){ ev.preventDefault(); ev.stopPropagation(); closeUpdateConfirm(false); return; }
    if(ev.key!=='Tab') return;
    const items=[cancel,ok].filter(Boolean);
    const i=items.indexOf(document.activeElement);
    ev.preventDefault();
    items[(i + (ev.shiftKey ? items.length-1 : 1)) % items.length].focus();
  });
});
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden && document.getElementById('update-start'))pollUpdateState();
});
window.addEventListener('pagehide',()=>{UPDATE_STOPPED=true;clearTimeout(UPDATE_TIMER);});
window.addEventListener('pageshow',()=>{if(UPDATE_STOPPED){UPDATE_STOPPED=false;pollUpdateState();}});

// Preserve the original URL for ordinary page navigation, including query/hash.
// Modified clicks retain native browser behavior and do not disturb this tab.
document.addEventListener('click',event=>{
  const link=event.target.closest('a[href]');
  if(!link || event.defaultPrevented || event.button!==0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target==='_blank')return;
  if(!NOTE_REQUESTS.size && !LABEL_WRITES.size && !SKILL_REQUEST && !hasDetailDraft())return;
  event.preventDefault();
  allowDetailNavigation().then(allowed=>{if(allowed){DETAIL_LEAVING=true;location.href=link.href;}});
});
let DETAIL_LEAVING=false;
window.addEventListener('beforeunload',event=>{
  if(!DETAIL_LEAVING && (NOTE_REQUESTS.size || LABEL_WRITES.size || hasDetailDraft() || SKILL_SAVING)){event.preventDefault();event.returnValue='';}
});
window.addEventListener('hashchange',()=>{
  if(!document.getElementById('detail'))return;
  const h=readDetailHash();
  if(h.uid)openJob(h.uid,h.id);else if(OPEN_UID)closeDetail();
});
document.addEventListener('DOMContentLoaded',()=>{
  let saved=null;
  try{saved=JSON.parse(sessionStorage.getItem('jobs.result.position')||'null');sessionStorage.removeItem('jobs.result.position');}catch(e){}
  if(saved && saved.url===location.pathname+location.search){requestAnimationFrame(()=>{
    const list=document.querySelector('.list');
    const anchor=Array.from(document.querySelectorAll('.card')).find(el=>el.dataset.uid===saved.uid);
    if(list){list.scrollTop=saved.top||0;if(anchor)list.scrollTop+=anchor.getBoundingClientRect().top-list.getBoundingClientRect().top-(saved.delta||0);}
    window.scrollTo(0,saved.windowY||0);
  });}
});

async function navigatePanel(target){
  if(!await allowDetailNavigation())return;
  DETAIL_LEAVING=true;location.href=target;
}
