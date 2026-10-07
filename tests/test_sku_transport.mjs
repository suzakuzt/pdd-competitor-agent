import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {createBrowserAdapter} from '../scripts/local_browser.mjs';

const entryUrl='https://mobile.yangkeduo.com/mall_page.html?mall_id=123';
const envelope=value=>({structuredContent:{message:`Script ran on page and returned:\n\`\`\`json\n${JSON.stringify(value)}\n\`\`\``}});

// All page operations execute against this in-memory page, never a real browser.
async function fixture(){
 const calls=[],sleeps=[],events=[];
 const pages=[{id:2,url:'https://example.org/unrelated',selected:true},{id:7,url:entryUrl,selected:false}];
 const sandbox={location:{origin:'https://mobile.yangkeduo.com',href:entryUrl},innerWidth:1280,innerHeight:731,window:{version:1,reads:[],injected:false},queueMicrotask};
 const client={
  async close(){events.push('closed');},
  async callTool(name,args={},timeoutMs){
   calls.push({name,args,timeoutMs});events.push(name);
   if(name==='list_pages'||name==='select_page')return {structuredContent:{pages}};
   if(name==='evaluate_script')return envelope(await vm.runInNewContext(`(${args.function})()`,sandbox));
   if(name==='click_at')return {structuredContent:{message:'Done'}};
   throw new Error(`Unexpected fake browser tool: ${name}`);
  }
 };
 const browser=await createBrowserAdapter(client,'C:/SYNTHETIC_ONLY',{entryUrl,sleep:async ms=>{sleeps.push(ms);events.push(`sleep:${ms}`);}});
 calls.length=0;events.length=0;
 return {browser,client,calls,sleeps,events,sandbox};
}

test('evaluateMany returns ordered same-page readings using exactly one evaluate_script call',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());
 const observed=await f.browser.evaluateMany([
  [({label})=>({label,url:location.href,version:window.version}),{label:'identity'}],
  [()=>({width:innerWidth,height:innerHeight,version:window.version})],
  [({price})=>({price,version:window.version}),{price:'12.50'}],
 ]);
 assert.deepEqual(observed,[{label:'identity',url:entryUrl,version:1},{width:1280,height:731,version:1},{price:'12.50',version:1}]);
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].name,'evaluate_script');assert.equal(f.calls[0].args.pageId,7);
 assert.equal(f.calls[0].args.waitForStableDom,false);assert.equal(f.calls[0].args.dialogAction,'dismiss');assert.equal(f.calls[0].timeoutMs,45000);
 assert.deepEqual(f.sleeps,[]);
});

test('evaluateMany completes synchronous readers before page microtasks change the observation',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());
 const observed=await f.browser.evaluateMany([
  [()=>{window.reads.push('identity');queueMicrotask(()=>{window.version=2;});return window.version;}],
  [()=>{window.reads.push('selection');return window.version;}],
  [()=>{window.reads.push('price');return window.version;}],
  [()=>{window.reads.push('image');return window.version;}],
 ]);
 assert.deepEqual(observed,[1,1,1,1]);assert.deepEqual(f.sandbox.window.reads,['identity','selection','price','image']);
 assert.equal(f.sandbox.window.version,2);assert.equal(f.calls.filter(call=>call.name==='evaluate_script').length,1);
});

test('evaluateMany JSON arguments retain quotes, code-looking strings, Unicode and object values as data',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());
 const payload={text:"x'); window.injected = true; throw new Error('not code'); //",unicode:'款式\u2028甲\u2029乙',nested:['</script>',{quote:'"\\\n',flag:false,unknown:null}],__pddSourceChanged:false};
 const observed=await f.browser.evaluateMany([[value=>value,payload],[({text})=>text,{text:'${window.injected=true}`;'}]]);
 assert.deepEqual(observed,[payload,'${window.injected=true}`;']);assert.equal(f.sandbox.window.injected,false);
 assert.equal(f.calls.length,1);
});

test('evaluateMany rejects invalid cardinality, readers and non-JSON arguments before a browser call',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());
 const circular={};circular.self=circular;
 const invalid=[null,[],Array.from({length:5},()=>[()=>1]),[['not a function']],[[()=>1,circular]],[[()=>1,{number:1n}]]];
 for(const readers of invalid)await assert.rejects(f.browser.evaluateMany(readers),error=>error.reason==='browser_operation_failed');
 assert.deepEqual(f.calls,[]);assert.deepEqual(f.sleeps,[]);
 assert.deepEqual(await f.browser.evaluateMany([[()=>({stillUsable:true})]]),[{stillUsable:true}]);
});

test('evaluateMany refuses a foreign origin before any reader and blocks later page operations',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());f.sandbox.location.origin='https://example.org';
 await assert.rejects(f.browser.evaluateMany([[()=>{window.reads.push('must not run');return 1;}],[()=>{throw new Error('must not run');}]]),error=>error.status==='manual_review'&&error.reason==='source_changed');
 assert.deepEqual(f.sandbox.window.reads,[]);assert.equal(f.calls.length,1);
 for(const operation of [()=>f.browser.evaluateMany([[()=>1]]),()=>f.browser.evaluate(()=>1),()=>f.browser.click({x:10,y:10},{settleMs:0})])await assert.rejects(operation(),error=>error.status==='needs_browser');
 assert.equal(f.calls.length,1);assert.deepEqual(f.sleeps,[]);
});

test('click settleMs zero omits fixed sleep but still checks viewport before exact same-page click',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());
 await f.browser.click({x:300,y:400},{settleMs:0});
 assert.deepEqual(f.calls.map(call=>call.name),['evaluate_script','click_at']);
 assert.equal(f.calls[0].args.pageId,7);assert.deepEqual(f.calls[1].args,{pageId:7,x:300,y:400,includeSnapshot:false});
 assert.deepEqual(f.sleeps,[]);assert.deepEqual(f.events,['evaluate_script','click_at']);
});

test('ordinary click retains 700ms default and explicit bounded waits occur only after the click',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());
 await f.browser.click({x:0,y:0});await f.browser.click({x:1,y:1},{});await f.browser.click({x:2,y:2},{settleMs:125});
 assert.deepEqual(f.sleeps,[700,700,125]);
 assert.deepEqual(f.events,['evaluate_script','click_at','sleep:700','evaluate_script','click_at','sleep:700','evaluate_script','click_at','sleep:125']);
});

test('zero-wait clicks remain bounded by the current viewport and never click outside it',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());
 for(const point of [{x:-1,y:0},{x:0,y:-1},{x:1280,y:0},{x:0,y:731}])await assert.rejects(f.browser.click(point,{settleMs:0}),error=>error.status==='manual_review');
 assert.equal(f.calls.filter(call=>call.name==='click_at').length,0);assert.equal(f.calls.filter(call=>call.name==='evaluate_script').length,4);
 await f.browser.click({x:1279,y:730},{settleMs:0});
 f.sandbox.innerWidth=100;f.sandbox.innerHeight=80;
 await assert.rejects(f.browser.click({x:100,y:1},{settleMs:0}),error=>error.status==='manual_review');
 await f.browser.click({x:99,y:79},{settleMs:0});
 assert.equal(f.calls.filter(call=>call.name==='click_at').length,2);assert.deepEqual(f.sleeps,[]);
 assert.ok(f.calls.every(call=>call.args.pageId===7));
});

test('invalid click waits or nonfinite coordinates reject without reading or clicking the page',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());
 for(const settleMs of [-1,701,NaN,Infinity,'0',null])await assert.rejects(f.browser.click({x:5,y:5},{settleMs}),error=>error.reason==='browser_operation_failed');
 for(const point of [null,{x:NaN,y:5},{x:5,y:Infinity},{x:'5',y:5}])await assert.rejects(f.browser.click(point,{settleMs:0}),error=>error.reason==='browser_operation_failed');
 assert.deepEqual(f.calls,[]);assert.deepEqual(f.sleeps,[]);
});

test('zero-wait click checks origin and never issues click_at after source change',async t=>{
 const f=await fixture();t.after(()=>f.browser.close());f.sandbox.location.origin='https://example.org';
 await assert.rejects(f.browser.click({x:10,y:10},{settleMs:0}),error=>error.reason==='source_changed');
 assert.deepEqual(f.calls.map(call=>call.name),['evaluate_script']);assert.deepEqual(f.sleeps,[]);
});
