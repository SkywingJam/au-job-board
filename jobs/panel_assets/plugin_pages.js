/* Core-owned bridge for registered browser pages. No installed Python imports. */
(()=>{
'use strict';
const context=window.__PAGE_CONTEXT__;
if(!context)return;
const frame=document.getElementById('plugin-display-frame');
const isMap=context.display!=='list';
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
if(!frame)return;
// 地图里打开的详情是覆盖层：Esc 返回地图。输入框、菜单和弹窗自己处理 Esc。
// 捕获阶段先于 panel.js 的 Esc 处理运行：标注面板、抽屉、更多筛选和显示菜单
// 开着时，那一下 Esc 只关它们，不连详情一起关掉。
document.addEventListener('keydown',event=>{
  if(event.key!=='Escape'||event.defaultPrevented||event.isComposing||!document.body.classList.contains('plugin-detail-open'))return;
  const target=event.target;
  if(target instanceof Element&&target.closest('input,textarea,select,[contenteditable],[role=dialog],[role=listbox],[role=menu],.note-menu'))return;
  if(document.querySelector('.modal:not([hidden]),#detail.label-open,#drawer:not([hidden]),#display-pop:not([hidden]),.choicepick [aria-expanded=true]'))return;
  if(document.body.classList.contains('portrait')&&document.querySelector('#more-panel:not([hidden])'))return;
  event.preventDefault();closeDetail();
},{capture:true});
let channel='',generation=0,lastId=0,closed=false,checking=null,checkedAt=0,failures=0,loads=0;
const active=new Map();
// One saved map state per page plugin in this tab: a new search or filter keeps the
// map where it was and only refreshes the results.
const stateKey='jobs.plugin.page-state:'+context.plugin_id;
function validBox(b){return Array.isArray(b)&&b.length===4&&b.every(Number.isFinite)&&b[0]>=-180&&b[2]<=180&&b[1]>=-90&&b[3]<=90&&b[0]<b[2]&&b[1]<b[3];}
function validState(s){return s&&typeof s==='object'&&Object.keys(s).sort().join(',')==='area,box,collapsed,selected'&&validBox(s.box)&&(s.area===null||validBox(s.area))&&(s.selected===null||s.selected==='unmapped'||s.selected==='area'||s.selected==='all'||typeof s.selected==='string'&&/^(\d{4}|city:[A-Za-z ]{1,32})$/.test(s.selected))&&typeof s.collapsed==='boolean';}
try{const saved=JSON.parse(sessionStorage.getItem(stateKey)||'null');if(validState(saved))context.state=saved;}catch(_){}

function themeNow(){
  const t=document.documentElement.dataset.theme;
  return t==='dark'||t==='light'?t:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
}
// The page follows the system or the Display menu live; tell the frame too.
let sentTheme='';
function sendTheme(){
  const theme=themeNow();
  if(closed||!channel||theme===sentTheme||!frame.isConnected)return;
  sentTheme=theme;frame.contentWindow.postMessage({type:'jobs-plugin-theme',channel,theme},'*');
}
new MutationObserver(sendTheme).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',sendTheme);
function token(){const b=new Uint8Array(24);crypto.getRandomValues(b);return Array.from(b,n=>n.toString(16).padStart(2,'0')).join('');}
function stop(){
  if(closed)return;
  closed=true;generation++;
  for(const c of active.values())c.abort();active.clear();
  frame.remove();
  const note=document.createElement('div');note.className='placeholder';
  note.textContent=context.lang==='zh'?'地图已关闭或需要重新授权，请切回列表继续使用。':'The map is turned off or needs permission again. Switch back to the list to continue.';
  document.querySelector('main').prepend(note);
}
async function json(url,signal){
  const response=await fetch(url,{signal,cache:'no-store'});
  const result=await response.json();
  if(!response.ok)throw Object.assign(new Error('request failed'),{code:result.error?.code||result.error||'page_unavailable'});
  return result;
}
async function permission(){
  if(closed)return false;
  if(Date.now()-checkedAt<1500)return true;     // bridge calls in a burst share one check
  // Only an explicit "not enabled" closes the map at once; a transient read
  // failure (restart, network blip) closes it after three in a row.
  if(!checking)checking=json('/api/plugins').then(value=>{
    failures=0;
    const plugin=(value.plugins||[]).find(p=>p.id===context.plugin_id);
    if(plugin?.status!=='enabled'){stop();return false;}
    checkedAt=Date.now();return true;
  }).catch(()=>{if(++failures>=3){stop();return false;}return true;}).finally(()=>{checking=null;});
  return checking;
}
async function operation(name,payload,signal){
  if(!await permission())throw Object.assign(new Error('disabled'),{code:'plugin_disabled_or_permission_required'});
  if(name==='save_state'){
    if(!validState(payload))throw new Error('invalid state');
    try{sessionStorage.setItem(stateKey,JSON.stringify(payload));}catch(_){}
    return {ok:true};
  }
  if(name==='load'){
    if(Object.keys(payload).length)throw new Error('invalid payload');
    const q=new URL(context.query_url,location.origin).search;
    return json('/api/plugin-view/'+encodeURIComponent(context.plugin_id)+'/jobs'+q,signal);
  }
  if(name==='reference'){
    if(Object.keys(payload).length!==1||typeof payload.file!=='string'||payload.file.length>80)throw new Error('invalid reference');
    return json('/api/plugin-view/'+encodeURIComponent(context.plugin_id)+'/reference?file='+encodeURIComponent(payload.file),signal);
  }
  if(name==='open_job'){
    if(Object.keys(payload).sort().join(',')!=='snapshot_id,uid'||typeof payload.uid!=='string'||payload.uid.length>512||typeof payload.snapshot_id!=='string'||payload.snapshot_id.length>128)throw new Error('invalid job');
    await json('/api/plugin-view/'+encodeURIComponent(context.plugin_id)+'/member?'+new URLSearchParams(payload),signal);
    const opened=await openJob(payload.uid);
    // 焦点离开 iframe，让 Esc 等详情快捷键生效，键盘用户也不会停留在被遮住的地图里。
    if(opened)document.getElementById('plugin-detail-back')?.focus({preventScroll:true});
    return {opened};
  }
  if(name==='navigate_list'){
    if(Object.keys(payload).length)throw new Error('invalid navigation');
    await returnToList();return {ok:true};
  }
  throw Object.assign(new Error('unsupported operation'),{code:'unsupported_operation'});
}
window.addEventListener('message',async event=>{
  if(closed||event.source!==frame.contentWindow||!event.data||typeof event.data!=='object'||Array.isArray(event.data))return;
  const message=event.data;
  if(message.type==='jobs-plugin-ready'&&message.version===1){
    generation++;lastId=0;channel=token();sentTheme=themeNow();
    for(const c of active.values())c.abort();active.clear();
    if(!await permission())return;
    frame.contentWindow.postMessage({type:'jobs-plugin-context',channel,context:{...context,theme:themeNow()}},'*');
    return;
  }
  if(message.type!=='jobs-plugin-request'||!channel||message.channel!==channel||typeof message.id!=='string'||!/^\d{1,10}$/.test(message.id)||Number(message.id)<=lastId||typeof message.operation!=='string'||!message.payload||typeof message.payload!=='object'||Array.isArray(message.payload))return;
  lastId=Number(message.id);
  const ownGeneration=generation,ownChannel=channel;
  const reply=value=>{if(!closed&&ownGeneration===generation&&frame.isConnected)frame.contentWindow.postMessage({type:'jobs-plugin-response',channel:ownChannel,id:message.id,...value},'*');};
  if(active.size>=8){reply({ok:false,error:{code:'page_busy',message:'Too many requests'}});return;}
  const controller=new AbortController();active.set(message.id,controller);
  const timer=setTimeout(()=>controller.abort(),14000);
  try{reply({ok:true,result:await operation(message.operation,message.payload,controller.signal)});}
  catch(error){reply({ok:false,error:{code:error.name==='AbortError'?'page_timeout':typeof error.code==='string'?error.code:'invalid_request',message:'The requested plugin operation could not be completed.'}});}
  finally{clearTimeout(timer);active.delete(message.id);}
});
const timer=setInterval(permission,3000);
window.addEventListener('pagehide',()=>{closed=true;clearInterval(timer);for(const c of active.values())c.abort();active.clear();});
window.addEventListener('storage',event=>{if(event.key==='jobs.plugins.changed')permission();});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)permission();});
// The bridge belongs to the page core loaded. If the frame navigates itself
// elsewhere, close it rather than keep answering the new document.
frame.addEventListener('load',()=>{if(++loads>1)stop();});
// Back/forward cache restores a page whose bridge was disposed on pagehide.
window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
frame.src=frame.dataset.pluginSrc;
})();
