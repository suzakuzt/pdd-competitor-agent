import assert from 'node:assert/strict';
import {createBoundedCuaTab,withBoundedBoundaryFallback} from '../scripts/cua_capture_io.mjs';
let reads=0,wheels=[];
const tab={id:'SYNTHETIC_ONLY',playwright:{evaluate:async()=>{reads++;return {verified:true};}}};
const adapter=createBoundedCuaTab(tab,{viewportHeight:300,wheel:async data=>{wheels.push(data);}});
assert.deepEqual(await adapter.playwright.evaluate(()=>null),{verified:true});
await adapter.scroll([10,20],'down',2);
assert.deepEqual(wheels,[{x:10,y:20,deltaY:600}]);assert.equal(reads,1);assert.equal(adapter.ioStatus().pending,0);
await assert.rejects(async()=>adapter.scroll([0,0],'down',7));assert.equal(wheels.length,1);
let resolveLate;
const delayed=createBoundedCuaTab({id:'SYNTHETIC_DELAY',playwright:{evaluate:()=>new Promise(resolve=>{resolveLate=resolve;})}},{viewportHeight:300,wheel:async()=>{},timeoutMs:15});
await assert.rejects(delayed.playwright.evaluate(()=>null),/CUA_IO_DEADLINE/);
assert.equal(delayed.ioStatus().poisoned,true);assert.equal(delayed.ioStatus().pending,1);
await assert.rejects(delayed.scroll([1,1],'down',1),/CUA_IO_POISONED/);
resolveLate({mustNeverBecomeABatch:true});await new Promise(resolve=>setImmediate(resolve));assert.equal(delayed.ioStatus().pending,0);
assert.equal(delayed.ioStatus().timings.length,1);
console.log(JSON.stringify({passed:true,checks:['normal wheel bound','read passthrough','deadline without kernel reset','late reply discarded and no new calls'],synthetic_only:true}));
const inspected={scrollTop:0,viewportHeight:300,scrollHeight:1200,readCoverageBottom:1150};let ends=0;
const keys=createBoundedCuaTab({id:'SYNTHETIC_KEYS',playwright:{evaluate:async()=>({...inspected})}},{viewportHeight:300,endKey:async()=>{ends++;}});
await assert.rejects(async()=>keys.scroll([10,10],'down',3),/END_TARGET_NOT_INSPECTED/);
await keys.playwright.evaluate(()=>null);
await assert.rejects(async()=>keys.scroll([10,10],'down',1),/END_TARGET_NOT_INSPECTED/);assert.equal(ends,0);
await keys.scroll([10,10],'down',3);assert.equal(ends,1);assert.equal(keys.ioStatus().timings.at(-1).kind,'ordinary_end_key');
inspected.readCoverageBottom=500;await keys.playwright.evaluate(()=>null);await assert.rejects(async()=>keys.scroll([10,10],'down',3),/END_TARGET_NOT_INSPECTED/);assert.equal(ends,1);
assert.throws(()=>createBoundedCuaTab(tab,{viewportHeight:300,wheel:async()=>{},endKey:async()=>{}}));
console.log(JSON.stringify({passed:true,checks:['End requires inspected state','End requires exact requested bottom','End stays inside inspected content','exactly one input method'],synthetic_only:true}));

function fallbackFixture({timeoutMs=1000,boundaryClick,endKey}={}){
 const state={scrollTop:1000,viewportHeight:300,scrollHeight:2000,readCoverageBottom:1450,
  shopVerified:true,listPresent:true,columnLayoutVerified:true,
  boundaries:[{documentTop:1460,top:460,bottom:480,rendered:true,visible:false}],
  recommendations:[{documentTop:1510,rendered:true}]};
 let readCount=0,endCount=0,clickCount=0;
 const current=createBoundedCuaTab({id:'SYNTHETIC_BOUNDARY',playwright:{evaluate:async()=>{readCount++;return structuredClone(state);}}},
  {viewportHeight:300,endKey:endKey||(async()=>{endCount++;}),timeoutMs});
 const existingReference=current;
 const wrapped=withBoundedBoundaryFallback(current,{timeoutMs,boundaryClick:async()=>{clickCount++;return boundaryClick?.();}});
 assert.equal(wrapped,existingReference);
 return {adapter:current,state,counts:()=>({readCount,endCount,clickCount})};
}

const boundary=fallbackFixture();
const realRead=await boundary.adapter.playwright.evaluate(()=>null);
assert.deepEqual(realRead,boundary.state);
await boundary.adapter.scroll([10,10],'down',1.5);
assert.deepEqual(boundary.counts(),{readCount:1,endCount:0,clickCount:1});
assert.equal(boundary.adapter.ioStatus().pending,0);
assert.equal(boundary.adapter.ioStatus().poisoned,false);
assert.equal(boundary.adapter.ioStatus().timings.at(-1).kind,'ordinary_boundary_click');
assert.equal(boundary.adapter.ioStatus().timings.at(-1).maxAllowedScrollTop,1450);
assert.equal(boundary.adapter.ioStatus().timings.at(-1).expectedCenterScrollTop,1320);
// The adapter passes the actual DOM through unchanged, including an overshoot.
// It must never clamp coordinates to conceal a violation from v5's validator.
boundary.state.scrollTop=1600;
assert.equal((await boundary.adapter.playwright.evaluate(()=>null)).scrollTop,1600);
assert.throws(()=>withBoundedBoundaryFallback(boundary.adapter,{boundaryClick:async()=>{}}));

const noRead=fallbackFixture();
await assert.rejects(noRead.adapter.scroll([10,10],'down',1.5),/BOUNDARY_TARGET_NOT_INSPECTED/);
assert.equal(noRead.counts().clickCount,0);assert.equal(noRead.adapter.ioStatus().poisoned,true);
await assert.rejects(noRead.adapter.playwright.evaluate(()=>null),/CUA_IO_POISONED/);

const guardMutations=[
 s=>{s.boundaries=[];},
 s=>{s.boundaries.push({...s.boundaries[0]});},
 s=>{s.boundaries[0].rendered=false;},
 s=>{s.boundaries[0].documentTop=1800;s.boundaries[0].top=800;s.boundaries[0].bottom=820;},
 s=>{s.boundaries[0].top=400;},
 s=>{s.recommendations[0].documentTop=1400;},
 s=>{s.shopVerified=false;},
 s=>{s.listPresent=false;},
 s=>{s.columnLayoutVerified=false;}
];
for(const mutate of guardMutations){
 const rejected=fallbackFixture();mutate(rejected.state);
 await rejected.adapter.playwright.evaluate(()=>null);
 await assert.rejects(rejected.adapter.scroll([10,10],'down',1.5),/BOUNDARY_TARGET_NOT_INSPECTED/);
 assert.equal(rejected.counts().clickCount,0);assert.equal(rejected.adapter.ioStatus().poisoned,true);
}
const shortTarget=fallbackFixture();await shortTarget.adapter.playwright.evaluate(()=>null);
await assert.rejects(shortTarget.adapter.scroll([10,10],'down',1),/BOUNDARY_TARGET_NOT_INSPECTED/);
assert.equal(shortTarget.counts().clickCount,0);

const ordinary=fallbackFixture();ordinary.state.scrollHeight=1750;
await ordinary.adapter.playwright.evaluate(()=>null);await ordinary.adapter.scroll([10,10],'down',1.5);
assert.equal(ordinary.counts().endCount,1);assert.equal(ordinary.counts().clickCount,0);
const failingEnd=fallbackFixture({endKey:async()=>{throw new Error('SYNTHETIC real input failure');}});
failingEnd.state.scrollHeight=1750;await failingEnd.adapter.playwright.evaluate(()=>null);
await assert.rejects(failingEnd.adapter.scroll([10,10],'down',1.5),/SYNTHETIC real input failure/);
assert.equal(failingEnd.counts().clickCount,0);assert.equal(failingEnd.adapter.ioStatus().poisoned,true);

let releaseBoundary;
const stuckBoundary=fallbackFixture({timeoutMs:15,boundaryClick:()=>new Promise(resolve=>{releaseBoundary=resolve;})});
await stuckBoundary.adapter.playwright.evaluate(()=>null);
await assert.rejects(stuckBoundary.adapter.scroll([10,10],'down',1.5),/CUA_IO_DEADLINE/);
assert.equal(stuckBoundary.adapter.ioStatus().poisoned,true);assert.equal(stuckBoundary.adapter.ioStatus().pending,1);
await assert.rejects(stuckBoundary.adapter.playwright.evaluate(()=>null),/CUA_IO_POISONED/);
releaseBoundary({mustNotBecomeADomBatch:true});await new Promise(resolve=>setImmediate(resolve));
assert.equal(stuckBoundary.adapter.ioStatus().pending,0);assert.equal(stuckBoundary.counts().readCount,1);
assert.equal(stuckBoundary.adapter.ioStatus().timings.at(-1).status,'failed');

const failedBoundary=fallbackFixture({boundaryClick:async()=>{throw new Error('SYNTHETIC click denied');}});
await failedBoundary.adapter.playwright.evaluate(()=>null);
await assert.rejects(failedBoundary.adapter.scroll([10,10],'down',1.5),/SYNTHETIC click denied/);
await assert.rejects(failedBoundary.adapter.scroll([10,10],'down',1.5),/CUA_IO_POISONED/);
assert.equal(failedBoundary.counts().clickCount,1);
console.log(JSON.stringify({passed:true,checks:['in-place adapter identity and current DOM passthrough','bounded end-label click after End-only guard',
 'actual post-click overshoot remains visible to v5','no fallback before a fresh read','missing/hidden/ambiguous/distant or conflicting boundary rejected',
 'only exact v5 frontier target admitted','ordinary End still preferred','real End error never retried via click',
 'click deadline poisons adapter and discards late reply','click failure stops further browser calls'],synthetic_only:true}));

const loadedLongPage={scrollTop:0,viewportHeight:300,scrollHeight:30300,readCoverageBottom:30250};let longEnds=0;
const longAdapter=createBoundedCuaTab({id:'SYNTHETIC_ALREADY_READ_LONG_LIST',playwright:{evaluate:async()=>({...loadedLongPage})}},
 {viewportHeight:300,endKey:async()=>{longEnds++;}});
await longAdapter.playwright.evaluate(()=>null);
await longAdapter.scroll([10,10],'down',100);assert.equal(longEnds,1);
await assert.rejects(async()=>longAdapter.scroll([10,10],'down',101),/up to 100 viewports/);
await assert.rejects(async()=>longAdapter.scroll([10,10],'down',56),/END_TARGET_NOT_INSPECTED/);
loadedLongPage.readCoverageBottom=1200;await longAdapter.playwright.evaluate(()=>null);
await assert.rejects(async()=>longAdapter.scroll([10,10],'down',100),/END_TARGET_NOT_INSPECTED/);assert.equal(longEnds,1);
await assert.rejects(async()=>adapter.scroll([10,20],'down',6.01),/up to 6 viewports/);assert.equal(wheels.length,1);
const longBoundary=fallbackFixture();Object.assign(longBoundary.state,{readCoverageBottom:25000,scrollHeight:26000});
Object.assign(longBoundary.state.boundaries[0],{documentTop:25010,top:24010,bottom:24030});
longBoundary.state.recommendations[0].documentTop=25060;
await longBoundary.adapter.playwright.evaluate(()=>null);await longBoundary.adapter.scroll([10,10],'down',80);
assert.equal(longBoundary.counts().clickCount,1);assert.equal(longBoundary.counts().endCount,0);
assert.equal(longBoundary.adapter.ioStatus().timings.at(-1).maxAllowedScrollTop,25000);
console.log(JSON.stringify({passed:true,checks:['End permits at most 100 already-read viewports','long End still requires exact inspected document bottom',
 'wheel remains limited to 6 viewports','long boundary fallback preserves exact frontier and rendered-boundary guards'],synthetic_only:true}));
