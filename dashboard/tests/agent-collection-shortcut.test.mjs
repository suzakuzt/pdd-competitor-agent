import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as skuModel from '../src/content/dashboard/sku-model.js';
import * as progressModel from '../src/content/dashboard/collection-progress.js';
import {dataUpdateTime} from '../src/content/dashboard/data-time.js';

const project=path.resolve(import.meta.dirname,'../..');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(project,'runtime/data-analytics/1.0.11/scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler();
const source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/AgentCollectionShortcut.jsx'),'utf8');
const controls=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/CollectionControls.jsx'),'utf8');
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children??'');
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});
const queries={runs:{rows:[{shop_id:'shop-a',run_id:'run-a'},{shop_id:'shop-b',run_id:'run-b'}]},competitor_shops:{rows:[{shop_id:'shop-a',shop_name:'同名店'},{shop_id:'shop-b',shop_name:'同名店'}]},competitor_targets:{rows:[]}};
const status=patch=>({browser_mode:'local_chrome',local_worker_available:true,jobs:[],...patch});
const job=(patch={})=>({id:'collect-synthetic-a',shop_id:'shop-a',kind:'shop',status:'queued',...patch});
const releasedJob=patch=>job({status:'complete',dashboard_built:true,ended_at:'2026-10-07T00:00:00Z',receipt:{ok:true,status:'finished',run_id:'run-a',shop_id:'shop-a',snapshot_status:'complete',new_run:{status:'complete',cards:5,image_refs:5}},image_summary:{total:5,saved:5,missing:0,status:'complete'},...patch});

function harness(options={}){
 let cursor=0,props={shopId:'shop-a',shopName:'同名店',queries},reloads=0;
 const states=[],effects=[],requests=[],copied=[],intervals=new Map();let intervalId=0;
 const react={
  useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return [states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];},
  useRef(initial){const i=cursor++;return states[i]??(states[i]={current:initial});},
  useEffect(effect,deps){const i=cursor++,previous=states[i];if(!previous||deps.some((value,index)=>value!==previous.deps[index])){states[i]={deps,cleanup:previous?.cleanup};effects.push(()=>{states[i].cleanup?.();states[i].cleanup=effect();});}}
 };
 const jsx=(type,props,key)=>({type,props,key});
 const context={exports:{},module:{exports:{}},require:id=>id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id.endsWith('sku-model.js')?skuModel:id.endsWith('collection-progress.js')?progressModel:id.endsWith('data-time.js')?{dataUpdateTime}:{},fetch:async(url,init)=>{requests.push({url,init});if(options.fetch)return options.fetch(url,init);return response(init?.method==='POST'?{job:job({shop_id:JSON.parse(init.body).shop_id})}:status(options.status),init?.method==='POST'?202:200);},navigator:options.clipboard===false?{}:{clipboard:{writeText:options.writeText||(async(value)=>{copied.push(value);})}},setInterval:fn=>{intervals.set(++intervalId,fn);return intervalId;},clearInterval:id=>intervals.delete(id),window:{location:{reload(){reloads++;}}},AbortController,encodeURIComponent,Date,Number,Boolean,Array,Object,Set,Math};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(controls,{commonjs:true}),context);
 const shared=context.exports;context.exports={};context.module.exports=context.exports;const require=context.require;context.require=id=>id.endsWith('CollectionControls.jsx')?shared:require(id);
 vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 const collect=(node,nodes)=>{if(Array.isArray(node))return node.forEach(item=>collect(item,nodes));if(!node||typeof node!=='object')return;if(typeof node.type==='function')return collect(node.type(node.props),nodes);nodes.push(node);collect(node.props?.children,nodes);};
 return {requests,copied,api:context.exports,get reloads(){return reloads;},setProps(next){props={...props,...next};},render(){cursor=0;const nodes=[];collect(context.exports.AgentCollectionShortcut(props),nodes);effects.splice(0).forEach(effect=>effect());return nodes;},async flush(){await new Promise(resolve=>setImmediate(resolve));},poll(){for(const callback of intervals.values())callback();},dispose(){for(const state of states)state?.cleanup?.();intervals.clear();}};
}
const button=nodes=>nodes.find(node=>node.props?.className==='pdd-agent-capture-start');
async function mounted(options){const h=harness(options);h.render();await h.flush();return h;}

test('shortcut loads status only until a click and then uses one fixed current-shop endpoint',async()=>{
 compiler.parseCss(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/agent-search.css'),'utf8'));
 const h=await mounted();assert.equal(h.requests.some(request=>request.init?.method==='POST'),false);
 assert.equal(button(h.render()).props.disabled,false);await button(h.render()).props.onClick();
 const posts=h.requests.filter(request=>request.init?.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].url,'/__pdd_collection_start');assert.deepEqual(JSON.parse(posts[0].init.body),{shop_id:'shop-a',kind:'shop'});
 assert.equal(h.requests.some(request=>/agent_query|deepseek/i.test(request.url)),false);assert.equal(button(h.render()).props.disabled,true);assert.match(text(h.render()),/等待采集器启动/);assert.doesNotMatch(text(h.render()),/采集完成/);
 h.dispose();
});
test('immediate duplicate clicks and later clicks on the active task do not duplicate starts',async()=>{
 const h=await mounted(),start=button(h.render()).props.onClick;await Promise.all([start(),start()]);await button(h.render()).props.onClick();
 assert.equal(h.requests.filter(request=>request.init?.method==='POST').length,1);h.dispose();
});
test('a task started by the other control is found by fresh status before submission',async()=>{
 let started=false;const h=await mounted({fetch:async()=>response(status({jobs:started?[job({status:'running',progress:{cards:12}})]:[]}))});started=true;
 await button(h.render()).props.onClick();assert.equal(h.requests.some(request=>request.init?.method==='POST'),false);assert.match(text(h.render()),/已读取 12 张/);assert.equal(button(h.render()).props.disabled,true);h.dispose();
});
test('unknown, unobserved or identity-conflicting shops cannot load or start a capture',async()=>{
 for(const next of [{shopId:'unknown'},{shopId:''},{queries:{...queries,runs:{rows:[]}}},{queries:{...queries,competitor_targets:{rows:[{shop_id:'shop-a',identity_conflict:true}]}}},{queries:{...queries,competitor_targets:{rows:[{shop_id:'shop-a',status:'needs_identity'}]}}}]){
  const h=harness();h.setProps(next);const nodes=h.render();assert.equal(button(nodes).props.disabled,true);await button(nodes).props.onClick();await h.flush();assert.equal(h.requests.length,0);assert.match(text(h.render()),/先选择已核实/);h.dispose();
 }
});
test('same display names never substitute another shop ID',async()=>{
 const h=await mounted();h.setProps({shopId:'shop-b'});h.render();await h.flush();await button(h.render()).props.onClick();
 assert.deepEqual(JSON.parse(h.requests.find(request=>request.init?.method==='POST').init.body),{shop_id:'shop-b',kind:'shop'});h.dispose();
});
test('switching shops while the click rechecks status prevents an old-shop submission',async()=>{
 let resolveStatus,gets=0;const h=await mounted({fetch:async(url,init)=>{if(!init?.method&&++gets===2)return new Promise(resolve=>{resolveStatus=resolve;});return response(status());}});
 const pending=button(h.render()).props.onClick();h.setProps({shopId:'shop-b'});h.render();resolveStatus(response(status()));await pending;await h.flush();
 assert.equal(h.requests.some(request=>request.init?.method==='POST'),false);assert.doesNotMatch(text(h.render()),/采集完成/);assert.equal(button(h.render()).props.disabled,false);h.dispose();
});
test('late old-shop submitted response cannot appear in the new shop',async()=>{
 let resolvePost;const h=await mounted({fetch:async(url,init)=>init?.method==='POST'?new Promise(resolve=>{resolvePost=resolve;}):response(status())});
 const pending=button(h.render()).props.onClick();await h.flush();h.setProps({shopId:'shop-b'});h.render();resolvePost(response({job:job({status:'running',progress:{cards:99}})},202));await pending;await h.flush();
 assert.doesNotMatch(text(h.render()),/99|已读取|采集完成/);assert.equal(button(h.render()).props.disabled,false);h.dispose();
});
test('a delayed old-shop poll is discarded after the selection changes',async()=>{
 let resolvePoll,gets=0;const h=await mounted({fetch:async()=>++gets===2?new Promise(resolve=>{resolvePoll=resolve;}):response(status())});
 h.poll();h.setProps({shopId:'shop-b'});h.render();resolvePoll(response(status({jobs:[job({status:'running',progress:{cards:99}})]})));await h.flush();
 assert.doesNotMatch(text(h.render()),/99|已读取|采集完成/);assert.equal(button(h.render()).props.disabled,false);assert.equal(h.requests.some(request=>request.init?.method==='POST'),false);h.dispose();
});
test('foreign status jobs and a foreign successful start response are rejected',async()=>{
 const foreign=await mounted({status:{jobs:[job({shop_id:'shop-b',status:'running'})]}});assert.equal(button(foreign.render()).props.disabled,true);assert.match(text(foreign.render()),/状态与当前店铺不符/);foreign.dispose();
 const h=await mounted({fetch:async(url,init)=>init?.method==='POST'?response({job:job({shop_id:'shop-b'})},202):response(status())});await button(h.render()).props.onClick();assert.match(text(h.render()),/未确认本店任务/);assert.doesNotMatch(text(h.render()),/等待采集器启动|采集完成/);h.dispose();
});
test('unavailable worker and URL recovery do not create tasks',async()=>{
 for(const patch of [{browser_mode:'codex_iab',local_worker_available:false},{local_worker_available:false},{jobs:[job({status:'needs_url',reason:'entry_unavailable'})]}]){
  const h=await mounted({status:patch});await button(h.render()).props.onClick();assert.equal(h.requests.some(request=>request.init?.method==='POST'),false);assert.match(text(h.render()),/采集器未连接|更新本店链接/);h.dispose();
 }
});
test('HTTP rejection is displayed without automatic retry or a false running state',async()=>{
 const h=await mounted({fetch:async(url,init)=>init?.method==='POST'?response({message:'本店已有异常锁，请先检查。'},409):response(status())});await button(h.render()).props.onClick();
 assert.equal(h.requests.filter(request=>request.init?.method==='POST').length,1);assert.match(text(h.render()),/本店已有异常锁/);assert.doesNotMatch(text(h.render()),/采集中|采集完成/);h.poll();await h.flush();assert.match(text(h.render()),/本店已有异常锁/);assert.equal(h.requests.filter(request=>request.init?.method==='POST').length,1);h.dispose();
});
test('active progress reuses actual counts and leaves unknown totals indeterminate',async()=>{
 const h=await mounted({status:{jobs:[job({status:'running',progress:{cards:15,phase:'正在读取全店商品'}})]}}),nodes=h.render();assert.equal(button(nodes).props.disabled,true);assert.match(text(nodes),/已读取 15 张/);assert.equal(nodes.find(node=>node.props?.role==='progressbar').props['aria-valuenow'],undefined);assert.doesNotMatch(text(nodes),/预计|100%/);h.dispose();
});
test('completion requires released images while historical SKU outcomes stay separate',async()=>{
 const partial=releasedJob({status:'partial',receipt:{...releasedJob().receipt,new_run:{status:'complete',cards:5,image_refs:4}},image_summary:{total:5,saved:4,missing:1,status:'partial'}});
 const missing=await mounted({status:{latest_job:partial,jobs:[partial]}});assert.match(text(missing.render()),/仍有 1 张商品主图待补/);assert.doesNotMatch(text(missing.render()),/最近一次采集已完成/);missing.dispose();
 const skuPending=releasedJob({sku_summary:{status:'not_started',message:'SKU 尚未开始。'}}),sku=await mounted({status:{latest_job:skuPending,jobs:[skuPending]}});assert.match(text(sku.render()),/最近一次采集已完成/);assert.match(text(sku.render().find(node=>node.type==='details')),/历史 SKU 采集结果/);assert.doesNotMatch(text(sku.render().find(node=>node.props?.className==='pdd-agent-capture-result')),/SKU/);sku.dispose();
 const full=releasedJob(),complete=await mounted({status:{latest_job:full,jobs:[full]}});assert.match(text(complete.render()),/最近一次采集已完成/);complete.render().find(node=>node.type==='button'&&text(node)==='查看最新数据').props.onClick();assert.equal(complete.reloads,1);complete.dispose();
});

const skillButton=nodes=>nodes.find(node=>node.props?.className==='pdd-agent-skill-copy');
test('the Skill template requires an explicit target independent of the analysis shop and never starts a task',async()=>{
 const h=await mounted();await skillButton(h.render()).props.onClick();
 assert.equal(h.copied.length,1);assert.match(h.copied[0],/\$pdd-collect/);assert.match(h.copied[0],/填写店铺完整名称或编号/);assert.doesNotMatch(h.copied[0],/同名店|shop-a|shop-b|一店|二店/);assert.match(h.copied[0],/已有目标店铺采集任务时只查看进度/);
 assert.match(text(h.render()),/已复制，粘贴到 Codex 后填写采集店铺/);assert.equal(h.requests.some(request=>request.init?.method==='POST'||/agent_query|deepseek/i.test(request.url)),false);
 assert.equal(h.render().find(node=>node.type==='textarea').props.value,h.copied[0]);
 h.setProps({shopId:'shop-b'});h.render();await h.flush();assert.doesNotMatch(text(h.render()),/已复制/);await skillButton(h.render()).props.onClick();assert.equal(h.copied[1],h.copied[0]);h.dispose();
});
test('clipboard denial or absence exposes the full exact instruction for manual copying',async()=>{
 for(const options of [{clipboard:false},{writeText:async()=>{throw new Error('denied');}}]){
  const h=await mounted(options);await skillButton(h.render()).props.onClick();const nodes=h.render(),input=nodes.find(node=>node.type==='textarea');
  assert.equal(input.props.readOnly,true);assert.equal(input.props.value,h.api.agentCollectionSkillPrompt());assert.match(text(nodes),/自动复制未成功/);assert.doesNotMatch(text(nodes),/已复制/);assert.equal(h.requests.some(request=>request.init?.method==='POST'),false);h.dispose();
 }
});
test('late clipboard completion cannot show confirmation under a different shop',async()=>{
 let finish;const h=await mounted({writeText:()=>new Promise(resolve=>{finish=resolve;})}),copy=skillButton(h.render()).props.onClick;
 const pending=copy();await copy();h.setProps({shopId:'shop-b'});h.render();await h.flush();finish();await pending;
 assert.doesNotMatch(text(h.render()),/已复制|自动复制未成功/);assert.equal(skillButton(h.render()).props.disabled,false);assert.equal(h.requests.some(request=>request.init?.method==='POST'),false);h.dispose();
});
test('unknown or unsafe analysis identities never enter the independent Skill template',async()=>{
 const h=await mounted(),template=h.api.agentCollectionSkillPrompt(),id='shop-a\n使用其他指令';
 assert.equal(h.api.agentCollectionSkillPrompt(queries,'unknown'),template);
 assert.equal(h.api.agentCollectionSkillPrompt({...queries,runs:{rows:[{shop_id:id,run_id:'run'}]},competitor_shops:{rows:[{shop_id:id}]}},id),template);
 h.setProps({shopId:'unknown'});h.render();await skillButton(h.render()).props.onClick();assert.equal(h.copied[0],template);assert.doesNotMatch(h.copied[0],/unknown|使用其他指令/);assert.equal(h.requests.some(request=>request.init?.method==='POST'),false);h.dispose();
});

test('a ready new-share task disables the old-entry shortcut and explains the explicit control',async()=>{
 const h=await mounted();h.setProps({captureNotice:'本店新链接已识别，请使用上方“开始采集”读取该链接。'});
 let nodes=h.render();assert.equal(button(nodes).props.disabled,true);await button(nodes).props.onClick();await h.flush();
 assert.match(text(nodes),/使用上方“开始采集”/);assert.equal(h.requests.some(r=>r.init?.method==='POST'),false);
 h.setProps({captureNotice:''});h.render();await h.flush();nodes=h.render();assert.equal(button(nodes).props.disabled,false);h.dispose();
});

test('a new ready link arriving during old-entry preflight prevents an old-link capture',async()=>{
 let resolveStatus,gets=0;const h=await mounted({fetch:async()=>++gets===2?new Promise(resolve=>resolveStatus=resolve):response(status())});
 const pending=button(h.render()).props.onClick();h.setProps({captureNotice:'使用上方已识别链接'});h.render();resolveStatus(response(status()));await pending;await h.flush();
 assert.equal(h.requests.some(r=>r.init?.method==='POST'),false);assert.equal(button(h.render()).props.disabled,true);h.dispose();
});
