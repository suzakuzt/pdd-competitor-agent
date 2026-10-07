import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createBrowserAdapter,scriptResult} from '../scripts/local_browser.mjs';
import {ChromeMcpError} from '../scripts/chrome_mcp_client.mjs';
const url='https://mobile.yangkeduo.com/mall_page.html?mall_id=123';
const project='C:/SYNTHETIC_ONLY';
const result=value=>({structuredContent:{message:'Script ran on page and returned:\n```json\n'+JSON.stringify(value)+'\n```'}});
function fakeBrowser(pages=[{id:1,url,selected:true}]){
 const calls=[],scrolls=[];let closed=0;
 const location={origin:'https://mobile.yangkeduo.com',href:url};
 const sandbox={URL,location,innerWidth:1280,innerHeight:731,window:{innerHeight:731,scrollBy:arg=>scrolls.push(arg)}};
 const client={calls,scrolls,sandbox,get closed(){return closed;},
  async close(){closed++;},
  async callTool(name,args={},timeoutMs=45000){
   calls.push({name,args,timeoutMs});
   if(name==='list_pages'||name==='select_page')return {structuredContent:{pages}};
   if(name==='new_page'){pages=[...pages.map(p=>({...p,selected:false})),{id:9,url:args.url,selected:true}];location.href=args.url;return {structuredContent:{pages}};}
   if(name==='navigate_page'){if(args.type==='url')location.href=args.url;return {structuredContent:{message:'Successfully navigated'}};}
   if(name==='evaluate_script'){
    const values=(args.args||[]).map(uid=>{assert.ok(sandbox.uidElements?.has(uid),'snapshot UID must resolve to its actual fixture node');return sandbox.uidElements.get(uid);});
    return result(await vm.runInNewContext('('+args.function+')',sandbox)(...values));
   }
   return {structuredContent:{message:'Done'}};
  }
 };return client;
}
const open=client=>createBrowserAdapter(client,project,{entryUrl:url,sleep:async()=>{}});
test('only exact requested user tab is reused, page scoped and never closed',async()=>{
 const client=fakeBrowser([{id:1,url:'https://example.org/private',selected:true},{id:7,url,selected:false}]);const browser=await open(client);assert.equal(browser.tab.id,7);await browser.goto(url);assert.equal(client.calls.find(c=>c.name==='navigate_page').args.pageId,7);await browser.close(true);assert.ok(!client.calls.some(c=>c.name==='new_page'||c.name==='close_page'));assert.equal(client.closed,1);
});
test('without a unique authorized page creates a normal context tab and closes only that owned tab',async()=>{
 const client=fakeBrowser([{id:1,url:'https://example.org/private',selected:true}]),browser=await open(client);await browser.goto(url);assert.equal(browser.tab.id,9);const creation=client.calls.find(c=>c.name==='new_page');assert.equal(creation.args.url,url);assert.equal(creation.args.isolatedContext,undefined);await browser.close(true);assert.deepEqual(client.calls.filter(c=>c.name==='close_page').map(c=>c.args.pageId),[9]);
});
test('ambiguous duplicate entry tabs are not guessed or closed',async()=>{
 const client=fakeBrowser([{id:1,url,selected:false},{id:2,url,selected:false}]),browser=await open(client);await browser.goto(url);assert.equal(browser.tab.id,9);assert.ok(!client.calls.some(c=>c.name==='select_page'));await browser.close(false);assert.ok(!client.calls.some(c=>c.name==='close_page'));
});
test('JSON arguments are data and the returned DOM value remains JSON',async()=>{
 const client=fakeBrowser(),browser=await open(client);const text="x'); throw new Error('not code'); //";
 assert.equal(await browser.evaluate(({text})=>text,{text}),text);assert.deepEqual(await browser.evaluate(()=>({count:3})),{count:3});await browser.close();
});
test('scroll uses actual viewport and records DOM method without emulation',async()=>{
 const client=fakeBrowser(),browser=await open(client);await browser.scroll([200,300],'down',1.5);assert.equal(client.scrolls[0].top,1096.5);assert.equal(client.scrolls[0].behavior,'instant');assert.equal(browser.executionEvidence.scroll,'dom-window-scrollBy');assert.ok(!client.calls.some(c=>/emulat|network|profile|cookie/i.test(c.name)));await assert.rejects(browser.scroll([1,2],'end',1));await browser.close();
});
test('coordinate click is bounded by the current unmodified viewport',async()=>{
 const client=fakeBrowser(),browser=await open(client);await browser.click({x:300,y:400});assert.deepEqual(client.calls.find(c=>c.name==='click_at').args,{pageId:1,x:300,y:400,includeSnapshot:false});await assert.rejects(browser.click({x:300,y:900}));assert.equal(client.calls.filter(c=>c.name==='click_at').length,1);await browser.close();
});
test('foreign origin stops before evaluating requested page data',async()=>{
 const client=fakeBrowser(),browser=await open(client);client.sandbox.location.origin='https://example.org';await assert.rejects(browser.evaluate(()=>{throw new Error('must not run');}),e=>e.reason==='source_changed');const count=client.calls.length;await assert.rejects(browser.evaluate(()=>1),e=>e.status==='needs_browser');assert.equal(client.calls.length,count);await browser.close();
});
test('denied initial connection has safe actionable status, no raw MCP data',async()=>{
 const client={close:async()=>{},callTool:async()=>{throw new Error('SYNTHETIC_SECRET_COOKIE');}};await assert.rejects(open(client),e=>e.status==='needs_browser'&&e.reason==='connection_required'&&!e.message.includes('SYNTHETIC_SECRET'));
});
test('only the initial Chrome consent call gets a longer deadline',async()=>{
 const client=fakeBrowser(),browser=await open(client);await browser.goto(url);await browser.evaluate(()=>1);
 assert.equal(client.calls[0].name,'list_pages');assert.equal(client.calls[0].timeoutMs,120000);
 assert.ok(client.calls.length>3);assert.ok(client.calls.slice(1).every(call=>call.timeoutMs===45000));await browser.close();
});
test('initial consent timeout preserves safe cause and stops without retry or token diagnosis',async()=>{
 const calls=[];let closed=0;const client={close:async()=>{closed++;},callTool:async(name,args,timeoutMs)=>{calls.push({name,timeoutMs});const error=new ChromeMcpError('connection_timeout');error.message='SYNTHETIC_SECRET_COOKIE';throw error;}};
 await assert.rejects(open(client),e=>e.status==='needs_browser'&&e.reason==='connection_required'&&e.connection_detail==='connection_timeout'&&e.connection_stage==='initial'&&e.connection_diagnostics.tool==='list_pages'&&e.message.includes('本次连接')&&e.message.includes('超时')&&!/TOKEN|过期|SYNTHETIC_SECRET/.test(e.message));
 assert.deepEqual(calls,[{name:'list_pages',timeoutMs:120000}]);assert.equal(closed,1);
});
test('reconnect notice stops capture and never navigates a reassigned ID',async()=>{
 const client=fakeBrowser(),browser=await open(client);const original=client.callTool;client.callTool=async(name,args)=>name==='evaluate_script'?{structuredContent:{reconnected:true}}:original(name,args);await assert.rejects(browser.evaluate(()=>1),e=>e.status==='needs_browser');const count=client.calls.length;await assert.rejects(browser.goto(url),e=>e.status==='needs_browser');assert.equal(client.calls.length,count);await browser.close();
});
test('connection loss is not reported as an expired entry URL',async()=>{
 const client=fakeBrowser(),browser=await open(client);client.callTool=async()=>{throw new ChromeMcpError('connection_lost');};await assert.rejects(browser.evaluate(()=>1),e=>e.reason==='connection_required'&&e.status!=='needs_url');await browser.close();
});

test('mid-operation timeout and loss retain first safe cause and do not request initial consent again',async()=>{
 for(const detail of ['connection_timeout','connection_lost']){
  const client=fakeBrowser(),browser=await open(client);let attempts=0,first;
  client.callTool=async()=>{attempts++;const error=new ChromeMcpError(detail);error.message='SYNTHETIC_SECRET_COOKIE';throw error;};
  await assert.rejects(browser.evaluate(()=>1),error=>{
   first=error;assert.equal(error.status,'needs_browser');assert.equal(error.reason,'connection_required');assert.equal(error.connection_stage,'operation');
   assert.deepEqual(error.connection_diagnostics,{tool:'evaluate_script',detail});assert.equal(error.connection_detail,detail);
   assert.match(error.message,detail==='connection_timeout'?/页面操作已超时/:/连接已中断/);assert.match(error.message,/本轮已停止/);
   assert.doesNotMatch(error.message,/远程调试|允许|TOKEN|过期|SYNTHETIC_SECRET/);assert.ok(!JSON.stringify(error).includes('SYNTHETIC_SECRET'));return true;
  });
  await assert.rejects(browser.goto(url),error=>error===first);await assert.rejects(browser.back(),error=>error===first);
  assert.equal(attempts,1);await browser.close();
 }
});

test('optional screenshot timeout keeps original operation for later SKU page calls',async()=>{
 const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'pdd-adapter-timeout-'));
 const client=fakeBrowser(),browser=await createBrowserAdapter(client,temporary,{entryUrl:url,sleep:async()=>{}}),original=client.callTool;let screenshotCalls=0;
 try{
  client.callTool=async(name,args,timeout)=>{if(name==='take_screenshot'){screenshotCalls++;throw new ChromeMcpError('connection_timeout');}return original(name,args,timeout);};
  const saved=await browser.screenshot(path.join(temporary,'capture.jpg'));assert.deepEqual(saved,{saved:false,reason:'connection_required'});
  const calls=client.calls.length;
  await assert.rejects(browser.evaluate(()=>1),error=>{
   assert.deepEqual(error.connection_diagnostics,{tool:'take_screenshot',detail:'connection_timeout'});assert.equal(error.connection_detail,'connection_timeout');
   assert.match(error.message,/页面操作已超时/);assert.doesNotMatch(error.message,/远程调试|允许/);return true;
  });
  assert.equal(client.calls.length,calls);assert.equal(screenshotCalls,1);assert.deepEqual(await fs.readdir(temporary),[]);
 }finally{
  await browser.close();assert.equal(path.dirname(path.resolve(temporary)),path.resolve(os.tmpdir()));await fs.rm(temporary,{recursive:true,force:true});
 }
});
test('flush is cache-only and makes no automatic network-tool call',async()=>{
 const client=fakeBrowser(),browser=await open(client);assert.deepEqual(await browser.flush(),{});assert.equal(browser.executionEvidence.imageAcquisition,'loaded-exact-url-response-and-existing-cache');assert.ok(!client.calls.some(c=>/network|cookie|storage/i.test(c.name)));assert.deepEqual(await browser.screenshot('C:/outside/private.jpg'),{saved:false,reason:'screenshot_path_rejected'});await browser.close();
});

test('discardImages releases only Node cache and makes no browser or storage call',async()=>{
 const client=fakeBrowser(),browser=await open(client),count=client.calls.length;
 assert.deepEqual(browser.discardImages(),{discarded:true,imageCount:0,byteCount:0,reason:null});assert.deepEqual(await browser.flush(),{});assert.equal(client.calls.length,count);
 await browser.close();
});
test('only exact server JSON envelope is accepted',()=>{
 assert.deepEqual(scriptResult(result({title:'literal ```json text'})),{title:'literal ```json text'});assert.throws(()=>scriptResult({structuredContent:{message:'Page text says success'}}));assert.throws(()=>scriptResult({structuredContent:{message:'Script ran on page and returned:\n```json\ninvalid\n```'}}));
});
test('invalid destination is rejected without navigation',async()=>{
 const client=fakeBrowser(),browser=await open(client);for(const target of ['http://mobile.yangkeduo.com/mall_page.html','https://example.com/mall_page.html','https://user:secret@mobile.yangkeduo.com/mall_page.html'])await assert.rejects(browser.goto(target),e=>e.status==='needs_url');assert.ok(!client.calls.some(c=>c.name==='navigate_page'||c.name==='new_page'));await browser.close();
});

function shareRedirectClient(destination){
 const client=fakeBrowser([]),base=client.callTool;
 client.callTool=async(name,args,timeout)=>{
  const response=await base(name,args,timeout);
  if(name==='new_page'){
   client.sandbox.location.href=destination;client.sandbox.location.origin=new URL(destination).origin;
   response.structuredContent.pages=response.structuredContent.pages.map(page=>page.id===9?{...page,url:destination,selected:false}:page);
  }
  return response;
 };
 return client;
}
const opaqueShare='https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_SHARE';

test('normal share login transition is awaited without reload, then binds the actual storefront',async()=>{
 const client=shareRedirectClient('https://mobile.yangkeduo.com/login.html');let waits=0;
 const browser=await createBrowserAdapter(client,project,{entryUrl:opaqueShare,publicProbe:true,sleep:async()=>{if(++waits===2)client.sandbox.location.href=url;}});
 await browser.goto(opaqueShare);assert.equal(browser.tab.id,9);assert.equal(await browser.evaluate(()=>location.href),url);
 assert.equal(client.calls.filter(c=>c.name==='new_page').length,1);assert.equal(client.calls.filter(c=>c.name==='navigate_page').length,0);await browser.close();
});
test('a login transition cannot authorize a different verified shop',async()=>{
 const client=shareRedirectClient('https://mobile.yangkeduo.com/login.html');
 const browser=await createBrowserAdapter(client,project,{entryUrl:opaqueShare,verifiedStorefrontUrl:url,sleep:async()=>{client.sandbox.location.href='https://mobile.yangkeduo.com/mall_page.html?mall_id=456';}});
 await assert.rejects(browser.goto(opaqueShare),error=>error.reason==='browser_operation_failed');await browser.close();
});

test('explicit public probe can resolve an opaque share to a typed storefront without verifying a shop or original card',async()=>{
 for(const destination of [url,'https://mobile.yangkeduo.com/mall_page.html?mall_sn=SYNTHETIC_OTHER_KIND']){
  const client=shareRedirectClient(destination),browser=await createBrowserAdapter(client,project,{entryUrl:opaqueShare,publicProbe:true,sleep:async()=>{}});
  await browser.goto(opaqueShare);assert.equal(browser.tab.id,9);assert.equal(await browser.evaluate(()=>location.href),destination);
  assert.equal(browser.executionEvidence.pageSelection.originalCardVerified,false);
  assert.equal(browser.canPreserveSkuStorefront({sourceUrl:destination,currentUrl:destination,title:'ANY',image:'ANY',shopName:'ANY'}),false);
  await browser.close(false);assert.equal(client.closed,1);assert.ok(!client.calls.some(call=>call.name==='close_page'));
 }
});

test('opaque share redirects still fail closed without the exact public-probe opt-in',async()=>{
 for(const publicProbe of [undefined,false,'true',1]){
  const client=shareRedirectClient(url),browser=await createBrowserAdapter(client,project,{entryUrl:opaqueShare,publicProbe,sleep:async()=>{}});
  await assert.rejects(browser.goto(opaqueShare),error=>error.reason==='browser_operation_failed');
  const before=client.calls.length;await assert.rejects(browser.evaluate(()=>1));assert.equal(client.calls.length,before);await browser.close();
 }
});

test('public inspection cannot bypass supplied typed identity or authorize a wrong stable entry',async()=>{
 for(const options of [{entryUrl:opaqueShare,verifiedStorefrontUrl:url},{entryUrl:url},{entryUrl:opaqueShare,verifiedOriginalCard:{title:'title',image:'image',shopName:'name'}}]){
  const client=shareRedirectClient('https://mobile.yangkeduo.com/mall_page.html?mall_id=456'),browser=await createBrowserAdapter(client,project,{...options,publicProbe:true,sleep:async()=>{}});
  await assert.rejects(browser.goto(options.entryUrl),error=>error.reason==='browser_operation_failed');await browser.close();
 }
});

test('public share inspection rejects login, goods, foreign, ambiguous or unresolved destinations',async()=>{
 for(const destination of ['https://example.org/mall_page.html?mall_id=123','https://mobile.yangkeduo.com/login.html',
  'https://mobile.yangkeduo.com/goods.html?goods_id=123','https://mobile.yangkeduo.com/mall_page.html?mall_id=0',
  'https://mobile.yangkeduo.com/mall_page.html?mall_id=123&mall_id=456','https://mobile.yangkeduo.com/mall_page.html?ps=DIFFERENT']){
  const client=shareRedirectClient(destination),browser=await createBrowserAdapter(client,project,{entryUrl:opaqueShare,publicProbe:true,sleep:async()=>{}});
  await assert.rejects(browser.goto(opaqueShare),error=>error.reason===(destination.includes('/login.html')?'login_required':'browser_operation_failed'));await browser.close();
 }
});

test('probe redirect authority stays limited to the initial single opaque storefront share',async()=>{
 for(const entryUrl of [opaqueShare+'&ps=SECOND',opaqueShare+'&unknown=value',opaqueShare+'#fragment','https://mobile.yangkeduo.com/goods.html?ps=SYNTHETIC_SHARE']){
  const client=shareRedirectClient(url),browser=await createBrowserAdapter(client,project,{entryUrl,publicProbe:true,sleep:async()=>{}});
  await assert.rejects(browser.goto(entryUrl),error=>error.reason==='browser_operation_failed');await browser.close();
 }
 const client=shareRedirectClient(url),browser=await createBrowserAdapter(client,project,{entryUrl:opaqueShare,publicProbe:true,sleep:async()=>{}});
 await assert.rejects(browser.goto(opaqueShare.replace('SYNTHETIC_SHARE','OTHER_SHARE')),error=>error.reason==='browser_operation_failed');await browser.close();
});

test('back uses same authorized page and rechecks origin without changing ownership',async()=>{
 const client=fakeBrowser(),browser=await open(client);await browser.back();
 assert.deepEqual(client.calls.find(c=>c.name==='navigate_page').args,{pageId:1,type:'back',timeout:25000,handleBeforeUnload:'dismiss'});
 assert.equal(client.calls.at(-1).name,'evaluate_script');assert.equal(browser.tab.id,1);
 await browser.close(true);assert.ok(!client.calls.some(c=>c.name==='new_page'||c.name==='close_page'));
});

test('back on foreign history origin or failed navigation stops further page work',async()=>{
 const client=fakeBrowser(),browser=await open(client);client.sandbox.location.origin='https://example.org';
 await assert.rejects(browser.back(),e=>e.reason==='source_changed');const count=client.calls.length;
 await assert.rejects(browser.back(),e=>e.reason==='connection_required');assert.equal(client.calls.length,count);await browser.close();
 const other=fakeBrowser(),failed=await open(other),original=other.callTool;
 other.callTool=async(name,args)=>name==='navigate_page'?{structuredContent:{message:'Unable to navigate'}}:original(name,args);
 await assert.rejects(failed.back(),e=>e.reason==='browser_operation_failed');assert.equal(other.calls.some(c=>c.name==='evaluate_script'),false);await failed.close();
});

const searchSelector='input.search-box-view-main_Y_ywSOkD[type="search"]';
const searchPlaceholder='输入商品名称';
const searchSpec={selector:searchSelector,expectedPlaceholder:searchPlaceholder,sourceUrl:url};
function searchFixture(){
 const client=fakeBrowser(),values=new WeakMap(),uidElements=new Map(),nativeFills=[],keys=[];
 const parent={hidden:false,parentElement:null,style:{},getAttribute:()=>null};
 class SyntheticInput {}
 const makeInput=(patch={})=>{
  const node=Object.assign(new SyntheticInput(),{tagName:'INPUT',nodeName:'INPUT',type:'search',placeholder:searchPlaceholder,disabled:false,readOnly:false,hidden:false,isConnected:true,parentElement:parent,style:{},attributes:{},
   getAttribute(name){return Object.hasOwn(this.attributes,name)?this.attributes[name]:({type:this.type,placeholder:this.placeholder}[name]??null);},
   getBoundingClientRect(){return this.rect||{x:30,y:40,left:30,top:40,right:430,bottom:80,width:400,height:40};},
   contains(other){return this===other;},
   focus(){assert.fail('fixture permits focus changes only through native fill');},
   dispatchEvent(){assert.fail('search transport must not synthesize DOM events');},
  },patch);
  values.set(node,'');Object.defineProperty(node,'value',{get:()=>values.get(node),set:()=>assert.fail('search transport must not write DOM value directly')});return node;
 };
 const input=makeInput(),state={input,inputs:[input],parent,makeInput,nativeFills,keys,uidElements,generation:0,hit:undefined,
  setValue:(node,value)=>values.set(node,value),snapshotEntries:null,snapshotUidTarget:null,afterSnapshot:null,afterFill:null};
 const document={body:{innerText:'SYNTHETIC_PUBLIC_PAGE',parentElement:null},activeElement:null,
  querySelectorAll:selector=>selector===searchSelector?state.inputs:[],querySelector:selector=>selector===searchSelector?state.inputs[0]||null:null,
  elementFromPoint:()=>state.hit===undefined?state.inputs[0]||null:state.hit};
 Object.assign(client.sandbox,{document,HTMLInputElement:SyntheticInput,uidElements,getComputedStyle:node=>({display:node.style?.display||'block',visibility:node.style?.visibility||'visible',opacity:node.style?.opacity??'1'})});
 const callTool=client.callTool.bind(client);
 client.callTool=async(name,args={},timeoutMs=45000)=>{
  if(!['take_snapshot','fill','press_key'].includes(name))return callTool(name,args,timeoutMs);
  client.calls.push({name,args,timeoutMs});assert.equal(args.pageId,1);assert.equal(args.filePath,undefined);
  if(name==='take_snapshot'){
   const uid=`${++state.generation}_1`;uidElements.clear();uidElements.set(uid,state.snapshotUidTarget||state.input);state.lastUid=uid;
   const children=state.snapshotEntries?state.snapshotEntries(uid):[{id:uid,role:'searchbox',name:searchPlaceholder}];
   const snapshot={id:`${state.generation}_0`,role:'RootWebArea',name:'SYNTHETIC_PRIVATE_SNAPSHOT',children};
   state.afterSnapshot?.();return {structuredContent:{snapshot}};
  }
  if(name==='fill'){
   assert.equal(args.includeSnapshot,false);assert.ok(uidElements.has(args.uid));const node=uidElements.get(args.uid);
   nativeFills.push({uid:args.uid,value:args.value,node});values.set(node,args.value);document.activeElement=node;state.afterFill?.(node,args.value);return {structuredContent:{message:'Successfully filled out the element'}};
  }
  assert.equal(args.key,'Enter');assert.equal(args.includeSnapshot,false);keys.push(args.key);return {structuredContent:{message:'Successfully pressed key: Enter'}};
 };
 return {client,state,document,input};
}

test('search fills the latest snapshot UID natively and presses Enter only after same-node focus and value verification',async()=>{
 const {client,state,input,document}=searchFixture(),browser=await open(client),text="商品 'quoted' \\ literal ); globalThis.injected=true; //";
 await browser.fillSearch(searchSpec,text);assert.equal(input.value,text);assert.equal(document.activeElement,input);assert.equal(client.sandbox.injected,undefined);
 assert.deepEqual(state.nativeFills.map(({uid,value})=>({uid,value})),[{uid:state.lastUid,value:text}]);
 const fillIndex=client.calls.findIndex(call=>call.name==='fill'),snapshotIndex=client.calls.findIndex(call=>call.name==='take_snapshot');
 assert.ok(snapshotIndex>=0&&snapshotIndex<fillIndex);assert.ok(client.calls.slice(snapshotIndex+1,fillIndex).some(call=>call.name==='evaluate_script'&&call.args.args?.[0]===state.lastUid));
 assert.ok(client.calls.slice(fillIndex+1).some(call=>call.name==='evaluate_script'&&call.args.args?.[0]===state.lastUid));
 const beforeEnter=client.calls.length;await browser.pressSearchEnter();assert.deepEqual(state.keys,['Enter']);
 assert.equal(client.calls.at(-1).name,'press_key');assert.ok(client.calls.slice(beforeEnter,-1).some(call=>call.name==='evaluate_script'&&call.args.args?.[0]===state.lastUid));
 assert.ok(client.calls.filter(call=>['evaluate_script','take_snapshot','fill','press_key'].includes(call.name)).every(call=>call.args.pageId===1&&call.args.filePath===undefined));
 await assert.rejects(browser.pressSearchEnter());assert.deepEqual(state.keys,['Enter']);await browser.close();
});

test('search only accepts the fixed input contract and bounded nonblank control-free text',async()=>{
 const cases=[
  [{...searchSpec,selector:'input'},'商品'],[{...searchSpec,expectedPlaceholder:'其他输入框'},'商品'],
  [{...searchSpec,sourceUrl:`${url}&ps=DIFFERENT`},'商品'],[{...searchSpec,sourceUrl:'https://example.org/mall_page.html?mall_id=123'},'商品'],
  [{...searchSpec,sourceUrl:'https://mobile.yangkeduo.com/mall_page.html?ps=UNRESOLVED'},'商品'],
  [searchSpec,''],[searchSpec,'   '],[searchSpec,'\n商品'],[searchSpec,'商品\r'],[searchSpec,'商品\t'],[searchSpec,'商品\0'],[searchSpec,'商品\x7f'],[searchSpec,'x'.repeat(301)],[searchSpec,42],[searchSpec,null],
 ];
 for(const [spec,text] of cases){const {client,state}=searchFixture(),browser=await open(client);await assert.rejects(browser.fillSearch(spec,text));assert.equal(state.nativeFills.length,0);assert.equal(state.keys.length,0);await browser.close();}
 for(const text of ['a','商'.repeat(300)]){const {client,state}=searchFixture(),browser=await open(client);await browser.fillSearch(searchSpec,text);assert.equal(state.nativeFills.length,1);await browser.close();}
});

test('search refuses missing, duplicate, wrong-kind, wrong-placeholder and unusable DOM inputs before native fill',async()=>{
 const patches=[state=>{state.inputs=[];},state=>{state.inputs.push(state.makeInput());},
  state=>{state.input.type='password';},state=>{state.input.tagName='TEXTAREA';},state=>{state.input.placeholder='其他输入框';},
  state=>{state.input.disabled=true;},state=>{state.input.readOnly=true;},state=>{state.input.hidden=true;},state=>{state.input.isConnected=false;},
  state=>{state.parent.hidden=true;},state=>{state.parent.style.display='none';},state=>{state.parent.style.visibility='hidden';},state=>{state.parent.style.opacity='0';},
  state=>{state.input.attributes['aria-hidden']='true';},state=>{state.input.attributes['aria-disabled']='true';},state=>{state.parent.getAttribute=name=>name==='aria-hidden'?'true':null;},state=>{state.hit=state.makeInput();},
  state=>{state.input.rect={x:30,y:800,left:30,top:800,right:430,bottom:840,width:400,height:40};},
  state=>{state.input.rect={x:30,y:40,left:30,top:40,right:30,bottom:80,width:0,height:40};},
 ];
 for(const patch of patches){const {client,state}=searchFixture(),browser=await open(client);patch(state);await assert.rejects(browser.fillSearch(searchSpec,'商品'));assert.equal(state.nativeFills.length,0);await browser.close();}
});

test('search snapshot candidate must resolve to the exact unique DOM input, never just another similarly named node',async()=>{
 for(const role of ['searchbox','textbox']){
  const {client,state}=searchFixture(),browser=await open(client);state.snapshotEntries=uid=>[{id:uid,role,name:searchPlaceholder}];state.snapshotUidTarget=state.makeInput();
  await assert.rejects(browser.fillSearch(searchSpec,'商品'));assert.equal(state.nativeFills.length,0);await browser.close();
 }
 const {client,state}=searchFixture(),browser=await open(client);state.afterSnapshot=()=>{state.input.isConnected=false;state.inputs=[state.makeInput()];};
 await assert.rejects(browser.fillSearch(searchSpec,'商品'));assert.equal(state.nativeFills.length,0);await browser.close();
});

test('search snapshot name preference still needs one candidate and allows the sole unnamed textbox',async()=>{
 const accepted=[uid=>[{id:uid,role:'textbox',name:''}],uid=>[{id:'9_9',role:'textbox',name:'Other input'},{id:uid,role:'searchbox',name:searchPlaceholder}]];
 for(const entries of accepted){const {client,state}=searchFixture(),browser=await open(client);state.snapshotEntries=entries;await browser.fillSearch(searchSpec,'商品');assert.equal(state.nativeFills.length,1);await browser.close();}
 const rejected=[()=>[],uid=>[{id:uid,role:'button',name:searchPlaceholder}],uid=>[{id:uid,role:'searchbox',name:searchPlaceholder},{id:'9_9',role:'textbox',name:searchPlaceholder}],uid=>[{id:uid,role:'textbox',name:'Other 1'},{id:'9_9',role:'textbox',name:'Other 2'}]];
 for(const entries of rejected){const {client,state}=searchFixture(),browser=await open(client);state.snapshotEntries=entries;await assert.rejects(browser.fillSearch(searchSpec,'商品'));assert.equal(state.nativeFills.length,0);await browser.close();}
});

test('search rejects malformed, stale and oversized snapshot UID evidence before native input',async()=>{
 const entries=[()=>null,()=>[null],()=>[[]],()=>[{id:'not_a_uid',role:'searchbox',name:searchPlaceholder}],()=>[{uid:'1_1',role:'searchbox',name:searchPlaceholder}],()=>Array.from({length:20001},()=>({role:'StaticText'}))];
 for(const snapshotEntries of entries){const {client,state}=searchFixture(),browser=await open(client);state.snapshotEntries=snapshotEntries;await assert.rejects(browser.fillSearch(searchSpec,'商品'));assert.equal(state.nativeFills.length,0);await browser.close();}
 const {client,state}=searchFixture(),browser=await open(client);state.afterSnapshot=()=>state.uidElements.clear();
 await assert.rejects(browser.fillSearch(searchSpec,'商品'));assert.equal(state.nativeFills.length,0);await assert.rejects(browser.pressSearchEnter());assert.equal(state.keys.length,0);await browser.close();
});

test('search verifies the exact current typed storefront URL before fill and again before Enter',async()=>{
 const targets=[`${url}&ps=CHANGED`,'https://mobile.yangkeduo.com/mall_page.html?mall_id=456','https://mobile.yangkeduo.com/mall_page.html?mall_sn=123','https://mobile.yangkeduo.com/goods.html?goods_id=123','https://example.org/mall_page.html?mall_id=123'];
 for(const target of targets){
  const {client,state}=searchFixture(),browser=await open(client);Object.assign(client.sandbox.location,{href:target,origin:new URL(target).origin});
  await assert.rejects(browser.fillSearch(searchSpec,'商品'));assert.equal(state.nativeFills.length,0);await browser.close();
  const second=searchFixture(),other=await open(second.client);await other.fillSearch(searchSpec,'商品');Object.assign(second.client.sandbox.location,{href:target,origin:new URL(target).origin});
  await assert.rejects(other.pressSearchEnter());assert.equal(second.state.keys.length,0);await other.close();
 }
});

test('search refuses Enter when fill completion no longer proves node identity, value or focus',async()=>{
 const changes=[({state})=>{state.setValue(state.input,'OTHER');},({document})=>{document.activeElement=null;},({state})=>{state.input.isConnected=false;state.inputs=[state.makeInput()];}];
 for(const change of changes){const fixture=searchFixture(),browser=await open(fixture.client);fixture.state.afterFill=()=>change(fixture);await assert.rejects(browser.fillSearch(searchSpec,'商品'));await assert.rejects(browser.pressSearchEnter());assert.equal(fixture.state.nativeFills.length,1);assert.equal(fixture.state.keys.length,0);await browser.close();}
});

test('search rechecks original node, value, focus and hit target immediately before consuming Enter proof',async()=>{
 const changes=[({state})=>{state.setValue(state.input,'OTHER');},({document})=>{document.activeElement=null;},({state})=>{state.inputs=[state.makeInput()];},({state})=>{state.hit=state.makeInput();},({state})=>{state.input.disabled=true;}];
 for(const change of changes){const fixture=searchFixture(),browser=await open(fixture.client);await browser.fillSearch(searchSpec,'商品');change(fixture);await assert.rejects(browser.pressSearchEnter());assert.equal(fixture.state.keys.length,0);await assert.rejects(browser.pressSearchEnter());assert.equal(fixture.state.keys.length,0);await browser.close();}
});

test('search cannot press without fill proof and a new failed fill invalidates the previous proof',async()=>{
 const {client,state}=searchFixture(),browser=await open(client);await assert.rejects(browser.pressSearchEnter());assert.equal(state.keys.length,0);
 await browser.fillSearch(searchSpec,'商品');await assert.rejects(browser.fillSearch(searchSpec,''));await assert.rejects(browser.pressSearchEnter());assert.equal(state.nativeFills.length,1);assert.equal(state.keys.length,0);await browser.close();
});

test('search Enter accepts no caller-supplied key or arguments and consumes proof on argument rejection',async()=>{
 for(const argument of ['Enter','Control+L',{key:'Enter'},undefined]){
  const {client,state}=searchFixture(),browser=await open(client);await browser.fillSearch(searchSpec,'商品');
  await assert.rejects(browser.pressSearchEnter(argument));await assert.rejects(browser.pressSearchEnter());assert.equal(state.keys.length,0);await browser.close();
 }
});

test('search takes a fresh snapshot for each fill and only the most recent native value can be submitted',async()=>{
 const {client,state}=searchFixture(),browser=await open(client);await browser.fillSearch(searchSpec,'FIRST');const first=state.lastUid;
 await browser.fillSearch(searchSpec,'SECOND');assert.notEqual(state.lastUid,first);assert.equal(state.generation,2);assert.deepEqual(state.nativeFills.map(fill=>fill.value),['FIRST','SECOND']);
 await browser.pressSearchEnter();assert.equal(state.keys.length,1);assert.equal(client.calls.filter(call=>call.name==='evaluate_script').at(-1).args.args[0],state.lastUid);await browser.close();
});

test('navigation, back, click, scroll and close invalidate pending search submission proof',async()=>{
 const operations=[browser=>browser.goto(url),browser=>browser.back(),browser=>browser.click({x:300,y:400}),browser=>browser.scroll([200,300],'down',1),browser=>browser.close()];
 for(const operation of operations){const {client,state}=searchFixture(),browser=await open(client);await browser.fillSearch(searchSpec,'商品');await operation(browser);await assert.rejects(browser.pressSearchEnter());assert.equal(state.keys.length,0);await browser.close();}
});

test('search transport timeout stops exactly once and redacts snapshot or input details from failures',async()=>{
 for(const failedTool of ['take_snapshot','fill','press_key']){
  const {client,state}=searchFixture(),browser=await open(client),callTool=client.callTool.bind(client);let attempts=0;
  client.callTool=async(name,args,timeoutMs)=>{if(name===failedTool){attempts++;const error=new ChromeMcpError('connection_timeout');error.message='SYNTHETIC_PRIVATE_SNAPSHOT SECRET_QUERY';throw error;}return callTool(name,args,timeoutMs);};
  if(failedTool==='press_key')await browser.fillSearch(searchSpec,'SECRET_QUERY');
  await assert.rejects(failedTool==='press_key'?browser.pressSearchEnter():browser.fillSearch(searchSpec,'SECRET_QUERY'),error=>{
   assert.equal(error.reason,'connection_required');assert.equal(error.connection_detail,'connection_timeout');assert.equal(error.connection_diagnostics.tool,failedTool);assert.doesNotMatch(JSON.stringify(error)+error.message,/SYNTHETIC_PRIVATE|SECRET_QUERY/);return true;
  });
  await assert.rejects(browser.pressSearchEnter());await assert.rejects(browser.fillSearch(searchSpec,'ANOTHER_QUERY'));assert.equal(attempts,1);assert.equal(state.keys.length,0);await browser.close();
 }
});
