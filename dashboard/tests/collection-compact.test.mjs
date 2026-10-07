import * as skuModel from '../src/content/dashboard/sku-model.js';
import * as progressModel from '../src/content/dashboard/collection-progress.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
const project=path.resolve(import.meta.dirname,'../..');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(project,'runtime/data-analytics/1.0.11/scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler(),source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/CollectionControls.jsx'),'utf8');
const {dataUpdateTime}=await import(pathToFileURL(path.join(project,'dashboard/src/content/dashboard/data-time.js')));
const text=n=>typeof n==='string'||typeof n==='number'?String(n):Array.isArray(n)?n.map(text).join(''):text(n?.props?.children??'');
function harness(patch={},options={}){
 const data={browser_mode:'local_chrome',schedule:{mode:'daily',times:['08:00'],at:'',enabled:false,revision:1},jobs:[],...patch};
 let cursor=0;const states=[],requests=[],completionEffects=[];let reloads=0;
 const react={useState(initial){const i=cursor++;if(!(i in states))states[i]=i===0?{url:patch.loadedUrl||'/__pdd_collection_status?shop_id=shop-a',value:data}:initial;return [states[i],v=>states[i]=typeof v==='function'?v(states[i]):v];},useRef(initial){const i=cursor++;return states[i]??(states[i]={current:initial});},useEffect(effect,deps){if(deps.length===2)completionEffects.push(effect);}};
 const jsx=(type,props,key)=>({type,props,key});
 const context={exports:{},module:{exports:{}},require:id=>id.endsWith('collection-progress.js')?progressModel:id.endsWith('sku-model.js')?skuModel:id.endsWith('CollectionControls.jsx')?{SkuDialog:'SkuDialog'}:id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='./data-time.js'?{dataUpdateTime}:{},fetch:async(url,requestOptions)=>{requests.push({url,options:requestOptions});if(options.fetch)return options.fetch(url,requestOptions,data);return {ok:true,json:async()=>requestOptions?.method==='POST'?options.postResult||{message:'已提交程序采集'}:data};},window:{location:{reload(){reloads++;}}},encodeURIComponent,Date,Number,Math,AbortController};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 function collect(node,nodes){if(Array.isArray(node))return node.forEach(n=>collect(n,nodes));if(!node||typeof node!=='object')return;if(typeof node.type==='function')return collect(node.type(node.props),nodes);nodes.push(node);collect(node.props?.children,nodes);}
 return {requests,api:context.exports,get reloads(){return reloads;},update(patch){Object.assign(data,patch);states[0]={url:'/__pdd_collection_status?shop_id=shop-a',value:{...data}};},render(){cursor=0;const nodes=[];collect(context.exports.CollectionControl({shopId:'shop-a',shopName:'合成店',compact:true}),nodes);completionEffects.splice(0).forEach(effect=>effect());return nodes;}};
}
test('one start button and no copy, settings, history or explanatory panel',()=>{
 compiler.parseCss(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/collection-controls.css'),'utf8'));
 const h=harness({browser_mode:'codex_iab',jobs:[{id:'pending',status:'awaiting_host',handoff_text:'legacy command'}]}),nodes=h.render();
 assert.equal(nodes.filter(n=>n.type==='button').length,1);assert.equal(text(nodes.find(n=>n.type==='button')),'开始采集');assert.doesNotMatch(text(nodes),/复制|采集设置|等待 Codex|legacy command/);
});
test('available local worker receives exactly one current-shop request',async()=>{
 const h=harness();await h.render().find(n=>n.type==='button').props.onClick();
 const posts=h.requests.filter(r=>r.options?.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].url,'/__pdd_collection_start');assert.deepEqual(JSON.parse(posts[0].options.body),{shop_id:'shop-a',kind:'shop'});
});
test('unconnected IAB click reports failure and never creates a pretend-running request',async()=>{
 const h=harness({browser_mode:'codex_iab',local_worker_available:false});await h.render().find(n=>n.type==='button').props.onClick();
 assert.equal(h.requests.length,0);assert.match(text(h.render()),/采集器未连接，暂时无法启动/);assert.doesNotMatch(text(h.render()),/等待 Codex|复制|采集中/);
});
test('running job shows real count and blocks duplicate starts',()=>{
 const nodes=harness({jobs:[{id:'running',status:'running',progress:{cards:35}}]}).render();assert.equal(nodes.find(n=>n.type==='button').props.disabled,true);assert.match(text(nodes),/已读取 35 张/);
});

test('running worker awaiting Chrome connection does not claim that collection has started',()=>{
 const nodes=harness({jobs:[{id:'connecting',status:'running',progress:{cards:0,phase:'正在连接你已打开的 Chrome'}}]}).render();
 assert.equal(nodes.find(n=>n.type==='button').props.disabled,true);assert.equal(text(nodes.find(n=>n.type==='button')),'连接 Chrome…');assert.match(text(nodes),/等待 Chrome 连接/);assert.doesNotMatch(text(nodes),/采集中|已读取/);
});
test('unknown evidence and unclosed attempt both block duplicate runs',()=>{
 for(const flag of ['host_status_unavailable','host_attempt_open']){
  const nodes=harness({jobs:[{id:'blocked',status:'manual_review',[flag]:true}]}).render();assert.equal(nodes.find(n=>n.type==='button').props.disabled,true);assert.match(text(nodes),/采集异常/);
  assert.doesNotMatch(text(harness({jobs:[{id:'blocked',status:'running',[flag]:true}]}).render()),/采集中|正在校验/);
 }
});
test('stale other-shop response cannot enable the start button',()=>{
 const nodes=harness({loadedUrl:'/__pdd_collection_status?shop_id=shop-b'}).render();assert.equal(nodes.find(n=>n.type==='button').props.disabled,true);assert.match(text(nodes),/连接中/);
});

test('recovery requires a terminal current-mode failure and does not appear for historical IAB login',()=>{
 const h=harness({jobs:[{id:'old-iab',browser_mode:'codex_iab',status:'needs_login'}]});assert.equal(h.render().some(n=>n.type==='input'),false);assert.doesNotMatch(text(h.render()),/完成登录/);
 assert.equal(harness({latest_job:null,jobs:[{id:'historical',kind:'sku',status:'needs_login'}]}).render().some(n=>n.type==='input'),false);
 for(const patch of [{status:'needs_url',reason:'entry_unavailable'},{status:'manual_review',recovery_required:true,recovery_reason:'zero_products'},{status:'manual_review',recovery_required:true,recovery_reason:'identity_mismatch'}]){
  const nodes=harness({jobs:[{id:'latest',browser_mode:'local_chrome',...patch}]}).render();assert.equal(nodes.some(n=>n.type==='input'),true);assert.match(text(nodes),/粘贴本店最新分享链接/);assert.equal(nodes.find(n=>n.props?.type==='submit').props.disabled,true);
 }
});

test('replacement submits current shop, entered URL and revision in one request',async()=>{
 const h=harness({entry:{revision:4},jobs:[{id:'failed',status:'needs_url',reason:'zero_products'}]});
 h.render().find(n=>n.type==='input').props.onChange({target:{value:' https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC '}});
 const nodes=h.render();assert.equal(nodes.find(n=>n.props?.type==='submit').props.disabled,false);nodes.find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
 const posts=h.requests.filter(r=>r.options?.method==='POST');assert.equal(posts.length,1);assert.deepEqual(JSON.parse(posts[0].options.body),{shop_id:'shop-a',kind:'shop',entry_url:'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC',entry_revision:4});
});

test('HTTP rejection preserves recovery input and reports backend message',async()=>{
 const h=harness({jobs:[{status:'needs_url',reason:'entry_unavailable'}]},{fetch:async(url,options,data)=>({ok:options?.method!=='POST',json:async()=>options?.method==='POST'?{message:'链接设置已更新，请刷新后再试。'}:data})});
 h.render().find(n=>n.type==='input').props.onChange({target:{value:'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC'}});h.render().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
 const nodes=h.render();assert.equal(nodes.find(n=>n.type==='input').props.value,'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC');assert.match(text(nodes),/链接设置已更新，请刷新后再试/);
});

test('manual challenge never offers a replacement URL',()=>{
 const restricted=harness({recovery_required:true,recovery_reason:'access_restricted',jobs:[{status:'manual_review',reason:'access_restricted'}]}).render();assert.equal(restricted.some(n=>n.type==='input'),false);assert.match(text(restricted),/在 Chrome 完成人工验证/);assert.doesNotMatch(text(restricted),/更新链接|专用/);
});

test('website login reuses the same entry without requesting a new link, even with recovery flags',async()=>{
 for(const job of [{status:'needs_login'},{status:'failed',reason:'login_required'},{status:'needs_login',recovery_required:true,recovery_reason:'login_required'}]){
  const h=harness({recovery_required:true,recovery_reason:'login_required',entry:{revision:4},jobs:[{id:'login',browser_mode:'local_chrome',...job}]}),nodes=h.render();
  assert.match(text(nodes),/在原 Chrome 登录拼多多/);assert.match(text(nodes),/登录状态由 Chrome 自动保存/);assert.doesNotMatch(text(nodes),/允许本机采集连接|更新链接|粘贴|专用/);
  assert.equal(nodes.some(n=>n.type==='input'||n.type==='form'),false);assert.equal(nodes.filter(n=>n.type==='button').length,1);assert.equal(nodes.find(n=>n.type==='button').props.disabled,false);
  await nodes.find(n=>n.type==='button').props.onClick();const posts=h.requests.filter(r=>r.options?.method==='POST');assert.equal(posts.length,1);assert.deepEqual(JSON.parse(posts[0].options.body),{shop_id:'shop-a',kind:'shop'});
 }
});

test('Chrome connection permission is distinct from login and URL recovery and remains retryable',async()=>{
 for(const job of [{status:'needs_browser'},{status:'connection_required'},{status:'failed',recovery_reason:'connection_required'}]){
  const h=harness({local_worker_available:false,recovery_required:true,jobs:[{id:'connection',browser_mode:'local_chrome',...job}]}),nodes=h.render();
  assert.match(text(nodes),/请在 Chrome 允许本机采集连接，再点开始采集/);assert.match(text(nodes),/拼多多登录由原 Chrome 保留/);assert.match(text(nodes),/chrome:\/\/inspect\/#remote-debugging/);assert.doesNotMatch(text(nodes),/完成登录|更新链接|专用/);
  assert.equal(nodes.some(n=>n.type==='input'||n.type==='a'),false);assert.equal(nodes.filter(n=>n.type==='button').length,1);assert.equal(nodes.find(n=>n.type==='button').props.disabled,false);
  await nodes.find(n=>n.type==='button').props.onClick();const posts=h.requests.filter(r=>r.options?.method==='POST');assert.equal(posts.length,1);assert.deepEqual(JSON.parse(posts[0].options.body),{shop_id:'shop-a',kind:'shop'});
 }
});

test('validation and completion are distinct and data reload requires a built dashboard receipt',()=>{
 const validating=harness({jobs:[{status:'running',progress:{cards:489,phase:'正在配对备份、恢复副本和验收入库'}}]}).render();assert.match(text(validating),/正在备份并预演入库 · 已读取 489 张/);assert.equal(validating.find(n=>n.props?.className==='collection-start').props.disabled,true);
 const noReceipt=harness({jobs:[{id:'no-receipt',status:'complete',dashboard_built:false}]}).render();assert.match(text(noReceipt),/尚未确认数据舱更新完成/);assert.doesNotMatch(text(noReceipt),/采集完成/);assert.equal(noReceipt.filter(n=>n.type==='button').length,1);
 const h=harness({jobs:[{id:'built',status:'complete',dashboard_built:true}]}),button=h.render().find(n=>text(n)==='查看最新数据'&&n.type==='button');assert.ok(button);assert.equal(h.reloads,0);button.props.onClick();assert.equal(h.reloads,1);
});

test('published data stage is distinct from scraping and shows elapsed time',()=>{
 const data={browser_mode:'local_chrome',jobs:[{status:'running',started_at:'2026-10-06T02:30:00Z',progress:{cards:506,phase:'正在更新数据舱'}}]},h=harness(data),nodes=h.render();
 assert.equal(text(nodes.find(n=>n.props?.className==='collection-start')),'更新数据舱…');assert.doesNotMatch(text(nodes),/采集中/);assert.match(text(nodes),/正在更新数据舱 · 已读取 506 张/);
 assert.match(h.api.collectionView(data,Date.parse('2026-10-06T02:33:20Z')).progress,/已用 3分20秒/);
 assert.doesNotMatch(h.api.collectionView({...data,jobs:[{...data.jobs[0],started_at:'invalid'}]}).progress,/NaN|已用/);
});

test('a failed dashboard publish is visible without pretending that new data is displayed',()=>{
 const nodes=harness({jobs:[{status:'complete',reason:'dashboard_build_failed',dashboard_built:false}]}).render();
 assert.match(text(nodes),/数据已入库，数据舱更新失败/);assert.doesNotMatch(text(nodes),/采集完成|查看最新数据/);
});

test('release stages name the current work in label button and aria while preserving confirmed images',()=>{
 for(const [stage,phase,button,aria] of [
  ['release_preparation','正在备份并预演入库','备份验收中…','数据备份与入库预演进度'],
  ['release_commit','正在保存商品数据','正在保存…','商品数据保存进度'],
  ['dashboard_build','正在更新数据舱','更新数据舱…','数据舱更新进度'],
  ['image_validation','正在更新数据舱','更新数据舱…','数据舱更新进度'],
  ['image_validation','正在配对备份、恢复副本和验收入库','备份验收中…','数据备份与入库预演进度'],
 ]){
  const nodes=harness({jobs:[{kind:'shop',status:'running',progress:{stage,phase,cards:555,image_total:555,image_saved:555,image_missing:0}}]}).render();
  const start=nodes.find(n=>n.props?.className==='collection-start'),bar=nodes.find(n=>n.props?.role==='progressbar');
  assert.equal(text(start),button);assert.equal(start.props.disabled,true);
  assert.equal(bar.props['aria-label'],aria);assert.equal(bar.props['aria-valuenow'],undefined);
  assert.match(text(nodes),/图片已齐 555 \/ 555 · 待补 0/);
  assert.doesNotMatch(text(nodes),/图片校验中|正在核对图片缓存|100%|预计/);
 }
});

test('a held collection lock exposes an actionable reason and accessible details, not only a hover title',()=>{
 const job={id:'collect_634778562f1041c58c382e52a496f6eb',status:'manual_review',reason:'capture_lock_held',message:'raw private error',ended_at:'2026-10-06T12:40:00Z'};
 const h=harness({latest_job:job,jobs:[job]}),nodes=h.render();
 assert.match(text(nodes.find(n=>n.props?.role==='alert')),/上次采集尚未释放，暂时不能启动/);
 const details=nodes.find(n=>n.type==='details'&&n.props.className==='collection-failure-details');assert.ok(details);
 assert.match(text(details),/查看原因与处理方式/);assert.match(text(details),/独占锁仍被占用/);assert.match(text(details),/连续重试不会解除占用/);
 assert.match(text(details),/collect_634778562f1041c58c382e52a496f6eb/);assert.match(text(details),/2026-10-06 20:40/);assert.match(text(details),/capture_lock_held/);
 assert.doesNotMatch(text(nodes),/采集未完成，请重试或查看异常|raw private error|采集完成/);
 assert.equal(nodes.find(n=>n.props?.role==='alert').props.title,undefined);assert.equal(h.requests.length,0);
});

test('an explicit permission failure is distinct from an unknown local worker failure',()=>{
 const base={id:'permission-case',status:'failed',reason:'local_worker_failed_requires_review',message:'PermissionError: C:/private/credentials/token.txt'};
 const permission=harness({jobs:[{...base,error_type:'PermissionError'}]}).render();
 assert.match(text(permission),/本地文件访问权限问题/);assert.match(text(permission),/检查项目目录和文件的访问权限、是否被占用/);assert.doesNotMatch(text(permission),/C:\/private|credentials|token\.txt|PermissionError:/);
 const unknown=harness({jobs:[base]}).render();assert.match(text(unknown),/本机采集程序异常停止/);assert.doesNotMatch(text(unknown),/文件访问权限问题/);
});

test('unknown failures retain a supportable task reference without rendering unreviewed exception content',()=>{
 const nodes=harness({jobs:[{id:'collect_unknown',status:'failed',reason:'https://private.example/?token=secret',message:'Traceback raw-secret',error_type:'raw-secret'}]}).render();
 assert.match(text(nodes),/具体原因尚未确认/);assert.match(text(nodes),/collect_unknown/);assert.doesNotMatch(text(nodes),/private\.example|token=secret|raw-secret|采集完成/);
});

test('unclosed attempts provide details while continuing to block new starts',()=>{
 const nodes=harness({jobs:[{id:'open-attempt',status:'manual_review',host_attempt_open:true}]}).render();
 assert.equal(nodes.find(n=>n.props?.className==='collection-start').props.disabled,true);assert.match(text(nodes),/之前的任务尚未确认安全结束/);assert.ok(nodes.some(n=>n.type==='details'));
});

test('SKU stage has separate progress after store publication and reports incomplete SKU results honestly',()=>{
 const nodes=harness({jobs:[{kind:'shop',status:'running',dashboard_built:true,progress:{cards:512,sku_total:250,sku_completed:10,sku_partial:2,sku_failed:1,phase:'正在采集 SKU'}}]}).render();
 assert.match(text(nodes),/全店数据已入库/);assert.match(text(nodes),/SKU 已处理 13 \/ 250 个商品/);assert.equal(text(nodes.find(n=>n.props?.className==='collection-start')),'SKU 采集中…');
 const done=harness({jobs:[completedJob({sku_summary:{status:'partial',total:250,completed:248,partial:1,failed:1,remaining:0}})]}).render();assert.match(text(done),/SKU 完整 248 \/ 250 个商品 · 部分 1 · 未成功 1/);
});

test('SKU validation is shown as saving verification instead of more browser reads',()=>{
 const nodes=harness({jobs:[{kind:'shop',status:'running',dashboard_built:false,progress:{stage:'sku_validation',sku_total:250,sku_completed:10,phase:'SKU 已读取，正在备份并校验保存规格'}}]}).render();assert.equal(text(nodes.find(n=>n.props?.className==='collection-start')),'SKU 校验中…');assert.match(text(nodes),/全店数据已入库 · SKU 正在备份并校验保存/);assert.doesNotMatch(text(nodes),/SKU 已处理|SKU 采集中/);
});

test('pending IAB is not collection progress and shop identity remounts all local input state',()=>{
 const h=harness({browser_mode:'codex_iab',jobs:[{status:'awaiting_host'}]});assert.doesNotMatch(text(h.render()),/采集中|已读取|正在校验/);
 assert.equal(h.api.CollectionControl({shopId:'shop-a'}).key,'shop-a');assert.equal(h.api.CollectionControl({shopId:'shop-b'}).key,'shop-b');
});

test('double click submits once and a queued recovery clears the stale form without pretending it is running',async()=>{
 let release;const queued={id:'new-job',status:'queued',browser_mode:'local_chrome'};
 const h=harness({jobs:[{id:'old-job',status:'needs_url',reason:'entry_unavailable'}]},{fetch:async(url,options,data)=>{
  if(options?.method==='POST'){await new Promise(resolve=>release=resolve);data.jobs=[queued,...data.jobs];data.latest_job=queued;data.recovery_required=false;return {ok:true,json:async()=>({job:queued})};}
  return {ok:true,json:async()=>data};
 }});
 h.render().find(n=>n.type==='input').props.onChange({target:{value:'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC'}});
 const form=h.render().find(n=>n.type==='form');form.props.onSubmit({preventDefault(){}});form.props.onSubmit({preventDefault(){}});assert.equal(h.requests.filter(r=>r.options?.method==='POST').length,1);
 release();await new Promise(resolve=>setImmediate(resolve));const nodes=h.render();assert.equal(nodes.some(n=>n.type==='input'),false);assert.match(text(nodes),/等待采集器启动/);assert.doesNotMatch(text(nodes),/采集中/);assert.equal(nodes.find(n=>n.props?.className==='collection-start').props.disabled,true);
});

const completedJob=(patch={})=>({id:'completed-current',shop_id:'shop-a',kind:'shop',browser_mode:'local_chrome',status:'complete',dashboard_built:true,ended_at:'2026-10-06T03:23:59Z',progress:{cards:512},receipt:{ok:true,status:'finished',shop_id:'shop-a',run_id:'run_synthetic',snapshot_status:'complete',new_run:{status:'complete',cards:512,image_refs:patch.image_summary?.saved??512,observed_to:'2026-10-06T03:21:25Z'}},...patch});
test('existing completed task is a quiet summary with imported count and completion minute, distinct from observation time',()=>{
 const h=harness({jobs:[completedJob()]}),nodes=h.render();h.render();
 assert.match(text(nodes),/最近一次采集已完成/);assert.match(text(nodes),/本店 512 张商品/);assert.match(text(nodes),/完成时间：2026-10-06 11:23（北京时间）/);
 assert.doesNotMatch(text(nodes),/11:21|11:23:59|本店采集完成，数据已更新/);
 const banner=nodes.find(n=>n.props?.className==='collection-completion');assert.equal(banner.props['aria-live'],'off');assert.equal(banner.props.role,undefined);
});

test('completed shop is independent of historical SKU outcomes and keeps those outcomes collapsed',()=>{
 for(const status of ['partial','needs_browser','needs_login','manual_review','failed','cancelled']){
  const h=harness({jobs:[completedJob({sku_summary:{status,total:250,completed:0,failed:1,remaining:249,reason:status==='needs_browser'?'connection_required':undefined}})]}),nodes=h.render();
  const banner=nodes.find(n=>n.props?.className==='collection-completion');assert.match(text(banner),/最近一次采集已完成/);assert.doesNotMatch(text(banner),/SKU/);
  const history=nodes.find(n=>n.type==='details');assert.match(text(history),/历史 SKU 采集结果/);assert.match(text(history),/SKU 完整 0 \/ 250/);assert.equal(history.props.open,undefined);assert.equal(history.props.role,undefined);
  assert.equal(text(nodes.find(n=>n.props?.className==='collection-completion-mark')),'✓');assert.ok(nodes.some(n=>n.type==='button'&&text(n)==='查看最新数据'));
 }
 for(const status of ['pending','not_started','queued'])assert.match(text(harness({jobs:[completedJob({sku_summary:{status}})]}).render()),/最近一次采集已完成/);
});

test('new shop completion announces product and image success while old SKU failure stays historical',()=>{
 const job=completedJob({id:'sku-new-stop',sku_summary:{status:'needs_browser',total:250,completed:0,failed:1,remaining:249,reason:'connection_required'}}),h=harness({jobs:[{...job,status:'running',dashboard_built:false}]});
 h.render();h.update({jobs:[job]});h.render();const nodes=h.render();assert.match(text(nodes),/本店采集完成，数据已更新/);assert.match(text(nodes.find(n=>n.type==='details')),/允许本机采集连接/);
 const banner=nodes.find(n=>n.props?.className?.includes('collection-completion-new'));assert.equal(banner.props.role,'status');
 assert.match(text(harness({jobs:[completedJob({sku_summary:{status:'complete',total:2,completed:2}})]}).render()),/最近一次采集已完成/);
 assert.equal(h.api.collectionCompletionTitle({skuOutcome:'complete',imageSummary:{status:'complete'}},true),'本店采集完成，数据已更新');assert.equal(h.api.collectionCompletionTitle({skuOutcome:null},true),'采集未完成：图片完整性待核验');
});

test('observing a running task then successful publication produces one persistent completion announcement',()=>{
 const job=completedJob(),h=harness({jobs:[{...job,status:'running',dashboard_built:false}]});
 h.render();h.update({jobs:[job]});h.render();const nodes=h.render();
 assert.match(text(nodes),/本店采集完成，数据已更新/);assert.equal(nodes.find(n=>n.props?.className==='collection-completion collection-completion-new').props.role,'status');
 const tracker={shopId:'shop-a',pending:new Set([job.id]),delivered:new Set()};
 assert.equal(h.api.collectionCompletionEvent(tracker,{latest:job},'shop-a').id,job.id);
 assert.equal(h.api.collectionCompletionEvent(tracker,{latest:job},'shop-a'),null);
 tracker.pending.add(job.id);assert.equal(h.api.collectionCompletionEvent(tracker,{latest:job},'shop-a'),null);
});

test('fast start response completed before the next poll still announces this submitted task',async()=>{
 const job=completedJob({id:'fast-job'}),h=harness({}, {fetch:async(url,options,data)=>{
  if(options?.method==='POST'){Object.assign(data,{latest_job:job,jobs:[job]});return {ok:true,json:async()=>({job})};}
  return {ok:true,json:async()=>data};
 }});
 await h.render().find(n=>n.props?.className==='collection-start').props.onClick();h.render();
 assert.match(text(h.render()),/本店采集完成，数据已更新/);assert.equal(h.requests.filter(r=>r.options?.method==='POST').length,1);
});

test('switching shops or mounting an already completed task never invents a completion event',()=>{
 const h=harness(),job=completedJob(),tracker={shopId:'shop-a',pending:new Set([job.id]),delivered:new Set()};
 assert.equal(h.api.collectionCompletionEvent(tracker,{latest:{...job,shop_id:'shop-b'}},'shop-b'),null);
 assert.equal(h.api.collectionCompletionEvent(tracker,{latest:job},'shop-a'),null);
 assert.equal(h.api.collectionCompletion({...job,shop_id:'shop-b'},'shop-a'),null);
});

test('partial, failed, cancelled and unpublished outcomes cannot produce success notices',()=>{
 const h=harness();
 for(const patch of [{status:'partial'},{status:'failed'},{status:'cancelled'},{status:'manual_review'},{dashboard_built:false},{dashboard_built:undefined},{reason:'dashboard_build_failed'}]){
  const job=completedJob(patch),tracker={shopId:'shop-a',pending:new Set([job.id]),delivered:new Set()};
  assert.equal(h.api.collectionCompletionEvent(tracker,{latest:job},'shop-a'),null);assert.equal(h.api.collectionCompletion(job,'shop-a'),null);
  const nodes=harness({jobs:[job]}).render();assert.doesNotMatch(text(nodes),/本店采集完成，数据已更新|最近一次采集已完成|查看最新数据/);
 }
});

const standaloneBatch=(patch={})=>({id:'standalone-batch',shop_id:'shop-a',kind:'sku_batch',browser_mode:'local_chrome',status:'running',created_at:'2026-10-06T04:00:00Z',started_at:'2026-10-06T04:00:01Z',progress:{stage:'sku',sku_total:248,sku_completed:4,sku_partial:1,sku_failed:1,sku_remaining:242},...patch});

test('independent running batch supersedes older shop summary without claiming a new shop import',()=>{
 const batch=standaloneBatch(),h=harness({latest_job:completedJob({created_at:'2026-10-06T02:00:00Z'}),latest_sku_batch_job:batch,jobs:[batch]});
 const nodes=h.render();assert.match(text(nodes),/补采规格 · SKU 已处理 6 \/ 248 个商品/);assert.doesNotMatch(text(nodes),/全店数据已入库|本店采集完成|最近一次采集已完成|本店 512/);assert.equal(nodes.find(n=>n.props?.className==='collection-start').props.disabled,true);assert.ok(nodes.some(n=>n.type==='button'&&text(n)==='停止 SKU'));
 h.update({latest_sku_batch_job:{...batch,progress:{...batch.progress,stage:'sku_validation'}},jobs:[{...batch,progress:{...batch.progress,stage:'sku_validation'}}]});assert.match(text(h.render()),/SKU 正在备份并校验保存/);assert.doesNotMatch(text(h.render()),/全店数据已入库/);
});

test('independent SKU batch completion announces once without a dashboard build and does not relabel its scope as shop cards',()=>{
 const running=standaloneBatch({id:'batch-announce'}),done={...running,status:'complete',ended_at:'2026-10-06T05:00:00Z',sku_summary:{status:'complete',total:248,completed:248,partial:0,failed:0,remaining:0,scope_total:250,already_complete:2,source_run_id:'synthetic-run'}};
 const h=harness({latest_job:completedJob({created_at:'2026-10-06T02:00:00Z'}),latest_sku_batch_job:running,jobs:[running]});h.render();h.update({latest_sku_batch_job:done,jobs:[done]});h.render();const nodes=h.render();
 assert.match(text(nodes),/SKU 采集完成/);assert.match(text(nodes),/本轮 SKU 完整 248 \/ 248 个商品 · 已有完整 2 · 全部有销量 250/);assert.match(text(nodes),/本轮规格采集 · 结束时间：2026-10-06 13:00/);assert.doesNotMatch(text(nodes),/全店数据已更新|本店 248|尚未确认数据舱/);
 const banner=nodes.find(n=>n.props?.className==='collection-completion collection-completion-new');assert.equal(banner.props.role,'status');assert.ok(h.api.collectionCompletion(done,'shop-a'));
 const tracker={shopId:'shop-a',pending:new Set([done.id]),delivered:new Set()};assert.equal(h.api.collectionCompletionEvent(tracker,{latest:done},'shop-a').id,done.id);assert.equal(h.api.collectionCompletionEvent(tracker,{latest:done},'shop-a'),null);
});

test('already ended batch partial or stopped result is quiet collapsed history with no active prompt',()=>{
 const batch=standaloneBatch({status:'partial',ended_at:'2026-10-06T05:00:00Z',sku_summary:{status:'partial',total:248,completed:246,partial:1,failed:1,remaining:0}});
 const nodes=harness({latest_sku_batch_job:batch,jobs:[batch]}).render();assert.match(text(nodes),/SKU 部分完成/);assert.match(text(nodes),/本轮 SKU 完整 246 \/ 248 个商品 · 部分 1 · 未成功 1/);assert.doesNotMatch(text(nodes),/尚未确认数据舱|全店数据已更新/);
 const stopped={...batch,status:'needs_browser',reason:'connection_required',sku_summary:{...batch.sku_summary,status:'needs_browser',stop_message:'Chrome 页面操作已超时，本轮已停止。'}};
 const stoppedNodes=harness({latest_sku_batch_job:stopped,jobs:[stopped]}).render();assert.match(text(stoppedNodes),/SKU 采集未完成/);assert.match(text(stoppedNodes),/Chrome 页面操作已超时/);assert.doesNotMatch(text(stoppedNodes),/chrome:\/\/inspect|允许本机采集连接|尚未确认数据舱/);
 for(const result of [nodes,stoppedNodes]){const history=result.find(n=>n.type==='details');assert.match(text(history),/历史 SKU 采集结果/);assert.equal(history.props.open,undefined);assert.equal(result.some(n=>n.props?.role==='status'||n.props?.role==='alert'),false);assert.equal(result.some(n=>n.props?.className?.startsWith('collection-completion')),false);assert.equal(result.find(n=>n.props?.className==='collection-start').props.disabled,false);}
});

test('newer shop takes precedence over a historical completed SKU batch',()=>{
 const batch=standaloneBatch({status:'complete',sku_summary:{status:'complete',total:1,completed:1}}),shop=completedJob({created_at:'2026-10-06T06:00:00Z'});
 const nodes=harness({latest_job:shop,latest_sku_batch_job:batch,jobs:[shop,batch]}).render();assert.match(text(nodes),/本店 512 张商品/);assert.doesNotMatch(text(nodes),/本轮规格采集/);
});

test('a newer ended batch cannot replace shop completion or turn its failure into a current connection prompt',()=>{
 const batch=standaloneBatch({status:'needs_browser',ended_at:'2026-10-06T05:00:00Z',reason:'connection_required',sku_summary:{status:'needs_browser',reason:'connection_required',total:2,completed:1,failed:1}}),shop=completedJob({created_at:'2026-10-06T02:00:00Z'});
 const h=harness({latest_job:shop,latest_sku_batch_job:batch,jobs:[batch,shop]}),nodes=h.render(),banner=nodes.find(n=>n.props?.className==='collection-completion');
 assert.match(text(banner),/最近一次采集已完成/);assert.match(text(banner),/本店 512 张商品/);assert.doesNotMatch(text(banner),/SKU|连接/);assert.match(text(nodes.find(n=>n.type==='details')),/历史 SKU 采集结果.*SKU 采集未完成/);assert.doesNotMatch(text(nodes),/chrome:\/\/inspect/);assert.equal(nodes.some(n=>n.props?.role==='alert'),false);assert.equal(nodes.find(n=>n.props?.className==='collection-start').props.disabled,false);
 assert.equal(h.api.collectionView({browser_mode:'local_chrome',latest_job:shop,latest_sku_batch_job:batch,jobs:[batch,shop]}).latest.id,shop.id);
});

test('stop SKU sends the exact running batch id once and keeps start disabled until safe stop',async()=>{
 const batch=standaloneBatch(),h=harness({latest_sku_batch_job:batch,jobs:[batch]},{fetch:async(url,options,data)=>{
  if(options?.method==='POST'){const current={...batch,cancel_requested:true};Object.assign(data,{latest_sku_batch_job:current,jobs:[current]});return {ok:true,json:async()=>({message:'将在安全边界停止'})};}
  return {ok:true,json:async()=>data};
 }});
 const stop=h.render().find(n=>n.type==='button'&&text(n)==='停止 SKU');const first=stop.props.onClick(),second=stop.props.onClick();await Promise.all([first,second]);
 const posts=h.requests.filter(r=>r.options?.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].url,'/__pdd_collection_cancel');assert.deepEqual(JSON.parse(posts[0].options.body),{shop_id:'shop-a',id:batch.id});
 const nodes=h.render();assert.match(text(nodes),/已请求停止 SKU，正在保存已读取规格/);assert.equal(nodes.find(n=>n.props?.className==='collection-start').props.disabled,true);assert.equal(nodes.find(n=>n.type==='button'&&text(n)==='正在停止 SKU…').props.disabled,true);
});

test('rendered progress exposes independent accessible batch and current-spec bars with elapsed time',()=>{
 const batch=standaloneBatch({progress:{stage:'sku',phase:'逐项读取 SKU 价格和图片',sku_total:10,sku_completed:2,sku_partial:1,sku_failed:1,variants:3,total:6}});
 const nodes=harness({latest_sku_batch_job:batch,jobs:[batch]}).render(),bars=nodes.filter(n=>n.props?.role==='progressbar');
 assert.equal(bars.length,2);assert.equal(bars[0].props['aria-label'],'SKU 商品处理进度');assert.equal(bars[0].props['aria-valuenow'],40);assert.equal(bars[0].props['aria-valuemax'],100);assert.match(bars[0].props['aria-valuetext'],/完整 2，部分 1，失败 1/);
 assert.equal(bars[1].props['aria-valuenow'],50);assert.match(text(nodes),/完整 2部分 1失败 1/);assert.match(text(nodes),/3 \/ 6 个规格 · 50%/);assert.match(text(nodes),/已用/);assert.match(text(nodes),/逐项读取 SKU 价格和图片/);assert.doesNotMatch(text(nodes),/预计|剩余.*分钟|ETA/);
});

test('unknown store and save/build stages render indeterminate bars without fake aria percentages',()=>{
 for(const progress of [{cards:521,phase:'正在读取全店商品'},{stage:'sku_validation',sku_total:250,sku_completed:250,phase:'SKU 已读取，正在备份并校验保存规格'},{cards:521,phase:'正在更新数据舱'}]){
  const nodes=harness({jobs:[{kind:'shop',status:'running',progress}]}).render(),bars=nodes.filter(n=>n.props?.role==='progressbar');assert.equal(bars.length,1);assert.equal(bars[0].props['aria-valuenow'],undefined);assert.match(bars[0].props.className,/indeterminate/);assert.doesNotMatch(text(nodes),/100%|NaN%/);
 }
});

test('one hundred percent with failed products stays labelled traversal and uses failure segment only',()=>{
 const batch=standaloneBatch({progress:{stage:'sku',sku_total:2,sku_completed:0,sku_partial:0,sku_failed:2}}),nodes=harness({latest_sku_batch_job:batch,jobs:[batch]}).render();
 const bar=nodes.find(n=>n.props?.role==='progressbar');assert.equal(bar.props['aria-valuenow'],100);assert.match(text(nodes),/完整 0部分 0失败 2/);assert.match(text(nodes),/商品已遍历，仍有未完整项/);
 assert.equal(nodes.find(n=>n.props?.className==='collection-progress-segment collection-progress-complete').props.style.width,'0%');assert.equal(nodes.find(n=>n.props?.className==='collection-progress-segment collection-progress-failed').props.style.width,'100%');assert.doesNotMatch(text(nodes),/SKU 采集完成/);
});

test('top control identifies a single SKU job and preserves exact list, pagination or direct-open phase',()=>{
 const job={id:'single-top',kind:'sku',shop_id:'shop-a',observation_id:12,status:'running'};
 for(const phase of ['在本店列表定位商品，核对标题和主图','正在翻页定位商品 · 第 3 次','直接打开已核实商品链接','逐项读取 SKU 价格和图片']){
  const nodes=harness({jobs:[{...job,progress:{phase}}]}).render();assert.match(text(nodes),new RegExp(`单品 SKU · ${phase}`));assert.equal(text(nodes.find(n=>n.props?.className==='collection-start')),'SKU 采集中…');assert.doesNotMatch(text(nodes),/全店数据已入库|采集本店|已读取 \d+ 张/);
 }
 const saving=harness({jobs:[{...job,progress:{phase:'SKU 已读取，正在备份和校验保存'}}]}).render();assert.match(text(saving),/单品 SKU 已读取，正在核验并保存/);assert.equal(text(saving.find(n=>n.props?.className==='collection-start')),'SKU 校验中…');assert.doesNotMatch(text(saving),/全店数据已入库|100%|备份并校验/);
});

test('image repair and verification have distinct honest progress and concise image counts',()=>{
 const job={id:'images-live',shop_id:'shop-a',kind:'shop',status:'running',progress:{stage:'images',image_total:100,image_saved:80,image_missing:20,image_attempted:95}};
 const nodes=harness({jobs:[job]}).render();assert.equal(text(nodes.find(n=>n.props?.className==='collection-start')),'正在补图…');assert.match(text(nodes),/正在核对并补齐商品图片/);assert.match(text(nodes),/图片已齐 80 \/ 100 · 待补 20/);assert.equal(nodes.find(n=>n.props?.role==='progressbar').props['aria-valuenow'],80);assert.doesNotMatch(text(nodes),/采集完成|预计|剩余.*分钟/);
 const verifying=harness({jobs:[{...job,progress:{...job.progress,stage:'image_validation'}}]}).render();assert.equal(text(verifying.find(n=>n.props?.className==='collection-start')),'图片校验中…');assert.equal(verifying.find(n=>n.props?.role==='progressbar').props['aria-valuenow'],undefined);assert.match(text(verifying),/正在核对图片缓存和本轮主图/);assert.match(text(verifying),/待补 20/);
});

test('a published collection with missing pictures reports updated data without all-complete success',()=>{
 for(const status of ['complete','partial']){
  const job=completedJob({id:`image-missing-${status}`,status,image_summary:{total:512,saved:510,missing:2,status:'partial'}}),h=harness({jobs:[{...job,status:'running',dashboard_built:false}]});
  h.render();h.update({jobs:[job]});h.render();const nodes=h.render();assert.match(text(nodes),/采集未完成：仍有 2 张商品主图待补/);assert.match(text(nodes),/图片已齐 510 \/ 512 · 待补 2/);assert.doesNotMatch(text(nodes),/最近一次采集已完成|本店采集完成|完成时间|采集未完成，请重试/);
  assert.equal(text(nodes.find(n=>n.props?.className==='collection-completion-mark')),'!');assert.equal(nodes.find(n=>n.props?.className?.includes('collection-completion-new')).props.role,'status');assert.ok(nodes.some(n=>n.type==='button'&&text(n)==='查看最新数据'));assert.ok(nodes.some(n=>n.props?.className?.includes('collection-completion-incomplete')));
 }
});

test('a newer published recovery keeps 15 missing images visible and supersedes preserved failed attempts',()=>{
 const previous=completedJob({id:'preserved-failure',status:'failed',reason:'local_file_access_failed',error_type:'PermissionError',dashboard_built:false,created_at:'2026-10-06T12:40:00Z'});
 const restored=completedJob({id:'recovery-success',trigger:'recovery',kind:'shop',status:'partial',created_at:'2026-10-06T13:00:00Z',receipt:{...completedJob().receipt,new_run:{status:'complete',cards:524,image_refs:509}},progress:{cards:524},image_summary:{total:524,saved:509,missing:15,status:'partial'}});
 const h=harness({latest_job:restored,jobs:[restored,previous]}),nodes=h.render();
 assert.match(text(nodes),/采集未完成：仍有 15 张商品主图待补/);assert.match(text(nodes),/图片已齐 509 \/ 524 · 待补 15/);assert.match(text(nodes),/本店 524 张商品/);
 assert.doesNotMatch(text(nodes),/本地文件访问失败|本店采集完成|最近一次采集已完成/);assert.equal(nodes.some(n=>n.type==='details'&&n.props.className==='collection-failure-details'),false);assert.ok(nodes.some(n=>n.type==='button'&&text(n)==='查看最新数据'));
});

test('missing-image updates require a published receipt and cannot disguise an unpublished or stopped job',()=>{
 const h=harness(),base=completedJob({status:'partial',image_summary:{total:512,saved:510,missing:2,status:'partial'}});
 for(const patch of [{receipt:null},{dashboard_built:false},{status:'cancelled'},{status:'failed'},{reason:'dashboard_build_failed'}]){
  const job={...base,...patch};assert.equal(h.api.collectionCompletion(job,'shop-a'),null);assert.doesNotMatch(text(harness({jobs:[job]}).render()),/商品数据已更新|本店采集完成|最近一次采集已完成|查看最新数据/);
 }
 const valid=h.api.collectionCompletion(base,'shop-a');assert.equal(valid.imageSummary.missing,2);
 const unknown=harness({jobs:[completedJob({image_summary:{total:512,saved:512,missing:1,status:'complete'}})]}).render();assert.match(text(unknown),/图片完整性待核验/);assert.doesNotMatch(text(unknown),/最近一次采集已完成|本店采集完成/);
 const wrongScope=harness({jobs:[completedJob({image_summary:{total:500,saved:500,missing:0,status:'complete'}})]}).render();assert.match(text(wrongScope),/图片完整性待核验/);assert.doesNotMatch(text(wrongScope),/最近一次采集已完成|本店采集完成|图片已齐 500/);
 const done=harness({jobs:[completedJob({image_summary:{total:512,saved:512,missing:0,status:'complete'}})]}).render();assert.match(text(done),/最近一次采集已完成/);assert.match(text(done),/图片已齐 512 \/ 512 · 待补 0/);assert.equal(text(done.find(n=>n.props?.className==='collection-completion-mark')),'✓');
});

test('complete worker status or alleged all-images summary without independent release evidence cannot show success',()=>{
 for(const patch of [{receipt:null},{receipt:{new_run:{cards:512,image_refs:512}}},
                    {receipt:{...completedJob().receipt,new_run:{status:'complete',cards:512}}}]){
  const job=completedJob({image_summary:{total:512,saved:512,missing:0,status:'complete'},...patch});
  const nodes=harness({jobs:[job]}).render();assert.match(text(nodes),/采集未完成：图片完整性待核验/);
  assert.doesNotMatch(text(nodes),/最近一次采集已完成|本店采集完成|完成时间/);
  assert.equal(text(nodes.find(n=>n.props?.className==='collection-completion-mark')),'!');
 }
});

test('a separately verified image recovery confirms shop coverage independently of unstarted historical SKU',()=>{
 const images={total:554,saved:554,missing:0,status:'complete'};
 const job=completedJob({receipt:{...completedJob().receipt,new_run:{status:'complete',cards:554,image_refs:416}},
  image_summary:images,image_recovery:{run_id:'run_synthetic'},
  image_verification:{...images,verified:true,basis:'current_run_image_refs',shop_id:'shop-a',run_id:'run_synthetic'},
  sku_summary:{status:'not_started',message:'SKU 尚未开始。'}});
 const nodes=harness({jobs:[job]}).render();assert.match(text(nodes),/图片已齐 554 \/ 554 · 待补 0/);assert.match(text(nodes),/最近一次采集已完成/);assert.match(text(nodes.find(n=>n.type==='details')),/SKU 尚未开始/);
 assert.doesNotMatch(text(nodes),/SKU 待采集|完整 0 \/ 0|图片完整性待核验/);
});
