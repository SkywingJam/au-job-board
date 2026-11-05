/* ---- 设置页：技术栈词表 ----------------------------------------------------
   列表由客户端用 GET /api/skills 的数据渲染。为什么不在服务端也渲染一份：
   那样列表逻辑就有了 Python / JS 两套，迟早走样（和 report.py 与面板共用
   render.py 是同一条理由）。改动后重新取数、只重渲染这一块，不整页 reload。

   每行只有一个铅笔按钮，点开编辑窗：主要展示名（拖拽或点选，确认后保存）、
   别名增删、删除技能。名称与别名的颜色表示来源：种子（skills.yaml）灰、
   本地（skills.local.yaml）蓝；条目来源三态：种子 / 本地 / 种子+本地。
   写入只走已有的 /api/skills（add / alias / remove），不新增接口。 */
let TREE = window.__SKILL_TREE__ || {skills: []};
let EDITING = null;          // 编辑窗当前打开的技能名
let PRIMARY_PREVIEW = Object.create(null);    // 未确认的展示名草稿；确认后写入本地补充层

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
    const hay=((s.name||'')+' '+(s.display_name||'')+' '+((s.aliases||[]).join(' '))).toLowerCase();
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
  name.append(bubble(s.display_name||s.name, s.display_name_origin||s.origin, 'name'));
  if(s.display_name&&s.display_name!==s.name)name.title=s.name;
  if(s.ambiguous){ const f=el('span','skflags','?'); f.title=T('set.flag_ambiguous'); name.append(f); }
  if(s.has_not){ const f=el('span','skflags','not'); f.title=T('set.flag_not'); name.append(f); }
  const al=el('span','sk-aliases');
  [s.name,...(s.aliases||[])].filter(a=>a!==(s.display_name||s.name)).forEach(a=>al.append(bubble(a,a===s.name?s.origin:(s.alias_origins||{})[a]||'local')));
  // 手机没有列头：命中数前补一个「命中」（桌面由 CSS 隐藏，列头已经写了）
  const hits=el('span','skhits'); hits.append(el('span','skhits-k',T('set.col_hits')+' '), String(s.hits));
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
function closeEditor(force=false){
  if(SKILL_SAVING)return;
  const selected=EDITING?findSkill(EDITING):null;
  if(!force&&selected&&PRIMARY_PREVIEW[EDITING]&&PRIMARY_PREVIEW[EDITING]!== (selected.display_name||selected.name)){
    if(!window.confirm(T('set.primary_unsaved')))return;
  }
  const m=document.getElementById('sk-modal');
  if(EDITING)delete PRIMARY_PREVIEW[EDITING];
  EDITING=null;
  if(m)conceal(m,()=>{m.textContent='';});
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
  close.setAttribute('aria-label',T('set.close')); close.onclick=()=>closeEditor();
  head.append(h, sourceBadge(s.source||s.origin), close);

  const body=el('div','dialog-body');
  const all=[{n:s.name, src:s.origin, alias:false}].concat(
    (s.aliases||[]).map(a=>({n:a, src:(s.alias_origins||{})[a]||'local', alias:true})));
  const primary=PRIMARY_PREVIEW[s.name]||s.display_name||s.name;
  const pItem=all.find(x=>x.n===primary)||all[0];

  // 主要展示名草稿：确认后保存；规范身份与匹配保持不变。
  const sec1=el('div');
  const t1=el('div','sec-title'); t1.append(T('set.primary'));
  const dz=el('div','dz'); dz.setAttribute('aria-label',T('set.primary'));
  dz.append(bubble(pItem.n, pItem.src), el('small',null,T('set.primary_now')));
  if(primary!==s.name){
    const undo=el('button','linkbtn',T('set.reset_to',{name:s.name})); undo.type='button';
    undo.onclick=()=>{if(SKILL_SAVING)return; PRIMARY_PREVIEW[s.name]=s.name; renderEditor(); };
    dz.append(undo);
  }
  const primaryContent=Array.from(dz.childNodes);
  dz.addEventListener('dragover',ev=>{
    ev.preventDefault();
    if(!dz.classList.contains('over')){
      dz.classList.add('over');
      dz.replaceChildren(el('span','drop-text',T('set.drop_here')));
    }
  });
  const leave=(ev)=>{ if(ev && ev.relatedTarget && dz.contains(ev.relatedTarget)) return; if(dz.classList.contains('over')){ dz.classList.remove('over'); dz.replaceChildren(...primaryContent); } };
  dz.addEventListener('dragleave',leave);
  dz.addEventListener('drop',ev=>{
    ev.preventDefault();if(SKILL_SAVING)return;
    let n=''; try{ n=ev.dataTransfer.getData('text/plain'); }catch(e){}
    if(all.some(item=>item.n===n)){ PRIMARY_PREVIEW[s.name]=n; renderEditor(); } else leave();
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
    nm.onclick=()=>{if(SKILL_SAVING)return; PRIMARY_PREVIEW[s.name]=x.n; renderEditor(); };
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
  const done=el('button','primary-btn',T('set.done')); done.type='button';
  done.onclick=async()=>{
    if(SKILL_SAVING)return;
    const chosen=PRIMARY_PREVIEW[s.name]||s.display_name||s.name;
    if(chosen!== (s.display_name||s.name)){
      done.disabled=true;const saved=await mutate({token:chosen,action:'display_name',target:s.name});
      if(!saved){done.disabled=false;return;}
    }
    closeEditor(true);
  };
  foot.append(del, el('span','spacer'), done);

  dlg.append(head, body, foot);
  m.append(dlg);
  freezeSkillControls(SKILL_SAVING);
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
function freezeSkillControls(busy){
  const modal=document.getElementById('sk-modal');if(!modal)return;
  modal.querySelectorAll('button,input').forEach(control=>control.disabled=busy);
  modal.querySelectorAll('.dchip').forEach(chip=>chip.draggable=!busy);
  modal.setAttribute('aria-busy',busy?'true':'false');
}
async function mutate(body){
  if(SKILL_SAVING)return false;
  SKILL_SAVING=true;freezeSkillControls(true);
  try{
    const r=await fetch('/api/skills',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
    toast(body.action==='remove' ? T('js.deleted') : T('js.saved'));
    await refreshTree();
    return true;
  }catch(e){ toast(T('js.save_failed')+e.message);return false; }
  finally{SKILL_SAVING=false;freezeSkillControls(false);}
}
function bindNewSkill(){
  const inp=document.getElementById('new-skill');
  const btn=document.getElementById('new-skill-btn');
  if(!inp||!btn) return;
  const go=()=>{
    const v=(inp.value||'').trim(); if(!v) return;
    mutate({token:v, action:'add'}).then(saved=>{if(saved&&inp.value.trim()===v)inp.value='';});
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
window.addEventListener('beforeunload',event=>{
  const skill=EDITING?findSkill(EDITING):null;
  if(skill&&PRIMARY_PREVIEW[EDITING]&&PRIMARY_PREVIEW[EDITING]!== (skill.display_name||skill.name)){event.preventDefault();event.returnValue='';}
});
document.addEventListener('DOMContentLoaded',()=>{
  const m=document.getElementById('sk-modal');
  if(m){
    m.addEventListener('click',ev=>{ if(ev.target===m) closeEditor(); });
    document.addEventListener('keydown',ev=>{ if(ev.key==='Escape' && EDITING) closeEditor(); });
  }
  if(!document.getElementById('skill-tree')) return;
  renderTree(TREE); bindNewSkill();
});

/* Settings read from the file; current values come only from successful analysis. */
function bindConfigEditor(){
  const root=document.getElementById('config-editor'); if(!root)return;
  const modal=document.getElementById('config-modal'), confirmation=document.getElementById('config-confirm');
  const en=window.__LANG__==='en',msg=(zh,eng)=>en?eng:zh;
  const words={queries:msg('抓取关键词','Search terms'),'seek.locations':msg('地区','Locations'),enabled:msg('启用来源','Source enabled'),location:msg('地区','Location'),pages_per_query:msg('每个关键词页数','Pages per term'),daterange:msg('最近天数','Posted within days'),detail_sleep:msg('正文请求间隔（秒）','Detail request delay (seconds)'),country_indeed:msg('国家','Country'),results_wanted:msg('每个关键词请求条数','Requested results per term'),fetch_description:msg('抓取完整描述','Fetch full descriptions'),profile:msg('规则预设','Rule profile')};
  const groups=[{id:'queries',prefix:'queries',name:msg('关键词','Search terms')},{id:'seek',prefix:'seek.',name:'SEEK'},{id:'indeed',prefix:'jobspy.indeed.',name:'Indeed'},{id:'linkedin',prefix:'jobspy.linkedin.',name:'LinkedIn'},{id:'rules',prefix:'legacy_rule_profile.',name:msg('规则预设与逐条开关','Rule preset & switches')}];
  // 「资格与规则」与「抓取与更新」是两个分区，各显示自己的几组；读写、digest 与冲突检测仍是同一套。
  const only=(root.dataset.configGroups||'').split(' ').filter(Boolean);
  const shownGroups=only.length?groups.filter(group=>only.includes(group.id)):groups;
  const errors={conflict:msg('文件已被其他页面或外部编辑。输入已保留，请重新载入后再修改。','The file changed in another tab or editor. Your input is retained; reload and reapply your changes.'),run_busy:msg('更新正在运行，暂时不能保存。输入已保留。','An update is running. Your input is retained; try saving afterwards.'),config_invalid:msg('配置文件包含非法值、重复键或不支持的 YAML。请先修复文件。','The file contains invalid values, duplicate keys or unsupported YAML. Repair it first.'),config_unavailable:msg('无法读取配置文件，请检查读取权限。','Cannot read the configuration file. Check its permissions.'),write_failed:msg('写入失败，请检查目录权限和磁盘空间。','Could not write the file. Check directory permissions and disk space.'),validation_failed:msg('字段类型或数值范围不合法。','A field has an invalid type or range.'),demo_disabled:msg('演示环境不能编辑配置。','Configuration editing is disabled in the demo.')};
  const listPaths=new Set(['queries','seek.locations']),numberPaths=new Set(['seek.pages_per_query','seek.daterange','seek.detail_sleep','jobspy.indeed.results_wanted','jobspy.linkedin.results_wanted']);
  let state=null,editing=null,controls={},form=null,submit=null,modalStatus=null,dirty=false,saving=false,loading=false,leaving=false,refreshPending=false;
  const status=el('p','config-status'); status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const cards=el('div','config-regions');
  const toolbar=el('div','config-toolbar');
  const reload=el('button','iconbtn config-reload');reload.type='button';reload.title=msg('重新载入文件','Reload file');reload.setAttribute('aria-label',reload.title);reload.id='config-reload';
  reload.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8M13 3v5h5L13 3Z"/><path d="M20 12v4h-4"/><path d="M19.3 16a4.5 4.5 0 1 0-.9 4.3"/></svg>';
  toolbar.append(reload);(document.getElementById('config-actions')||root).append(toolbar);root.append(status,cards);
  const pathsFor=group=>Object.keys(state.values).filter(path=>path.startsWith(group.prefix));
  const isRule=path=>path.startsWith('legacy_rule_profile.')&&path!=='legacy_rule_profile.profile';
  const labelFor=path=>isRule(path)?path.slice('legacy_rule_profile.'.length):(words[path]||words[path.split('.').pop()]||path);
  const profileLabel=value=>{const translated=T('set.profile_'+value);return translated==='set.profile_'+value?value:translated;};
  function valueText(path,value){
    if(path==='legacy_rule_profile.profile')return profileLabel(value);
    if(isRule(path)&&value===null)return msg('使用预设默认值','Use preset default');
    if(value===null||(path==='seek.daterange'&&value===0))return msg('不限','Any time');
    if(typeof value==='boolean')return value?msg('启用','Enabled'):msg('停用','Disabled');
    return Array.isArray(value)?value.join('、'):String(value);
  }
  function comparable(path,view){
    const value=view.values[path];
    if(path==='seek.daterange')return value||null;
    if(isRule(path)){
      const parts=path.split('.'),namespace=parts[1],rid=parts.slice(2).join('.');
      return [value,view.explicit?.[path]??(value!==null),view.effective_rules?.[namespace]?.[rid]??value];
    }
    return value;
  }
  function sameGroup(group,current,next){return pathsFor(group).every(path=>JSON.stringify(comparable(path,current))===JSON.stringify(comparable(path,next)));}
  function contentFor(group,view){
    const list=el('dl','config-summary');
    pathsFor(group).filter(path=>!isRule(path)).forEach(path=>{
      const row=el('div','config-summary-row');row.append(el('dt',null,labelFor(path)),el('dd',null,valueText(path,view.values[path])));list.append(row);
    });
    if(group.id==='rules'){
      const changed=pathsFor(group).filter(path=>isRule(path)&&view.values[path]!==null).length;
      const row=el('div','config-summary-row');row.append(el('dt',null,msg('逐条开关','Individual switches')),el('dd',null,changed?msg(changed+' 项覆盖预设',changed+' preset overrides'):msg('使用预设默认值','Use preset defaults')));list.append(row);
    }
    return list;
  }
  function render(data){
    state=data;cards.textContent='';
    const recorded=state.applied?.status==='recorded',fallback=!recorded&&state.baseline?.status==='recorded';
    const currentView=recorded?state.applied:fallback?state.baseline:state;
    status.classList.remove('error');status.textContent=recorded?msg('当前为最近成功分析使用的配置；保存的修改在下一次运行生效。','Current values were used by the last successful analysis. Saved changes apply to the next run.'):fallback?msg('最近运行配置未记录。当前区域保留修改前文件配置，下一次区域展示已保存的改动。','The last-run configuration was not recorded. Current shows the file before editing; Next run shows saved changes.'):msg('最近运行配置尚未记录；以下展示当前文件配置，保存后在下一次运行生效。','The last-run configuration was not recorded. Showing the current file; saved changes apply to the next run.');
    shownGroups.forEach(group=>{
      const paired=(recorded||fallback)&&!sameGroup(group,currentView,state);
      const region=el('section','config-region');region.dataset.configRegion=group.id;
      const current=el('div','config-version config-current');current.dataset.configCurrent=group.id;
      const head=el('div','config-region-head');const title=el('h3',null,group.name);
      const pen=el('button','pen config-edit');pen.type='button';pen.innerHTML=ICON_PEN;pen.dataset.configEdit=group.id;pen.title=msg('修改','Edit');pen.setAttribute('aria-label',msg('修改','Edit')+' '+group.name);pen.onclick=()=>open(group);
      head.append(title,pen);current.append(head);
      if(paired||fallback)current.append(el('span','config-version-label',fallback?msg('当前 · 修改前文件配置','Current · File before editing'):msg('当前','Current')));
      current.append(contentFor(group,currentView));
      region.append(current);
      if(paired){const next=el('div','config-version config-next');next.dataset.configNext=group.id;next.append(el('span','config-version-label',msg('下一次运行','Next run')),contentFor(group,state));region.append(next);}
      cards.append(region);
    });
  }
  function fieldValue(path){
    const input=controls[path];
    if(isRule(path))return input.value==='default'?null:input.value==='true';
    if(input.type==='checkbox')return input.checked;
    if(listPaths.has(path))return input.value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);
    if(numberPaths.has(path))return input.value===''?null:Number(input.value);
    return input.value;
  }
  function changes(){const changed={};Object.keys(controls).forEach(path=>{const value=fieldValue(path);if(JSON.stringify(value)!==JSON.stringify(state.values[path]))changed[path]=value;});return changed;}
  function dirtyChanged(){dirty=Object.keys(changes()).length>0;submit.disabled=!dirty||saving;modalStatus.classList.remove('error');modalStatus.textContent=dirty?msg('有未保存的改动','Unsaved changes'):'';}
  function makeField(path){
    const value=state.values[path],wrap=el('label','config-field'),text=el('span',null,labelFor(path));let input;
    if(path==='legacy_rule_profile.profile'||isRule(path)){
      input=el('select');const options=isRule(path)?[['default',msg('使用预设默认值','Use preset default')],['true',msg('启用','Enabled')],['false',msg('停用','Disabled')]]:state.profiles.map(profile=>[profile,profileLabel(profile)]);
      options.forEach(([key,label])=>{const option=el('option',null,label);option.value=key;input.append(option);});input.value=isRule(path)?(value===null?'default':String(value)):value;
    }else if(typeof value==='boolean'){input=el('input');input.type='checkbox';input.checked=value;}
    else if(listPaths.has(path)){input=el('textarea');input.rows=Math.max(3,Math.min(value.length,7));input.value=value.join('\n');input.required=true;text.append(el('small','muted',msg('每行一个','One per line')));}
    else{input=el('input');input.type=numberPaths.has(path)?'number':'text';input.value=value===null?'':String(value);
      if(input.type==='number'){input.min=path==='seek.daterange'||path==='seek.detail_sleep'?'0':'1';input.max=path==='seek.detail_sleep'?'60':path==='seek.daterange'?'3650':path==='seek.pages_per_query'?'100':'1000';input.step=path==='seek.detail_sleep'?'any':'1';input.required=path!=='seek.daterange';}
      else{input.required=true;input.maxLength=1000;}}
    input.dataset.configPath=path;input.setAttribute('aria-label',labelFor(path));controls[path]=input;
    input.addEventListener('input',dirtyChanged);input.addEventListener('change',dirtyChanged);
    wrap.append(text,input);return wrap;
  }
  function modalShell(container,title,description){
    container.textContent='';const dialog=el('div','dialog config-dialog');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label',title);
    const head=el('div','dialog-head');head.append(el('h3',null,title));const close=el('button','pen','✕');close.type='button';close.setAttribute('aria-label',msg('关闭','Close'));head.append(close);
    const body=el('div','dialog-body');if(description)body.append(el('p','config-dialog-hint',description));const foot=el('div','dialog-foot');dialog.append(head,body,foot);container.append(dialog);return{dialog,body,foot,close};
  }
  function open(group){
    if(!state||saving||loading)return;editing=group;controls={};dirty=false;
    const shell=modalShell(modal,msg('修改','Edit')+' '+group.name,msg('保存后用于下一次运行，不会自动抓取或重算。','Saving applies to the next run. It does not fetch or reanalyze.'));
    shell.close.onclick=()=>close(false);form=el('form','config-form');form.id='config-form';
    pathsFor(group).filter(path=>!isRule(path)).forEach(path=>form.append(makeField(path)));
    if(group.id==='rules'){
      const advanced=el('details','config-advanced');advanced.append(el('summary',null,msg('高级','Advanced')));const fields=el('div','config-advanced-fields');pathsFor(group).filter(isRule).forEach(path=>fields.append(makeField(path)));advanced.append(fields);form.append(advanced);
    }
    modalStatus=el('p','config-status');modalStatus.setAttribute('role','status');shell.body.append(form,modalStatus);
    const cancel=el('button','btn',msg('取消','Cancel'));cancel.type='button';cancel.onclick=()=>close(false);
    submit=el('button','primary-btn',msg('确认','Confirm'));submit.type='submit';submit.setAttribute('form','config-form');submit.disabled=true;
    shell.foot.append(cancel,el('span','spacer'),submit);form.onsubmit=event=>{event.preventDefault();if(!saving&&dirty&&form.reportValidity())confirmChanges();};
    reveal(modal);requestAnimationFrame(()=>Object.values(controls)[0]?.focus());
  }
  function close(force){
    if(saving)return false;
    if(!force&&dirty&&!window.confirm(msg('放弃未保存的改动？','Discard unsaved changes?')))return false;
    const group=editing;editing=null;dirty=false;controls={};
    conceal(modal,()=>{modal.textContent='';if(group)root.querySelector('[data-config-edit="'+group.id+'"]')?.focus();});
    if(refreshPending){refreshPending=false;load();}return true;
  }
  function cancelConfirmation(){if(saving)return;modal.inert=false;conceal(confirmation,()=>{confirmation.textContent='';submit?.focus();});}
  function confirmChanges(){
    const edited=changes();if(!Object.keys(edited).length)return;
    const shell=modalShell(confirmation,msg('确认修改','Confirm changes'),'');shell.close.onclick=cancelConfirmation;
    const lines=el('div','config-change-lines');
    Object.entries(edited).forEach(([path,value])=>lines.append(el('p',null,msg(labelFor(path)+'将从 '+valueText(path,state.values[path])+' 变成 '+valueText(path,value)+'，确认吗？',labelFor(path)+' will change from '+valueText(path,state.values[path])+' to '+valueText(path,value)+'. Confirm?'))));
    const note=el('p','config-confirm-note',msg('新规则将在下一次生效','The new rules will take effect on the next run.'));
    const result=el('p','config-status');result.setAttribute('role','status');shell.body.append(lines,note,result);
    const back=el('button','btn',msg('返回修改','Back'));back.type='button';back.onclick=cancelConfirmation;
    const save=el('button','primary-btn',msg('确认保存','Save changes'));save.type='button';
    shell.foot.append(back,el('span','spacer'),save);modal.inert=true;reveal(confirmation);save.focus();
    save.onclick=async()=>{
      if(saving)return;saving=true;save.disabled=true;back.disabled=true;shell.close.disabled=true;reload.disabled=true;Object.values(controls).forEach(input=>input.disabled=true);
      try{
        const response=await fetch('/api/config-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({digest:state.digest,changes:edited})});const data=await response.json();
        if(!response.ok||!data.ok){result.classList.add('error');result.textContent=errors[data.error]||msg('保存失败，输入已保留。','Save failed. Your input is retained.');return;}
        const group=editing;render(data);dirty=false;saving=false;modal.inert=false;
        conceal(confirmation,()=>{confirmation.textContent='';});close(true);toast(msg('配置已保存，下次运行生效','Configuration saved for the next run'));
        document.dispatchEvent(new CustomEvent('config-saved',{detail:data,group:group.id}));
      }catch(error){result.classList.add('error');result.textContent=msg('请求失败，输入已保留。','The request failed; your input is retained.');}
      finally{saving=false;reload.disabled=false;save.disabled=false;back.disabled=false;shell.close.disabled=false;Object.values(controls).forEach(input=>input.disabled=false);}
    };
  }
  async function load(){
    if(loading||saving||editing){refreshPending=true;return;}
    loading=true;reload.disabled=true;root.querySelectorAll('.config-edit').forEach(button=>button.disabled=true);status.classList.remove('error');status.textContent=msg('正在读取配置…','Reading configuration…');
    try{const response=await fetch('/api/config-settings'),data=await response.json();if(!response.ok||!data.ok){status.classList.add('error');status.textContent=errors[data.error]||msg('配置读取失败','Could not read configuration');return;}render(data);}
    catch(error){status.classList.add('error');status.textContent=msg('配置读取失败，请重试。','Could not read configuration. Try again.');}
    finally{loading=false;reload.disabled=false;root.querySelectorAll('.config-edit').forEach(button=>button.disabled=false);if(refreshPending&&!saving&&!editing){refreshPending=false;load();}}
  }
  reload.onclick=()=>{if(saving)return;if(editing&&!close(false))return;load();};
  modal.addEventListener('click',event=>{if(event.target===modal)close(false);});confirmation.addEventListener('click',event=>{if(event.target===confirmation)cancelConfirmation();});
  document.addEventListener('keydown',event=>{
    const active=shown(confirmation)?confirmation:shown(modal)?modal:null;if(!active)return;
    if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();if(active===confirmation)cancelConfirmation();else close(false);}
    if(event.key==='Tab'){const focusable=Array.from(active.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary')).filter(node=>node.getClientRects().length);const first=focusable[0],last=focusable[focusable.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
  });
  window.addEventListener('beforeunload',event=>{if(!leaving&&(dirty||saving)){event.preventDefault();event.returnValue='';}});
  document.addEventListener('click',event=>{
    const link=event.target.closest&&event.target.closest('a[href]');if(!link||(!dirty&&!saving)||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target==='_blank'||link.hasAttribute('download'))return;
    if(saving){event.preventDefault();return;}if(!window.confirm(msg('放弃未保存的配置改动并继续？','Discard unsaved configuration changes and continue?'))){event.preventDefault();event.stopImmediatePropagation();}else{dirty=false;leaving=true;}
  },true);
  document.addEventListener('job-results-updated',()=>load());
  load();
}
document.addEventListener('DOMContentLoaded',bindConfigEditor);

/* Plugins: core renders the controls; installed code never runs in this page. */
(function pluginSettings(){
  const status=document.getElementById('plugin-status');
  let busy=false;
  const message=code=>T('plugin.'+code)===('plugin.'+code)?T('plugin.failed'):T('plugin.'+code);
  document.querySelectorAll('[data-plugin-toggle]').forEach(button=>{
    button.addEventListener('click',async()=>{
      if(busy)return;
      busy=true;button.disabled=true;
      const menu=button.closest('details');if(menu)menu.open=false;
      if(status)status.textContent=T('plugin.loading');
      try{
        const currentResponse=await fetch('/api/plugins');
        const current=await currentResponse.json();
        if(!currentResponse.ok||!current.ok)throw new Error(current.error||'failed');
        const response=await fetch('/api/plugins',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({digest:current.digest,id:button.dataset.pluginToggle,enabled:button.dataset.enabled!=='true'})});
        const result=await response.json();
        if(!response.ok||!result.ok)throw new Error(result.error||'failed');
        const item=result.plugins.find(p=>p.id===button.dataset.pluginToggle);
        if(!item)throw new Error('failed');
        button.dataset.enabled=String(item.enabled);
        button.textContent=T(item.enabled?'plugin.disable':'plugin.enable');
        const row=button.closest('.plugin-row');
        row.querySelector('.plugin-state').textContent=T('plugin.'+item.status);
        if(status)status.textContent=T('plugin.saved');
        const summary=row.querySelector('summary');if(summary)summary.focus();
      }catch(error){if(status)status.textContent=message(error.message);}
      finally{busy=false;button.disabled=false;}
    });
  });
  const debug=document.querySelector('[data-plugin-debug]');
  if(debug)debug.addEventListener('click',async()=>{
    if(busy)return;
    const output=document.getElementById('plugin-debug-output');
    if(debug.getAttribute('aria-expanded')==='true'){
      output.hidden=true;debug.setAttribute('aria-expanded','false');return;
    }
    busy=true;debug.disabled=true;output.hidden=false;
    output.textContent=T('plugin.loading');debug.setAttribute('aria-expanded','true');
    try{
      const response=await fetch('/api/plugins/debug?id='+encodeURIComponent(debug.dataset.pluginDebug));
      const data=await response.json();
      if(!response.ok||!data.ok)throw new Error(data.error||'failed');
      output.textContent=data.entries.length?data.entries.map(e=>[e.time,e.stage,e.error,e.message].join('  ')).join('\n'):T('plugin.no_errors');
    }catch(error){output.textContent=message(error.message);}
    finally{busy=false;debug.disabled=false;}
  });
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    const menu=document.querySelector('.plugin-menu[open]');
    if(menu){menu.open=false;menu.querySelector('summary').focus();}
  });
  document.addEventListener('click',event=>document.querySelectorAll('.plugin-menu[open]').forEach(menu=>{
    if(!menu.contains(event.target))menu.open=false;
  }));
})();
