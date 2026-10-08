/* ---- 设置页：技术栈词表 ----------------------------------------------------
   列表由客户端用 GET /api/skills 的数据渲染。为什么不在服务端也渲染一份：
   那样列表逻辑就有了 Python / JS 两套，迟早走样（和 report.py 与面板共用
   render.py 是同一条理由）。改动后重新取数、只重渲染这一块，不整页 reload。

   每行只有一个铅笔按钮，点开编辑窗：主要展示名（拖拽，**功能暂未实现**）、
   别名增删、删除技能。名称与别名的颜色表示来源：种子（skills.yaml）灰、
   本地（skills.local.yaml）蓝；条目来源三态：种子 / 本地 / 种子+本地。
   写入只走已有的 /api/skills（add / alias / remove），不新增接口。 */
let TREE = window.__SKILL_TREE__ || {skills: []};
let EDITING = null;          // 编辑窗当前打开的技能名
let PRIMARY_PREVIEW = {};    // 拖拽选的展示名（只在本页预览，尚未落库）

function el(tag, cls, text){
  const e=document.createElement(tag);
  if(cls) e.className=cls;
  if(text!==undefined && text!==null) e.textContent=text;
  return e;
}
function srcTip(src){ return T(src==='seed' ? 'set.tip_seed' : 'set.tip_local'); }
function bubble(text, src, extra){
  const b=el('span','bub '+src+(extra?' '+extra:''),text);
  b.title=srcTip(src);
  return b;
}
function sourceBadge(source){
  if(source==='mixed'){
    const w=el('span','srcpair'); w.title=T('set.tip_mixed');
    w.append(el('span','src seed',T('set.seed')), el('span','src local','+'+T('set.local')));
    return w;
  }
  const s=el('span','src '+source,T(source==='seed'?'set.seed':'set.local'));
  s.title=srcTip(source);
  return s;
}
const ICON_PEN='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/></svg>';
const ICON_GRIP='<svg class="grip" width="10" height="14" viewBox="0 0 12 14" fill="currentColor" aria-hidden="true"><circle cx="3" cy="3" r="1.3"/><circle cx="9" cy="3" r="1.3"/><circle cx="3" cy="7" r="1.3"/><circle cx="9" cy="7" r="1.3"/><circle cx="3" cy="11" r="1.3"/><circle cx="9" cy="11" r="1.3"/></svg>';

function currentFilter(){
  const f=document.getElementById('skill-filter');
  return f ? (f.value||'').trim().toLowerCase() : '';
}
function renderTree(data, query){
  const root=document.getElementById('skill-tree'); if(!root) return;
  root.textContent='';
  const q=(query===undefined ? currentFilter() : (query||'').trim().toLowerCase());
  // 前端搜索：名字或任一别名包含即命中，不发请求。
  const list=(data.skills||[]).filter(s=>{
    if(!q) return true;
    const hay=((s.name||'')+' '+((s.aliases||[]).join(' '))).toLowerCase();
    return hay.indexOf(q)>=0;
  });
  if(!list.length){
    root.appendChild(el('div','skempty',(data.skills||[]).length ? T('set.no_match') : T('set.empty')));
    return;
  }
  list.forEach(s=>root.appendChild(skillRow(s)));
}
async function refreshTree(){
  const fresh=await fetch('/api/skills');
  TREE=await fresh.json();
  renderTree(TREE);
  if(EDITING) renderEditor();
}
function skillRow(s){
  const row=el('div','skrow');
  const name=el('span');
  name.append(bubble(s.name, s.origin, 'name'));
  if(s.ambiguous){ const f=el('span','skflags','?'); f.title=T('set.flag_ambiguous'); name.append(f); }
  if(s.has_not){ const f=el('span','skflags','not'); f.title=T('set.flag_not'); name.append(f); }
  const al=el('span','sk-aliases');
  (s.aliases||[]).forEach(a=>al.append(bubble(a, (s.alias_origins||{})[a]||'local')));
  const hits=el('span','skhits',String(s.hits));
  const src=el('span'); src.append(sourceBadge(s.source||s.origin));
  const act=el('span');
  const pen=el('button','pen'); pen.type='button';
  pen.innerHTML=ICON_PEN;
  pen.setAttribute('aria-label',T('set.edit')+': '+s.name); pen.title=T('set.edit');
  pen.onclick=()=>openEditor(s.name);
  act.append(pen);
  row.append(name,al,hits,src,act);
  return row;
}

/* ---- 编辑窗 ---------------------------------------------------------- */
function findSkill(name){ return (TREE.skills||[]).find(s=>s.name===name); }
function openEditor(name){
  EDITING=name;
  const m=document.getElementById('sk-modal'); if(!m) return;
  renderEditor();
  reveal(m);
  const close=m.querySelector('.dialog-head button'); if(close) close.focus();
}
function closeEditor(){
  const m=document.getElementById('sk-modal');
  if(EDITING && PRIMARY_PREVIEW[EDITING] && PRIMARY_PREVIEW[EDITING]!==EDITING) toast(T('set.primary_unsaved'));
  if(EDITING) delete PRIMARY_PREVIEW[EDITING];
  EDITING=null;
  if(m) conceal(m, ()=>{ m.textContent=''; });
}
function renderEditor(){
  const m=document.getElementById('sk-modal'); if(!m) return;
  const s=findSkill(EDITING);
  if(!s){ closeEditor(); return; }
  m.textContent='';
  const dlg=el('div','dialog');
  dlg.setAttribute('role','dialog'); dlg.setAttribute('aria-modal','true');
  dlg.setAttribute('aria-label',T('set.edit_title')+' '+s.name);

  const head=el('div','dialog-head');
  const h=el('h3'); h.append(T('set.edit_title')+' ', el('b',null,s.name));
  const close=el('button','pen','✕'); close.type='button';
  close.setAttribute('aria-label',T('set.close')); close.onclick=closeEditor;
  head.append(h, sourceBadge(s.source||s.origin), close);

  const body=el('div','dialog-body');
  const all=[{n:s.name, src:s.origin, alias:false}].concat(
    (s.aliases||[]).map(a=>({n:a, src:(s.alias_origins||{})[a]||'local', alias:true})));
  const primary=PRIMARY_PREVIEW[s.name]||s.name;
  const pItem=all.find(x=>x.n===primary)||all[0];

  // 主要展示名：把下方名称拖进虚线框（功能暂未实现，只在本页预览）
  const sec1=el('div');
  const t1=el('div','sec-title'); t1.append(T('set.primary'), el('span','notyet',T('set.not_yet')));
  const dz=el('div','dz'); dz.setAttribute('aria-label',T('set.primary'));
  dz.append(bubble(pItem.n, pItem.src), el('small',null,T('set.primary_now')));
  if(primary!==s.name){
    const undo=el('button','linkbtn',T('set.reset_to',{name:s.name})); undo.type='button';
    undo.onclick=()=>{ delete PRIMARY_PREVIEW[s.name]; renderEditor(); };
    dz.append(undo);
  }
  dz.addEventListener('dragover',ev=>{
    ev.preventDefault();
    if(!dz.classList.contains('over')){
      dz.classList.add('over');
      dz.dataset.prev=dz.innerHTML;
      dz.innerHTML=''; dz.append(el('span','drop-text',T('set.drop_here')));
    }
  });
  const leave=(ev)=>{ if(ev && ev.relatedTarget && dz.contains(ev.relatedTarget)) return; if(dz.classList.contains('over')){ dz.classList.remove('over'); dz.innerHTML=dz.dataset.prev||''; } };
  dz.addEventListener('dragleave',leave);
  dz.addEventListener('drop',ev=>{
    ev.preventDefault();
    let n=''; try{ n=ev.dataTransfer.getData('text/plain'); }catch(e){}
    if(n){ PRIMARY_PREVIEW[s.name]=n; renderEditor(); } else leave();
  });
  const guide=el('div','guide'); guide.setAttribute('role','note');
  const g1=el('span','gstep'); g1.innerHTML=ICON_GRIP; g1.append(T('set.guide1'));
  guide.append(g1, el('span','muted','→'), el('span','gstep',T('set.guide2')),
               el('span','muted','→'), el('span','gstep',T('set.guide3')));
  sec1.append(t1, dz, guide, el('p','guide-note',T('set.guide_note')));

  // 其他名称：可拖；点名称 = 设为展示名（触屏 / 键盘替代）；别名带 ×
  const sec2=el('div');
  const others=all.filter(x=>x.n!==primary);
  const t2=el('div','sec-title'); t2.append(T('set.others')+' ', el('span','muted',String(others.length)));
  const list=el('div','dchips');
  others.forEach(x=>{
    const chip=el('span','dchip '+x.src); chip.draggable=true;
    chip.title=srcTip(x.src)+' — '+T('set.drag_tip');
    chip.innerHTML=ICON_GRIP;
    chip.addEventListener('dragstart',ev=>{
      try{ ev.dataTransfer.setData('text/plain',x.n); ev.dataTransfer.effectAllowed='move'; }catch(e){}
      chip.classList.add('dragging'); dz.classList.add('ready');
    });
    chip.addEventListener('dragend',()=>{ chip.classList.remove('dragging'); dz.classList.remove('ready'); });
    const nm=el('button','nm',x.n); nm.type='button';
    nm.setAttribute('aria-label',T('set.set_primary')+': '+x.n);
    nm.onclick=()=>{ PRIMARY_PREVIEW[s.name]=x.n; renderEditor(); };
    chip.append(nm);
    if(x.alias){
      const rm=el('button','x','×'); rm.type='button';
      rm.setAttribute('aria-label',T('set.remove_alias')+': '+x.n);
      rm.onclick=()=>mutate({token:x.n, action:'remove'});
      chip.append(rm);
    }
    list.append(chip);
  });
  const inp=el('input','alias-in');
  inp.placeholder=T('set.alias_placeholder'); inp.autocomplete='off'; inp.spellcheck=false;
  inp.setAttribute('aria-label',T('set.alias_placeholder'));
  inp.onkeydown=(ev)=>{
    if(imeComposing(ev)||imeBusy(inp)||imeJustComposed(inp)) return;
    if(ev.key==='Enter'){
      ev.preventDefault();
      const v=(inp.value||'').trim(); if(!v) return;
      mutate({token:v, action:'alias', target:s.name});
    }
  };
  inp.addEventListener('compositionstart',()=>imeStart(inp));
  inp.addEventListener('compositionend',()=>imeEnd(inp));
  sec2.append(t2, list, inp, el('p','guide-note',T('set.alias_hint')));
  body.append(sec1, sec2);

  const foot=el('div','dialog-foot');
  const del=el('button','danger-btn',T('set.delete_skill')); del.type='button';
  // **onclick 里才 armConfirm**：渲染时调用会让按钮一出现就处于「确认删除」态
  del.onclick=()=>armConfirm(del, ()=>mutate({token:s.name, action:'remove'}).then(()=>{ if(!findSkill(s.name)) closeEditor(); }));
  const done=el('button','primary-btn',T('set.done')); done.type='button'; done.onclick=closeEditor;
  foot.append(del, el('span','spacer'), done);

  dlg.append(head, body, foot);
  m.append(dlg);
}
/* 浏览器 confirm() 有时被挡、打断感也强。两步确认：第一次点变「确认删除」，
   2.5 秒内再点才真的删。 */
function armConfirm(btn, fn){
  if(btn.dataset.arm==='1'){
    delete btn.dataset.arm; btn.textContent=T('set.delete_skill'); btn.classList.remove('arm'); fn(); return;
  }
  btn.dataset.arm='1'; btn.textContent=T('set.confirm_delete'); btn.classList.add('arm');
  setTimeout(()=>{
    if(btn.dataset.arm==='1'){
      delete btn.dataset.arm; btn.textContent=T('set.delete_skill'); btn.classList.remove('arm');
    }
  }, 2500);
}
async function mutate(body){
  try{
    const r=await fetch('/api/skills',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    toast(body.action==='remove' ? T('js.deleted') : T('js.saved'));
    await refreshTree();
  }catch(e){ toast(T('js.save_failed')+e.message); }
}
function bindNewSkill(){
  const inp=document.getElementById('new-skill');
  const btn=document.getElementById('new-skill-btn');
  if(!inp||!btn) return;
  const go=()=>{
    const v=(inp.value||'').trim(); if(!v) return;
    mutate({token:v, action:'add'}).then(()=>{ inp.value=''; });
  };
  btn.onclick=go;
  inp.onkeydown=(ev)=>{
    if(imeComposing(ev)||imeBusy(inp)||imeJustComposed(inp)) return;
    if(ev.key==='Enter'){ ev.preventDefault(); go(); }
  };
  inp.addEventListener('compositionstart',()=>imeStart(inp));
  inp.addEventListener('compositionend',()=>imeEnd(inp));
  const fi=document.getElementById('skill-filter');
  if(fi) fi.addEventListener('input',()=>renderTree(TREE, fi.value));
}
document.addEventListener('DOMContentLoaded',()=>{
  const m=document.getElementById('sk-modal');
  if(m){
    m.addEventListener('click',ev=>{ if(ev.target===m) closeEditor(); });
    document.addEventListener('keydown',ev=>{ if(ev.key==='Escape' && EDITING) closeEditor(); });
  }
  if(!document.getElementById('skill-tree')) return;
  renderTree(TREE); bindNewSkill();
});
