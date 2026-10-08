const LABELS = window.__LABELS__ || {};
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
/* 三个圆点：可投性 · 兴趣 · 投递进度，固定顺序；空心 = 未标注。
   之前只在标了某几项时显示一个文字徽标，看不出到底标的是哪一项。 */
const AXES=['eligibility','interest','action'];
const TONE={eligible:'good',ineligible:'bad',want:'good',no:'bad',applied:'good',skipped:'bad'};
function dotClass(v){ return 'dot '+(v ? (TONE[v]||'mid') : 'unset'); }
function dotsTitle(s){
  return AXES.map(k=>T('field.'+k)+T('js.colon')+(s[k] ? T('value.'+k+'.'+s[k]) : T('dots.unset')))
             .join(T('js.dots_sep'));
}
function refreshDot(uid){
  const s=LABELS[uid]||{};
  document.querySelectorAll('.dots[data-uid="'+uid+'"]').forEach(d=>{
    d.querySelectorAll('.dot').forEach(el=>{ setDot(el, dotClass(s[el.dataset.axis])); });
    const title=dotsTitle(s); d.title=title; d.setAttribute('aria-label',title);
  });
  // 详情里每条轴标签前的同款圆点
  AXES.forEach(k=>{
    const el=document.querySelector('#detail [data-axis-dot="'+k+'"]');
    if(el && document.querySelector('#detail [data-uid="'+uid+'"]')) setDot(el, dotClass(s[k]));
  });
}
/* 圆点状态变化时轻轻弹一下，让人看到「标注落到了哪一颗」 */
function setDot(el, cls){
  if(el.className.replace(/\s*pop\b/,'')===cls) return;
  el.className=cls;
  if(REDUCED.matches) return;
  void el.offsetWidth; el.classList.add('pop');
  el.addEventListener('animationend',()=>el.classList.remove('pop'),{once:true});
}
async function openJob(uid){
  document.querySelectorAll('.card').forEach(c=>c.classList.toggle('sel',c.dataset.uid===uid));
  history.replaceState(null,'','#'+encodeURIComponent(uid));
  const pane=document.getElementById('detail');
  const seq=(OPEN_SEQ+=1);
  // 请求很快时不闪「载入中」：160ms 内回来就直接换内容，慢了才显示骨架屏
  const slow=setTimeout(()=>{ if(seq===OPEN_SEQ) showSkeleton(pane); },160);
  if(document.body.classList.contains('portrait') && (!pane.classList.contains('open') || pane.classList.contains('leaving'))){
    pane.classList.add('open'); pane.classList.remove('leaving');
    reveal(pane);                                   // 竖屏：详情从右侧滑入
  }
  try{
    const r=await fetch('/api/job?uid='+encodeURIComponent(uid));
    if(!r.ok) throw new Error(await r.text());
    const d=await r.json();
    if(seq!==OPEN_SEQ) return;                      // 连点时只认最后一次
    clearTimeout(slow);
    pane.innerHTML=d.html;
    pane.querySelectorAll('.note input').forEach(noteSync);
    pane.classList.add('open');
    pane.scrollTop=0;
    swapIn(pane);
  }catch(e){
    clearTimeout(slow);
    if(seq!==OPEN_SEQ) return;
    pane.innerHTML='';
    const d=document.createElement('div'); d.className='placeholder';
    d.textContent=T('js.load_failed')+e.message; pane.appendChild(d);
  }
}
let OPEN_SEQ=0;
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
function closeDetail(){
  OPEN_SEQ+=1; // Ignore pending responses and skeleton timers after closing.
  const pane=document.getElementById('detail');
  if(!document.body.classList.contains('portrait') || REDUCED.matches){ pane.classList.remove('open'); return; }
  conceal(pane, ()=>{ pane.hidden=false; pane.classList.remove('open'); });
}
async function setLabel(uid,field,value,btn){
  const wasOn=btn.classList.contains('on');
  try{
    const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({uid:uid,field:field,value:wasOn?null:value})});
    if(!r.ok) throw new Error(await r.text());
  }catch(e){toast(T('js.label_failed')+e.message);return;}
  document.querySelectorAll('[data-uid="'+uid+'"][data-field="'+field+'"]')
    .forEach(b=>{ const on=(b===btn&&!wasOn); b.classList.toggle('on',on); b.setAttribute('aria-pressed',on?'true':'false'); });
  LABELS[uid]=LABELS[uid]||{}; LABELS[uid][field]= wasOn?null:value;
  refreshDot(uid);
  if(field==='eligibility' && !wasOn && value==='ineligible') toast(T('js.miss_hint'));
  else toast(wasOn ? T('js.unlabelled') : T('js.labelled')+' · '+T('field.'+field)+T('js.colon')+T('value.'+field+'.'+value));
}
async function setNote(input){
  const uid=input.dataset.uid;
  // 落库前把界面显示值映射回 canonical：默认标签用中文原文，自定义内容原样，
  // 服务端仍走 labels.normalize_note 做最终规范化。显示值本身不入库。
  const payload=noteCanonicalize(input);
  try{
    const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({uid:uid,field:'note',value:payload||null})});
    if(!r.ok) throw new Error(await r.text());
    // 服务端回传 canonical；LABELS 存它，输入框按当前语言显示。
    const d=await r.json();
    const saved=d.value||'';
    noteAdopt(input,saved);
    LABELS[uid]=LABELS[uid]||{}; LABELS[uid].note=saved||null; refreshDot(uid);
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
  if(input.dataset.noteLoaded!==undefined && input.value===input.dataset.noteLoaded) return;
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
/* 下拉菜单定位。**必须用 visualViewport**：iOS 弹键盘时 layout viewport 不变
   （window.innerHeight 也不变），屏幕下沿已经被键盘盖住；用 innerHeight 算
   「下方够不够」会以为够，菜单就排到键盘背后 —— 现象正是「闪一下没了 /
   被上推遮住输入框」。visualViewport 给的是真正可见的那块，按它选上/下，
   两边都不够时压缩高度，始终不盖住输入框。 */
function placeMenu(input,menu,opts){
  opts=opts||{};
  const gap=opts.gap||4;
  const minWidth=opts.minWidth||200;
  const vv=window.visualViewport;
  // 统一换算到 **可见视口坐标**。iOS 弹键盘时 fixed 元素按可见视口定位，而
  // getBoundingClientRect 给的是 layout 坐标，差值就是 offsetTop/offsetLeft。
  // 页面在顶部时 iOS 只能平移可见视口，差值最大 —— 不换算就会整体上移盖住
  // 输入框；先手动下滑过之后差值归 0，所以那种情况看着正常。桌面无键盘时
  // offset 恒为 0，退化成原来的行为。
  const ox=vv?vv.offsetLeft:0, oy=vv?vv.offsetTop:0;
  const viewW=vv?vv.width:window.innerWidth;
  const viewH=vv?vv.height:window.innerHeight;
  const r=input.getBoundingClientRect();
  const left=r.left-ox, top=r.top-oy, bottom=r.bottom-oy;
  menu.style.left=Math.max(4,left)+'px';
  menu.style.width=Math.min(Math.max(minWidth,r.width),viewW-8)+'px';
  menu.style.maxHeight='';                 // 恢复 CSS 上限，才能量到真实高度
  menu.classList.add('open');              // 先显示才能量高度
  const h=menu.offsetHeight;
  const below=viewH-bottom-gap;
  const above=top-gap;
  if(h<=below){                            // 下方够：放下面
    menu.style.top=(bottom+gap)+'px';
  } else if(h<=above){                     // 上方够：放上面
    menu.style.top=(top-h-gap)+'px';
  } else if(below>=above){                 // 两边都不够：挑大的一侧并压缩高度
    const avail=Math.max(96,below);
    menu.style.maxHeight=avail+'px';
    menu.style.top=Math.min(bottom+gap,viewH-avail)+'px';
  } else {
    const avail=Math.max(96,above);
    menu.style.maxHeight=avail+'px';
    menu.style.top=Math.max(gap,top-avail-gap)+'px';
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
    return;
  }
  document.body.classList.remove('portrait');
  placeFilters(false);
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
  Q_TIMER=setTimeout(applyFilter,380);
}
function searchStart(ev){ clearTimeout(Q_TIMER); imeStart(ev.target); }
function searchEnd(ev){
  imeEnd(ev.target);
  clearTimeout(Q_TIMER);
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
  document.getElementById('skill-panel').classList.toggle('open');
}
function skillPanelClose(){
  const p=document.getElementById('skill-panel');
  if(p) p.classList.remove('open');
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
   复用备注候选表那套形态（输入即过滤、候选可滚、点选生效），但语义不同：
   点候选 = 把输入框里的词作为该技能的**别名**；回车 = **新增**一个技能。
   落库前由 skills.apply_edit 校验（同名、重复、超长都会被打回）。 */
const SKILLS = window.__SKILLS__ || [];
function skillInput(){ return document.getElementById('skill-q'); }
function skillMenu(){ return document.getElementById('skill-menu'); }
function skillRender(){
  const input=skillInput(); if(!input) return;
  const m=skillMenu(); if(!m) return;
  const rows=[];
  if(input.value.trim())
    rows.push({kind:'add', label:T('js.skill_add',{token:input.value.trim()}), target:''});
  // **候选不按输入过滤**：词表已按字母排好，滚一遍就能找到。之前按输入筛，
  // 想打 "Amazon Web Service" 去认 AWS 时，列表会被逐字筛空，反而找不到。
  // 候选标签带上别名，扫一眼就知道是不是它。
  SKILLS.forEach(s=>{
    rows.push({kind:'alias', label:T('js.skill_as_alias',{name:s.name}), target:s.name});
  });
  m.innerHTML=''; m.scrollTop=0;
  if(!rows.length){
    const d=document.createElement('div'); d.className='note-empty';
    d.textContent=T('js.skill_empty'); m.appendChild(d);
  }
  rows.slice(0,400).forEach(r=>{
    const d=document.createElement('div'); d.className='note-item'; d.textContent=r.label;
    // mousedown + preventDefault：和备注候选一样，别让输入框失焦导致菜单先被收掉
    d.addEventListener('mousedown',ev=>{ev.preventDefault(); skillPick(r.kind,r.target);});
    m.appendChild(d);
  });
  placeMenu(input,m,{minWidth:240});
}
let SKILL_SAVING=false;
function skillStatus(text){
  const s=document.getElementById('skill-status');
  if(s) s.textContent=text||'';
}
/* 失焦收起候选。点候选用的是 mousedown+preventDefault，不会触发 blur，
   所以这里只会被「点到别处 / Tab 走」触发。 */
function skillBlur(){ skillMenuClose(); }
async function skillPick(kind,target){
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
  const verb = kind==='add' ? T('js.skill_added',{token:token}) : T('js.skill_aliased',{token:token,name:target});
  try{
    const r=await fetch('/api/skills',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:token,action:(kind==='add'?'add':'alias'),target:target})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    skillStatus(T('js.skill_recalc'));
    toast(verb);
    // 重算 + 整页重载要点时间；先让状态和 toast 露个面，否则一刷新就看不见了
    setTimeout(()=>location.reload(), 300);
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
  if(ev.key==='Enter'){ ev.preventDefault(); skillPick('add',''); }
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
    if(el.value) p.append(k, el.value);
  });
  return p;
}
/* 所有筛选控件共用这一套：data-fkey -> URL 参数。
   **不要给排序另起状态管理**，否则 URL 里会有两个真相。 */
function applyFilter(){
  clearTimeout(Q_TIMER);
  const view=window.__VIEW__||'recommend';
  const p=viewParams();
  const st=filterStore();
  const s=p.toString();
  if(s) st[view]=s; else delete st[view];
  writeFilterStore(st);
  p.set('view',view);
  location.href='/'+ (p.toString()?'?'+p.toString():'');
}
/* 清除筛选 = 回到该视图的默认口径，并忘掉记住的那套 */
function clearFilter(){
  const view=window.__VIEW__||'recommend';
  const st=filterStore(); delete st[view]; writeFilterStore(st);
  location.href='/?view='+encodeURIComponent(view);
}
/* 点导航切换视图：套上那个视图自己记住的筛选条件。
   修饰键 / 中键点仍然走原生链接（新标签打开），所以 href 是真的能用。 */
function gotoView(view,ev){
  if(ev && (ev.metaKey||ev.ctrlKey||ev.shiftKey||ev.altKey)) return true;
  if(ev) ev.preventDefault();
  const s=filterStore()[view]||'';
  location.href='/?view='+encodeURIComponent(view)+(s?'&'+s:'');
  return false;
}
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.dots').forEach(d=>refreshDot(d.dataset.uid));
  const h=decodeURIComponent(location.hash.slice(1));
  if(h) openJob(h);
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
});
// 技能筛选面板在失焦时也要收起（Tab 走、焦点落到别处）。面板**内部**移动
// 焦点不算 —— 勾选项之间来回点会一直触发 focusout，那样面板就选不成了。
document.addEventListener('focusout',ev=>{
  const w=document.getElementById('skillpick');
  if(!w || !w.contains(ev.target)) return;
  if(ev.relatedTarget && w.contains(ev.relatedTarget)) return;
  skillPanelClose();
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
    if(si && active===si) placeMenu(si,sm,{minWidth:240});
    else skillMenuClose();
  }
  const ni=document.querySelector('.note input'), nm=noteMenu();
  if(nm && nm.classList.contains('open')){
    if(ni && active===ni) placeMenu(ni,nm,{minWidth:200});
    else noteClose();
  }
  skillPanelClose();          // 技术栈多选在顶栏，任何页面滚动都该收起
}
document.addEventListener('scroll',ev=>{
  // 菜单自己滚动时不能收，否则滚轮一动面板就没了。技能候选 / 技术栈多选
  // 面板也必须豁免 —— 之前只判了 #note-menu，所以技术栈多选根本滚不动。
  const t=ev.target instanceof Node ? ev.target : null;
  const menus=[noteMenu(), document.getElementById('skill-menu'),
               document.getElementById('skill-panel')].filter(Boolean);
  if(t && menus.some(el=>el===t || el.contains(t))) return;
  syncMenusOnScroll();
},true);
window.addEventListener('resize',()=>{
  skillMenuClose(); noteClose(); skillPanelClose(); fitLayout();
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
function toggleMore(force){
  const p=document.getElementById('more-panel'); if(!p) return;
  const open = (force===undefined) ? !shown(p) : !!force;
  if(open===shown(p)) return;
  moreButtons().forEach(b=>b.setAttribute('aria-expanded',open?'true':'false'));
  if(open){ document.body.classList.remove('sheet-closing'); document.body.classList.add('sheet-open'); reveal(p); }
  else {
    document.body.classList.add('sheet-closing');
    conceal(p, ()=>document.body.classList.remove('sheet-open','sheet-closing'));
  }
  try{ if(open) sessionStorage.setItem(MORE_KEY,'1'); else sessionStorage.removeItem(MORE_KEY); }catch(e){}
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
/* 移除单个筛选：把对应控件复位，再走同一个 applyFilter（URL 仍是唯一真相） */
function resetControl(key){
  document.querySelectorAll('[data-fkey="'+key+'"]').forEach(el=>{
    if(el.type==='checkbox') el.checked=false; else el.value='';
  });
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
  applyFilter();
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
function toggleTheme(){ setTheme(effectiveTheme()==='dark' ? 'light' : 'dark'); }
function syncThemeUi(){
  const eff=effectiveTheme();
  const label=eff==='dark' ? T('theme.to_light') : T('theme.to_dark');
  document.querySelectorAll('.theme-btn').forEach(b=>{ b.setAttribute('aria-label',label); b.title=label; });
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

/* ---- 详情：? 说明与技能编辑的展开 ------------------------------------- */
function toggleHelp(btn){
  const p=document.getElementById('help-panel'); if(!p) return;
  const open=!shown(p);
  if(open) reveal(p); else conceal(p);
  btn.setAttribute('aria-expanded',open?'true':'false');
}
function toggleSkillEdit(btn){
  const box=document.getElementById('skill-edit'); if(!box) return;
  const open=!shown(box);
  if(open) reveal(box); else conceal(box);
  btn.setAttribute('aria-expanded',open?'true':'false');
  if(open){ const i=skillInput(); if(i) i.focus(); }
}

fitLayout();   // 脚本在 body 末尾，元素已就绪，先跑一次减少首帧跳动
