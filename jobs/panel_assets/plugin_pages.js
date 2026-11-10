/* Core-owned host for registered browser pages and sidebars (host API 2).
   No installed Python imports. Every plugin frame gets its own bridge instance:
   own channel, generation, request ids, active set and stop/cleanup. */
(()=>{
'use strict';
const context=window.__PAGE_CONTEXT__;
if(!context)return;
const STRINGS=window.__HOST_I18N__||{};
function HT(key,vars){let s=STRINGS[key]||key;if(vars)for(const k in vars)s=s.split('{'+k+'}').join(String(vars[k]));return s;}
const lang=context.lang==='zh'?'zh':'en';
const bilingual=v=>v&&typeof v==='object'?String(v[lang]||v.en||v.zh||''):'';
const sidebars=Array.isArray(context.sidebars)?context.sidebars:[];
const handedIds=new Set(sidebars.map(s=>s&&s.id));
const isMap=context.display!=='list';     // a registered page (map or compare) is shown
const pageInfo=isMap&&Array.isArray(context.pages)?context.pages.find(p=>p&&p.id===context.display)||null:null;
// A page outside the List/Map switch (compare) is shown alone: no toolbar, no sidebar.
const solo=!!(pageInfo&&pageInfo.switch===false);
const navUrl=(url,display)=>{const u=new URL(url,location.origin);u.searchParams.set('display',display);return u.pathname+u.search+u.hash;};
// The list's own query, without the display switch: a saved list position only
// applies to the same results.
const listQuery=url=>{const u=new URL(url,location.origin);u.searchParams.delete('display');u.searchParams.sort();return u.search;};
function rememberList(){
  const list=document.querySelector('.list');
  if(!list)return;
  try{sessionStorage.removeItem('jobs.plugin.return-position');}catch(_){}
  if(list.scrollTop<=0&&window.scrollY<=0)return;      // at the top: come back to the top
  const card=Array.from(document.querySelectorAll('.list .card')).find(el=>el.getBoundingClientRect().bottom>list.getBoundingClientRect().top);
  try{sessionStorage.setItem('jobs.plugin.return-position',JSON.stringify({query:listQuery(context.query_url),top:list.scrollTop,uid:card?.dataset.uid||'',delta:card?card.getBoundingClientRect().top-list.getBoundingClientRect().top:0,windowY:window.scrollY}));}catch(_){}
}
function returnToList(){
  const target=navUrl(context.return_url,'list');
  try{
    const {query,...position}=JSON.parse(sessionStorage.getItem('jobs.plugin.return-position')||'null')||{};
    // Restore only when the list shows the same results; after a new search it starts at the top.
    if(query&&query===listQuery(target))sessionStorage.setItem('jobs.result.position',JSON.stringify({...position,url:target}));
  }catch(_){}
  return navigatePanel(target);
}
document.querySelectorAll('[data-plugin-display]').forEach(link=>link.addEventListener('click',event=>{
  if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button!==0)return;
  event.preventDefault();
  if(!isMap&&link.dataset.pluginDisplay!=='list')rememberList();
  if(isMap&&link.dataset.pluginDisplay==='list')returnToList();
  else navigatePanel(link.getAttribute('href'));
}));
const pageFrame=isMap?document.getElementById('plugin-display-frame'):null;
const sidebarInfo=!solo&&sidebars.length?sidebars[0]:null;
const sidebarPane=sidebarInfo?document.getElementById('plugin-sidebar'):null;
const sidebarFrame=sidebarPane?document.getElementById('plugin-sidebar-frame'):null;
if(!pageFrame&&!sidebarFrame)return;
if(pageFrame){
// 页面里打开的详情是覆盖层：Esc 返回页面；对比页（整页、没有工具栏）没有详情时 Esc 返回列表。
// 输入框、菜单和弹窗自己处理 Esc。捕获阶段先于 panel.js 的 Esc 处理运行：标注面板、
// 抽屉、更多筛选和显示菜单开着时，那一下 Esc 只关它们，不连详情一起关掉。
document.addEventListener('keydown',event=>{
  if(event.key!=='Escape'||event.defaultPrevented||event.isComposing)return;
  const detail=document.body.classList.contains('plugin-detail-open');
  if(!detail&&!solo)return;
  const target=event.target;
  if(target instanceof Element&&target.closest('input,textarea,select,[contenteditable],[role=dialog],[role=listbox],[role=menu],.note-menu'))return;
  if(document.querySelector('.modal:not([hidden]),#detail.label-open,#drawer:not([hidden]),#display-pop:not([hidden]),.choicepick [aria-expanded=true]'))return;
  if(document.body.classList.contains('portrait')&&document.querySelector('#more-panel:not([hidden])'))return;
  if(document.body.classList.contains('sidebar-sheet'))return;     // the sidebar sheet on top closes first
  event.preventDefault();
  if(detail)closeDetail();else returnToList();
},{capture:true});
}

// ---- Shared permission polling ------------------------------------------------
// One /api/plugins read serves every plugin on the page. Only an explicit "not
// enabled" closes a plugin at once; a transient read failure (restart, network
// blip) closes them after three in a row.
const units=[], bridges=[];
let checking=null,checkedAt=0,failures=0;
async function json(url,signal){
  const response=await fetch(url,{signal,cache:'no-store'});
  const result=await response.json();
  if(!response.ok)throw Object.assign(new Error('request failed'),{status:response.status,code:result.error?.code||result.error||'page_unavailable'});
  return result;
}
function checkPlugins(){
  if(!checking)checking=json('/api/plugins').then(value=>{
    failures=0;
    const on=new Set((value.plugins||[]).filter(p=>p&&p.status==='enabled').map(p=>p.id));
    for(const unit of units)if(!unit.stopped&&!on.has(unit.id))unit.stop();
    checkedAt=Date.now();
  }).catch(()=>{if(++failures>=3)for(const unit of units)unit.stop();}).finally(()=>{checking=null;});
  return checking;
}
async function permission(unit){
  if(unit.stopped)return false;
  if(Date.now()-checkedAt<1500)return true;     // bridge calls in a burst share one check
  await checkPlugins();
  return !unit.stopped;
}
function poll(){if(units.some(u=>!u.stopped))permissionPoll();}
function permissionPoll(){if(Date.now()-checkedAt>=1500)checkPlugins();}

function themeNow(){
  const t=document.documentElement.dataset.theme;
  return t==='dark'||t==='light'?t:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
}
function token(){const b=new Uint8Array(24);crypto.getRandomValues(b);return Array.from(b,n=>n.toString(16).padStart(2,'0')).join('');}
const failure=(code)=>Object.assign(new Error(code),{code});
const keysAre=(p,list)=>Object.keys(p).sort().join(',')===list;

// One bridge per frame. `unit` is the plugin whose permission gates it.
function createBridge(frame,{unit,slot,profile,onStop}){
  const b={frame,unit,slot,profile,pluginId:unit.id,channel:'',generation:0,lastId:0,closed:false,loads:0,sentTheme:'',active:new Map()};
  b.abortAll=()=>{for(const c of b.active.values())c.abort();b.active.clear();};
  b.post=(type,extra)=>{if(!b.closed&&b.channel&&frame.isConnected)frame.contentWindow.postMessage({type,channel:b.channel,...extra},'*');};
  b.sendTheme=()=>{
    const theme=themeNow();
    if(b.closed||!b.channel||theme===b.sentTheme||!frame.isConnected)return;
    b.sentTheme=theme;frame.contentWindow.postMessage({type:'jobs-plugin-theme',channel:b.channel,theme},'*');
  };
  b.stop=()=>{
    if(b.closed)return;
    b.closed=true;b.generation++;b.abortAll();
    frame.remove();
    if(onStop)onStop();
  };
  // The bridge belongs to the page core loaded. If the frame navigates itself
  // elsewhere, close it rather than keep answering the new document.
  frame.addEventListener('load',()=>{if(++b.loads>1)b.stop();});
  bridges.push(b);
  return b;
}

// ---- Map profile (unchanged semantics) ------------------------------------------
// One saved map state per page plugin in this tab: a new search or filter keeps the
// map where it was and only refreshes the results.
const mapStateKey='jobs.plugin.page-state:'+context.plugin_id;
function validBox(b){return Array.isArray(b)&&b.length===4&&b.every(Number.isFinite)&&b[0]>=-180&&b[2]<=180&&b[1]>=-90&&b[3]<=90&&b[0]<b[2]&&b[1]<b[3];}
function validState(s){return s&&typeof s==='object'&&Object.keys(s).sort().join(',')==='area,box,collapsed,selected'&&validBox(s.box)&&(s.area===null||validBox(s.area))&&(s.selected===null||s.selected==='unmapped'||s.selected==='area'||s.selected==='all'||typeof s.selected==='string'&&/^(\d{4}|city:[A-Za-z ]{1,32})$/.test(s.selected))&&typeof s.collapsed==='boolean';}
let mapState=null;
const MAP_OPS={
  async save_state(b,payload){
    if(!validState(payload))throw new Error('invalid state');
    try{sessionStorage.setItem(mapStateKey,JSON.stringify(payload));}catch(_){}
    return {ok:true};
  },
  async load(b,payload,signal){
    if(Object.keys(payload).length)throw new Error('invalid payload');
    const q=new URL(context.query_url,location.origin).search;
    return json('/api/plugin-view/'+encodeURIComponent(b.pluginId)+'/jobs'+q,signal);
  },
  async reference(b,payload,signal){
    if(Object.keys(payload).length!==1||typeof payload.file!=='string'||payload.file.length>80)throw new Error('invalid reference');
    return json('/api/plugin-view/'+encodeURIComponent(b.pluginId)+'/reference?file='+encodeURIComponent(payload.file),signal);
  },
  async open_job(b,payload,signal){
    if(!keysAre(payload,'snapshot_id,uid')||typeof payload.uid!=='string'||payload.uid.length>512||typeof payload.snapshot_id!=='string'||payload.snapshot_id.length>128)throw new Error('invalid job');
    await json('/api/plugin-view/'+encodeURIComponent(b.pluginId)+'/member?'+new URLSearchParams(payload),signal);
    const opened=await openJob(payload.uid);
    // 焦点离开 iframe，让 Esc 等详情快捷键生效，键盘用户也不会停留在被遮住的地图里。
    if(opened)document.getElementById('plugin-detail-back')?.focus({preventScroll:true});
    return {opened};
  },
  async navigate_list(b,payload){
    if(Object.keys(payload).length)throw new Error('invalid navigation');
    await returnToList();return {ok:true};
  },
};

// ---- Handed-jobs profile ----------------------------------------------------------
// The handed list is host-owned: only this file writes it. Shape per item:
// {uid, title, company, revision, added_at}; at most ten, unique by uid.
const HANDED_MAX=10, STATE_MAX=4096;
const handedKey=id=>'jobs.plugin.handed:'+id;
const stateKey=id=>'jobs.plugin.state:'+id;
const shortText=(v,max)=>typeof v==='string'?v.slice(0,max):v==null?'':String(v).slice(0,max);
function validItem(x){
  return !!x&&typeof x==='object'&&!Array.isArray(x)&&typeof x.uid==='string'&&x.uid.length>0&&x.uid.length<=512
    &&typeof x.title==='string'&&x.title.length<=500&&typeof x.company==='string'&&x.company.length<=500
    &&typeof x.revision==='string'&&x.revision.length<=64&&typeof x.added_at==='string'&&x.added_at.length<=40;
}
function readHanded(id){
  let raw=null;
  try{raw=localStorage.getItem(handedKey(id));}catch(_){return [];}
  if(raw===null)return [];
  let list=null;
  try{if(typeof raw==='string'&&raw.length<=65536)list=JSON.parse(raw);}catch(_){}
  const ok=Array.isArray(list)&&list.length<=HANDED_MAX&&list.every(validItem)&&new Set(list.map(x=>x.uid)).size===list.length;
  if(!ok){   // corrupt or oversized (hand-edited, other version): start empty rather than guess
    try{localStorage.removeItem(handedKey(id));}catch(_){}
    return [];
  }
  return list.map(x=>({uid:x.uid,title:x.title,company:x.company,revision:x.revision,added_at:x.added_at}));
}
function writeHanded(id,list){
  try{localStorage.setItem(handedKey(id),JSON.stringify(list));return true;}catch(_){return false;}
}
function byteLength(text){return encodeURIComponent(text).replace(/%[0-9A-F]{2}/g,'x').length;}
function readState(id){
  try{
    const raw=localStorage.getItem(stateKey(id));
    if(raw===null||raw.length>STATE_MAX)return null;
    const value=JSON.parse(raw);
    return value&&typeof value==='object'&&!Array.isArray(value)?value:null;
  }catch(_){return null;}
}
function uidList(v){
  return Array.isArray(v)&&v.length>=1&&v.length<=HANDED_MAX&&v.every(u=>typeof u==='string'&&u.length>0&&u.length<=512)&&new Set(v).size===v.length;
}
function broadcastHanded(id){
  const handed=readHanded(id);
  for(const b of bridges)if(b.profile==='handed'&&b.pluginId===id)b.post('jobs-plugin-handed',{handed});
  if(sidebarInfo&&sidebarInfo.id===id)syncHandoffUi();
  return handed;
}
function mutateHanded(id,change){
  const next=change(readHanded(id));
  if(!writeHanded(id,next))throw failure('storage_unavailable');
  return broadcastHanded(id);
}
function inHanded(id,uids){const have=new Set(readHanded(id).map(x=>x.uid));return uids.every(u=>have.has(u));}
function copyDialogShown(){const box=document.getElementById('copy-modal');return !!box&&!box.hidden;}
const HANDED_OPS={
  handed_jobs:{slots:['sidebar','page'],async run(b,payload,signal){
    if(!keysAre(payload,'detail,uids')||typeof payload.detail!=='boolean'||!uidList(payload.uids))throw new Error('invalid handed request');
    if(!inHanded(b.pluginId,payload.uids))throw failure('not_handed');
    const q=new URLSearchParams();payload.uids.forEach(u=>q.append('uid',u));if(payload.detail)q.set('detail','1');
    return json('/api/plugin-view/'+encodeURIComponent(b.pluginId)+'/handed?'+q,signal);
  }},
  remove_handed:{slots:['sidebar','page'],async run(b,payload){
    if(!keysAre(payload,'uids')||!uidList(payload.uids))throw new Error('invalid removal');
    const drop=new Set(payload.uids);
    return {handed:mutateHanded(b.pluginId,list=>list.filter(x=>!drop.has(x.uid)))};
  }},
  clear_handed:{slots:['sidebar'],async run(b,payload){
    if(Object.keys(payload).length)throw new Error('invalid clear');
    return {handed:mutateHanded(b.pluginId,()=>[])};
  }},
  open_job:{slots:['sidebar','page'],async run(b,payload){
    if(!keysAre(payload,'uid')||typeof payload.uid!=='string'||!payload.uid||payload.uid.length>512)throw new Error('invalid job');
    if(!inHanded(b.pluginId,[payload.uid]))throw failure('not_handed');
    // openJob keeps every guard: in-flight saves, unsaved notes, deferred refresh.
    const opened=!!await openJob(payload.uid);
    if(opened&&b.slot==='page')document.getElementById('plugin-detail-back')?.focus({preventScroll:true});
    if(opened&&b.slot==='sidebar'&&document.body.classList.contains('portrait')){
      setSidebar(false,{focus:false});            // the sheet would cover the detail
      document.getElementById('back')?.focus({preventScroll:true});
    }
    return {opened};
  }},
  copy_text:{slots:['sidebar','page'],async run(b,payload){
    if(!keysAre(payload,'text')||typeof payload.text!=='string'||!payload.text||payload.text.length>2000)throw new Error('invalid text');
    const copied=!!await copyPlain(payload.text);
    if(copied){toast(HT('sidebar.copied'));return {copied:true,fallback:false};}
    openCopyDialog(payload.text,b.frame);          // manual copy, focus returns to the frame
    return {copied:false,fallback:copyDialogShown()};
  }},
  open_page:{slots:['sidebar'],async run(b,payload){
    if(Object.keys(payload).length)throw new Error('invalid navigation');
    if(!(context.pages||[]).some(p=>p&&p.id===b.pluginId))throw failure('unsupported_operation');
    if(!isMap)rememberList();
    return {ok:!!await navigatePanel(navUrl(context.query_url,b.pluginId))};
  }},
  navigate_list:{slots:['page'],async run(b,payload){
    if(Object.keys(payload).length)throw new Error('invalid navigation');
    await returnToList();return {ok:true};
  }},
  save_state:{slots:['sidebar','page'],async run(b,payload){
    if(!keysAre(payload,'state')||!payload.state||typeof payload.state!=='object'||Array.isArray(payload.state))throw new Error('invalid state');
    let text='';
    try{text=JSON.stringify(payload.state);if(byteLength(text)>STATE_MAX)throw 0;}catch(_){throw failure('state_too_large');}
    try{localStorage.setItem(stateKey(b.pluginId),text);return {ok:true};}catch(_){return {ok:false};}
  }},
};
async function operation(b,name,payload,signal){
  if(!await permission(b.unit))throw failure('plugin_disabled_or_permission_required');
  if(b.profile==='map'){
    if(!Object.prototype.hasOwnProperty.call(MAP_OPS,name))throw failure('unsupported_operation');
    return MAP_OPS[name](b,payload,signal);
  }
  const op=Object.prototype.hasOwnProperty.call(HANDED_OPS,name)?HANDED_OPS[name]:null;
  if(!op||!op.slots.includes(b.slot))throw failure('unsupported_operation');
  return op.run(b,payload,signal);
}
function frameContext(b){
  const base={...context,plugin_id:b.pluginId,theme:themeNow(),slot:b.slot};
  if(b.profile==='map')return mapState?{...base,state:mapState}:base;
  const extra={handed:readHanded(b.pluginId),agent_access:context.agent_access==='on'?'on':'off',state:readState(b.pluginId),compare_columns:window.innerWidth>=900?3:2};
  if(b.slot==='sidebar')extra.current=currentJob();
  return {...base,...extra};
}
window.addEventListener('message',async event=>{
  const b=bridges.find(x=>!x.closed&&event.source===x.frame.contentWindow);
  if(!b||!event.data||typeof event.data!=='object'||Array.isArray(event.data))return;
  const message=event.data;
  if(message.type==='jobs-plugin-ready'&&message.version===1){
    b.generation++;b.lastId=0;b.channel=token();b.sentTheme=themeNow();
    b.abortAll();
    const ownGeneration=b.generation;
    if(!await permission(b.unit)||b.closed||ownGeneration!==b.generation)return;
    b.frame.contentWindow.postMessage({type:'jobs-plugin-context',channel:b.channel,context:frameContext(b)},'*');
    return;
  }
  if(message.type!=='jobs-plugin-request'||!b.channel||message.channel!==b.channel||typeof message.id!=='string'||!/^\d{1,10}$/.test(message.id)||Number(message.id)<=b.lastId||typeof message.operation!=='string'||!message.payload||typeof message.payload!=='object'||Array.isArray(message.payload))return;
  b.lastId=Number(message.id);
  const ownGeneration=b.generation,ownChannel=b.channel;
  const reply=value=>{if(!b.closed&&ownGeneration===b.generation&&b.frame.isConnected)b.frame.contentWindow.postMessage({type:'jobs-plugin-response',channel:ownChannel,id:message.id,...value},'*');};
  if(b.active.size>=8){reply({ok:false,error:{code:'page_busy',message:'Too many requests'}});return;}
  const controller=new AbortController();b.active.set(message.id,controller);
  const timer=setTimeout(()=>controller.abort(),14000);
  try{reply({ok:true,result:await operation(b,message.operation,message.payload,controller.signal)});}
  catch(error){reply({ok:false,error:{code:error.name==='AbortError'?'page_timeout':typeof error.code==='string'?error.code:'invalid_request',message:'The requested plugin operation could not be completed.'}});}
  finally{clearTimeout(timer);b.active.delete(message.id);}
});

// ---- Page frame (map or compare) --------------------------------------------------
if(pageFrame){
  const pageUnit={id:context.plugin_id,stopped:false,stop(){pageBridge.stop();}};
  const pageBridge=createBridge(pageFrame,{unit:pageUnit,slot:'page',profile:handedIds.has(context.plugin_id)?'handed':'map',onStop(){
    pageUnit.stopped=true;
    const note=document.createElement('div');note.className='placeholder';
    note.textContent=!solo?(context.lang==='zh'?'地图已关闭或需要重新授权，请切回列表继续使用。':'The map is turned off or needs permission again. Switch back to the list to continue.')
      :HT('sidebar.page_closed');
    document.querySelector('main').prepend(note);
  }});
  units.push(pageUnit);
  if(pageBridge.profile==='map'){
    try{const saved=JSON.parse(sessionStorage.getItem(mapStateKey)||'null');if(validState(saved))mapState=saved;}catch(_){}
  }
  if(solo)document.getElementById('plugin-page-back')?.addEventListener('click',()=>returnToList());
}

// ---- Sidebar slot -----------------------------------------------------------------
let sidebarUnit=null,sidebarBridge=null,sidebarStopped=true;
const HANDOFF_KEYS={default:'w',vim:'w',gamer:'e'};
function handoffKey(){const p=typeof keyProfile==='function'?keyProfile():'default';return HANDOFF_KEYS[p]||'w';}
function currentJob(){
  const uid=typeof openUid==='function'?openUid():null;
  if(!uid)return null;
  const scope=document.querySelector('#detail [data-label-scope]');
  if(!scope||scope.dataset.uid!==uid)return {uid,title:'',company:''};
  const heading=scope.querySelector('h2'), copy=scope.querySelector('[data-copy-text]');
  // The copy text is server-made: "UID: …\nTitle: …\nCompany: …"; take the company line.
  const line=copy?String(copy.dataset.copyText||'').split('\n')[2]||'':'';
  const cut=line.indexOf(': ');
  return {uid,title:heading?heading.textContent:'',company:cut>=0?line.slice(cut+2):''};
}
function sendCurrent(){
  const current=currentJob();
  for(const b of bridges)if(b.slot==='sidebar')b.post('jobs-plugin-current',{current});
}
function setSidebar(){}   // replaced below when a sidebar exists
function syncHandoffUi(){}
if(sidebarFrame){
  const id=sidebarInfo.id, label=bilingual(sidebarInfo.label)||id, action=bilingual(sidebarInfo.handoff_label)||label;
  const body=document.body, toggle=document.getElementById('sidebar-toggle'), count=document.getElementById('sidebar-count');
  const closeBtn=document.getElementById('sidebar-close'), handle=document.getElementById('sidebar-resize');
  const scrim=document.getElementById('sidebar-scrim'), drop=document.getElementById('sidebar-drop');
  sidebarStopped=false;
  sidebarUnit={id,stopped:false,stop:stopSidebar};
  units.push(sidebarUnit);
  const isOpen=()=>body.classList.contains('sidebar-open');
  const portrait=()=>body.classList.contains('portrait');
  function startFrame(){
    if(sidebarBridge||sidebarStopped)return;
    sidebarBridge=createBridge(sidebarFrame,{unit:sidebarUnit,slot:'sidebar',profile:'handed',onStop:stopSidebar});
    sidebarFrame.src=sidebarFrame.dataset.pluginSrc;
  }
  setSidebar=function(open,{focus=true,remember=true}={}){
    if(sidebarStopped)open=false;
    const was=isOpen();
    body.classList.toggle('sidebar-open',!!open);
    if(toggle)toggle.setAttribute('aria-expanded',open?'true':'false');
    if(open)startFrame();
    // 竖屏的底部抽屉是临时的：只记桌面上的开合。
    if(remember&&!portrait()&&typeof storeSidebarPrefs==='function')storeSidebarPrefs({open:!!open});
    if(typeof fitLayout==='function')fitLayout();
    if(!focus||was===!!open)return;
    if(open){
      const target=portrait()?closeBtn:sidebarFrame;
      if(target&&target.focus)target.focus({preventScroll:true});
    }else{
      const active=document.activeElement;
      if((!active||active===body||sidebarPane.contains(active))&&toggle&&toggle.focus)toggle.focus({preventScroll:true});
    }
  };
  syncHandoffUi=function(){
    if(sidebarStopped)return;
    const list=readHanded(id), have=new Set(list.map(x=>x.uid));
    if(count){count.textContent=String(list.length);count.hidden=!list.length;}
    if(toggle)toggle.setAttribute('aria-label',HT('sidebar.toggle',{label,n:list.length}));
    document.querySelectorAll('#detail [data-handoff-uid]').forEach(btn=>{
      const on=have.has(btn.dataset.handoffUid);
      btn.classList.toggle('in',on);
      btn.textContent=on?HT('sidebar.in',{label}):action;
    });
    document.querySelectorAll('.card-handoff').forEach(btn=>btn.classList.toggle('in',have.has(btn.dataset.handoffCard)));
  };
  function stopSidebar(){
    if(sidebarStopped)return;
    sidebarStopped=true;sidebarUnit.stopped=true;
    if(sidebarBridge)sidebarBridge.stop();else sidebarFrame.remove();
    body.classList.remove('has-sidebar','sidebar-open','handoff-dragging');
    if(toggle)toggle.hidden=true;
    sidebarPane.hidden=true;
    if(scrim)scrim.hidden=true;
    document.querySelectorAll('.card-handoff,[data-handoff-uid],.selhandoff').forEach(el=>el.remove());
    document.querySelectorAll('.list .card[draggable]').forEach(card=>card.removeAttribute('draggable'));
    if(typeof fitLayout==='function')fitLayout();
    toast(HT('sidebar.closed',{label}));
  }
  // One host function for every entry point. Calls run one after another so two
  // quick adds never both read the same stored list.
  let chain=Promise.resolve();
  function handOff(uids){const run=chain.then(()=>handOffNow(uids));chain=run.catch(()=>{});return run;}
  async function handOffNow(raw){
    const uids=[...new Set((Array.isArray(raw)?raw:[]).filter(u=>typeof u==='string'&&u&&u.length<=512))];
    if(sidebarStopped||!uids.length)return {added:0,skipped:uids.length};
    const before=readHanded(id), have=new Set(before.map(x=>x.uid));
    const fresh=uids.filter(u=>!have.has(u));
    if(!fresh.length){toast(HT(uids.length===1?'sidebar.already':'sidebar.all_already',{label}));return {added:0,skipped:uids.length};}
    if(before.length>=HANDED_MAX){toast(HT('sidebar.full',{label,max:HANDED_MAX}));return {added:0,skipped:uids.length};}
    const ask=fresh.slice(0,HANDED_MAX-before.length);
    let items=null;
    try{
      const q=new URLSearchParams();ask.forEach(u=>q.append('uid',u));
      const result=await json('/api/plugin-view/'+encodeURIComponent(id)+'/handed?'+q);
      if(result&&Array.isArray(result.items)&&result.items.length===ask.length
         &&result.items.every((it,i)=>it&&typeof it==='object'&&it.uid===ask[i]))items=result.items;
    }catch(error){if(error&&error.status===403)checkPlugins();}
    if(sidebarStopped)return {added:0,skipped:uids.length};
    if(!items){toast(HT('sidebar.failed',{label}));return {added:0,skipped:uids.length,failed:true};}
    // Merge into what is stored now: another tab may have changed it meanwhile.
    const now=readHanded(id), seen=new Set(now.map(x=>x.uid)), stamp=new Date().toISOString(), add=[];
    for(const it of items){
      if(it.missing===true||seen.has(it.uid)||now.length+add.length>=HANDED_MAX)continue;
      add.push({uid:it.uid,title:shortText(it.title,500),company:shortText(it.company,500),revision:shortText(it.revision,64),added_at:stamp});
      seen.add(it.uid);
    }
    if(add.length&&!writeHanded(id,now.concat(add))){toast(HT('sidebar.saved_failed',{label}));return {added:0,skipped:uids.length,failed:true};}
    if(add.length){
      broadcastHanded(id);
      if(toggle){toggle.classList.remove('pulse');void toggle.offsetWidth;toggle.classList.add('pulse');}
    }
    const skipped=uids.length-add.length;
    if(uids.length===1){
      toast(add.length?HT('sidebar.added',{label}):items[0]&&items[0].missing===true?HT('sidebar.missing')
        :now.length>=HANDED_MAX?HT('sidebar.full',{label,max:HANDED_MAX}):HT('sidebar.already',{label}));
    }else toast(skipped?HT('sidebar.some',{n:add.length,m:skipped}):HT('sidebar.added_n',{n:add.length,label}));
    return {added:add.length,skipped};
  }
  // 2. Detail action bar: drawn by core after each detail load (the detail HTML is unchanged).
  function injectDetailButton(){
    if(sidebarStopped)return;
    const scope=document.querySelector('#detail [data-label-scope]');
    const actions=scope&&scope.querySelector('.d-actions');
    if(!actions||actions.querySelector('[data-handoff-uid]'))return;
    const btn=document.createElement('button');
    const key=handoffKey().toUpperCase();
    btn.type='button';btn.className='btn handoff-btn';btn.dataset.handoffUid=scope.dataset.uid;
    btn.title=action+' ('+key+')';btn.setAttribute('aria-keyshortcuts',key);
    btn.addEventListener('click',()=>{if(btn.classList.contains('in'))setSidebar(true);else handOff([btn.dataset.handoffUid]);});
    actions.insertBefore(btn,actions.firstChild);
    syncHandoffUi();
  }
  // 3. Multi-select bar: disabled with the other bar actions while nothing is selected.
  const selbar=document.getElementById('selbar');
  if(selbar){
    const btn=document.createElement('button');
    btn.type='button';btn.className='selbtn selhandoff';btn.dataset.selWrite='';btn.disabled=true;btn.textContent=action;
    btn.addEventListener('click',()=>handOff(typeof SEL!=='undefined'?Array.from(SEL):[]));
    selbar.insertBefore(btn,selbar.querySelector('[data-sel-cancel]'));
  }
  // 4. Card affordance: shown on pointer hover or keyboard focus only (CSS). Not a tab
  //    stop of its own: with a card focused, the shortcut adds that card.
  const PLUS='<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg>';
  document.querySelectorAll('.list .card').forEach(card=>{
    const top=card.querySelector('.card-top');
    if(!top||top.querySelector('.card-handoff'))return;
    const btn=document.createElement('button');
    btn.type='button';btn.className='card-handoff';btn.tabIndex=-1;btn.dataset.handoffCard=card.dataset.uid;
    btn.title=action;btn.setAttribute('aria-label',HT('sidebar.card_add',{action,title:(card.querySelector('.t')||{}).textContent||''}));
    btn.innerHTML=PLUS;
    btn.addEventListener('click',ev=>{ev.preventDefault();ev.stopPropagation();handOff([card.dataset.uid]);});
    btn.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' ')ev.stopPropagation();});   // not "open the card"
    top.insertBefore(btn,top.querySelector('.score'));
  });
  // 1. Drag a card onto the sidebar or its toggle. Only a mouse press away from text
  //    starts a drag, so text selection, clicks, long-press multi-select and touch
  //    scrolling keep their meaning. No data crosses into any frame.
  function overText(x,y){
    let node=null,offset=0;
    if(document.caretPositionFromPoint){const p=document.caretPositionFromPoint(x,y);if(p){node=p.offsetNode;offset=p.offset;}}
    else if(document.caretRangeFromPoint){const r=document.caretRangeFromPoint(x,y);if(r){node=r.startContainer;offset=r.startOffset;}}
    if(!node||node.nodeType!==3)return false;
    const range=document.createRange();
    for(const start of [offset-1,offset]){
      if(start<0||start+1>node.length)continue;
      range.setStart(node,start);range.setEnd(node,start+1);
      for(const r of range.getClientRects())if(x>=r.left&&x<=r.right&&y>=r.top&&y<=r.bottom)return true;
    }
    return false;
  }
  let dragUids=null;
  document.addEventListener('pointerdown',ev=>{
    const card=ev.target&&ev.target.closest?ev.target.closest('.list .card'):null;
    if(!card)return;
    const ok=!sidebarStopped&&ev.pointerType==='mouse'&&ev.button===0&&!ev.metaKey&&!ev.ctrlKey&&!ev.shiftKey&&!ev.altKey
      &&!ev.target.closest('.card-check')&&!overText(ev.clientX,ev.clientY);
    if(ok)card.setAttribute('draggable','true');else card.removeAttribute('draggable');
  },true);
  function endDrag(){
    dragUids=null;body.classList.remove('handoff-dragging');
    for(const el of [toggle,drop])if(el)el.classList.remove('over');
  }
  document.addEventListener('dragstart',ev=>{
    const card=ev.target&&ev.target.closest?ev.target.closest('.list .card[draggable="true"]'):null;
    if(!card||sidebarStopped)return;
    const uid=card.dataset.uid;
    dragUids=typeof SEL!=='undefined'&&SEL.has(uid)&&SEL.size>1?Array.from(SEL):[uid];
    try{ev.dataTransfer.effectAllowed='copy';ev.dataTransfer.setData('application/x-jobs-handoff','1');}catch(_){}
    body.classList.add('handoff-dragging');
  });
  document.addEventListener('dragend',endDrag);
  for(const el of [toggle,drop]){
    if(!el)continue;
    const over=ev=>{if(!dragUids)return;ev.preventDefault();try{ev.dataTransfer.dropEffect='copy';}catch(_){}el.classList.add('over');};
    el.addEventListener('dragenter',over);
    el.addEventListener('dragover',over);
    el.addEventListener('dragleave',()=>el.classList.remove('over'));
    el.addEventListener('drop',ev=>{if(!dragUids)return;ev.preventDefault();const uids=dragUids;endDrag();handOff(uids);});
  }
  // 5. Shortcut for the open job (or the focused card), all three key profiles.
  document.addEventListener('keydown',ev=>{
    if(sidebarStopped||ev.shiftKey||String(ev.key||'').toLowerCase()!==handoffKey())return;
    if(typeof navKeyIgnored==='function'&&navKeyIgnored(ev))return;
    const active=document.activeElement;
    const card=active&&active.classList&&active.classList.contains('card')&&active.closest&&active.closest('.list')?active:null;
    const uid=card?card.dataset.uid:(typeof openUid==='function'?openUid():null);
    if(!uid)return;
    ev.preventDefault();handOff([uid]);
  });
  // Toggle, close, scrim, resize; Esc closes the portrait sheet.
  if(toggle)toggle.addEventListener('click',()=>setSidebar(!isOpen()));
  if(closeBtn)closeBtn.addEventListener('click',()=>setSidebar(false));
  if(scrim)scrim.addEventListener('click',()=>setSidebar(false));
  if(handle){
    handle.addEventListener('pointerdown',ev=>{if(typeof sidebarDrag==='function')sidebarDrag(ev);});
    handle.addEventListener('keydown',ev=>{if(typeof sidebarResizeKeys==='function')sidebarResizeKeys(ev);});
  }
  document.addEventListener('keydown',ev=>{
    if(ev.key!=='Escape'||ev.defaultPrevented||ev.isComposing||!isOpen()||!portrait())return;
    if(document.querySelector('.modal:not([hidden])'))return;
    ev.preventDefault();setSidebar(false);
  });
  window.__JOBS_PLUGIN_HOST__={
    detailOpened(){injectDetailButton();sendCurrent();},
    detailClosed(){sendCurrent();},
    handOff,
  };
  syncHandoffUi();
  if(typeof sidebarPrefs==='function'&&sidebarPrefs().open===true&&!portrait())setSidebar(true,{focus:false,remember:false});
}

// ---- Page-wide wiring (one listener each) ------------------------------------------
function sendThemeAll(){for(const b of bridges)b.sendTheme();}
new MutationObserver(sendThemeAll).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',sendThemeAll);
const timer=setInterval(poll,3000);
window.addEventListener('pagehide',()=>{
  clearInterval(timer);
  for(const b of bridges){b.closed=true;b.abortAll();}
});
window.addEventListener('storage',event=>{
  if(event.key==='jobs.plugins.changed')poll();
  else if(sidebarInfo&&!sidebarStopped&&(event.key===null||event.key===handedKey(sidebarInfo.id)))broadcastHanded(sidebarInfo.id);
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
// Back/forward cache restores a page whose bridges were disposed on pagehide.
window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
if(pageFrame)pageFrame.src=pageFrame.dataset.pluginSrc;
})();
