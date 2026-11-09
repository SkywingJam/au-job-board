import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
let playwright;try{playwright=require(process.env.PLAYWRIGHT_MODULE||'playwright');}catch{playwright=require('/opt/homebrew/lib/node_modules/@playwright/cli/node_modules/playwright');}
const root=resolve('plugins/map/web');
const harness=`<!doctype html><iframe id="frame" sandbox="allow-scripts" src="/plugins/map/frame" style="width:100%;height:720px"></iframe><script>
window.calls=[];window.fail=new URLSearchParams(location.search).get('fail')||'';window.delay=false;window.held=[];window.version=0;window.unit='jobs';window.failReference=false;window.expireOpen=0;
const ref={schema_version:1,reference_version:'fixture',files:[{path:'one.geojson.gz'}],postcodes:{'3000':{point:[145,-37],bbox:[144,-38,146,-36],file:'one.geojson.gz'},'2000':{point:[151,-33],bbox:[150,-34,152,-32],file:'one.geojson.gz'}}};
const feature=pc=>({type:'Feature',properties:{postcode:pc},geometry:{type:'Polygon',coordinates:[pc==='3000'?[[144,-38],[146,-38],[146,-36],[144,-36],[144,-38]]:[[150,-34],[152,-34],[152,-32],[150,-32],[150,-34]]]}});
const items=[{uid:'b',title:'Second',company:'Company',core_order:2,location_fact:{precision:'postcode',postcodes:['3000'],reason:'explicit_postcode'}},{uid:'a',title:'First',core_order:1,location_fact:{precision:'locality',postcodes:['3000'],reason:'unique_locality'}},{uid:'c',title:'Sydney',core_order:3,location_fact:{precision:'postcode',postcodes:['2000']}},{uid:'d',title:'Broad city',core_order:4,location_fact:{precision:'city',postcodes:[]}},{uid:'e',title:'Ambiguous',core_order:5,location_fact:{precision:'locality',postcodes:['3000'],reason:'ambiguous_locality'}},{uid:'f',title:'Missing boundary',core_order:6,location_fact:{precision:'postcode',postcodes:['9999']}},{uid:'g',title:'Melbourne city',core_order:7,location_fact:{precision:'city',city:'Melbourne',states:['VIC'],postcodes:[]}},{uid:'h',title:'Sydney city',core_order:8,location_fact:{precision:'city',city:'Sydney',states:['NSW'],postcodes:[]}},{uid:'i',title:'WA only',core_order:9,location_fact:{precision:'state',states:['WA'],postcodes:[]}}];
if(new URLSearchParams(location.search).get('only')==='wa')items.splice(0,items.length,...items.filter(x=>['a','i'].includes(x.uid)));
if(new URLSearchParams(location.search).has('perth')){ref.postcodes['6000']={point:[115.86,-31.95],bbox:[115.8,-32,115.9,-31.9],file:'one.geojson.gz'};items.push({uid:'p',title:'Perth city',core_order:10,location_fact:{precision:'city',city:'Perth',states:['WA'],postcodes:[]}});}
if(new URLSearchParams(location.search).has('many'))for(let i=0;i<150;i++)items.push({uid:'m'+i,title:'Bulk '+i,core_order:100+i,location_fact:{precision:'postcode',postcodes:['3000']}});
window.respond=(m,ok,result)=>frame.contentWindow.postMessage({type:'jobs-plugin-response',channel:'token',id:m.id,ok,...(ok?{result}:{error:{code:result,message:result}})},'*');
window.addEventListener('message',e=>{if(e.source!==frame.contentWindow)return;let m=e.data;if(m.type==='jobs-plugin-ready'){frame.contentWindow.postMessage({type:'jobs-plugin-context',channel:'token',context:{plugin_id:'map',lang:'en',theme:'light'}},'*');return;}if(m.type!=='jobs-plugin-request'||m.channel!=='token')return;calls.push(m);if(m.operation==='load'){if(delay){held.push(m);return;}if(fail){respond(m,false,fail);return;}respond(m,true,{snapshot_id:'s'+(++version),count_unit:unit,items,total:items.length});}else if(m.operation==='reference'){if(failReference && m.payload.file!=='index.json')respond(m,false,'reference_error');else respond(m,true,m.payload.file==='index.json'?ref:{type:'FeatureCollection',features:[feature('3000'),feature('2000')]});}else if(m.operation==='open_job' && expireOpen>0){expireOpen--;respond(m,false,'snapshot_expired');}else if(m.operation==='open_job' && fail)respond(m,false,fail);else respond(m,true,{});});</script>`;
const server=createServer(async(req,res)=>{try{const path=new URL(req.url,'http://localhost').pathname;if(path==='/'){res.setHeader('Content-Type','text/html');res.end(harness);return;}const relative=path==='/plugins/map/frame'?'index.html':path.startsWith('/plugins/map/assets/')?path.slice('/plugins/map/assets/'.length):'';if(!relative||relative.includes('..'))throw Error();if(process.env.TEST_MODULE_CORS)res.setHeader('Access-Control-Allow-Origin','*');const file=resolve(root,relative);if(!file.startsWith(root+'/'))throw Error();const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css'};res.setHeader('Content-Type',types[extname(file)]||'application/octet-stream');res.end(await readFile(file));}catch{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
try{
browser=await playwright.chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',args:['--no-sandbox']});
const page=await browser.newPage({viewport:{width:1100,height:850},reducedMotion:'reduce'});
if(process.env.TEST_MODULE_CORS)await page.route('https://tiles.openfreemap.org/**',route=>route.abort());
const consoleErrors=[];page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
const base=`http://127.0.0.1:${server.address().port}/`;
await page.goto(base);
const frame=page.frameLocator('#frame');
const text=sel=>frame.locator(sel).textContent();
const focused=()=>frame.locator('body').evaluate(()=>{const a=document.activeElement;return a.id||a.dataset.key||a.tagName;});
await frame.locator('.marker').first().waitFor();
await frame.getByText('The background map couldn’t load. Job locations are still shown.').waitFor();
if(!process.env.TEST_MODULE_CORS){const cors=consoleErrors.find(x=>x.includes('blocked by CORS policy'));assert.ok(cors,'expected opaque-origin module CORS failure');console.log('Opaque sandbox evidence:',cors);}
else console.log('CORS-enabled offline basemap fallback observed');
assert.ok(await frame.locator('#north').isHidden(),'no compass without the map engine');
// Markers come from the index points; nothing else is loaded up front.
assert.equal(await frame.locator('.marker').count(),2);
assert.deepEqual(await page.evaluate(()=>calls.filter(x=>x.operation==='reference').map(x=>x.payload.file)),['index.json']);
// Default view is the results area (south-east): WA's state-only job is not listed.
assert.equal(await text('#unmapped'),'Other locations (3)');
assert.equal(await text('#summary'),'3 in view · 9 jobs in total');
// Capital-city-only jobs are citywide pills, not other locations and not counted in view.
assert.deepEqual(await frame.locator('.city').allTextContents(),['Melbourne citywide1','Sydney citywide1']);
await frame.locator('.city',{hasText:'Melbourne'}).click();
assert.equal(await text('#sheet-title'),'City only · Melbourne');
assert.deepEqual(await frame.locator('.job button').allTextContents(),['Melbourne city']);
assert.equal(await frame.locator('.city[aria-pressed=true]').count(),1);
assert.equal(await focused(),'sheet-title','opening the sheet moves focus to its title');
await frame.locator('#close').click();
assert.equal(await focused(),'city:Melbourne','closing returns focus to the pill that opened it');
// A rebuild of the markers (here: a resize) keeps focus on the marker that has it.
await frame.locator('.marker[title="3000"]').focus();
await page.setViewportSize({width:1090,height:850});
await page.waitForTimeout(100);
assert.equal(await focused(),'3000','focus survives the marker rebuild');
await page.setViewportSize({width:1100,height:850});
// Zoomed out to almost all of Australia: only one "all of Australia" pill, no circles or city pills.
for(let i=0;i<5;i++)await frame.locator('#zoom-out').click();
assert.equal(await frame.locator('.marker').count(),0);
assert.deepEqual(await frame.locator('.city').allTextContents(),['All of Australia9']);
assert.equal(await text('#unmapped'),'Other locations (4)','WA is in view');
await frame.locator('.city').click();
assert.equal(await text('#sheet-title'),'Jobs across Australia');
assert.equal(await text('#sheet-meta'),'9 jobs');
assert.equal(await frame.locator('.job button').count(),9);
await frame.locator('#close').click();
await frame.locator('#reset').click();
assert.equal(await frame.locator('.marker').count(),2);
for(const sel of ['#list','#area','#clear-area'])assert.equal(await frame.locator(sel).count(),0,sel+' removed');
// Postcode: list in core order, outline loads on demand.
await frame.locator('.marker[title="3000"]').click();
assert.deepEqual(await frame.locator('.job button').allTextContents(),['First','Second']);
assert.equal(await text('#sheet-meta'),'2 jobs');
await frame.locator('#geometry path').waitFor();
await frame.locator('.job button').first().click();
await page.waitForFunction(()=>calls.some(x=>x.operation==='open_job'));
assert.deepEqual(await page.evaluate(()=>calls.find(x=>x.operation==='open_job').payload),{snapshot_id:'s1',uid:'a'});
// Other locations, grouped in plain words.
await frame.locator('#unmapped').click();
assert.deepEqual(await frame.locator('.job button').allTextContents(),['Broad city','Ambiguous','Missing boundary']);
assert.deepEqual(await frame.locator('#jobs .group').allTextContents(),['No location given (1)','Place name is unclear (1)','Postcode not on the map (1)']);
await frame.locator('#jobs').evaluate(el=>{el.firstElementChild.dataset.probe='kept';});
await frame.locator('#zoom-out').click();await frame.locator('#zoom-in').click();
assert.equal(await frame.locator('#jobs [data-probe]').count(),1,'unchanged list is not rebuilt on a move');
// Jobs in view: the summary is live; tapping it lists them.
await frame.locator('#zoom-in').click();await frame.locator('#zoom-in').click();
assert.equal(await text('#summary'),'0 in view · 9 jobs in total');
assert.equal(await frame.locator('.marker').count(),2,'markers are never hidden by a mode');
await frame.locator('#summary').click();
assert.equal(await text('#sheet-title'),'Jobs in view');
assert.equal(await text('#sheet-meta'),'No jobs in this area');
assert.ok(await frame.locator('#notice').isHidden()||!(await text('#notice')).includes('area'));
await frame.locator('#reset').click();
assert.equal(await text('#summary'),'3 in view · 9 jobs in total');
assert.equal(await text('#sheet-meta'),'3 jobs','open list follows the view');
assert.deepEqual(await frame.locator('.job button').allTextContents(),['First','Second','Sydney']);
await frame.locator('#summary').click();
assert.ok(await frame.locator('#sheet').isHidden(),'tapping the summary again closes the list');
// Live theme from the host; foreign channels ignored.
await page.evaluate(()=>frame.contentWindow.postMessage({type:'jobs-plugin-theme',channel:'wrong',theme:'dark'},'*'));
await page.evaluate(()=>frame.contentWindow.postMessage({type:'jobs-plugin-theme',channel:'token',theme:'dark'},'*'));
await frame.locator('body.dark').waitFor();
await page.evaluate(()=>frame.contentWindow.postMessage({type:'jobs-plugin-theme',channel:'token',theme:'light'},'*'));
await frame.locator('body:not(.dark)').waitFor();
// Attribution: tap the bar to open, again to close; tapping elsewhere also closes.
await frame.locator('#attr-toggle').click();assert.ok(await frame.locator('#attr-full').isVisible());
await frame.locator('#attr-toggle').click();assert.ok(await frame.locator('#attr-full').isHidden());
await frame.locator('#attr-toggle').click();await frame.locator('#surface').click({position:{x:400,y:400}});
assert.ok(await frame.locator('#attr-full').isHidden());
// Phone sheet.
await page.setViewportSize({width:390,height:844});
await frame.locator('.marker[title="3000"]').click();
await frame.locator('#grip').click();
assert.equal(await frame.locator('#grip').getAttribute('aria-expanded'),'false');
assert.equal(await frame.locator('#jobs').isVisible(),false);
await frame.locator('#grip').click();
assert.equal(await frame.locator('#jobs').isVisible(),true);
// Dragging the handle: up snaps to (near) full height, far down closes the sheet.
{const g=await frame.locator('#grip').boundingBox(),before=(await frame.locator('#sheet').boundingBox()).height;
 await page.mouse.move(g.x+g.width/2,g.y+g.height/2);await page.mouse.down();await page.mouse.move(g.x+g.width/2,g.y-300,{steps:8});await page.mouse.up();
 assert.ok(await frame.locator('#sheet.full').count()===1||(await frame.locator('#sheet').boundingBox()).height>=before,'drag up enlarges');
 assert.ok(await frame.locator('#jobs').isVisible(),'a drag is not a tap');
 const g2=await frame.locator('#grip').boundingBox();
 await page.mouse.move(g2.x+g2.width/2,g2.y+g2.height/2);await page.mouse.down();await page.mouse.move(g2.x+g2.width/2,705,{steps:10});await page.mouse.up();
 await frame.locator('#sheet').waitFor({state:'hidden'});
 await frame.locator('.marker[title="3000"]').click();}
// Errors, stale replies and empty results.
await frame.locator('#close').click();
await frame.locator('#unmapped').click();
// An expired snapshot is reloaded once and the same job is opened again, without a notice.
await page.evaluate(()=>{expireOpen=1;calls.length=0});
await frame.locator('.job button').first().click();
await page.waitForFunction(()=>calls.filter(x=>x.operation==='open_job').length===2);
assert.equal(await page.evaluate(()=>calls.filter(x=>x.operation==='load').length),1);
assert.equal(await page.evaluate(()=>{const o=calls.filter(x=>x.operation==='open_job');return o[0].payload.uid===o[1].payload.uid&&o[0].payload.snapshot_id!==o[1].payload.snapshot_id;}),true);
assert.ok(await frame.locator('#notice').isHidden());
await page.evaluate(()=>{fail='snapshot_expired'});
await frame.locator('.job button').first().click();
await frame.getByText('The results are out of date. Please refresh.').waitFor();
await page.evaluate(()=>{fail='';delay=true});
await frame.getByRole('button',{name:'Refresh'}).click();
await page.waitForFunction(()=>held.length===1);
await page.evaluate(()=>{const m=held[0];frame.contentWindow.postMessage({type:'jobs-plugin-response',channel:'wrong',id:m.id,ok:true,result:{snapshot_id:'forged',count_unit:'jobs',total:99,items:[]}},'*')});
assert.match(await text('#summary'),/Loading/);
await page.evaluate(()=>{delay=false;unit='source_records';const m=held.shift();respond(m,true,{snapshot_id:'new',count_unit:'source_records',total:0,items:[]})});
await frame.getByText('No jobs in the current results.').waitFor();
assert.equal(await text('#summary'),'0 in view · 0 records in total');
await page.evaluate(()=>{frame.contentWindow.postMessage({type:'jobs-plugin-response',channel:'token',id:'1',ok:true,result:{snapshot_id:'old',count_unit:'jobs',total:99,items:[]}},'*')});
assert.equal(await text('#summary'),'0 in view · 0 records in total');
// Disabled, then an outline that fails to load: jobs stay on the map and in the list.
await page.goto(base+'?fail=disabled');
await frame.getByText('The map is turned off or needs permission again.').waitFor();
await page.evaluate(()=>{fail='';failReference=true});
await frame.getByRole('button',{name:'Refresh'}).click();
await frame.locator('.marker').first().waitFor();
assert.equal(await frame.locator('.marker').count(),2);
await frame.locator('.marker[title="3000"]').click();
await frame.getByText('This postcode’s outline couldn’t load. Its jobs are still listed.').waitFor();
assert.deepEqual(await frame.locator('.job button').allTextContents(),['First','Second']);
// A state-only job outside the view is not an "other location": no button at all, then it appears when the state is in view.
await page.goto(base+'?only=wa');
await frame.locator('.marker').first().waitFor();
assert.ok(await frame.locator('#unmapped').isHidden(),'no "Other locations (0)" button');
for(let i=0;i<12;i++)await frame.locator('#zoom-out').click();
assert.ok(await frame.locator('#unmapped').isVisible());
assert.equal(await text('#unmapped'),'Other locations (1)');
// "Show all results" also reveals capital-city markers outside the first (postcode) view.
await page.goto(base+'?perth=1');
await frame.locator('.marker').first().waitFor();
assert.ok(!(await frame.locator('.city').allTextContents()).some(x=>x.startsWith('Perth')),'first view stays on postcode results');
await frame.locator('#reset').click();
assert.ok((await frame.locator('.city').allTextContents()).includes('Perth citywide1'),'show all results reaches every placeable job');
// Long lists render in batches of 60 as the list is scrolled.
await page.goto(base+'?many=1');
await frame.locator('.marker').first().waitFor();
await frame.locator('.marker[title="3000"]').click();
assert.equal(await frame.locator('#jobs .job').count(),60);
for(const expected of [120,152]){
  await frame.locator('#jobs').evaluate(el=>{el.scrollTop=el.scrollHeight;el.dispatchEvent(new Event('scroll'));});
  await frame.locator('#jobs .job').nth(expected-1).waitFor();
  assert.equal(await frame.locator('#jobs .job').count(),expected);
}
console.log('PASS: sheet focus in and out, marker focus kept on rebuild, expired snapshot reloaded and reopened, empty other-locations button hidden, batched long lists, index-only first render, counts, core order, on-demand outline, citywide capital pills, one all-of-Australia pill when zoomed out, other locations with state groups limited to the view, live in-view summary and list, live theme, attribution toggle, phone sheet drag and snap, expired/disabled errors, wrong-channel and stale replies, empty state, record unit, outline failure');
}finally{await browser?.close();await new Promise(r=>server.close(r));}
