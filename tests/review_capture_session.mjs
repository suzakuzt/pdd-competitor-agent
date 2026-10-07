import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {createCaptureSession,recoverCaptureSession,readCaptureSession} from '../scripts/capture_session.mjs';
const HERE=path.dirname(fileURLToPath(import.meta.url)),ROOT=path.dirname(HERE);
const args=process.argv.slice(2);let fixtureRoot=path.join(ROOT,'sources/shop_tracking_B_v5_20261004'),retainedIntegration=null;
for(let i=0;i<args.length;i+=2){
 if(!['--fixture-root','--retain-integration'].includes(args[i])||!args[i+1]||args[i+1].startsWith('--'))throw new Error('Usage: node review_capture_session.mjs [--fixture-root <historical bundle>] [--retain-integration <new synthetic output directory>]');
 if(args[i]==='--fixture-root')fixtureRoot=path.resolve(args[i+1]);else retainedIntegration=path.resolve(args[i+1]);
}
const TEMP=await fs.mkdtemp(path.join(HERE,'SYNTHETIC_SESSION_ONLY_'));
const SOURCE='https://mobile.yangkeduo.com/mall_page.html?mall_id=101';
const OPTIONS={sourceUrl:SOURCE,shopName:'SYNTHETIC SHOP',checkpointReference:'SYNTHETIC/batches',attemptId:'attempt_synthetic_1'};
const results=[];let serial=0;
let crossLanguageFixture=null;
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const at=n=>`2026-10-05T00:00:${String(n).padStart(2,'0')}.000Z`;
function page(n,{end=false,y=0,cards=1,source=SOURCE}={}){
 const rows=Array.from({length:cards},(_,i)=>({slot:`${i}:0`,domColumn:i,domRow:0,title:`SYNTHETIC ${i}`,identityTitle:`SYNTHETIC ${i}`,
  imageUrl:`https://example.invalid/synthetic/${i}.png`,goodsUrl:null,goodsId:null,salesRaw:null,priceRaw:'¥1',rawText:'SYNTHETIC',
  ready:true,pendingReasons:[],inViewport:true,position:{top:100,left:i*220,bottom:260,viewportTop:100-y,viewportBottom:260-y}}));
 return {at:at(n),pageUrl:source,shopVerified:true,listPresent:true,scrollTop:y,viewportHeight:300,viewportWidth:1000,scrollHeight:1000,
  columnLayoutVerified:true,columnSlotCounts:rows.map(()=>1),loadedCardCount:rows.length,slotManifest:rows.map(c=>({slot:c.slot,domColumn:c.domColumn,domRow:0,state:'ready',position:{...c.position},pendingReasons:[]})),
  cards:rows,readCoverageBottom:260,endBoundaryObserved:end,boundaries:end?[{rendered:true,visible:true,documentTop:y+280,top:280,bottom:295}]:[],recommendations:[]};
}
function tabFor(pages){let reads=0,scrolls=0;return {id:'SYNTHETIC_TAB',playwright:{evaluate:async()=>{const p=pages[reads++];if(p instanceof Error)throw p;if(!p)throw new Error('Synthetic exhausted');return structuredClone(p);}},scroll:async()=>{scrolls++;},counts:()=>({reads,scrolls})};}
async function harness(pages,extra={}){const directory=path.join(TEMP,String(++serial));const tab=tabFor(pages);const session=await createCaptureSession(tab,directory,{...OPTIONS,...extra});return {directory,tab,session};}
async function json(file){return JSON.parse(await fs.readFile(file,'utf8'));}
async function test(name,fn){try{await fn();results.push({name,status:'passed'});}catch(error){results.push({name,status:'failed',error:error.stack});}}
async function deadSession(pages,{stale=false,completeOrphan=false}={}){
 const directory=path.join(TEMP,String(++serial)),fixture=path.join(TEMP,`child-${serial}.json`);
 await fs.writeFile(fixture,JSON.stringify({directory,pages,options:OPTIONS,stale,completeOrphan}));
 const moduleUrl=pathToFileURL(path.join(ROOT,'scripts/capture_session.mjs')).href;
 const script=`import * as fs from 'node:fs/promises';import path from 'node:path';import {createCaptureSession} from ${JSON.stringify(moduleUrl)};
 const f=JSON.parse(await fs.readFile(process.argv[1],'utf8'));let i=0;const tab={id:'DEAD_SYNTHETIC',playwright:{evaluate:async()=>structuredClone(f.pages[i++])},scroll:async()=>{}};
 const s=await createCaptureSession(tab,f.directory,f.options);await s.capture();const before=await fs.readFile(path.join(f.directory,'session.json'));
 if(f.stale||f.completeOrphan){await s.step();await fs.writeFile(path.join(f.directory,'session.json'),before);}
 process.exit(0);`;
 const child=spawnSync(process.execPath,['--input-type=module','-e',script,fixture],{encoding:'utf8',timeout:30000});
 assert.equal(child.status,0,child.stderr);return {directory,state:await json(path.join(directory,'session.json'))};
}

await test('fresh session persists atomic public state without private credentials',async()=>{
 const {directory,session}=await harness([page(1)]);const returned=await session.capture();assert.equal(returned.busy,false);const s=await json(path.join(directory,'session.json'));
 assert.equal(s.phase,'running');assert.equal(s.progress.cards,1);assert.equal(s.batchManifest.length,1);assert.equal(s.batchManifest[0].sha256,digest(await fs.readFile(path.join(directory,'batches/batch_0001.json'))));
 assert.deepEqual(Object.keys(s.owner),['kind','pid']);assert.equal(s.owner.kind,'node_process');assert.equal(typeof s.owner.pid,'number');assert.equal('token' in s,false);assert.equal((await fs.readdir(directory)).some(n=>n.endsWith('.tmp')),false);
 await session.seal('synthetic operator stop');
});
await test('complete requires driver visible end and full ready coverage',async()=>{
 const {directory,session,tab}=await harness([page(1,{end:true})]);const result=await session.capture();assert.equal(result.phase,'complete');assert.equal(result.snapshot.cardCount,1);
 const snap=await json(path.join(directory,result.snapshot.file));assert.equal(snap.status,'complete');assert.equal(snap.endBoundaryObserved,true);
 assert.equal((await session.step()).phase,'complete');assert.equal(tab.counts().reads,1);assert.equal((await session.seal()).snapshot.sha256,result.snapshot.sha256);
});
await test('repeated capture and concurrent step or seal do not issue extra reads',async()=>{
 let release;const barrier=new Promise(r=>{release=r;});let reads=0;
 const tab={id:'GATED',playwright:{evaluate:async()=>{reads++;await barrier;return page(1);}},scroll:async()=>{}};
 const dir=path.join(TEMP,String(++serial));const s=await createCaptureSession(tab,dir,OPTIONS);const started=s.capture();
 await assert.rejects(s.step(),e=>e.code==='SESSION_BUSY');await assert.rejects(s.seal(),e=>e.code==='SESSION_BUSY');release();await started;
 await assert.rejects(s.capture(),e=>e.code==='INITIAL_CAPTURE_ALREADY_DONE');assert.equal(reads,1);await s.seal('test end');
});
await test('timeout seals only verified durable prefix and never loops browser calls',async()=>{
 const error=new Error('Synthetic timeout');error.code='TOOL_TIMEOUT';const {directory,session,tab}=await harness([page(1),error]);await session.capture();const result=await session.step();
 assert.equal(result.phase,'partial');assert.equal(result.retry.allowed,true);assert.equal(result.snapshot.cardCount,1);assert.equal(result.progress.verifiedBatches,1);
 const snap=await json(path.join(directory,result.snapshot.file));assert.equal(snap.observedFrom,at(1));assert.equal(snap.observedTo,at(1));assert.equal(snap.status,'partial');
 await session.step();assert.equal(tab.counts().reads,2);
});
await test('login versus CAPTCHA permission and identity are terminal not retried',async()=>{
 for(const [message,phase] of [['LOGIN_REQUIRED','needs_login'],['CAPTCHA_REQUIRED','manual_review'],['permission denied','manual_review']]){
  const h=await harness([new Error(message)]);const r=await h.session.capture();assert.equal(r.phase,phase);assert.equal(r.snapshot,null);assert.equal(r.retry.allowed,false);await h.session.capture();assert.equal(h.tab.counts().reads,1);
 }
 const h=await harness([page(1),page(2,{source:'https://mobile.yangkeduo.com/mall_page.html?mall_id=999'})]);await h.session.capture();const r=await h.session.step();assert.equal(r.phase,'manual_review');assert.equal(r.snapshot.cardCount,1);assert.equal(r.retry.allowed,false);
 await assert.rejects(createCaptureSession(tabFor([page(1)]),path.join(TEMP,String(++serial)),{...OPTIONS,attemptId:'other',parentSession:{directory:h.directory}}),e=>e.code==='RETRY_NOT_ALLOWED');
});
await test('count budgets preserve oversized read evidence and stop before another action',async()=>{
 for(const limits of [{maxSteps:1},{maxBatches:1},{maxCards:1}]){
  const h=await harness([page(1,{cards:2})],{limits});const r=await h.session.capture();assert.equal(r.phase,'partial');assert.equal(r.snapshot.cardCount,2);
  await h.session.step();assert.equal(h.tab.counts().reads,1);
 }
 await assert.rejects(harness([page(1)],{limits:{maxFreshRetries:3}}),e=>e.code==='INVALID_LIMIT');
});
await test('fresh directory exclusivity and immutable snapshot prevent overwrites',async()=>{
 const h=await harness([page(1,{end:true})]);await assert.rejects(createCaptureSession(h.tab,h.directory,OPTIONS),e=>e.code==='FRESH_DIRECTORY_REQUIRED');
 const r=await h.session.capture(),before=await fs.readFile(path.join(h.directory,r.snapshot.file));await h.session.seal();assert.deepEqual(await fs.readFile(path.join(h.directory,r.snapshot.file)),before);
});
await test('source reference is safe relative metadata distinct from local output directory',async()=>{
 for(const checkpointReference of ['/absolute/batches','C:/private/batches','../escape','sources/../escape'])await assert.rejects(harness([page(1)],{checkpointReference}),e=>e.code==='INVALID_CHECKPOINT_REFERENCE');
});
await test('active owner cannot be recovered even with explicit session confirmation',async()=>{
 const h=await harness([page(1)]);await h.session.capture();
 await assert.rejects(recoverCaptureSession(h.directory,{interruptionConfirmed:true,expectedSessionId:h.session.status().sessionId}),e=>e.code==='OWNER_STILL_ACTIVE');
 await h.session.seal('synthetic manual finish');
});
await test('dead owner recovery revalidates durable prefix without a browser',async()=>{
 const {directory,state}=await deadSession([page(1)]);const raw=await fs.readFile(path.join(directory,'batches/batch_0001.json'));
 await assert.rejects(recoverCaptureSession(directory),e=>e.code==='RECOVERY_CONFIRMATION_REQUIRED');
 const r=await recoverCaptureSession(directory,{interruptionConfirmed:true,expectedSessionId:state.sessionId});
 assert.equal(r.phase,'partial');assert.equal(r.snapshot.cardCount,1);assert.equal(r.retry.allowed,true);assert.deepEqual(await fs.readFile(path.join(directory,'batches/batch_0001.json')),raw);
 const snap=await json(path.join(directory,r.snapshot.file));assert.equal(snap.collectionEvidence.captureSession.recoveredFromDurableCheckpoints,true);
 assert.equal(r.importReady,true);assert.equal(snap.collectionEvidence.importReady,true);
 assert.equal((await recoverCaptureSession(directory,{interruptionConfirmed:true,expectedSessionId:state.sessionId})).snapshot.sha256,r.snapshot.sha256);
});
await test('crash after batch sync before session update recovers new durable batch',async()=>{
 const {directory,state}=await deadSession([page(1),page(2)],{stale:true});assert.equal(state.batchManifest.length,1);
 const r=await recoverCaptureSession(directory,{interruptionConfirmed:true,expectedSessionId:state.sessionId});assert.equal(r.phase,'partial');assert.equal(r.progress.verifiedBatches,2);
 const snap=await json(path.join(directory,r.snapshot.file));assert.equal(snap.observedTo,at(2));assert.equal(snap.rows[0].firstObservedAt,at(1));assert.equal(snap.rows[0].observedAt,at(2));
});
await test('uncommitted complete snapshot remains untouched and recovery is partial',async()=>{
 const {directory,state}=await deadSession([page(1),page(2,{end:true})],{completeOrphan:true});const original=await fs.readFile(path.join(directory,'snapshot.json'));
 const r=await recoverCaptureSession(directory,{interruptionConfirmed:true,expectedSessionId:state.sessionId});assert.equal(r.phase,'partial');assert.equal(r.snapshot.file,'snapshot.recovered.json');assert.equal(r.snapshot.status,'partial');assert.deepEqual(await fs.readFile(path.join(directory,'snapshot.json')),original);
 assert.equal((await json(path.join(directory,r.snapshot.file))).endBoundaryObserved,true);
});
await test('torn untracked batch preserves accepted prefix but requires manual review',async()=>{
 const {directory,state}=await deadSession([page(1)]);await fs.writeFile(path.join(directory,'batches/batch_0002.json'),'{"batch":2,');
 const r=await recoverCaptureSession(directory,{interruptionConfirmed:true,expectedSessionId:state.sessionId});assert.equal(r.phase,'manual_review');assert.equal(r.snapshot.cardCount,1);assert.equal(r.retry.allowed,false);assert.equal(r.snapshot.status,'partial');
 assert.equal(r.importReady,false);const snap=await json(path.join(directory,r.snapshot.file));assert.equal(snap.collectionEvidence.batchCount,2);assert.equal(snap.collectionEvidence.importReady,false);assert.equal(snap.collectionEvidence.checkpointManifest[1].parseError,true);
});
await test('recorded raw batch tampering is rejected without rewriting evidence',async()=>{
 const {directory,state}=await deadSession([page(1)]);const file=path.join(directory,'batches/batch_0001.json');await fs.appendFile(file,' ');
 await assert.rejects(recoverCaptureSession(directory,{interruptionConfirmed:true,expectedSessionId:state.sessionId}),e=>e.code==='CHECKPOINT_HASH_CONFLICT');
 assert.equal(await fs.readFile(file,'utf8').then(s=>s.endsWith(' ')),true);assert.equal(await fs.stat(path.join(directory,'snapshot.json')).catch(()=>null),null);
});
await test('fresh retry gets new window new attempt and bounded parent lineage',async()=>{
 const first=await harness([page(1),new Error('timeout')]);await first.session.capture();await first.session.step();const parent=first.session.status();
 await assert.rejects(createCaptureSession(tabFor([page(3)]),path.join(TEMP,String(++serial)),{...OPTIONS,parentSession:{directory:first.directory}}),e=>e.code==='NEW_ATTEMPT_REQUIRED');
 const child=await harness([page(3),new Error('timeout')],{attemptId:'attempt_child',parentSession:{directory:first.directory}});await child.session.capture();const r=await child.session.step();
 await assert.rejects(createCaptureSession(tabFor([page(4)]),path.join(TEMP,String(++serial)),{...OPTIONS,attemptId:'sibling',parentSession:{directory:first.directory}}),e=>e.code==='RETRY_ALREADY_CLAIMED');
 assert.equal(r.lineage.parentSessionId,parent.sessionId);assert.equal(r.lineage.parentSnapshotSha256,parent.snapshot.sha256);assert.equal(r.lineage.freshRetryIndex,1);assert.equal(r.retry.allowed,false);
 const snap=await json(path.join(child.directory,r.snapshot.file));assert.equal(snap.observedFrom,at(3));assert.equal(snap.rows[0].firstObservedAt,at(3));assert.equal(snap.rows.length,1);
 await assert.rejects(createCaptureSession(tabFor([page(4)]),path.join(TEMP,String(++serial)),{...OPTIONS,attemptId:'grandchild',parentSession:{directory:child.directory},limits:{maxFreshRetries:2}}),e=>e.code==='RETRY_NOT_ALLOWED');
});
await test('legal failed tail remains in recovered batch evidence without adding its rows',async()=>{
 const {directory,state}=await deadSession([page(1),page(2,{source:'https://mobile.yangkeduo.com/mall_page.html?mall_id=999'})],{stale:true});
 const r=await recoverCaptureSession(directory,{interruptionConfirmed:true,expectedSessionId:state.sessionId});assert.equal(r.phase,'manual_review');assert.equal(r.importReady,false);
 const snap=await json(path.join(directory,r.snapshot.file));assert.equal(snap.rows.length,1);assert.equal(snap.collectionEvidence.batchCount,2);assert.equal(snap.collectionEvidence.verifiedBatchCount,1);
 assert.equal(snap.collectionEvidence.shopIdentityChecks.length,2);assert.equal(snap.collectionEvidence.shopIdentityChecks[1].verified,false);
 assert.deepEqual(snap.collectionEvidence.checkpointManifest.map(r=>r.verified),[true,false]);
});
await test('existing snapshot collision records durable manual review without overwriting it',async()=>{
 const h=await harness([page(1,{end:true})]);const marker='SYNTHETIC conflicting evidence';await fs.writeFile(path.join(h.directory,'snapshot.json'),marker);
 await assert.rejects(h.session.capture(),e=>e.code==='EEXIST');const state=await json(path.join(h.directory,'session.json'));
 assert.equal(state.phase,'manual_review');assert.equal(state.importReady,false);assert.equal(await fs.readFile(path.join(h.directory,'snapshot.json'),'utf8'),marker);
});
await test('manual seal failure is visible and never claims a publishable prefix',async()=>{
 const h=await harness([page(1)]);await h.session.capture();await fs.writeFile(path.join(h.directory,'snapshot.json'),'SYNTHETIC unrelated existing file');
 await assert.rejects(h.session.seal('manual stop'),e=>e.code==='EEXIST');assert.equal(h.session.status().phase,'manual_review');assert.equal(h.session.status().importReady,false);
 assert.equal((await json(path.join(h.directory,'session.json'))).phase,'manual_review');
});
await test('pending visible boundary cannot be falsely completed',async()=>{
 const pending=page(1,{end:true});pending.cards[0].ready=false;pending.cards[0].pendingReasons=['title_not_ready'];pending.cards[0].identityTitle=null;pending.cards[0].title='';pending.slotManifest[0].state='pending';pending.slotManifest[0].pendingReasons=['title_not_ready'];
 const h=await harness([pending],{limits:{maxSteps:1}});const r=await h.session.capture();assert.equal(r.phase,'failed');assert.equal(r.snapshot,null);assert.equal(r.progress.pending,1);assert.equal(r.progress.ended,false);
});
await test('real B 42-batch fixture offline replay preserves 551 independent cards',async()=>{
 const fixture=fixtureRoot,snapshot=await json(path.join(fixture,'snapshot.json'));
 const names=(await fs.readdir(path.join(fixture,'batches'))).filter(n=>/^batch_\d+\.json$/.test(n)).sort();assert.equal(names.length,42);
 const sourceHashes={};const pages=[];for(const name of names){const bytes=await fs.readFile(path.join(fixture,'batches',name));sourceHashes[name]=digest(bytes);pages.push(JSON.parse(bytes));}
 const h=await harness(pages,{sourceUrl:snapshot.sourceUrl,shopName:snapshot.shopName,attemptId:'OFFLINE_REAL_FIXTURE_NOT_NEW_CAPTURE',maxScrollViewports:6});
 let r=await h.session.capture();for(let i=1;i<pages.length;i++)r=await h.session.step();
 assert.equal(r.phase,'complete');assert.equal(r.snapshot.cardCount,551);assert.equal(r.progress.verifiedBatches,42);
 const sealed=await json(path.join(h.directory,r.snapshot.file));assert.deepEqual(sealed.rows,snapshot.rows);assert.equal(sealed.observedFrom,snapshot.observedFrom);assert.equal(sealed.observedTo,snapshot.observedTo);
 for(const name of names)assert.equal(digest(await fs.readFile(path.join(fixture,'batches',name))),sourceHashes[name]);
 results.push({name:'real fixture source receipt',status:'passed',kind:'OFFLINE_REPLAY_NOT_WEBSITE_CAPTURE',source_directory:fixture,batch_count:42,card_count:551,batch_sha256:sourceHashes});
});
await test('small synthetic cross-language integration fixtures are isolated and labeled',async()=>{
 crossLanguageFixture=retainedIntegration||path.join(HERE,`SYNTHETIC_CROSS_LANGUAGE_${crypto.randomUUID()}`);await fs.mkdir(path.dirname(crossLanguageFixture),{recursive:true});await fs.mkdir(crossLanguageFixture);
 for(const [name,pages] of [['complete',[page(1,{end:true})]],['partial',[page(1),new Error('Synthetic timeout')]]]){
  const h=await harness(pages);await h.session.capture();if(name==='partial')await h.session.step();
  await fs.cp(h.directory,path.join(crossLanguageFixture,name),{recursive:true,errorOnExist:true,force:false});
 }
 const dead=await deadSession([page(1)]);await recoverCaptureSession(dead.directory,{interruptionConfirmed:true,expectedSessionId:dead.state.sessionId});
 await fs.cp(dead.directory,path.join(crossLanguageFixture,'recovered_partial'),{recursive:true,errorOnExist:true,force:false});
 await fs.writeFile(path.join(crossLanguageFixture,'SYNTHETIC_ONLY.json'),JSON.stringify({kind:'SYNTHETIC_TEST_ONLY',website_collection_performed:false,production_import_allowed:false,reason:'Cross-language structural gate test; not real shop observations'},null,2));
});

const files=['capture_session.mjs','rendered_capture_driver.mjs','live_capture_driver.mjs'];const hashes={};for(const file of files)hashes[file]=digest(await fs.readFile(path.join(ROOT,'scripts',file)));
const report={status:results.every(r=>r.status==='passed')?'passed':'failed',checked_at:new Date().toISOString(),passed:results.filter(r=>r.status==='passed').length,total:results.length,results,code_sha256:hashes,
 scope:'Synthetic isolated sessions and explicitly labeled historical B42 offline fixture replay; no browser control/network/production writes.',temporary_fixture_directory:TEMP,cross_language_fixture:crossLanguageFixture};
await fs.writeFile(path.join(HERE,'capture_session_acceptance.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({status:report.status,passed:report.passed,total:report.total,failed:results.filter(r=>r.status!=='passed'),code_sha256:hashes},null,2));
// Only our freshly allocated, fixed-parent test directory is removed.
if(path.dirname(TEMP)===HERE&&path.basename(TEMP).startsWith('SYNTHETIC_SESSION_ONLY_'))await fs.rm(TEMP,{recursive:true,force:true});
process.exitCode=report.status==='passed'?0:1;
