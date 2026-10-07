// Integration regression for the restricted CUA module realm; no browser/network.
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {URL,fileURLToPath,pathToFileURL} from 'node:url';
import vm from 'node:vm';

const ROOT=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEMP=await fs.mkdtemp(path.join(ROOT,'SYNTHETIC_CUA_GLOBALS_'));
const absent=['process','Buffer','performance','structuredClone'];
const context=vm.createContext({URL});
for(const name of absent)assert.equal(vm.runInContext(`typeof ${name}`,context),'undefined');
const contextCaches=new Map();
const allowedBuiltins=new Set(['node:fs/promises','node:path','node:crypto','node:buffer','node:perf_hooks','node:v8']);
async function moduleFor(specifier,referrer,targetContext=context){
 let cache=contextCaches.get(targetContext);if(!cache){cache=new Map();contextCaches.set(targetContext,cache);}
 const key=specifier.startsWith('node:')?specifier:new URL(specifier,referrer).href;
 assert.notEqual(key,'node:process','CUA forbids importing node:process');
 if(cache.has(key))return cache.get(key);
 let module;
 if(key.startsWith('node:')){
  assert.ok(allowedBuiltins.has(key),'Restricted capture modules must use an explicitly reviewed builtin: '+key);
  const exports=await import(key);
  module=new vm.SyntheticModule(Object.keys(exports),function(){for(const name of Object.keys(exports))this.setExport(name,exports[name]);},{context:targetContext,identifier:key});
 }else{
  module=new vm.SourceTextModule(await fs.readFile(fileURLToPath(key),'utf8'),{context:targetContext,identifier:key});
 }
 cache.set(key,module);
 await module.link((next,owner)=>moduleFor(next,owner.identifier,targetContext));
 return module;
}
const source='https://mobile.yangkeduo.com/mall_page.html?mall_id=99900009';
function page(index,end=false){
 const card={slot:'0:0',domColumn:0,domRow:0,title:'SYNTHETIC CUA CARD',identityTitle:'SYNTHETIC CUA CARD',
  imageUrl:'https://example.invalid/synthetic.png',goodsUrl:null,goodsId:null,salesRaw:null,priceRaw:'¥1',rawText:'SYNTHETIC',
  ready:true,pendingReasons:[],inViewport:true,position:{top:100,left:0,bottom:260,viewportTop:100,viewportBottom:260}};
 return {at:`2026-10-05T01:00:0${index}.000Z`,pageUrl:source,shopVerified:true,listPresent:true,scrollTop:0,viewportHeight:300,viewportWidth:1000,scrollHeight:1000,
  columnLayoutVerified:true,columnSlotCounts:[1],loadedCardCount:1,slotManifest:[{slot:'0:0',domColumn:0,domRow:0,state:'ready',position:{...card.position},pendingReasons:[]}],
  cards:[card],readCoverageBottom:260,endBoundaryObserved:end,boundaries:end?[{rendered:true,visible:true,documentTop:280,top:280,bottom:295}]:[],recommendations:[]};
}
const results=[];
try{
 const entry=await moduleFor(pathToFileURL(path.join(ROOT,'scripts/capture_session.mjs')).href,pathToFileURL(ROOT+path.sep).href);
 await entry.evaluate();
 const {createCaptureSession,readCaptureSession,recoverCaptureSession}=entry.namespace;
 const options={sourceUrl:source,shopName:'SYNTHETIC CUA SHOP',checkpointReference:'SYNTHETIC/batches',attemptId:'attempt_synthetic_cua_1'};
 let reads=0;
 const complete=await createCaptureSession({id:'SYNTHETIC_TAB',playwright:{evaluate:async()=>{reads++;return page(1,true);}},scroll:async()=>{}},path.join(TEMP,'complete'),options);
 assert.equal(complete.status().phase,'ready');
 assert.equal(complete.status().owner.kind,'cua_host');assert.equal(complete.status().owner.pid,null);
 const result=await complete.capture();
 assert.equal(result.phase,'complete');assert.equal(result.importReady,true);assert.equal(result.snapshot.cardCount,1);
 const before=result.snapshot.sha256;
 assert.equal((await complete.seal()).snapshot.sha256,before);assert.equal(reads,1);
 assert.equal((await readCaptureSession(path.join(TEMP,'complete'))).snapshot.sha256,before);
 results.push({name:'missing Node globals: create, complete capture, immutable seal and read status',passed:true});
 let first=true;
 const partial=await createCaptureSession({id:'SYNTHETIC_TAB',playwright:{evaluate:async()=>{if(first){first=false;return page(1);}throw new Error('SYNTHETIC timeout');}},scroll:async()=>{}},path.join(TEMP,'partial'),{...options,attemptId:'attempt_synthetic_cua_2'});
 assert.equal((await partial.capture()).phase,'running');
 const failure=await partial.step();
 assert.equal(failure.phase,'partial');assert.equal(failure.snapshot.cardCount,1);assert.equal(failure.retry.allowed,true);
 const snapshot=JSON.parse(await fs.readFile(path.join(TEMP,'partial',failure.snapshot.file),'utf8'));
 assert.equal(snapshot.status,'partial');assert.equal(snapshot.collectionEvidence.stopReason,'page_read_failed');
 results.push({name:'missing Node globals: failed subsequent read seals partial prefix',passed:true});
 const runningDirectory=path.join(TEMP,'unknown_owner');
 const running=await createCaptureSession({id:'SYNTHETIC_TAB',playwright:{evaluate:async()=>page(1)},scroll:async()=>{}},runningDirectory,{...options,attemptId:'attempt_synthetic_cua_3'});
 await running.capture();
 const stateBefore=await fs.readFile(path.join(runningDirectory,'session.json'),'utf8');
 await assert.rejects(recoverCaptureSession(runningDirectory,{interruptionConfirmed:true,expectedSessionId:running.status().sessionId}),error=>error.code==='OWNER_RUNTIME_UNVERIFIED');
 assert.equal(await fs.readFile(path.join(runningDirectory,'session.json'),'utf8'),stateBefore);
 await assert.rejects(fs.stat(path.join(runningDirectory,'recovery.lock')),error=>error.code==='ENOENT');
 await running.seal('SYNTHETIC normal operator seal');
 results.push({name:'CUA unknown PID cannot recover or steal ownership; normal explicit seal still works',passed:true});
 const resetDirectory=path.join(TEMP,'reported_reset');
 const interrupted=await createCaptureSession({id:'SYNTHETIC_RESET_TAB',playwright:{evaluate:async()=>page(1)},scroll:async()=>{}},resetDirectory,{...options,attemptId:'attempt_synthetic_cua_reset'});
 await interrupted.capture();
 const interruptedBytes=await fs.readFile(path.join(resetDirectory,'session.json'));
 const prefixBytes=await fs.readFile(path.join(resetDirectory,'batches/batch_0001.json'));
 const proof={tool:'mcp__cua_repl.js',event:'kernel_reset',message:'js execution timed out; kernel reset, rerun your request',observedAt:new Date().toISOString(),sessionId:interrupted.status().sessionId,stateSha256:crypto.createHash('sha256').update(interruptedBytes).digest('hex')};
 await assert.rejects(recoverCaptureSession(resetDirectory,{interruptionConfirmed:true,expectedSessionId:proof.sessionId,cuaResetEvidence:proof}),error=>error.code==='OWNER_STILL_ACTIVE');
 results.push({name:'reset attestation cannot recover a session still active in this host',passed:true});
 // A second restricted realm models a replaced CUA kernel; the fixture's
 // attestation is explicitly SYNTHETIC and is never evidence of a live reset.
 const nextContext=vm.createContext({URL});
 const nextModule=await moduleFor(pathToFileURL(path.join(ROOT,'scripts/capture_session.mjs')).href,pathToFileURL(ROOT+path.sep).href,nextContext);await nextModule.evaluate();
 const recoverAfterReset=nextModule.namespace.recoverCaptureSession;
 for(const invalid of [{...proof,stateSha256:'0'.repeat(64)},{...proof,event:'timeout'},{...proof,sessionId:'different'},{...proof,observedAt:'2000-01-01T00:00:00Z'}]){
  await assert.rejects(recoverAfterReset(resetDirectory,{interruptionConfirmed:true,expectedSessionId:proof.sessionId,cuaResetEvidence:invalid}),error=>error.code==='INVALID_CUA_RESET_EVIDENCE');
  assert.deepEqual(await fs.readFile(path.join(resetDirectory,'session.json')),interruptedBytes);
 }
 results.push({name:'ordinary timeout, foreign session, stale state and invalid reset time reject without writes',passed:true});
 const recovered=await recoverAfterReset(resetDirectory,{interruptionConfirmed:true,expectedSessionId:proof.sessionId,cuaResetEvidence:proof});
 assert.equal(recovered.phase,'partial');assert.equal(recovered.importReady,true);assert.equal(recovered.owner.pid,null);assert.equal(recovered.recoveredByPid,null);assert.equal(recovered.snapshot.cardCount,1);
 assert.equal(recovered.cuaResetEvidence.stateSha256,proof.stateSha256);assert.deepEqual(await fs.readFile(path.join(resetDirectory,'batches/batch_0001.json')),prefixBytes);
 assert.equal((await recoverAfterReset(resetDirectory,{interruptionConfirmed:true,expectedSessionId:proof.sessionId})).snapshot.sha256,recovered.snapshot.sha256);
 await assert.rejects(interrupted.step(),error=>error.code==='SESSION_CHANGED');
 results.push({name:'explicit reset in a replacement host seals only immutable prefix as partial; old handle cannot continue',passed:true});
 const runnerModule=await moduleFor(pathToFileURL(path.join(ROOT,'scripts/host_capture_runner.mjs')).href,pathToFileURL(ROOT+path.sep).href);
 await runnerModule.evaluate();
 let runnerReads=0;
 const runnerSession=await createCaptureSession({id:'SYNTHETIC_RUNNER_TAB',playwright:{evaluate:async()=>{runnerReads++;return page(1,true);}},scroll:async()=>{}},path.join(TEMP,'host_runner'),{...options,attemptId:'attempt_synthetic_host_runner'});
 const runner=runnerModule.namespace.createHostCaptureRunner(runnerSession,{ioStatus:()=>({poisoned:false,pending:0})});
 const runnerResult=await runner.advance({maxSteps:3});
 assert.equal(runnerResult.phase,'complete');assert.equal(runnerResult.operations,1);assert.equal(runnerResult.cards,1);
 assert.equal(runnerResult.checkpointedBatches,1);assert.equal(runnerResult.canAdvance,false);assert.equal(runnerReads,1);
 assert.ok(Number.isFinite(runnerResult.elapsedMs));assert.doesNotMatch(JSON.stringify(runnerResult),/https?:|SYNTHETIC CUA CARD|owner_token|snapshot_json/);
 await runner.advance();assert.equal(runnerReads,1);
 results.push({name:'fixed host runner loads in restricted realm, uses module-local clock, seals real synthetic session and stops at terminal',passed:true});
 for(const name of absent)assert.equal(vm.runInContext(`typeof ${name}`,context),'undefined');
 results.push({name:'dependencies remain module-local and do not inject ambient globals',passed:true});
 const receipt={passed:true,tests:results.length,results,missing_ambient_globals:absent,synthetic_only:true,website_collection_performed:false,
  code_sha256:crypto.createHash('sha256').update(await fs.readFile(path.join(ROOT,'scripts/capture_session.mjs'))).digest('hex'),
  runner_code_sha256:crypto.createHash('sha256').update(await fs.readFile(path.join(ROOT,'scripts/host_capture_runner.mjs'))).digest('hex')};
 await fs.writeFile(path.join(ROOT,'cua_missing_globals_acceptance.json'),JSON.stringify(receipt,null,2)+'\n');
 console.log(JSON.stringify(receipt,null,2));
}finally{
 if(path.dirname(TEMP)===ROOT&&path.basename(TEMP).startsWith('SYNTHETIC_CUA_GLOBALS_'))await fs.rm(TEMP,{recursive:true,force:true});
}
