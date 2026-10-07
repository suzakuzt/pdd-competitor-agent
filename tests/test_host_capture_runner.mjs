import assert from 'node:assert/strict';
import {test} from 'node:test';
import {setTimeout as delay} from 'node:timers/promises';
import {createHostCaptureRunner} from '../scripts/host_capture_runner.mjs';

function fixture({phase='ready',verified=0,action}={}){
 const state={phase,busy:false,operation:null,importReady:false,
  progress:{verifiedBatches:verified,cards:verified*20,pending:0,ended:false},
  rows:[{url:'https://PRIVATE.invalid',token:'SECRET'}],sourceUrl:'https://PRIVATE.invalid',owner_token:'SECRET'};
 const io={poisoned:false,pending:0,timings:[{private:'SECRET'}]},calls=[];
 async function run(kind){calls.push(kind);if(action)await action({kind,state,io,calls});
  state.progress.verifiedBatches++;state.progress.cards+=20;if(state.phase==='ready')state.phase='running';}
 const session={status:()=>state,capture:()=>run('capture'),step:()=>run('step'),seal:()=>assert.fail('Runner must not seal independently')};
 return {state,io,calls,session,runner:createHostCaptureRunner(session,{ioStatus:()=>io})};
}

test('default advances exactly one capture, then one step; output is compact',async()=>{
 const f=fixture();const first=await f.runner.advance();assert.deepEqual(f.calls,['capture']);
 assert.equal(first.cards,20);assert.equal(first.operations,1);assert.equal(first.canAdvance,true);
 await f.runner.advance();assert.deepEqual(f.calls,['capture','step']);
 const output=JSON.stringify(first);assert.doesNotMatch(output,/SECRET|PRIVATE|rows|sourceUrl|owner_token|timings/);
});
test('explicit burst is capped at three operations',async()=>{
 const f=fixture();const result=await f.runner.advance({maxSteps:3});
 assert.deepEqual(f.calls,['capture','step','step']);assert.equal(result.operations,3);
 for(const options of [{maxSteps:4},{maxSteps:0},{maxSteps:1.5},{budgetMs:0},{budgetMs:60001},{unexpected:true}])
  await assert.rejects(f.runner.advance(options));
 assert.equal(f.calls.length,3);
});
test('terminal session is never advanced',async()=>{
 for(const phase of ['complete','partial','needs_login','manual_review','failed']){
  const f=fixture({phase,verified:1});const result=await f.runner.advance({maxSteps:3});
  assert.equal(result.reason,'session_terminal');assert.equal(result.canAdvance,false);assert.equal(f.calls.length,0);
 }
});
test('terminal result stops a burst immediately and preserves session checkpoints',async()=>{
 const f=fixture({action:async({state})=>{state.phase='partial';state.importReady=true;}});
 const result=await f.runner.advance({maxSteps:3});assert.equal(f.calls.length,1);
 assert.equal(result.checkpointedBatches,1);assert.equal(result.importReady,true);assert.equal(result.attentionRequired,true);
});
test('busy session, poisoned adapter and pending I/O make no browser call',async()=>{
 for(const mode of ['busy','operation','poisoned','pending']){
  const f=fixture();if(mode==='busy')f.state.busy=true;if(mode==='operation')f.state.operation={kind:'step'};
  if(mode==='poisoned')f.io.poisoned=true;if(mode==='pending')f.io.pending=1;
  const result=await f.runner.advance();assert.equal(result.canAdvance,false);assert.equal(f.calls.length,0);
 }
});
test('concurrent advance does not issue a duplicate operation',async()=>{
 let release;const f=fixture({action:()=>new Promise(resolve=>{release=resolve;})});
 const active=f.runner.advance();const concurrent=await f.runner.advance();
 assert.equal(concurrent.reason,'runner_busy');assert.equal(f.calls.length,1);
 release();await active;
});
test('budget prevents next operation without cancelling an issued slow operation',async()=>{
 let finished=false;const f=fixture({action:async()=>{await delay(20);finished=true;}});
 const result=await f.runner.advance({maxSteps:3,budgetMs:5});
 assert.equal(finished,true);assert.equal(f.calls.length,1);assert.equal(result.reason,'budget_exhausted');
 assert.equal(result.checkpointedBatches,1);assert.equal(result.canAdvance,true);
});
test('unexpected operation failure latches stop and never retries or leaks error contents',async()=>{
 const f=fixture({verified:2,phase:'running',action:async()=>{throw new Error('SECRET https://PRIVATE.invalid');}});
 const result=await f.runner.advance({maxSteps:3});assert.equal(result.reason,'operation_failed');
 assert.equal(result.checkpointedBatches,2);assert.equal(result.canAdvance,false);assert.equal(result.attentionRequired,true);
 assert.doesNotMatch(JSON.stringify(result),/SECRET|PRIVATE/);
 await f.runner.advance();assert.equal(f.calls.length,1);
 assert.throws(()=>createHostCaptureRunner(f.session,{ioStatus:()=>f.io}),/unwrapped/);
});
test('poison created during an operation prevents later operations',async()=>{
 const f=fixture({action:async({io})=>{io.poisoned=true;io.pending=1;}});
 const result=await f.runner.advance({maxSteps:3});assert.equal(result.reason,'io_poisoned');
 assert.equal(result.pendingIo,1);assert.equal(f.calls.length,1);
 f.io.poisoned=false;f.io.pending=0;await f.runner.advance();assert.equal(f.calls.length,1);
});
test('invalid status fails closed without an operation',async()=>{
 const f=fixture();f.state.progress.verifiedBatches=undefined;f.state.phase='https://PRIVATE.invalid';
 const result=await f.runner.advance();assert.equal(result.reason,'operation_failed');assert.equal(f.calls.length,0);
 assert.equal(result.canAdvance,false);assert.doesNotMatch(JSON.stringify(result),/PRIVATE/);
});
