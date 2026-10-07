// Advance an existing authorized capture session without per-card decisions.
// No browser creation, navigation, network, model calls, recovery or publication.
import {performance} from 'node:perf_hooks';

const PHASES=new Set(['ready','running','complete','partial','needs_login','manual_review','failed']);
const TERMINAL=new Set(['complete','partial','needs_login','manual_review','failed']);
const wrapped=new WeakSet();

function limits(options,defaults){
 if(!options||typeof options!=='object'||Array.isArray(options)||
    Object.keys(options).some(key=>!['maxSteps','budgetMs'].includes(key)))throw new Error('Only maxSteps and budgetMs are supported');
 const value={...defaults,...options};
 if(!Number.isSafeInteger(value.maxSteps)||value.maxSteps<1||value.maxSteps>3||
    !Number.isSafeInteger(value.budgetMs)||value.budgetMs<1||value.budgetMs>60000)
  throw new Error('Use 1 to 3 operations and a positive budget up to 60000 ms');
 return value;
}

/** The host has already verified the shop, selected 上新, and positioned at top.
 * Pass the existing capture_session object and its bounded adapter.ioStatus.
 * budgetMs only prevents another operation; it NEVER aborts pending I/O.
 */
export function createHostCaptureRunner(session,{ioStatus,maxSteps=1,budgetMs=20000}={}){
 if(!session||typeof session.status!=='function'||typeof session.capture!=='function'||
    typeof session.step!=='function'||typeof ioStatus!=='function'||wrapped.has(session))
  throw new Error('One existing unwrapped capture session and its bounded ioStatus are required');
 const defaults=limits({maxSteps,budgetMs},{maxSteps:1,budgetMs:20000});
 let busy=false,stopped=false,last=null;
 function inspect(){
  const state=session.status(),io=ioStatus();
  if(!state||!PHASES.has(state.phase)||!state.progress||
     !['verifiedBatches','cards','pending'].every(key=>Number.isSafeInteger(state.progress[key])&&state.progress[key]>=0)||
     !io||typeof io.poisoned!=='boolean'||!Number.isSafeInteger(io.pending)||io.pending<0)
   throw new Error('Capture or I/O status is not recognized');
  const value={state:{phase:state.phase,busy:Boolean(state.busy),operation:Boolean(state.operation),
   importReady:state.importReady===true,progress:{verifiedBatches:state.progress.verifiedBatches,
    cards:state.progress.cards,pending:state.progress.pending,ended:state.progress.ended===true}},
   io:{poisoned:io.poisoned,pending:io.pending}};
  last=value;return value;
 }
 function blocked(value){
  if(stopped)return 'runner_stopped';
  if(TERMINAL.has(value.state.phase))return 'session_terminal';
  if(value.state.busy||value.state.operation)return 'session_busy';
  if(value.io.poisoned)return 'io_poisoned';
  if(value.io.pending)return 'io_pending';
  return null;
 }
 function compact(value,reason,operations=0,elapsedMs=0){
  const state=value?.state,io=value?.io;
  // Deliberate field allowlist: no DOM rows, URLs, file paths, credentials,
  // exception messages or receipt bodies can reach normal progress output.
  return {phase:state?.phase??'manual_review',reason,
   operations,elapsedMs:Math.round(Math.max(0,elapsedMs)),
   cards:state?.progress.cards??0,checkpointedBatches:state?.progress.verifiedBatches??0,
   pendingCards:state?.progress.pending??0,endBoundaryObserved:state?.progress.ended===true,
   importReady:state?.importReady===true,ioPoisoned:io?.poisoned??true,pendingIo:io?.pending??0,
   canAdvance:!busy&&!stopped&&value!==null&&!blocked(value),
   attentionRequired:stopped||Boolean(io?.poisoned)||Boolean(state&&TERMINAL.has(state.phase)&&state.phase!=='complete')};
 }
 inspect();wrapped.add(session);
 return {
  status(){
   try{const value=inspect();return compact(value,busy?'runner_busy':blocked(value)||'ready');}
   catch{stopped=true;return compact(last,'status_unavailable');}
  },
  async advance(options={}){
   const selected=limits(options,defaults),started=performance.now();let operations=0;
   if(busy)return compact(last,'runner_busy');
   busy=true;
   let reason='step_limit';
   try{
    while(operations<selected.maxSteps){
     const value=inspect();reason=blocked(value);
     if(reason){if(reason==='io_poisoned')stopped=true;break;}
     if(performance.now()-started>=selected.budgetMs){reason='budget_exhausted';break;}
     // The session owns checkpoints, error-prefix sealing and completion gates.
     // Never race this promise, seal behind it, or automatically retry it.
     operations++;
     await (value.state.progress.verifiedBatches===0?session.capture():session.step());
     const after=inspect();reason=blocked(after);
     if(reason){if(reason==='io_poisoned')stopped=true;break;}
     reason='step_limit';
    }
   }catch{
    stopped=true;reason='operation_failed';
    try{inspect();}catch{/* Keep only the last known compact progress. */}
   }finally{busy=false;}
   return compact(last,reason,operations,performance.now()-started);
  },
 };
}
