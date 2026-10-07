import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as model from '../src/content/dashboard/shop-intake-model.js';
const project=path.resolve(import.meta.dirname,'../..');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(project,'runtime/data-analytics/1.0.11/scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler();
const id=prefix=>prefix+'1'.repeat(prefix==='onboard_'?32:24),targetId=id('target_'),stable='target_'+'2'.repeat(24),shopId=id('shop_'),oldShop='shop_'+'3'.repeat(24);
const job={id:id('onboard_'),target_id:targetId,resolved_target_id:stable,shop_id:shopId,shop_name:'新店',status:'ready'};
const target={target_id:targetId,shop_id:shopId,source_url:'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC',display_name:'新店',status:'pending_capture',onboarding:job};
const directory={status:'ok',targets:[{...target,target_id:stable},target],onboardings:[job]};
const text=node=>typeof node==='string'?node:Array.isArray(node)?node.map(text).join(''):text(node?.props?.children??'');
function harness(name,{data=directory,queries={},href=`http://localhost:8878/?pdd_shop=${oldShop}&f.run_id=stale&view=1`,effectsEnabled=false}={}){
 let cursor=0,timerId=0;const states=[],effects=[],requests=[],timers=new Map(),history=[];
 const react={useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return[states[i],v=>states[i]=typeof v==='function'?v(states[i]):v];},useRef(v){const i=cursor++;return states[i]??(states[i]={current:v});},useEffect(fn,deps){const i=cursor++,previous=states[i];if(!previous||deps.some((v,j)=>v!==previous.deps[j])){states[i]={deps,cleanup:previous?.cleanup};if(effectsEnabled)effects.push(()=>{states[i].cleanup?.();states[i].cleanup=fn();});}}};
 const jsx=(type,props)=>({type,props}),window={location:{href,search:new URL(href).search},history:{replaceState(_state,_title,url){history.push(url);window.location.href=new URL(url,window.location.href).href;window.location.search=new URL(window.location.href).search;}}};
 const shared=new Proxy({useDataApp:()=>({queries})},{get:(obj,key)=>obj[key]??key});
 const context={exports:{},module:{exports:{}},require:key=>key==='react'?{__esModule:true,default:react,...react}:key==='react/jsx-runtime'?{jsx,jsxs:jsx}:key.endsWith('shop-intake-model.js')?model:key==='../../data-app-public.jsx'?shared:key.endsWith('ShopDirectory.jsx')?{useShopDirectory:()=>({directory:data,error:'',refresh(){}})}:key.endsWith('portfolio-model.js')?{availableShops:()=>[{shop_id:oldShop,shop_name:'原店'}]}:new Proxy({},{get:(_o,key)=>key}),window,URL,URLSearchParams,AbortController,Set,Map,Object,Array,Number,Boolean,Date,encodeURIComponent,
 fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})),setTimeout(fn,ms){const tid=++timerId;timers.set(tid,{fn,ms});return tid;},clearTimeout:i=>timers.delete(i)};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(fs.readFileSync(path.join(project,`dashboard/src/content/dashboard/${name}.jsx`),'utf8'),{commonjs:true}),context);
 const walk=(node,all)=>{if(Array.isArray(node))return node.forEach(x=>walk(x,all));if(!node||typeof node!=='object')return;all.push(node);walk(node.props?.children,all);};
 const render=(props={})=>{cursor=0;const result=context.exports[name]?context.exports[name](props):context.exports.useShopDirectory(),all=[];walk(result,all);effects.splice(0).forEach(fn=>fn());return name==='ShopDirectory'?result:all;};
 return {exports:context.exports,window,history,requests,timers,render,async respond(i,value){requests[i].resolve({ok:true,json:async()=>value});await new Promise(resolve=>setImmediate(resolve));return render();},dispose(){states.forEach(s=>s?.cleanup?.());timers.clear();}};
}

test('live directory accepts only the original target or a proven resolved same-shop alias',()=>{
 const h=harness('ShopDirectory');try{
  assert.equal(h.exports.validateShopDirectory(directory),directory);
  for(const bad of [{...target,target_id:stable,onboarding:{...job,resolved_target_id:targetId}},{...target,target_id:stable,onboarding:{...job,shop_id:oldShop}},{...target,latest_collection:{shop_id:oldShop}}])assert.throws(()=>h.exports.validateShopDirectory({...directory,targets:[bad]}));
  assert.throws(()=>h.exports.validateShopDirectory({...directory,onboardings:[{...job,target_id:'target_'+'9'.repeat(24)}]}));
 }finally{h.dispose();}
});

test('existing unregistered observed-shop rows remain valid navigation data',()=>{
 const h=harness('ShopDirectory');try{
  const value={status:'ok',targets:[{target_id:'observed_shop_'+'5'.repeat(24),shop_id:oldShop}],onboardings:[]};
  assert.equal(h.exports.validateShopDirectory(value),value);
 }finally{h.dispose();}
});

test('directory polling is read-only, retains the last good list on errors, and aborts on unmount',async()=>{
 const h=harness('ShopDirectory',{effectsEnabled:true});try{
  h.render();assert.equal(h.requests[0].url,'/__pdd_shop_directory');assert.equal(h.requests[0].options.method,undefined);
  let result=await h.respond(0,directory);assert.equal(result.directory,directory);
  const poll=[...h.timers.values()].find(t=>t.ms===5000);assert.ok(poll);poll.fn();
  result=await h.respond(1,{status:'bad',targets:[]});assert.equal(result.directory,directory);assert.match(result.error,/列表/);
  result.refresh();h.render();assert.equal(h.requests[0].options.signal.aborted,true);assert.equal(h.requests[2].url,'/__pdd_shop_directory');
  h.dispose();assert.equal(h.requests[2].options.signal.aborted,true);
 }finally{h.dispose();}
});

test('a newly identified shop does not switch analysis until selected; selection survives refresh in URL',()=>{
 const queries={runs:{rows:[{run_id:'old',shop_id:oldShop,status:'complete',end_boundary_observed:true}]},competitor_targets:{rows:[]}};
 const h=harness('DashboardContent',{queries});let nodes=h.render();
 const dropdown=nodes.find(n=>n.type==='Dropdown');assert.equal(dropdown.props.value,oldShop);assert.ok(dropdown.props.choices.includes(`target:${stable}`));assert.equal(h.history.length,0);
 dropdown.props.onChange(`target:${stable}`);nodes=h.render();assert.equal(nodes.some(n=>n.type==='AgentSearch'),false);
 const empty=nodes.find(n=>n.type==='ShopTargetEmpty');assert.equal(empty.props.selection.target.shop_id,shopId);assert.equal(empty.props.selection.canAnalyze,false);
 const url=new URL(h.window.location.href);assert.equal(url.searchParams.get('pdd_target'),stable);assert.equal(url.searchParams.has('pdd_shop'),false);assert.equal(url.searchParams.has('f.run_id'),false);
 const refreshed=harness('DashboardContent',{queries,href:h.window.location.href});assert.equal(refreshed.render().find(n=>n.type==='Dropdown').props.value,`target:${stable}`);
 h.dispose();refreshed.dispose();
});

test('a saved live shop with no matching snapshot never claims that no goods were collected',()=>{
 const saved={...target,run_count:1,onboarding:null,latest_collection:{shop_id:shopId,status:'partial',dashboard_built:false}};
 const selection=model.resolveIntakeSelection(model.mergeShopDirectory({}, {targets:[saved]}),{targetId},[]);
 assert.equal(selection.state,'saved_unpublished');assert.equal(selection.canAnalyze,false);assert.equal(selection.hasData,false);
 assert.equal(model.targetDataHref(saved,'http://localhost:8878/'),null);
});

test('live ready rows render as operational state, never as a reviewed row from the old snapshot',()=>{
 const h=harness('ShopIntake');try{
  // The named export is used directly because the module's default named component is legacy registration.
  const jsx=h.exports.ShopTargetEmpty({selection:{target:{...target,directory_live:true},state:'pending_capture',label:'待采集'},onAdd(){}});
  const serialized=JSON.stringify(jsx);assert.doesNotMatch(serialized,/pdd-intake-selected-target/);assert.match(serialized,/店铺已识别/);
 }finally{h.dispose();}
});

test('a verified new share for a previously analyzed shop exposes its own explicit collection control',()=>{
 const own={...target,shop_id:oldShop,onboarding:{...job,shop_id:oldShop}},data={...directory,targets:[own],onboardings:[own.onboarding]};
 const queries={runs:{rows:[{run_id:'old',shop_id:oldShop,status:'complete',end_boundary_observed:true}]},competitor_targets:{rows:[]}};
 const h=harness('DashboardContent',{data,queries});try{
  const nodes=h.render(),control=nodes.find(n=>n.type==='ShopOnboardingControl');
  assert.ok(control);assert.equal(control.props.target.onboarding.id,job.id);assert.equal(nodes.some(n=>n.type==='CollectionControl'),false);
  assert.equal(nodes.some(n=>n.type==='WarehouseContent'),true);assert.equal(h.history.length,0);
 }finally{h.dispose();}
});
