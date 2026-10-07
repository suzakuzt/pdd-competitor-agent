// Small bounded stdio client for pinned official Chrome DevTools MCP.
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {StringDecoder} from 'node:string_decoder';
export class ChromeMcpError extends Error {
 constructor(code='connection_required'){super('Chrome connection could not complete the requested operation.');this.name='ChromeMcpError';this.code=code;}
}
export function chromeMcpLaunch(project,environment=process.env){
 const root=path.resolve(project),env={...environment,CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS:'1',CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS:'1'};
 for(const key of ['NODE_DEBUG','NODE_OPTIONS','DEBUG'])delete env[key];
 return {command:path.join(root,'runtime/node/bin/node.exe'),args:[path.join(root,'runtime/chrome-devtools-mcp/node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js'),'--autoConnect','--channel=stable','--no-usage-statistics','--no-performance-crux','--experimentalVision','--experimentalStructuredContent','--page-id-routing','--no-category-emulation','--no-category-performance','--no-category-memory','--redactNetworkHeaders','--no-source-maps',`--workspace=${root}`],options:{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe'],env}};
}
export class ChromeMcpClient {
 constructor(child,{timeoutMs=45000,maxBytes=16*1024*1024}={}){
  this.child=child;this.timeoutMs=timeoutMs;this.maxBytes=maxBytes;this.pending=new Map();this.sequence=0;this.buffer='';this.decoder=new StringDecoder('utf8');this.failed=null;this.closed=false;this.tools=new Map();
  child.stdout.on('data',chunk=>this.receive(chunk));child.stderr.on('data',()=>{});child.on('error',()=>this.fail('connection_required'));child.on('exit',()=>this.fail('connection_lost'));child.stdin.on('error',()=>this.fail('connection_lost'));child.stdout.on('error',()=>this.fail('connection_lost'));
 }
 fail(code){this.failed ||= new ChromeMcpError(code);for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(this.failed);}this.pending.clear();this.buffer='';return this.failed;}
 receive(chunk){
  if(this.failed||this.closed)return;
  this.buffer+=this.decoder.write(Buffer.isBuffer(chunk)?chunk:Buffer.from(chunk));
  if(Buffer.byteLength(this.buffer)>this.maxBytes){this.fail('invalid_response');return;}
  let index;
  while((index=this.buffer.indexOf('\n'))>=0){
   const line=this.buffer.slice(0,index).replace(/\r$/,'');this.buffer=this.buffer.slice(index+1);if(!line.trim())continue;
   let value;try{value=JSON.parse(line);}catch{this.fail('invalid_response');return;}
   if(!value||value.jsonrpc!=='2.0'||Array.isArray(value)){this.fail('invalid_response');return;}
   if(typeof value.method==='string'){
    if(value.id!==undefined)this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id:value.id,...(value.method==='ping'?{result:{}}:{error:{code:-32601,message:'Method not supported'}})})+'\n');continue;
   }
   const pending=this.pending.get(value.id);if(!pending)continue;
   this.pending.delete(value.id);clearTimeout(pending.timer);
   if(value.error)pending.reject(new ChromeMcpError('protocol_error'));
   else if(!Object.hasOwn(value,'result'))pending.reject(this.fail('invalid_response'));
   else pending.resolve(value.result);
  }
 }
 async request(method,params={},timeoutMs=this.timeoutMs){
  if(this.failed)throw this.failed;if(this.closed)throw new ChromeMcpError('connection_lost');
  const id=++this.sequence;
  return new Promise((resolve,reject)=>{const timer=setTimeout(()=>this.fail('connection_timeout'),timeoutMs);this.pending.set(id,{resolve,reject,timer});this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n',error=>{if(error)this.fail('connection_lost');});});
 }
 async initialize(){
  const result=await this.request('initialize',{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'pdd-local-collector',version:'1.0.0'}});
  if(!result?.serverInfo||!result?.capabilities?.tools||!['2024-11-05','2025-03-26','2025-06-18'].includes(result.protocolVersion))throw this.fail('unsupported_server');
  this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',method:'notifications/initialized'})+'\n');
  const listed=await this.request('tools/list');if(!Array.isArray(listed?.tools)||listed.nextCursor)throw this.fail('unsupported_server');
  this.tools=new Map(listed.tools.map(tool=>[tool.name,tool]));
  for(const [name,fields] of Object.entries({list_pages:[],new_page:['url'],select_page:['pageId'],navigate_page:['pageId','url'],evaluate_script:['pageId','function'],click_at:['pageId','x','y'],take_screenshot:['pageId','filePath'],close_page:['pageId'],list_network_requests:['pageId','resourceTypes','includePreservedRequests'],get_network_request:['pageId','reqid','responseFilePath']})){
   const schema=this.tools.get(name)?.inputSchema;
   if(!schema||fields.some(f=>!Object.hasOwn(schema.properties||{},f)))throw this.fail('unsupported_server');
   if(name!=='new_page'&&fields.includes('pageId')&&!schema.required?.includes('pageId'))throw this.fail('unsupported_server');
  }
  return this;
 }
 async callTool(name,args={},timeoutMs=this.timeoutMs){
  if(!this.tools.has(name))throw new ChromeMcpError('unsupported_tool');
  const result=await this.request('tools/call',{name,arguments:args},timeoutMs);
  if(!result||result.isError)throw new ChromeMcpError('browser_tool_failed');
  if(result.structuredContent?.reconnected)throw this.fail('connection_lost');
  if(result.structuredContent?.errorMessage)throw new ChromeMcpError('browser_tool_failed');
  return result;
 }
 async close(){
  if(this.closed)return;this.closed=true;this.fail('connection_lost');this.child.stdin.end();
  if(this.child.exitCode!==null&&this.child.exitCode!==undefined)return;
  await new Promise(resolve=>{const timer=setTimeout(()=>{this.child.kill();resolve();},1500);this.child.once('exit',()=>{clearTimeout(timer);resolve();});});
 }
}
export async function startChromeMcp(project,options={}){
 const {spawnImpl=spawn,timeoutMs=45000}=options;
 const config=chromeMcpLaunch(project),packageRoot=path.dirname(path.dirname(path.dirname(path.dirname(config.args[0]))));
 try{const manifest=JSON.parse(await fs.readFile(path.join(packageRoot,'package.json'),'utf8'));if(manifest.name!=='chrome-devtools-mcp'||manifest.version!=='1.10.1')throw new Error();await fs.access(config.command);await fs.access(config.args[0]);}catch{throw new ChromeMcpError('runtime_unavailable');}
 // The official project-scoped daemon retains the user-approved connection.
 // Tests may explicitly inject a child; ordinary collection never starts a new
 // stdio server per product/job and never automates Chrome's permission prompt.
 if(options.transport!=='stdio'&&!Object.hasOwn(options,'spawnImpl')){
  const starter=options.daemonStarter||(await import('./chrome_mcp_daemon.mjs')).startChromeDaemon;
  return starter(project,{launch:config,errorFactory:code=>new ChromeMcpError(code)});
 }
 const client=new ChromeMcpClient(spawnImpl(config.command,config.args,config.options),{timeoutMs});
 try{return await client.initialize();}catch(error){await client.close();throw error;}
}
