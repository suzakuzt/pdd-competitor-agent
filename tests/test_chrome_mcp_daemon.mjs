import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {startChromeDaemon,daemonSessionId} from '../scripts/chrome_mcp_daemon.mjs';

async function fixture(t){
 const project=await fs.mkdtemp(path.join(os.tmpdir(),'pdd-daemon-test-'));t.after(()=>fs.rm(project,{recursive:true,force:true}));
 const args=['--autoConnect','--channel=stable','--no-usage-statistics','--experimentalStructuredContent',`--workspace=${project}`];
 const env={...process.env,CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS:'1',CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS:'1'};
 for(const key of ['NODE_DEBUG','NODE_OPTIONS','DEBUG'])delete env[key];
 const launch={command:process.execPath,args:['SYNTHETIC_OFFICIAL_MCP.js',...args],options:{env,cwd:project}};
 const calls=[];let running=false,status={version:'1.10.1',args};
 const official={
  isDaemonRunning:()=>running,
  startDaemon:async(startArgs,session)=>{calls.push(['start',startArgs,session]);running=true;},
  stopDaemon:async session=>{calls.push(['stop',session]);running=false;},
  sendCommand:async(command,session,timeout)=>{calls.push([command.method,session,timeout]);return {success:true,result:JSON.stringify(command.method==='status'?status:{structuredContent:{pages:[]}})};},
 };
 const lease=path.join(project,'state/local_collection/chrome_daemon',`${daemonSessionId(project)}.lease.json`);
 return {project,launch,official,calls,lease,setRunning:value=>running=value,setStatus:value=>status=value};
}

test('two successive tasks reuse one official daemon and normal close only releases lease',async t=>{
 const f=await fixture(t),a=await startChromeDaemon(f.project,f);
 assert.equal(a.persistent,true);assert.match(a.sessionId,/^[a-f0-9]{32}$/);
 await a.callTool('list_pages',{},120000);await a.close();
 const b=await startChromeDaemon(f.project,f);await b.callTool('evaluate_script',{pageId:3,function:'()=>1'});await b.close();
 assert.equal(f.calls.filter(call=>call[0]==='start').length,1);
 assert.equal(f.calls.filter(call=>call[0]==='stop').length,0);
 assert.ok(f.calls.some(call=>call[0]==='invoke_tool'&&call[2]===120000));
 await assert.rejects(fs.access(f.lease));
});
test('session scope includes project and user',()=>{
 assert.notEqual(daemonSessionId('A',{username:'a',uid:1}),daemonSessionId('B',{username:'a',uid:1}));
 assert.notEqual(daemonSessionId('A',{username:'a',uid:1}),daemonSessionId('A',{username:'b',uid:2}));
});
test('active or unknown existing lease blocks without checking or starting daemon',async t=>{
 const f=await fixture(t);await fs.mkdir(path.dirname(f.lease),{recursive:true});
 await fs.writeFile(f.lease,JSON.stringify({pid:99999999,nonce:'SYNTHETIC_OLD'}));
 await assert.rejects(startChromeDaemon(f.project,f),e=>e.code==='connection_busy'&&e.session_detail==='lease_held_or_unverified');assert.equal(f.calls.length,0);
 assert.equal(JSON.parse(await fs.readFile(f.lease,'utf8')).nonce,'SYNTHETIC_OLD');
});
test('simultaneous task cannot steal active lease and cannot remove a changed owner',async t=>{
 const f=await fixture(t),a=await startChromeDaemon(f.project,f);
 await assert.rejects(startChromeDaemon(f.project,f),e=>e.session_detail==='lease_held_or_unverified');
 const replacement={pid:process.pid,nonce:'changed',session_id:a.sessionId};await fs.writeFile(f.lease,JSON.stringify(replacement));
 await assert.rejects(a.close(),e=>e.session_detail==='lease_owner_changed');
 assert.deepEqual(JSON.parse(await fs.readFile(f.lease,'utf8')),replacement);
});
test('reuse requires exact pinned version and launch argument order; foreign daemon is never stopped',async t=>{
 for(const change of [{version:'9.9.9'}, {args:['--autoConnect']}, {args:['--channel=stable','--autoConnect'] }]){
  const f=await fixture(t);f.setRunning(true);f.setStatus({version:'1.10.1',args:f.launch.args.slice(1),...change});
  await assert.rejects(startChromeDaemon(f.project,f),e=>e.code==='unsupported_server');
  assert.equal(f.calls.filter(call=>['start','stop'].includes(call[0])).length,0);await assert.rejects(fs.access(f.lease));
 }
});
test('official daemon spawn inherits sanitized launch environment then parent environment is restored',async t=>{
 const f=await fixture(t),before={...process.env};
 f.official.startDaemon=async()=>{
  assert.equal(process.env.CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS,'1');
  assert.equal(process.env.CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS,'1');
  for(const key of ['NODE_DEBUG','NODE_OPTIONS','DEBUG'])assert.equal(process.env[key],undefined);
  f.setRunning(true);
 };
 const a=await startChromeDaemon(f.project,f);assert.deepEqual({...process.env},before);await a.close();
});
test('failed startup preserves unverified lease and never stops unknown daemon',async t=>{
 const f=await fixture(t);f.official.startDaemon=async()=>{throw new Error('SYNTHETIC start failure');};
 await assert.rejects(startChromeDaemon(f.project,f));
 assert.equal(JSON.parse(await fs.readFile(f.lease,'utf8')).state,'fault');assert.equal(f.calls.some(call=>call[0]==='stop'),false);
});
test('transport timeout waits for own daemon stop before unlocking; next explicit task starts fresh',async t=>{
 const f=await fixture(t),send=f.official.sendCommand,a=await startChromeDaemon(f.project,f);let finishStop,entered;
 const stopping=new Promise(resolve=>entered=resolve);
 f.official.sendCommand=async command=>{if(command.method==='invoke_tool')throw new Error('Timeout waiting for daemon response');return send(command);};
 f.official.stopDaemon=async()=>{entered();await new Promise(resolve=>finishStop=resolve);f.setRunning(false);};
 const failure=assert.rejects(a.callTool('list_pages'),e=>e.code==='connection_timeout');await stopping;
 await assert.rejects(startChromeDaemon(f.project,f),e=>e.session_detail==='lease_held_or_unverified');
 finishStop();await failure;await a.close();await assert.rejects(fs.access(f.lease));
 f.official.sendCommand=send;const b=await startChromeDaemon(f.project,f);await b.close();
 assert.equal(f.calls.filter(call=>call[0]==='start').length,2);
});
test('unconfirmed or timed-out stop retains fault lease despite a later stop result',async t=>{
 for(const timeout of [false,true]){
  const f=await fixture(t),a=await startChromeDaemon(f.project,{...f,stopTimeoutMs:10});let finishStop;
  f.official.sendCommand=async()=>{throw new Error('SYNTHETIC socket closed');};
  f.official.stopDaemon=timeout?async()=>{await new Promise(resolve=>finishStop=resolve);f.setRunning(false);}:async()=>{};
  await assert.rejects(a.callTool('list_pages'),e=>e.code==='connection_lost');await a.close();
  if(finishStop){finishStop();await new Promise(resolve=>setImmediate(resolve));}
  assert.equal(JSON.parse(await fs.readFile(f.lease,'utf8')).detail,'daemon_stop_unconfirmed');
  await assert.rejects(startChromeDaemon(f.project,f),e=>e.session_detail==='lease_held_or_unverified');
 }
});
test('reconnected, invalid payload and internal SDK timeout poison instead of retry',async t=>{
 for(const reply of [{success:true,result:JSON.stringify({structuredContent:{reconnected:true}})},
  {success:true,result:'not json'}, {success:false,error:'MCP error: Request timed out'}]){
  const f=await fixture(t),a=await startChromeDaemon(f.project,f);let invoked=0;
  f.official.sendCommand=async()=>{invoked++;return reply;};
  await assert.rejects(a.callTool('list_pages'));await assert.rejects(a.callTool('list_pages'));await a.close();
  assert.equal(invoked,1);assert.equal(f.calls.filter(call=>call[0]==='stop').length,1);
 }
});
test('ordinary tool errors keep daemon; unapproved tool makes no call',async t=>{
 const f=await fixture(t),a=await startChromeDaemon(f.project,f);let invoked=0;
 f.official.sendCommand=async()=>{invoked++;return {success:true,result:JSON.stringify({isError:true})};};
 await assert.rejects(a.callTool('browser_login'),e=>e.code==='unsupported_tool');assert.equal(invoked,0);
 await assert.rejects(a.callTool('click_at'),e=>e.code==='browser_tool_failed');await a.close();
 assert.equal(f.calls.some(call=>call[0]==='stop'),false);
});

test('search snapshot, native fill and Enter are allowed while unrelated input tools stay blocked',async t=>{
 const f=await fixture(t),a=await startChromeDaemon(f.project,f),invoked=[];
 f.official.sendCommand=async(command,session,timeout)=>{invoked.push({command,session,timeout});return {success:true,result:JSON.stringify({structuredContent:{message:'Done'}})};};
 const actions=[['take_snapshot',{pageId:42,verbose:false}],['fill',{pageId:42,uid:'8_2',value:'SYNTHETIC 商品',includeSnapshot:false}],['press_key',{pageId:42,key:'Enter',includeSnapshot:false}]];
 for(const [name,args] of actions)await a.callTool(name,args);
 assert.deepEqual(invoked.map(({command})=>command),actions.map(([tool,args])=>({method:'invoke_tool',tool,args})));
 assert.ok(invoked.every(({session,timeout})=>session===a.sessionId&&timeout===45000));
 for(const tool of ['type_text','fill_form','upload_file','browser_login'])await assert.rejects(a.callTool(tool),error=>error.code==='unsupported_tool');
 assert.equal(invoked.length,3);await a.close();assert.equal(f.calls.some(call=>call[0]==='stop'),false);await assert.rejects(fs.access(f.lease));
});
test('close waits for an in-flight tool result before releasing lease',async t=>{
 const f=await fixture(t),a=await startChromeDaemon(f.project,f);let finish;
 f.official.sendCommand=async()=>new Promise(resolve=>finish=resolve);
 const operation=a.callTool('list_pages'),closing=a.close();
 await assert.rejects(startChromeDaemon(f.project,f),e=>e.session_detail==='lease_held_or_unverified');
 finish({success:true,result:JSON.stringify({structuredContent:{pages:[]}})});await operation;await closing;
 await assert.rejects(fs.access(f.lease));await assert.rejects(a.callTool('list_pages'),e=>e.code==='connection_lost');
});
