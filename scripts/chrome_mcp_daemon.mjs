// Reuse the pinned official daemon; one owned lease covers an entire collection.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash,randomUUID} from 'node:crypto';
import {pathToFileURL} from 'node:url';

const TOOLS=new Set(['list_pages','select_page','new_page','navigate_page','evaluate_script','click_at','take_snapshot','fill','press_key','take_screenshot','close_page','list_network_requests','get_network_request']);
const VERSION='1.10.1';
export function daemonSessionId(project,user=os.userInfo()){
 const root=path.resolve(project);return createHash('sha256').update(JSON.stringify([
  process.platform==='win32'?root.toLowerCase():root,user.username,user.uid])).digest('hex').slice(0,32);
}
async function withEnvironment(environment,action){
 const previous={...process.env};
 try{
  for(const key of Object.keys(process.env))if(!Object.hasOwn(environment,key))delete process.env[key];
  Object.assign(process.env,environment);return await action();
 }finally{
  for(const key of Object.keys(process.env))if(!Object.hasOwn(previous,key))delete process.env[key];
  Object.assign(process.env,previous);
 }
}
async function loadOfficial(project){
 const root=path.join(project,'runtime/chrome-devtools-mcp/node_modules/chrome-devtools-mcp');
 const pkg=JSON.parse(await fs.readFile(path.join(root,'package.json'),'utf8'));
 if(pkg.name!=='chrome-devtools-mcp'||pkg.version!==VERSION)throw new Error('Unsupported pinned daemon');
 const client=await import(pathToFileURL(path.join(root,'build/src/daemon/client.js')).href);
 const utils=await import(pathToFileURL(path.join(root,'build/src/daemon/utils.js')).href);
 return {startDaemon:client.startDaemon,sendCommand:client.sendCommand,stopDaemon:client.stopDaemon,isDaemonRunning:utils.isDaemonRunning};
}
function defaultError(code){const error=new Error('Chrome connection could not complete the requested operation.');error.name='ChromeMcpError';error.code=code;return error;}
async function bounded(action,timeoutMs){
 let timer;try{return await Promise.race([Promise.resolve().then(action),new Promise((_,reject)=>{
  timer=setTimeout(()=>reject(Object.assign(new Error('Operation timed out'),{code:'ETIMEDOUT'})),timeoutMs);
 })]);}finally{clearTimeout(timer);}
}

export async function startChromeDaemon(project,{launch,errorFactory=defaultError,official:injected,
 stopTimeoutMs=12000,statusTimeoutMs=5000,maxBytes=16*1024*1024}={}){
 const root=await fs.realpath(path.resolve(project)),sessionId=daemonSessionId(root);
 if(!launch||!Array.isArray(launch.args)||!launch.options?.env)throw errorFactory('runtime_unavailable');
 const args=launch.args.slice(1),leaseDirectory=path.join(root,'state/local_collection/chrome_daemon');
 const leasePath=path.join(leaseDirectory,`${sessionId}.lease.json`),nonce=randomUUID();
 await fs.mkdir(leaseDirectory,{recursive:true});
 let ownsLease=false,matched=false,started=false,official,failed=null,closed=false,poisoning=null,closing=null;
 const inFlight=new Set();
 const lease={session_id:sessionId,pid:process.pid,nonce,state:'active',created_at:new Date().toISOString()};
 const fail=(code,detail)=>{const error=errorFactory(code);if(detail)error.session_detail=detail;return error;};
 try{
  const handle=await fs.open(leasePath,'wx',0o600);ownsLease=true;
  try{await handle.writeFile(JSON.stringify(lease));await handle.sync();}finally{await handle.close();}
 }catch(error){
  // Unknown or dead owners are not automatically deleted: an old RPC may remain.
  if(error.code==='EEXIST')throw fail('connection_busy','lease_held_or_unverified');
  throw fail('connection_required','lease_unavailable');
 }
 async function verifyLease(){
  const value=JSON.parse(await fs.readFile(leasePath,'utf8'));
  if(value.nonce!==nonce||value.pid!==process.pid||value.session_id!==sessionId)throw fail('connection_required','lease_owner_changed');
 }
 async function release(){
  if(!ownsLease)return;await verifyLease();await fs.unlink(leasePath);ownsLease=false;
 }
 async function fault(detail){
  if(!ownsLease)return;await verifyLease();
  await fs.writeFile(leasePath,JSON.stringify({...lease,state:'fault',detail,failed_at:new Date().toISOString()}));
 }
 function parseResponse(response){
  if(response?.success!==true||typeof response.result!=='string')throw fail(
   /timed?\s*out|timeout/i.test(String(response?.error||''))?'connection_timeout':'connection_lost');
  if(Buffer.byteLength(response.result)>maxBytes)throw fail('invalid_response');
  let value;try{value=JSON.parse(response.result);}catch{throw fail('invalid_response');}
  if(!value||typeof value!=='object'||Array.isArray(value))throw fail('invalid_response');return value;
 }
 function poison(error){
  failed ||= error;
  if(!poisoning)poisoning=(async()=>{
   await fault('connection_failed');
   try{
    // Never stop a daemon whose version and launch arguments were not matched.
    if(!matched)throw fail('connection_required','daemon_owner_unverified');
    await bounded(()=>official.stopDaemon(sessionId),stopTimeoutMs);
    if(await official.isDaemonRunning(sessionId))throw fail('connection_required','daemon_stop_unconfirmed');
    await release();
   }catch{
    // Retain the lease if stop timed out: late daemon I/O must not meet a new job.
    await fault('daemon_stop_unconfirmed');
   }
  })();
  return poisoning;
 }
 try{
  official=injected||await withEnvironment(launch.options.env,()=>loadOfficial(root));
  if(!await official.isDaemonRunning(sessionId)){
   started=true;await withEnvironment(launch.options.env,()=>official.startDaemon(args,sessionId));
  }
  const status=parseResponse(await official.sendCommand({method:'status'},sessionId,statusTimeoutMs));
  if(status.version!==VERSION||JSON.stringify(status.args)!==JSON.stringify(args))throw fail('unsupported_server','daemon_configuration_mismatch');
  matched=true;
 }catch(error){
  failed=error.code?error:fail('connection_required','daemon_start_unconfirmed');
  if(started)await fault('daemon_start_unconfirmed');else await release();
  throw failed;
 }
 return {persistent:true,sessionId,
  async callTool(name,toolArgs={},timeoutMs=45000){
   if(failed)throw failed;if(closed)throw fail('connection_lost');
   if(!TOOLS.has(name))throw fail('unsupported_tool');
   const operation=(async()=>{
    let result;
    try{
     result=parseResponse(await official.sendCommand({method:'invoke_tool',tool:name,args:toolArgs},sessionId,timeoutMs));
     if(result.structuredContent?.reconnected)throw fail('connection_lost');
    }catch(error){
     const mapped=error.name==='ChromeMcpError'||['connection_timeout','connection_lost','invalid_response'].includes(error.code)
      ?error:fail(/timed?\s*out|timeout/i.test(error.message||'')?'connection_timeout':'connection_lost');
     await poison(mapped);throw failed;
    }
    if(result.isError||result.structuredContent?.errorMessage)throw fail('browser_tool_failed');
    return result;
   })();
   inFlight.add(operation);try{return await operation;}finally{inFlight.delete(operation);}
  },
  async close(){
   if(closing)return closing;closed=true;
   closing=(async()=>{await Promise.allSettled([...inFlight]);if(poisoning)await poisoning;else await release();})();
   return closing;
  }
 };
}
