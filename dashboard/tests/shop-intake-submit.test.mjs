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
const source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/ShopIntake.jsx'),'utf8');
const compiled=compiler.transform(source,{commonjs:true});
const targetId='target_'+'1'.repeat(24),jobId='intake_'+'2'.repeat(32);
const receipt=(extra={})=>({registration_status:'registered',target_id:targetId,shop_id:null,
 target_status:'needs_identity',website_collection_performed:false,dashboard_built:false,...extra});
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children??'');
const button=(nodes,label)=>nodes.find(node=>node.type==='button'&&text(node)===label);
const message=nodes=>nodes.find(node=>String(node.props?.className||'').startsWith('pdd-intake-message'));

// The actual component and actual intake JSON/receipt helpers run here. Only React,
// scheduling, navigation and fetch are simulated; no request can leave this process.
function harness(){
 let cursor=0,timerId=0,now=Date.UTC(2026,9,7,2),props;
 const states=[],effects=[],timers=new Map(),requests=[],navigations=[];
 props={open:true,onClose:()=>{props={...props,open:false};}};
 const react={
  useState(initial){const index=cursor++;if(!(index in states))states[index]=typeof initial==='function'?initial():initial;return [states[index],value=>states[index]=typeof value==='function'?value(states[index]):value];},
  useRef(initial){const index=cursor++;return states[index]??(states[index]={current:initial});},
  useEffect(effect,deps){const index=cursor++,prior=states[index];if(!prior||deps.length!==prior.deps.length||deps.some((value,i)=>value!==prior.deps[i])){states[index]={deps:[...deps],cleanup:prior?.cleanup};effects.push(()=>{states[index].cleanup?.();states[index].cleanup=effect();});}}
 };
 const jsx=(type,props,key)=>({type,props,key});
 const schedule=(fn,ms,kind)=>{const id=++timerId;timers.set(id,{fn,ms,kind});return id;};
 class Clock extends Date{static now(){return now;}}
 const context={exports:{},module:{exports:{}},
  require:id=>id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:
   id.endsWith('shop-intake-model.js')?intakeModel:id==='../../data-app-public.jsx'?{Dialog:'Dialog',DataComponent:'DataComponent',SourceSidebar:'SourceSidebar'}:{},
  fetch:(url,options={})=>new Promise((resolve,reject)=>{
   assert.match(url,/^\/__pdd_competitor_(?:add|status\?job_id=)/);
   requests.push({url,options,resolve,reject});
   options.signal?.addEventListener('abort',()=>{const error=new Error('synthetic abort');error.name='AbortError';reject(error);},{once:true});
  }),
  setTimeout:(fn,ms)=>schedule(fn,ms,'timeout'),clearTimeout:id=>timers.delete(id),
  setInterval:(fn,ms)=>schedule(fn,ms,'interval'),clearInterval:id=>timers.delete(id),
  window:{location:{href:'http://localhost:8878/?pdd_shop=shop-other&pdd_target=target-old&pdd_view=warehouse#catalogue',assign:value=>navigations.push(value)}},
  AbortController,encodeURIComponent,Date:Clock,Number,Boolean,Array,Object,Set,Math
 };
 context.module.exports=context.exports;
 vm.runInNewContext(compiled,context,{filename:'ShopIntake.jsx'});
 const collect=(node,nodes)=>{
  if(Array.isArray(node))return node.forEach(item=>collect(item,nodes));
  if(!node||typeof node!=='object')return;
  nodes.push(node);
  if(node.type!=='Dialog'||node.props.open)collect(node.props?.children,nodes);
 };
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 const render=()=>{cursor=0;const nodes=[];collect(context.exports.ShopIntake(props),nodes);effects.splice(0).forEach(effect=>effect());return nodes;};
 return {requests,navigations,timers,render,flush,
  async respond(index,data,status=200){assert.ok(requests[index],`request ${index} exists`);requests[index].resolve(response(data,status));await flush();},
  fill(){const nodes=render();nodes.find(node=>node.type==='textarea').props.onChange({target:{value:' https://example.invalid/SYNTHETIC-STORE '}});nodes.find(node=>node.type==='input').props.onChange({target:{value:' SYNTHETIC 新店 '}});render();},
  submit(){const form=render().find(node=>node.type==='form');assert.ok(form);let prevented=false;form.props.onSubmit({preventDefault(){prevented=true;}});assert.equal(prevented,true);},
  poll(){const entry=[...timers].find(([,timer])=>timer.kind==='timeout'&&timer.ms===1200);assert.ok(entry,'a real poll callback was scheduled');timers.delete(entry[0]);entry[1].fn();},
  tick(ms=1000){now+=ms;for(const timer of [...timers.values()])if(timer.kind==='interval')timer.fn();},
  close(){render().find(node=>node.type==='Dialog').props.onClose();return render();},
  reopen(){props={...props,open:true};return render();},
  dispose(){for(const state of states)state?.cleanup?.();timers.clear();}
 };
}

function assertOnlyRegistration(h){
 const posts=h.requests.filter(request=>request.options.method==='POST');
 assert.equal(posts.length,1);
 assert.equal(posts[0].url,'/__pdd_competitor_add');
 assert.deepEqual(JSON.parse(posts[0].options.body),{name:'SYNTHETIC 新店',url:'https://example.invalid/SYNTHETIC-STORE'});
 for(const request of h.requests.filter(request=>request.options.method!=='POST'))assert.equal(request.url,`/__pdd_competitor_status?job_id=${jobId}`);
}

function assertTargetNavigation(h){
 assert.equal(h.navigations.length,1);
 const destination=new URL(h.navigations[0],'http://localhost:8878');
 assert.equal(destination.searchParams.get('pdd_target'),targetId);
 assert.equal(destination.searchParams.has('pdd_shop'),false);
 assert.equal(destination.searchParams.get('pdd_view'),'warehouse');
 assert.equal(destination.hash,'#catalogue');
 assert.doesNotMatch(h.navigations[0],/shop-other|target-old/);
}

test('202 immediately shows saved receipt while running, then exact target refresh follows validated completion',async()=>{
 const h=harness();
 try{
  h.fill();h.submit();
  assert.equal(h.requests.length,1);
  assert.match(text(message(h.render())),/正在保存店铺链接/);
  await h.respond(0,{status:'running',job_id:jobId,receipt:receipt()},202);
  let nodes=h.render();
  assert.equal(h.requests.length,2,'the first status request is still pending');
  assert.match(text(message(nodes)),/链接已保存/);
  assert.match(text(nodes.find(node=>node.props?.className==='pdd-intake-saved')),/链接已添加成功.*待核验/);
  assert.equal(nodes.some(node=>node.props?.className==='pdd-intake-warning'),false);
  assert.equal(button(nodes,'正在处理…').props.disabled,true);
  assert.equal(h.navigations.length,0);
  await h.respond(1,{status:'running',job_id:jobId,receipt:receipt()});
  h.tick(3000);nodes=h.render();
  assert.match(text(message(nodes)),/已用 3 秒/);
  assert.doesNotMatch(text(message(nodes)),/失败|预计|100%/);
  h.poll();await h.respond(2,{status:'succeeded',job_id:jobId,receipt:receipt({dashboard_built:true})});
  h.render();assertTargetNavigation(h);assertOnlyRegistration(h);
  assert.equal([...h.timers.values()].some(timer=>timer.kind==='interval'),false);
 }finally{h.dispose();await h.flush();}
});

test('build failure keeps saved receipt and retrying checks the same task without another POST',async()=>{
 const h=harness();
 try{
  h.fill();h.submit();await h.respond(0,{status:'running',job_id:jobId,receipt:receipt()},202);h.render();
  await h.respond(1,{status:'failed',job_id:jobId,error_code:'dashboard_build_failed',message:'SYNTHETIC 列表构建失败'});
  let nodes=h.render();
  assert.match(text(message(nodes)),/SYNTHETIC 列表构建失败/);
  assert.match(text(nodes.find(node=>node.props?.className==='pdd-intake-warning')),/链接已保存.*无需重新添加/);
  assert.equal(nodes.find(node=>node.type==='textarea').props.value,' https://example.invalid/SYNTHETIC-STORE ');
  assert.equal(h.navigations.length,0);
  const checking=button(nodes,'检查这次任务').props.onClick();await h.flush();
  nodes=h.render();
  assert.match(text(nodes.find(node=>node.props?.className==='pdd-intake-saved')),/链接已添加成功/);
  assert.equal(nodes.some(node=>node.props?.className==='pdd-intake-warning'),false);
  assertOnlyRegistration(h);
  await h.respond(2,{status:'succeeded',job_id:jobId,receipt:receipt({dashboard_built:true})});await checking;
  assertTargetNavigation(h);assertOnlyRegistration(h);
 }finally{h.dispose();await h.flush();}
});

test('closing and reopening keeps the same in-flight task, receipt and elapsed time without resubmission',async()=>{
 const h=harness();
 try{
  h.fill();h.submit();await h.respond(0,{status:'running',job_id:jobId,receipt:receipt()},202);h.render();
  await h.respond(1,{status:'running',job_id:jobId,receipt:receipt()});
  const hidden=h.close();assert.equal(hidden.find(node=>node.type==='Dialog').props.open,false);
  assert.equal(h.requests[1].options.signal.aborted,false,'closing the dialog does not unmount the intake owner');
  h.tick(4000);let nodes=h.reopen();
  assert.match(text(message(nodes)),/已用 4 秒/);
  assert.match(text(nodes.find(node=>node.props?.className==='pdd-intake-saved')),/链接已添加成功/);
  assert.equal(button(nodes,'正在处理…').props.disabled,true);
  h.submit();h.submit();assertOnlyRegistration(h);
  h.close();h.poll();await h.respond(2,{status:'succeeded',job_id:jobId,receipt:receipt({dashboard_built:true})});
  assertTargetNavigation(h);assertOnlyRegistration(h);
 }finally{h.dispose();await h.flush();}
});

test('rejected submission preserves input without claiming saved or scheduling another request',async()=>{
 const h=harness();
 try{
  h.fill();h.submit();await h.respond(0,{status:'failed',message:'SYNTHETIC 登记被拒绝'},400);
  const nodes=h.render();
  assert.match(text(message(nodes)),/SYNTHETIC 登记被拒绝/);
  assert.doesNotMatch(text(message(nodes)),/已保存|添加成功/);
  assert.equal(nodes.some(node=>node.props?.className==='pdd-intake-saved'),false);
  assert.equal(nodes.find(node=>node.type==='textarea').props.value,' https://example.invalid/SYNTHETIC-STORE ');
  assert.equal(h.navigations.length,0);assert.equal(h.requests.length,1);
  assert.equal([...h.timers.values()].some(timer=>timer.ms===1200),false);
 }finally{h.dispose();await h.flush();}
});

test('unmount aborts an outstanding status read and cannot navigate on a late completion',async()=>{
 const h=harness();
 h.fill();h.submit();await h.respond(0,{status:'running',job_id:jobId,receipt:receipt()},202);h.render();
 assert.equal(h.requests.length,2);h.dispose();
 assert.equal(h.requests[1].options.signal.aborted,true);
 await h.respond(1,{status:'succeeded',job_id:jobId,receipt:receipt({dashboard_built:true})});
 assert.equal(h.navigations.length,0);assert.equal(h.timers.size,0);assertOnlyRegistration(h);
});
