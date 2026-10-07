import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as intakeModel from '../src/content/dashboard/shop-intake-model.js';

const project=path.resolve(import.meta.dirname,'../..');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(project,'runtime/data-analytics/1.0.11/scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler();
const compiled=compiler.transform(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/ShopOnboarding.jsx'),'utf8'),{commonjs:true});
const targetId='target_'+'1'.repeat(24),otherTarget='target_'+'9'.repeat(24);
const jobId='onboard_'+'2'.repeat(32),retryId='onboard_'+'3'.repeat(32),foreignId='onboard_'+'8'.repeat(32);
const shopId='shop_'+'4'.repeat(24),otherShop='shop_'+'5'.repeat(24);
const currentHref=`http://localhost:8878/?pdd_shop=${otherShop}&pdd_target=${otherTarget}&pdd_view=settings&f.run_id=old-run#catalogue`;
const sourceUrl='https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_STORE';
const target={target_id:targetId,source_url:sourceUrl,display_name:'SYNTHETIC pending shop'};
const job=(status='running',extra={})=>({id:jobId,target_id:targetId,status,collection_job_id:'collect_synthetic',shop_id:shopId,shop_name:'SYNTHETIC SHOP',message:'SYNTHETIC 正在读取本店',...extra});
const completed=(extra={})=>job('complete',{job:{shop_id:shopId,dashboard_built:true,progress:{cards:20,image_saved:20,image_total:20}},...extra});
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children??'');
const button=(nodes,label)=>nodes.find(node=>node.type==='button'&&(typeof label==='string'?text(node)===label:label.test(text(node))));
const resultLink=nodes=>nodes.find(node=>node.type==='a'&&text(node)==='查看本店数据');
const errorMessage=nodes=>nodes.filter(node=>String(node.props?.className||'').includes('is-error')).map(text).join('');

// Compile the actual JSX and run its hooks, request helper and handlers. All
// network responses, timers and dialog visibility remain inside this process.
function harness(component='ShopOnboardingControl'){
 let cursor=0,timerId=0,props=component==='ShopLinkIntake'?{open:true,onClose:()=>{props={...props,open:false};}}:{target};
 const states=[],effects=[],requests=[],timers=new Map();
 const react={
  useState(initial){const index=cursor++;if(!(index in states))states[index]=typeof initial==='function'?initial():initial;return [states[index],value=>states[index]=typeof value==='function'?value(states[index]):value];},
  useRef(initial){const index=cursor++;return states[index]??(states[index]={current:initial});},
  useEffect(effect,deps){const index=cursor++,prior=states[index];if(!prior||deps.length!==prior.deps.length||deps.some((value,i)=>value!==prior.deps[i])){states[index]={deps:[...deps],cleanup:prior?.cleanup};effects.push(()=>{states[index].cleanup?.();states[index].cleanup=effect();});}}
 };
 const jsx=(type,props,key)=>({type,props,key});
 const context={exports:{},module:{exports:{}},
  require:id=>id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:
   id.endsWith('shop-intake-model.js')?intakeModel:id==='../../data-app-public.jsx'?{Dialog:'Dialog'}:{},
  fetch:(url,options={})=>new Promise((resolve,reject)=>{
   assert.match(url,/^\/__pdd_collection_onboard(?:_cancel|_collect|\?(?:id|target_id)=[^&]+)?$/);
   requests.push({url,options,resolve,reject});
   const abort=()=>{const error=new Error('synthetic abort');error.name='AbortError';reject(error);};
   if(options.signal?.aborted)abort();else options.signal?.addEventListener('abort',abort,{once:true});
  }),
  setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
  window:{location:{href:currentHref}},URL,AbortController,encodeURIComponent,Date,Number,Boolean,Array,Object,Set,Math
 };
 context.module.exports=context.exports;vm.runInNewContext(compiled,context,{filename:'ShopOnboarding.jsx'});
 const collect=(node,nodes)=>{
  if(Array.isArray(node))return node.forEach(item=>collect(item,nodes));
  if(!node||typeof node!=='object')return;
  nodes.push(node);
  if(typeof node.type==='function')return collect(node.type(node.props),nodes);
  if(node.type!=='Dialog'||node.props.open)collect(node.props?.children,nodes);
 };
 const render=()=>{cursor=0;const nodes=[];collect(context.exports[component](props),nodes);effects.splice(0).forEach(effect=>effect());return nodes;};
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 return {exports:context.exports,requests,timers,render,flush,
  async respond(index,data,status=200){assert.ok(requests[index],`request ${index} exists`);requests[index].resolve(response(data,status));await flush();return render();},
  click(label){const control=button(render(),label);assert.ok(control,`button ${label} exists`);assert.notEqual(control.props.disabled,true);return control.props.onClick();},
  fill(name=' SYNTHETIC 新店 ',url=' '+sourceUrl+' '){const nodes=render();nodes.find(node=>node.type==='textarea').props.onChange({target:{value:url}});nodes.find(node=>node.type==='input').props.onChange({target:{value:name}});render();},
  submit(){const form=render().find(node=>node.type==='form');assert.ok(form);let prevented=false;form.props.onSubmit({preventDefault(){prevented=true;}});assert.equal(prevented,true);},
  poll(){const entry=[...timers].find(([,value])=>[800,2500].includes(value.ms));assert.ok(entry,'poll scheduled');timers.delete(entry[0]);entry[1].fn();},
  pendingPolls(){return [...timers.values()].filter(value=>[800,2500].includes(value.ms));},
  close(){render().find(node=>node.type==='Dialog').props.onClose();return render();},
  reopen(){props={...props,open:true};return render();},
  changeTarget(value){props={target:value};render();return render();},
  dispose(){for(const state of states)state?.cleanup?.();timers.clear();}
 };
}
const posts=h=>h.requests.filter(request=>request.options.method==='POST');
function assertShopLink(nodes){
 const link=resultLink(nodes);assert.ok(link,'data link exists only for published own-shop data');
 const destination=new URL(link.props.href,currentHref);
 assert.equal(destination.searchParams.get('pdd_shop'),shopId);assert.equal(destination.searchParams.get('pdd_view'),'warehouse');
 assert.equal(destination.searchParams.has('pdd_target'),false);assert.equal(destination.searchParams.has('f.run_id'),false);
 assert.equal(destination.hash,'#catalogue');assert.doesNotMatch(link.props.href,new RegExp(`${otherShop}|${otherTarget}|old-run`));
}

test('pending target lookup, explicit target POST, and same-job polling lead to the exact published shop',async()=>{
 const h=harness();try{
  h.render();assert.equal(h.requests[0].url,`/__pdd_collection_onboard?target_id=${targetId}`);
  await h.respond(0,{status:'idle',target_id:targetId});h.click('识别店铺');
  assert.deepEqual(JSON.parse(h.requests[1].options.body),{target_id:targetId});
  await h.respond(1,job('queued'),202);assert.equal(posts(h).length,1);assert.equal(resultLink(h.render()),undefined);
  h.poll();assert.equal(h.requests[2].url,`/__pdd_collection_onboard?id=${jobId}`);
  let nodes=await h.respond(2,job('running',{job:{shop_id:shopId,kind:'shop',progress:{phase:'SYNTHETIC 主图读取',cards:20,image_saved:18,image_total:20}}}));
  assert.match(text(nodes),/SYNTHETIC 主图读取/);assert.match(text(nodes),/已读取 20 件商品/);assert.match(text(nodes),/图片 18 \/ 20/);
  assert.equal(resultLink(nodes),undefined);h.poll();nodes=await h.respond(3,completed());assertShopLink(nodes);
  assert.equal(h.pendingPolls().length,0);assert.equal(posts(h).length,1);
 }finally{h.dispose();await h.flush();}
});

test('new link submission posts only trimmed name/url and polls the receipt ID without double submit',async()=>{
 const h=harness('ShopLinkIntake');try{
  h.fill();assert.equal(h.requests.length,0);h.submit();h.submit();assert.equal(posts(h).length,1);
  assert.deepEqual(JSON.parse(posts(h)[0].options.body),{name:'SYNTHETIC 新店',url:sourceUrl});
  await h.respond(0,job('queued'),202);h.poll();assert.equal(h.requests[1].url,`/__pdd_collection_onboard?id=${jobId}`);
  const nodes=await h.respond(1,completed());assertShopLink(nodes);assert.equal(posts(h).length,1);
 }finally{h.dispose();await h.flush();}
});

test('optional name stays null and rejected input is retained without claiming a saved task',async()=>{
 const h=harness('ShopLinkIntake');try{
  h.fill('   ');h.submit();assert.deepEqual(JSON.parse(posts(h)[0].options.body),{name:null,url:sourceUrl});
  const nodes=await h.respond(0,{message:'SYNTHETIC 分享链接拒绝'},400);
  assert.match(errorMessage(nodes),/SYNTHETIC 分享链接拒绝/);assert.equal(resultLink(nodes),undefined);
  assert.equal(nodes.find(node=>node.type==='textarea').props.value,' '+sourceUrl+' ');assert.equal(h.pendingPolls().length,0);
 }finally{h.dispose();await h.flush();}
});

test('same-task polling rejects a foreign task ID or target and retains the pinned task',async()=>{
 for(const changed of [{id:foreignId},{target_id:otherTarget}]){
  const h=harness('ShopLinkIntake');try{
   h.fill();h.submit();await h.respond(0,job('queued'),202);h.poll();
   const nodes=await h.respond(1,completed(changed));assert.match(errorMessage(nodes),/不一致/);assert.equal(resultLink(nodes),undefined);
   assert.equal(h.pendingPolls().length,0);h.click('刷新进度');assert.equal(h.requests[2].url,`/__pdd_collection_onboard?id=${jobId}`);
   assert.equal(posts(h).length,1);
  }finally{h.dispose();await h.flush();}
 }
});

test('initial foreign target receipt cannot enable navigation or a foreign polling chain',async()=>{
 const h=harness();try{
  h.render();let nodes=await h.respond(0,{status:'idle',target_id:otherTarget});
  assert.match(errorMessage(nodes),/不一致/);assert.equal(h.pendingPolls().length,0);
  h.click('识别店铺');nodes=await h.respond(1,completed({target_id:otherTarget}),202);
  assert.match(errorMessage(nodes),/不一致/);assert.equal(resultLink(nodes),undefined);assert.equal(h.pendingPolls().length,0);
 }finally{h.dispose();await h.flush();}
});

test('idle is valid only for initial exact-target lookup, never for an already pinned ID',()=>{
 const h=harness();try{
  const {validateOnboarding}=h.exports;
  assert.equal(validateOnboarding({status:'idle',target_id:targetId},{targetId}).status,'idle');
  for(const [value,scope] of [[{status:'idle',target_id:otherTarget},{targetId}],
    [{status:'idle',target_id:targetId},{id:jobId,targetId}],
    [{status:'idle',id:jobId,target_id:targetId},{id:jobId,targetId}],
    [{status:'idle',id:foreignId,target_id:targetId},{targetId}],
    [{status:'idle',id:foreignId,target_id:targetId},{id:jobId,targetId}],
    [job('running',{id:'invalid'}),{}],[job('running',{target_id:'invalid'}),{}]])assert.throws(()=>validateOnboarding(value,scope));
 }finally{h.dispose();}
});

test('complete or partial needs matching child shop and successful dashboard publication to expose data',()=>{
 const h=harness();try{
  const {onboardingDataHref}=h.exports;
  for(const value of [completed({job:{shop_id:otherShop,dashboard_built:true}}),completed({job:{shop_id:shopId,dashboard_built:false}}),
    completed({job:{shop_id:shopId,dashboard_built:'true'}}),completed({job:null}),completed({shop_id:'invalid'}),completed({status:'running'}),completed({status:'failed'})])assert.equal(onboardingDataHref(value,currentHref),null);
  for(const status of ['complete','partial'])assert.equal(new URL(onboardingDataHref(completed({status}),currentHref),currentHref).searchParams.get('pdd_shop'),shopId);
 }finally{h.dispose();}
});

test('cancel addresses the pinned ID, removes old polling and requires an explicit target-scoped retry',async()=>{
 const h=harness();try{
  h.render();await h.respond(0,job('running'));h.click('停止采集');
  assert.equal(h.requests[1].url,'/__pdd_collection_onboard_cancel');assert.deepEqual(JSON.parse(h.requests[1].options.body),{id:jobId});
  const nodes=await h.respond(1,job('cancelled',{message:'SYNTHETIC 已停止',job:{progress:{phase:'SYNTHETIC 旧的读取进度'}}}));
  assert.match(text(nodes),/SYNTHETIC 已停止/);assert.equal(resultLink(nodes),undefined);assert.equal(h.pendingPolls().length,0);
  assert.doesNotMatch(text(nodes),/SYNTHETIC 旧的读取进度/);
  h.click(/重新|重试/);assert.deepEqual(JSON.parse(h.requests[2].options.body),{target_id:targetId,retry:true});
  await h.respond(2,job('queued',{id:retryId}),202);h.poll();assert.equal(h.requests[3].url,`/__pdd_collection_onboard?id=${retryId}`);
  assert.equal(posts(h).length,2,'only explicit cancel and retry create POSTs');
 }finally{h.dispose();await h.flush();}
});

test('terminal new-link attempt retries its existing target instead of registering the link again',async()=>{
 const h=harness('ShopLinkIntake');try{
  h.fill();h.submit();await h.respond(0,job('failed',{message:'SYNTHETIC 无法核验'}),202);
  assert.equal(posts(h).length,1);assert.equal(h.pendingPolls().length,0);
  h.click(/重新|重试/);assert.deepEqual(JSON.parse(h.requests[1].options.body),{target_id:targetId,retry:true});
  await h.respond(1,job('queued',{id:retryId}),202);h.poll();assert.equal(h.requests[2].url,`/__pdd_collection_onboard?id=${retryId}`);
 }finally{h.dispose();await h.flush();}
});

test('closing and reopening retains one running task and never repeats its POST',async()=>{
 const h=harness('ShopLinkIntake');try{
  h.fill();h.submit();await h.respond(0,job('queued'),202);
  assert.equal(h.close().find(node=>node.type==='Dialog').props.open,false);h.poll();
  await h.respond(1,job('running'));let nodes=h.reopen();assert.match(text(nodes),/SYNTHETIC SHOP/);
  h.submit();h.submit();assert.equal(posts(h).length,1);h.close();h.poll();await h.respond(2,completed());
  nodes=h.reopen();assertShopLink(nodes);assert.equal(posts(h).length,1);assert.equal(h.pendingPolls().length,0);
 }finally{h.dispose();await h.flush();}
});

test('switching targets aborts the old read and cannot display a late foreign completion',async()=>{
 const h=harness();try{
  h.render();h.changeTarget({...target,target_id:otherTarget});
  assert.equal(h.requests[0].options.signal.aborted,true);assert.equal(h.requests[1].url,`/__pdd_collection_onboard?target_id=${otherTarget}`);
  await h.respond(0,completed());let nodes=await h.respond(1,{status:'idle',target_id:otherTarget});assert.equal(resultLink(nodes),undefined);
  assert.equal(errorMessage(nodes),'');h.click('识别店铺');assert.deepEqual(JSON.parse(h.requests[2].options.body),{target_id:otherTarget});
 }finally{h.dispose();await h.flush();}
});

test('unmount aborts pending requests and late completion never creates a polling timer',async()=>{
 const h=harness('ShopLinkIntake');h.fill();h.submit();h.dispose();
 assert.equal(h.requests[0].options.signal.aborted,true);await h.respond(0,job('running'),202);assert.equal(h.pendingPolls().length,0);assert.equal(posts(h).length,1);
});

test('starting before initial lookup returns supersedes that read without a false timeout error',async()=>{
 const h=harness();try{
  h.render();h.click('识别店铺');assert.equal(h.requests[0].options.signal.aborted,true);
  await h.flush();assert.equal(errorMessage(h.render()),'');
  await h.respond(0,completed({target_id:otherTarget}));const nodes=await h.respond(1,job('queued'),202);
  assert.equal(errorMessage(nodes),'');assert.equal(resultLink(nodes),undefined);assert.equal(posts(h).length,1);
  h.poll();assert.equal(h.requests[2].url,`/__pdd_collection_onboard?id=${jobId}`);
 }finally{h.dispose();await h.flush();}
});

test('cancel supersedes an outstanding poll so a late active receipt cannot restart polling',async()=>{
 const h=harness();try{
  h.render();await h.respond(0,job('running'));h.poll();h.click('停止采集');
  assert.equal(h.requests[1].options.signal.aborted,true);assert.equal(h.requests[2].url,'/__pdd_collection_onboard_cancel');
  await h.respond(2,job('cancelled',{message:'SYNTHETIC 已停止'}));const nodes=await h.respond(1,job('running'));
  assert.match(text(nodes),/SYNTHETIC 已停止/);assert.equal(errorMessage(nodes),'');assert.equal(h.pendingPolls().length,0);
  assert.equal(button(nodes,'停止采集'),undefined);assert.ok(button(nodes,/重新|重试/));
 }finally{h.dispose();await h.flush();}
});

test('adding a link ends at ready and never submits collection or changes the selected shop',async()=>{
 const h=harness('ShopLinkIntake');try{
  h.fill();h.submit();await h.respond(0,job('queued',{collection_job_id:null}),202);h.poll();
  let nodes=await h.respond(1,job('ready',{collection_job_id:null,message:'身份已核实，等待点击采集'}));
  assert.match(text(nodes),/已识别 · 待采集/);assert.match(text(nodes),/已加入“当前分析店铺”/);
  assert.equal(button(nodes,'开始采集'),undefined);assert.equal(h.pendingPolls().length,0);
  assert.equal(posts(h).length,1);assert.equal(posts(h)[0].url,'/__pdd_collection_onboard');
  h.close();nodes=h.reopen();assert.match(text(nodes),/待采集/);assert.equal(posts(h).length,1);
 }finally{h.dispose();await h.flush();}
});

test('only the explicit ready-state button submits the pinned collection ID once',async()=>{
 const h=harness();try{
  h.render();let nodes=await h.respond(0,job('ready',{collection_job_id:null}));
  assert.ok(button(nodes,'开始采集'));assert.equal(posts(h).length,0);assert.equal(h.pendingPolls().length,0);
  h.click('开始采集');assert.equal(h.requests[1].url,'/__pdd_collection_onboard_collect');
  assert.deepEqual(JSON.parse(h.requests[1].options.body),{id:jobId});
  await h.respond(1,job('queued'),202);h.poll();nodes=await h.respond(2,completed());
  assertShopLink(nodes);assert.equal(posts(h).length,1);assert.equal(button(nodes,'开始采集'),undefined);
 }finally{h.dispose();await h.flush();}
});

test('resolved stable target restores its proven original share task without another identification',async()=>{
 const h=harness();try{
  h.changeTarget({...target,target_id:otherTarget,shop_id:shopId,onboarding:job('ready',{collection_job_id:null,resolved_target_id:otherTarget})});
  assert.equal(h.requests[0].url,`/__pdd_collection_onboard?target_id=${targetId}`);
  await h.respond(0,job('ready',{collection_job_id:null,resolved_target_id:otherTarget}));
  h.click('开始采集');assert.equal(posts(h)[0].url,'/__pdd_collection_onboard_collect');
  assert.deepEqual(JSON.parse(posts(h)[0].options.body),{id:jobId});
 }finally{h.dispose();await h.flush();}
});

test('foreign-shop resolved alias cannot redirect the target-scoped status read',async()=>{
 const h=harness();try{
  h.changeTarget({...target,target_id:otherTarget,shop_id:otherShop,onboarding:job('ready',{resolved_target_id:otherTarget})});
  assert.equal(h.requests[0].url,`/__pdd_collection_onboard?target_id=${otherTarget}`);
 }finally{h.dispose();await h.flush();}
});

test('re-adding a completed link labels saved data without implying another capture',async()=>{
 const h=harness('ShopLinkIntake');try{
  h.fill();h.submit();const nodes=await h.respond(0,completed());
  assert.match(text(nodes),/已识别 · 已有数据/);assert.match(text(nodes),/本次添加没有重新采集/);
  assertShopLink(nodes);assert.equal(posts(h).length,1);assert.equal(h.pendingPolls().length,0);
 }finally{h.dispose();await h.flush();}
});
