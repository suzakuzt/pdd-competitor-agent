// Local durability wrapper. Only the adjacent v5 driver controls the supplied tab.
// No network, reload, login, CAPTCHA handling, scheduler or database writes.
import * as fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {Buffer} from 'node:buffer';
import {performance} from 'node:perf_hooks';
import {serialize,deserialize} from 'node:v8';
import {createRenderedCapture} from './rendered_capture_driver.mjs';
import {shopIdentityFromUrl} from './live_capture_driver.mjs';

const VERSION=1,active=new Set();
// CUA does not expose process and forbids importing node:process. Use a Node
// process only when the host already supplies it; never invent a CUA owner PID.
const nodeProcess=typeof process!=='undefined'&&Number.isSafeInteger(process.pid)&&process.pid>0&&typeof process.kill==='function'?process:null;
const TERMINAL=new Set(['complete','partial','needs_login','manual_review','failed']);
const DEFAULT_LIMITS={maxSteps:80,maxBatches:100,maxCards:2000,maxDurationMs:7200000,maxFreshRetries:1};
const HARD_LIMITS={maxSteps:500,maxBatches:1000,maxCards:10000,maxDurationMs:14400000,maxFreshRetries:2};
const LABELS={ready:'待开始',running:'采集中',complete:'完整采集已封存',partial:'部分采集已封存',needs_login:'需人工登录或恢复会话',manual_review:'需人工复核',failed:'采集失败，无有效原卡'};
const sha=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
// CUA's module realm does not supply Node ambient globals. Session values are
// serializable records; v8 cloning also preserves undefined and numeric values.
const copy=value=>deserialize(serialize(value));
const now=()=>new Date().toISOString();
function fail(code,message){const error=new Error(message);error.code=code;return error;}
async function exists(file){try{await fs.lstat(file);return true;}catch(e){if(e.code==='ENOENT')return false;throw e;}}
async function ordinary(file,kind='file'){
 const info=await fs.lstat(file);
 if(info.isSymbolicLink()||(kind==='file'?!info.isFile():!info.isDirectory()))throw fail('UNSAFE_PATH',`Expected an ordinary ${kind}: ${path.basename(file)}`);
 return info;
}
async function readJson(file){await ordinary(file);const bytes=await fs.readFile(file);return {value:JSON.parse(bytes.toString('utf8')),bytes,sha256:sha(bytes)};}
async function writeNew(file,value){
 const bytes=Buffer.from(JSON.stringify(value,null,2)+'\n');const handle=await fs.open(file,'wx');
 try{await handle.writeFile(bytes);await handle.sync();}finally{await handle.close();}
 return {file:path.basename(file),sha256:sha(bytes),bytes:bytes.length};
}
async function atomicState(directory,state){
 const temporary=path.join(directory,`.session-${crypto.randomUUID()}.tmp`);
 await writeNew(temporary,state);
 try{await fs.rename(temporary,path.join(directory,'session.json'));}
 catch(error){await fs.unlink(temporary).catch(()=>{});throw error;}
}
function limitsFrom(options={}){
 const limits={...DEFAULT_LIMITS,...options};
 for(const [key,value] of Object.entries(limits)){
  if(!(key in HARD_LIMITS)||!Number.isSafeInteger(value)||value<(key==='maxFreshRetries'?0:1)||value>HARD_LIMITS[key])throw fail('INVALID_LIMIT',`Invalid bounded limit: ${key}`);
 }
 return limits;
}
function pidAlive(pid){try{nodeProcess.kill(pid,0);return true;}catch(e){return e.code!=='ESRCH';}}
function retryPolicy(state,reason){
 const transient=['page_read_failed','scroll_failed','interrupted_session','operation_time_limit','operation_count_limit','batch_count_limit','card_count_limit'].includes(reason);
 const used=state.lineage.freshRetryIndex;
 return {automatic:false,allowed:transient&&used<state.limits.maxFreshRetries,
  remaining:Math.max(0,state.limits.maxFreshRetries-used),reason:transient?'explicit_fresh_page_and_new_directory_required':'manual_review_or_no_retry_needed'};
}
function classify(error,driverStop=null){
 const detail=`${error?.code||''} ${error?.name||''} ${error?.message||''}`;
 if(/captcha|验证码|权限|permission|access.denied|policy|forbidden|拒绝访问/i.test(detail))return {phase:'manual_review',reason:'permission_or_challenge_requires_manual_review'};
 if(/login.required|login.?expired|sign.in.required|登录|会话.*(?:失效|过期)/i.test(detail))return {phase:'needs_login',reason:'login_or_session_required'};
 if(driverStop==='page_read_failed'||driverStop==='scroll_failed')return {phase:'partial',reason:driverStop};
 if(driverStop)return {phase:'manual_review',reason:driverStop};
 return {phase:'manual_review',reason:'unclassified_capture_error'};
}
async function scanBatches(directory,recorded=[]){
 const batchDir=path.join(directory,'batches');await ordinary(batchDir,'directory');
 const names=(await fs.readdir(batchDir)).filter(n=>/^batch_\d+\.json$/.test(n)).sort();
 const old=new Map(recorded.map(r=>[r.file,r]));const entries=[];let anomaly=null;
 for(let i=0;i<names.length;i++){
  const file=names[i];await ordinary(path.join(batchDir,file));const bytes=await fs.readFile(path.join(batchDir,file));
  const digest=sha(bytes),prior=old.get(file);
  if(prior&&prior.sha256!==digest)throw fail('CHECKPOINT_HASH_CONFLICT',`Recorded checkpoint was changed: ${file}`);
  const item={file,sha256:digest,bytes:bytes.length,verified:false};
  if(file!==`batch_${String(i+1).padStart(4,'0')}.json`)anomaly ||= 'non_contiguous_checkpoint_files';
  try{item.page=JSON.parse(bytes.toString('utf8'));if(item.page.batch!==i+1)anomaly ||= 'checkpoint_batch_number_conflict';}
  catch{item.parseError=true;anomaly ||= 'incomplete_checkpoint_json';}
  if(!anomaly)item.verified=item.page.shopIdentityVerified===true&&!item.page.stop;
  entries.push(item);
 }
 for(const name of old.keys())if(!names.includes(name))throw fail('CHECKPOINT_MISSING',`Recorded checkpoint is missing: ${name}`);
 return {entries,anomaly};
}
const publicManifest=entries=>entries.map(({page,...item})=>item);
async function loadSession(directory){
 const resolved=path.resolve(directory);await ordinary(resolved,'directory');const record=await readJson(path.join(resolved,'session.json'));
 const s=record.value;
 const ownerValid=s.owner&&(((s.owner.kind===undefined||s.owner.kind==='node_process')&&Number.isSafeInteger(s.owner.pid)&&s.owner.pid>0)||(s.owner.kind==='cua_host'&&s.owner.pid===null));
 if(s.schemaVersion!==VERSION||typeof s.sessionId!=='string'||!s.sessionId.startsWith('capture_')||!ownerValid||!s.options||!Array.isArray(s.batchManifest)||!s.lineage)throw fail('INVALID_SESSION','Unsupported or damaged session state');
 limitsFrom(s.limits);
 return {directory:resolved,...record};
}
async function verifySnapshot(directory,state){
 if(!state.snapshot)return null;
 if(path.basename(state.snapshot.file)!==state.snapshot.file)throw fail('INVALID_SNAPSHOT_PATH','Snapshot must stay in its session directory');
 const result=await readJson(path.join(directory,state.snapshot.file));
 if(result.sha256!==state.snapshot.sha256||result.value.status!==state.snapshot.status||result.value.rows?.length!==state.snapshot.cardCount)throw fail('SNAPSHOT_HASH_CONFLICT','Sealed snapshot disagrees with its receipt');
 return result.value;
}
async function commit(directory,state,event){
 state.revision++;state.updatedAt=now();state.statusLabel=LABELS[state.phase];
 const receipt={schemaVersion:VERSION,sessionId:state.sessionId,revision:state.revision,at:state.updatedAt,event,
  phase:state.phase,importReady:state.importReady,operation:state.operation,progress:state.progress,batchManifest:state.batchManifest,
  snapshot:state.snapshot,lastError:state.lastError,previousEventSha256:state.lastEventSha256||null};
 const file=`event_${String(state.revision).padStart(6,'0')}_${crypto.randomUUID()}.json`;
 const saved=await writeNew(path.join(directory,'events',file),receipt);
 state.lastEventSha256=saved.sha256;state.lastEventFile=`events/${file}`;
 await atomicState(directory,state);
}
function addSessionEvidence(snapshot,state,recovered=false){
 return {...snapshot,collectionEvidence:{...snapshot.collectionEvidence,captureSession:{schemaVersion:VERSION,
  sessionId:state.sessionId,parentSessionId:state.lineage.parentSessionId,freshRetryIndex:state.lineage.freshRetryIndex,
  recoveredFromDurableCheckpoints:recovered,freshWindowOnly:true,automaticRetriesPerformed:0},
  checkpointManifest:copy(state.batchManifest),checkpointIntegrityStatus:state.checkpointIntegrityStatus||'verified',
  importReady:state.importReady}};
}

/** Caller must have verified a freshly loaded shop page, chosen 上新 and top=0.
 * No call resumes the old browser position or changes the browser on its own.
 */
export async function createCaptureSession(tab,directory,options={}){
 if(!tab?.playwright?.evaluate||typeof tab.scroll!=='function')throw fail('INVALID_TAB','An authorized existing CUA tab is required');
 const {parentSession=null,limits:requestedLimits,...driverOptions}=options;
 const allowed=new Set(['sourceUrl','checkpointReference','attemptId','shopName','browserName','browserTool','maxScrollViewports']);
 if(Object.keys(driverOptions).some(key=>!allowed.has(key)))throw fail('UNSUPPORTED_OPTION','Unsupported session option; arbitrary drivers and resume are not supported');
 if(typeof driverOptions.attemptId!=='string'||!driverOptions.attemptId.trim())throw fail('ATTEMPT_REQUIRED','A journal attemptId is required');
 if(typeof driverOptions.shopName!=='string'||!driverOptions.shopName.trim()||typeof driverOptions.checkpointReference!=='string'||!driverOptions.checkpointReference.trim())throw fail('INVALID_OPTIONS','Verified shop name and checkpoint reference are required');
 const checkpointReference=driverOptions.checkpointReference.replaceAll('\\','/');
 if(checkpointReference.startsWith('/')||checkpointReference.includes(':')||checkpointReference.split('/').includes('..')||/[\x00-\x1f]/.test(checkpointReference))throw fail('INVALID_CHECKPOINT_REFERENCE','Use a safe project-relative checkpoint reference; local output directory is a separate argument');
 const identity=shopIdentityFromUrl(driverOptions.sourceUrl);
 const resolved=path.resolve(directory),limits=limitsFrom(requestedLimits),sessionId=`capture_${crypto.randomUUID().replaceAll('-','')}`;
 if(await exists(resolved))throw fail('FRESH_DIRECTORY_REQUIRED','Session directory already exists; use a new fresh directory');
 let lineage={parentSessionId:null,parentDirectory:null,parentSnapshotSha256:null,freshRetryIndex:0};
 if(parentSession!==null){
  if(typeof parentSession!=='object'||Object.keys(parentSession).some(k=>k!=='directory')||!parentSession.directory)throw fail('INVALID_PARENT','parentSession only accepts its existing directory');
  let parent=await loadSession(parentSession.directory);
  if(parent.directory===resolved||resolved.startsWith(parent.directory+path.sep))throw fail('INVALID_PARENT','Fresh retry must use a separate sibling directory');
  if(!TERMINAL.has(parent.value.phase)){
   throw fail('PARENT_NOT_SEALED','Recover or seal the interrupted parent explicitly before starting its fresh retry');
  }
  await verifySnapshot(parent.directory,parent.value);
  await scanBatches(parent.directory,parent.value.batchManifest);
  if(!parent.value.retry?.allowed)throw fail('RETRY_NOT_ALLOWED','Parent requires manual review or has no remaining fresh retry');
  if(parent.value.attemptId===driverOptions.attemptId)throw fail('NEW_ATTEMPT_REQUIRED','Fresh retry needs a new journal attempt ID');
  const priorIdentity=shopIdentityFromUrl(parent.value.options.sourceUrl);
  if(priorIdentity.origin!==identity.origin||priorIdentity.identity_kind!==identity.identity_kind||priorIdentity.stable_identifier!==identity.stable_identifier||parent.value.options.shopName!==driverOptions.shopName)throw fail('PARENT_SHOP_CONFLICT','A fresh retry cannot switch shop identity');
  const next=parent.value.lineage.freshRetryIndex+1;
  // A child cannot increase the ancestor retry budget.
  limits.maxFreshRetries=Math.min(limits.maxFreshRetries,parent.value.limits.maxFreshRetries);
  if(next>limits.maxFreshRetries)throw fail('RETRY_LIMIT','Fresh retry limit exhausted');
  // One parent can reserve only one child, even if that child fails to start.
  // Never release the claim automatically: repeated sibling retries are a loop.
  try{await writeNew(path.join(parent.directory,'session-retry-child.json'),{schemaVersion:VERSION,
   parentSessionId:parent.value.sessionId,childSessionId:sessionId,childDirectory:resolved,
   childAttemptId:driverOptions.attemptId,claimedAt:now()});}
  catch(error){if(error.code==='EEXIST')throw fail('RETRY_ALREADY_CLAIMED','This parent has already reserved its only fresh retry');throw error;}
  lineage={parentSessionId:parent.value.sessionId,parentDirectory:parent.directory,parentSnapshotSha256:parent.value.snapshot?.sha256||null,freshRetryIndex:next};
 }
 await fs.mkdir(path.dirname(resolved),{recursive:true});await ordinary(path.dirname(resolved),'directory');
 await fs.mkdir(resolved);await fs.mkdir(path.join(resolved,'events'));
 const state={schemaVersion:VERSION,sessionId,attemptId:driverOptions.attemptId,
  owner:{kind:nodeProcess?'node_process':'cua_host',pid:nodeProcess?.pid??null},revision:0,phase:'ready',statusLabel:LABELS.ready,createdAt:now(),updatedAt:now(),
  options:{browserName:'Codex In-app Browser',maxScrollViewports:3,...driverOptions},tabId:tab.id??null,limits,lineage,
  operation:null,operationCount:0,elapsedMs:0,progress:{batches:0,verifiedBatches:0,cards:0,pending:0,ended:false,stop:null},
  batchManifest:[],snapshot:null,importReady:false,checkpointIntegrityStatus:'verified',lastError:null,retry:{automatic:false,allowed:false,remaining:limits.maxFreshRetries-lineage.freshRetryIndex,reason:'session_not_finished'}};
 let driver,busy=false;const started=performance.now();
 try{driver=await createRenderedCapture(tab,path.join(resolved,'batches'),state.options);await commit(resolved,state,'session_created');}
 catch(error){throw fail('SESSION_INITIALIZATION_FAILED',`Session initialization failed; preserve directory for review (${error.code||error.name})`);}
 active.add(resolved);
 const status=()=>copy({...state,busy,directory:resolved});
 async function ownership(){const current=await loadSession(resolved);if(current.value.sessionId!==state.sessionId||current.value.revision!==state.revision)throw fail('SESSION_CHANGED','Session ownership or revision changed; no further browser action is allowed');}
 async function refresh(){
  const scanned=await scanBatches(resolved,state.batchManifest);
  if(scanned.anomaly)throw fail('CHECKPOINT_INVALID',scanned.anomaly);
  state.batchManifest=publicManifest(scanned.entries);state.progress=copy(driver.status());delete state.progress.busy;
  state.elapsedMs=Math.round(performance.now()-started);
 }
 async function finish(phase,reason=null,event='session_sealed'){
  await refresh();state.operation=null;state.phase=phase;state.importReady=state.progress.cards>0&&['complete','partial'].includes(phase);
  if(reason)state.lastError={code:reason,message:LABELS[phase]||'采集已停止'};
  if(state.progress.cards>0){
   let snapshot=await driver.snapshot(phase==='complete'?null:reason||'operator_sealed_partial');
   if(snapshot.status==='complete'&&phase!=='complete')throw fail('FALSE_COMPLETE','Partial session cannot publish a complete snapshot');
   if(snapshot.status!=='complete'&&phase==='complete')throw fail('FALSE_COMPLETE','Driver did not establish complete coverage');
   snapshot=addSessionEvidence(snapshot,state);
   const saved=await writeNew(path.join(resolved,'snapshot.json'),snapshot);
   state.snapshot={...saved,status:snapshot.status,cardCount:snapshot.rows.length};
  }else if(phase==='partial'){state.phase='failed';if(state.lastError)state.lastError.message=LABELS.failed;}
  state.retry=retryPolicy(state,reason);await commit(resolved,state,event);active.delete(resolved);return {...status(),busy:false};
 }
 async function persistFailure(error){
  state.phase='manual_review';state.operation=null;state.lastError={code:error.code||'PERSISTENCE_OR_SESSION_ERROR',message:'本地持久化或会话一致性失败；保留目录，不能继续采集或宣称封存成功'};
  state.importReady=false;state.checkpointIntegrityStatus='manual_review_required';
  state.retry={automatic:false,allowed:false,remaining:0,reason:'durability_review_required'};
  // Never overwrite a partially published snapshot or another session revision.
  if(error.code!=='SESSION_CHANGED')await commit(resolved,state,'persistence_failure_manual_review').catch(()=>{});
  active.delete(resolved);
 }
 function limitReason(){
  if(performance.now()-started>=limits.maxDurationMs)return 'operation_time_limit';
  if(state.operationCount>=limits.maxSteps)return 'operation_count_limit';
  if(state.progress.batches>=limits.maxBatches)return 'batch_count_limit';
  if(state.progress.cards>=limits.maxCards)return 'card_count_limit';
  return null;
 }
 async function operate(kind){
  if(busy)throw fail('SESSION_BUSY','Another session operation is still running');
  if(TERMINAL.has(state.phase))return status();
  if(kind==='capture'&&state.progress.verifiedBatches>0)throw fail('INITIAL_CAPTURE_ALREADY_DONE','Use step() after the initial capture; repeated capture clicks do not read again');
  if(kind==='step'&&!state.progress.verifiedBatches)throw fail('CAPTURE_FIRST','Call capture() on the fresh top-of-list page first');
  busy=true;
  try{
   await ownership();const limited=limitReason();if(limited)return await finish('partial',limited);
   state.phase='running';state.operationCount++;state.operation={kind,index:state.operationCount,startedAt:now()};
   await commit(resolved,state,'operation_started');
   try{await driver[kind]();}
   catch(error){const problem=classify(error,driver.status().stop);return await finish(problem.phase,problem.reason,'operation_failed_prefix_sealed');}
   await refresh();state.operation=null;
   if(state.progress.stop){const problem=classify(null,state.progress.stop);return await finish(problem.phase,problem.reason,'driver_stopped_prefix_sealed');}
   if(state.progress.ended)return await finish('complete',null,'complete_boundary_and_coverage_sealed');
   const reached=limitReason();if(reached)return await finish('partial',reached,'bounded_limit_prefix_sealed');
   await commit(resolved,state,'operation_checkpointed');return {...status(),busy:false};
  }catch(error){
   await persistFailure(error);throw error;
  }finally{busy=false;}
 }
 async function seal(reason=null){
  if(busy)throw fail('SESSION_BUSY','Wait for the active operation before sealing');
  if(TERMINAL.has(state.phase)){await verifySnapshot(resolved,state);return status();}
  if(reason!==null&&(typeof reason!=='string'||!reason.trim()||reason.length>500))throw fail('INVALID_REASON','Provide a short explicit partial-seal reason');
  busy=true;try{await ownership();const phase=driver.status().ended&&!reason?'complete':'partial';return await finish(phase,phase==='complete'?null:reason||'operator_sealed_partial');}
  catch(error){await persistFailure(error);throw error;}finally{busy=false;}
 }
 return {capture:()=>operate('capture'),step:()=>operate('step'),seal,status};
}

/** Read-only status for the host; this does not resume a browser session. */
export async function readCaptureSession(directory){const loaded=await loadSession(directory);await verifySnapshot(loaded.directory,loaded.value);return copy({...loaded.value,directory:loaded.directory});}

/** Explicit offline recovery of a dead owner only. Replays immutable DOM evidence
 * through the same adjacent driver, never executes DOM code or touches a tab.
 * An uncommitted final boundary still recovers as PARTIAL, never invented complete.
 */
export async function recoverCaptureSession(directory,{interruptionConfirmed=false,expectedSessionId=null,cuaResetEvidence=null}={}){
 const loaded=await loadSession(directory),resolved=loaded.directory,state=copy(loaded.value);
 if(interruptionConfirmed!==true||expectedSessionId!==state.sessionId)throw fail('RECOVERY_CONFIRMATION_REQUIRED','Confirm the interrupted session ID and host interruption explicitly');
 if(TERMINAL.has(state.phase)){
  await verifySnapshot(resolved,state);await scanBatches(resolved,state.batchManifest);return copy({...state,directory:resolved,recovered:false});
 }
 let resetProof=null;
 if(state.owner.kind==='cua_host'){
  // A timeout alone does not establish host death. Only an explicit reset
  // reported by the authorized CUA coordinator may be attested, bound to
  // these exact state bytes. This is offline PARTIAL recovery, never resume.
  if(cuaResetEvidence===null)throw fail('OWNER_RUNTIME_UNVERIFIED','CUA owner liveness cannot be verified without an explicit tool-reported kernel reset bound to this session state. Preserve the session; never steal its lease.');
  if(active.has(resolved))throw fail('OWNER_STILL_ACTIVE','This CUA host still owns the session; normal seal is required.');
  const keys=['tool','event','message','observedAt','sessionId','stateSha256'];
  const at=Date.parse(cuaResetEvidence.observedAt),last=Date.parse(state.updatedAt);
  if(Object.keys(cuaResetEvidence).some(key=>!keys.includes(key))||keys.some(key=>typeof cuaResetEvidence[key]!=='string')||
     cuaResetEvidence.tool!=='mcp__cua_repl.js'||cuaResetEvidence.event!=='kernel_reset'||
     cuaResetEvidence.message!=='js execution timed out; kernel reset, rerun your request'||
     cuaResetEvidence.sessionId!==state.sessionId||cuaResetEvidence.stateSha256!==loaded.sha256||
     !Number.isFinite(at)||at<last||at>Date.now())throw fail('INVALID_CUA_RESET_EVIDENCE','An explicit CUA kernel-reset report must match the exact interrupted session, state SHA and observation time.');
  resetProof=copy(cuaResetEvidence);
 }else{
  if(cuaResetEvidence!==null)throw fail('INVALID_CUA_RESET_EVIDENCE','CUA reset evidence cannot override Node process liveness.');
  if(!nodeProcess||!Number.isSafeInteger(state.owner.pid)||state.owner.pid<=0)throw fail('OWNER_RUNTIME_UNVERIFIED','Owner liveness cannot be verified.');
  if(active.has(resolved)||pidAlive(state.owner.pid))throw fail('OWNER_STILL_ACTIVE','Owner process is still alive; stop/restart that host or request manual review. Never steal an active session.');
 }
 const lockFile=path.join(resolved,'recovery.lock');const lock=await fs.open(lockFile,'wx').catch(error=>{throw fail('RECOVERY_LOCKED',`Recovery is already reserved (${error.code})`);});
 const scratch=path.join(resolved,`.replay-${crypto.randomUUID()}`);let recoveredSnapshot=null,anomaly=null;
 try{
  await lock.writeFile(JSON.stringify({sessionId:state.sessionId,pid:nodeProcess?.pid??null,at:now(),cuaResetEvidence:resetProof}));await lock.sync();
  const latest=await loadSession(resolved);if(latest.sha256!==loaded.sha256)throw fail('SESSION_CHANGED','Session changed before recovery acquired its lock');
  const scanned=await scanBatches(resolved,state.batchManifest);state.batchManifest=publicManifest(scanned.entries);anomaly=scanned.anomaly;
  let current=null;
  const replayTab={id:state.tabId,playwright:{evaluate:async()=>copy(current.page)},scroll:async(_point,direction,amount)=>{
   if(direction!=='down'||!current.page.scrollEvidence||Math.abs(amount-current.page.scrollEvidence.requestedViewportUnits)>1e-9)throw fail('REPLAY_SCROLL_CONFLICT','Recorded scroll does not match driver reconstruction');
  }};
  const replay=await createRenderedCapture(replayTab,scratch,state.options);
  let accepted=0;
  for(const entry of scanned.entries){
   if(entry.parseError||entry.file!==`batch_${String(accepted+1).padStart(4,'0')}.json`){anomaly ||= 'incomplete_or_non_contiguous_durable_tail';break;}
   current=entry;
   try{
    const outcome=await (entry.page.scrollEvidence?replay.step():replay.capture());
    const regenerated=(await readJson(path.join(scratch,entry.file))).value;
    const fields=['batch','shopIdentityVerified','stop','complete','seen','pending'];
    if(fields.some(key=>regenerated[key]!==entry.page[key])){anomaly='replay_validation_conflict';break;}
    if(outcome.stop||!entry.verified){
     anomaly=entry.page.stop||'unverified_checkpoint';
     // The legal rejected attempt remains in batchCount and identity checks;
     // rows still come only from the preceding accepted prefix.
     if(replay.status().cards)recoveredSnapshot=await replay.snapshot('interrupted_session');
     break;
    }
    accepted++;
    if(replay.status().cards)recoveredSnapshot=await replay.snapshot('interrupted_session');
    if(replay.status().ended&&accepted<scanned.entries.length){anomaly='unexpected_batches_after_complete';break;}
   }catch{anomaly='replay_validation_failed';break;}
  }
  // Hash-check again before publishing; a concurrent writer cannot be ignored.
  const second=await scanBatches(resolved,state.batchManifest);
  if(JSON.stringify(publicManifest(second.entries))!==JSON.stringify(state.batchManifest))throw fail('CHECKPOINT_CHANGED','Checkpoint set changed during offline recovery');
  state.batchManifest=state.batchManifest.map((item,index)=>({...item,verified:index<accepted}));
  state.progress={...replay.status(),batches:scanned.entries.length,verifiedBatches:accepted,cards:recoveredSnapshot?.rows.length||0,
   pending:recoveredSnapshot?.collectionEvidence.pendingSlotCount??0,ended:false,stop:anomaly||'interrupted_session'};delete state.progress.busy;
  state.operation=null;state.phase=anomaly?'manual_review':recoveredSnapshot?'partial':'failed';
  state.lastError={code:anomaly||'interrupted_session',message:'仅从原始持久化批次重建已验证前缀；中断恢复保守封存部分，不续拼下一次窗口'};
  state.recoveredAt=now();state.recoveredByPid=nodeProcess?.pid??null;
  if(resetProof)state.cuaResetEvidence=resetProof;
  state.importReady=Boolean(recoveredSnapshot)&&!anomaly;
  state.checkpointIntegrityStatus=anomaly?'manual_review_required':'verified';
  if(recoveredSnapshot){
   recoveredSnapshot.collectionEvidence={...recoveredSnapshot.collectionEvidence,batchCount:scanned.entries.length,
    verifiedBatchCount:accepted,stopReason:anomaly||'interrupted_session',
    unverifiedCheckpointFiles:state.batchManifest.filter(entry=>!entry.verified).map(entry=>entry.file)};
   recoveredSnapshot=addSessionEvidence(recoveredSnapshot,state,true);
   const filename=await exists(path.join(resolved,'snapshot.json'))?'snapshot.recovered.json':'snapshot.json';
   if(filename!=='snapshot.json')state.uncommittedSnapshot={file:'snapshot.json',sha256:sha(await fs.readFile(path.join(resolved,'snapshot.json'))),notUsed:true};
   const saved=await writeNew(path.join(resolved,filename),recoveredSnapshot);
   state.snapshot={...saved,status:'partial',cardCount:recoveredSnapshot.rows.length};
  }
  state.retry=retryPolicy(state,anomaly||'interrupted_session');await commit(resolved,state,'offline_dead_owner_recovery');
  return copy({...state,directory:resolved,recovered:true});
 }finally{
  await lock.close();await fs.unlink(lockFile).catch(()=>{});
  if(path.dirname(scratch)===resolved&&path.basename(scratch).startsWith('.replay-'))await fs.rm(scratch,{recursive:true,force:true});
 }
}
