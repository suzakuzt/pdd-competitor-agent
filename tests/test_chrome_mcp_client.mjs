import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {ChromeMcpClient,chromeMcpLaunch,startChromeMcp} from '../scripts/chrome_mcp_client.mjs';
const fields={list_pages:[],new_page:['url'],select_page:['pageId'],navigate_page:['pageId','url'],evaluate_script:['pageId','function'],click_at:['pageId','x','y'],take_screenshot:['pageId','filePath'],close_page:['pageId'],list_network_requests:['pageId','resourceTypes','includePreservedRequests'],get_network_request:['pageId','reqid','responseFilePath']};
const schemaTools=()=>Object.entries(fields).map(([name,keys])=>({name,inputSchema:{type:'object',properties:Object.fromEntries(keys.map(k=>[k,{}])),required:keys.includes('pageId')?['pageId']:[]}}));
async function fakeRuntime(t){
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'pdd-mcp-test-'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const config=chromeMcpLaunch(root),pkg=path.join(root,'runtime/chrome-devtools-mcp/node_modules/chrome-devtools-mcp');
 for(const file of [config.command,config.args[0]]){await fs.mkdir(path.dirname(file),{recursive:true});await fs.writeFile(file,'SYNTHETIC_ONLY');}
 await fs.writeFile(path.join(pkg,'package.json'),JSON.stringify({name:'chrome-devtools-mcp',version:'1.10.1'}));return root;
}
test('ordinary start delegates to the persistent official connection transport',async t=>{
 const project=await fakeRuntime(t),connection={persistent:true,close:async()=>{}};let starts=0;
 const result=await startChromeMcp(project,{daemonStarter:async(root,options)=>{
  starts++;assert.equal(root,project);assert.deepEqual(options.launch.args,chromeMcpLaunch(project).args);
  assert.equal(options.errorFactory('connection_timeout').code,'connection_timeout');return connection;
 }});
 assert.equal(result,connection);assert.equal(starts,1);
});
test('an explicitly injected stdio child remains isolated from the persistent transport',async t=>{
 const project=await fakeRuntime(t),child=fakeChild(server);
 const client=await startChromeMcp(project,{spawnImpl:()=>child,daemonStarter:()=>assert.fail('must not start a daemon')});
 await client.callTool('list_pages');await client.close();assert.equal(client.closed,true);
});
function fakeChild(handler){
 const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.requests=[];child.killed=0;
 child.stdin=new Writable({write(chunk,encoding,callback){for(const line of chunk.toString().trim().split('\n')){const value=JSON.parse(line);child.requests.push(value);Promise.resolve().then(()=>handler(value,child)).catch(error=>child.emit('error',error));}callback();},final(callback){callback();queueMicrotask(()=>{child.exitCode=0;child.emit('exit',0);});}});
 child.kill=()=>{child.killed++;child.exitCode=0;child.emit('exit',0);};
 child.reply=(id,result)=>child.stdout.write(JSON.stringify({jsonrpc:'2.0',id,result})+'\n');return child;
}
function server(value,child){if(value.method==='initialize')child.reply(value.id,{protocolVersion:'2025-03-26',serverInfo:{name:'synthetic',version:'1'},capabilities:{tools:{}}});else if(value.method==='tools/list')child.reply(value.id,{tools:schemaTools()});else if(value.method==='tools/call')child.reply(value.id,{structuredContent:{message:'synthetic'}});}
test('launch requires ordinary Chrome autoConnect; no browser-changing flags',()=>{
 const config=chromeMcpLaunch('C:/SYNTHETIC_ONLY',{PATH:'synthetic',NODE_DEBUG:'*',NODE_OPTIONS:'--inspect',DEBUG:'*'});
 assert.match(config.args[0],/bin[\\/]chrome-devtools-mcp\.js$/);assert.ok(config.args.includes('--autoConnect'));assert.ok(config.args.includes('--experimentalStructuredContent'));assert.ok(config.args.includes('--page-id-routing'));
 assert.ok(config.args.includes('--redactNetworkHeaders'));assert.ok(!config.args.includes('--no-category-network'));
 assert.ok(!config.args.some(arg=>/^--(?:user.data.dir|browser.url|headless|viewport|chrome.arg|isolated|remote.debugging)/i.test(arg)));
 assert.equal(config.options.env.CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS,'1');assert.equal(config.options.env.CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS,'1');assert.equal(config.options.env.NODE_DEBUG,undefined);assert.equal(config.options.env.NODE_OPTIONS,undefined);assert.equal(config.options.windowsHide,true);
});
test('stdio handshake validates tools and then sends scoped calls',async()=>{
 const child=fakeChild(server),client=new ChromeMcpClient(child,{timeoutMs:1000});await client.initialize();
 assert.equal(child.requests[0].method,'initialize');assert.equal(child.requests[1].method,'notifications/initialized');
 await client.callTool('evaluate_script',{pageId:7,function:'() => 1'});assert.equal(child.requests.at(-1).params.arguments.pageId,7);await client.close();assert.equal(child.exitCode,0);
});
test('a missing required pageId schema is rejected before browser use',async()=>{
 const child=fakeChild((value,child)=>{if(value.method==='tools/list'){const tools=schemaTools();tools.find(t=>t.name==='evaluate_script').inputSchema.required=[];child.reply(value.id,{tools});}else server(value,child);});
 const client=new ChromeMcpClient(child,{timeoutMs:1000});await assert.rejects(client.initialize(),e=>e.code==='unsupported_server');assert.ok(!child.requests.some(r=>r.method==='tools/call'));await client.close();
});
test('timeout poisons connection; another call cannot reconnect silently',async()=>{
 const child=fakeChild(()=>{}),client=new ChromeMcpClient(child,{timeoutMs:10});await assert.rejects(client.request('synthetic'),e=>e.code==='connection_timeout');const count=child.requests.length;await assert.rejects(client.request('second'),e=>e.code==='connection_timeout');assert.equal(child.requests.length,count);await client.close();
});
test('a disconnected process rejects an outstanding read',async()=>{
 const child=fakeChild(()=>{}),client=new ChromeMcpClient(child,{timeoutMs:1000});const pending=client.request('read');child.emit('exit',1);await assert.rejects(pending,e=>e.code==='connection_lost');await client.close();
});
test('raw server errors cannot leak through exception messages',async()=>{
 const child=fakeChild((value,child)=>child.stdout.write(JSON.stringify({jsonrpc:'2.0',id:value.id,error:{code:-1,message:'SYNTHETIC_SECRET_COOKIE'}})+'\n'));
 const client=new ChromeMcpClient(child);await assert.rejects(client.request('read'),e=>e.code==='protocol_error'&&!e.message.includes('SYNTHETIC_SECRET'));await client.close();
});
test('oversized or malformed stdout is closed instead of being page data',async()=>{
 for(const raw of ['not JSON\n','x'.repeat(129)]){const child=fakeChild(()=>{}),client=new ChromeMcpClient(child,{maxBytes:128});const pending=client.request('read');child.stdout.write(raw);await assert.rejects(pending,e=>e.code==='invalid_response');await client.close();}
});
test('official reconnect notice prevents use of reassigned page IDs',async()=>{
 const child=fakeChild((value,child)=>value.method==='tools/call'?child.reply(value.id,{structuredContent:{reconnected:true}}):server(value,child));const client=new ChromeMcpClient(child);await client.initialize();await assert.rejects(client.callTool('list_pages'),e=>e.code==='connection_lost');const count=child.requests.length;await assert.rejects(client.callTool('list_pages'),e=>e.code==='connection_lost');assert.equal(child.requests.length,count);await client.close();
});
