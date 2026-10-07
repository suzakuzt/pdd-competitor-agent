import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import vm from 'node:vm';
import {createLoadedImageCache,readLoadedStoreImages,readLoadedSkuImages,saveMcpScreenshot,imageMime,pddImageUrl,MAX_IMAGE_BYTES,MAX_TOTAL_IMAGE_BYTES} from '../scripts/loaded_image_cache.mjs';
import {armListSkuModal,checkListSkuModal} from '../scripts/list_sku_modal.mjs';
const urls=['https://img.pddpic.com/SYNTHETIC_A.png','https://img.pddpic.com/SYNTHETIC_B.png'];
const png=Buffer.from([137,80,78,71,13,10,26,10,0,0,0,0]);
const jpeg=Buffer.from([255,216,255,224,0,0,0,0]);
async function fixture(body,options={}) {
 const project=await fs.mkdtemp(path.join(os.tmpdir(),'pdd-image-test-'));
 const calls=[];
 const env={project,calls,evaluations:[],dom:{verified:true,images:urls.map(url=>({url,src:url}))},requests:urls.map((url,i)=>({requestId:i+1,url,method:'GET',status:'200'})),
  async callTool(name,args){calls.push({name,args});if(name==='list_network_requests')return {structuredContent:{networkRequests:env.requests,pagination:{hasNextPage:false}}};
   const request=env.requests.find(item=>item.requestId===args.reqid);await fs.writeFile(args.responseFilePath,png);
   return {structuredContent:{networkRequest:{...request,responseBodyFilePath:args.responseFilePath,responseHeaders:{'content-type':'image/png','cookie':'SYNTHETIC_SECRET','authorization':'SYNTHETIC_SECRET'}}}};
  }};
 env.cache=createLoadedImageCache({project,getPageId:()=>7,evaluate:async(fn,args)=>{env.evaluations.push({fn,args});return env.dom;},callTool:(...args)=>env.callTool(...args),...options});
 try{await body(env);}finally{assert.equal(path.dirname(path.resolve(project)),path.resolve(os.tmpdir()));await fs.rm(project,{recursive:true,force:true});}
}
test('only exact HTTPS PDD image URLs and recognized image signatures are accepted',()=>{
 assert.equal(pddImageUrl(urls[0]),urls[0]);for(const u of ['https://pddpic.com.evil.test/a','http://img.pddpic.com/a','https://user:pass@img.pddpic.com/a'])assert.equal(pddImageUrl(u),null);
 assert.equal(imageMime(png),'image/png');assert.equal(imageMime(jpeg),'image/jpeg');assert.equal(imageMime(Buffer.from('GIF89a')),'image/gif');assert.equal(imageMime(Buffer.from('RIFFxxxxWEBP')),'image/webp');assert.equal(imageMime(Buffer.from('<html>login</html>')),null);
 assert.equal(MAX_IMAGE_BYTES,10*1024*1024);assert.equal(MAX_TOTAL_IMAGE_BYTES,128*1024*1024);
});
test('DOM selection excludes unloaded, variant, foreign, hidden and recommendation images',()=>{
 const rect=top=>({top,width:100,height:100});
 function card(url,top,patch={}){const img={src:url,currentSrc:url,complete:true,naturalWidth:100,naturalHeight:100,getBoundingClientRect:()=>rect(top),...patch};return {img,children:[],getBoundingClientRect:()=>rect(top),querySelector:()=>img};}
 const cards=[card(urls[0],10),card(urls[1],20,{complete:false}),card(urls[1],30,{currentSrc:urls[1]+'?size=1'}),card('https://evil.test/a.png',40),card(urls[1],50,{hidden:true}),card(urls[1],500)];
 const marker={children:[],textContent:'其他店铺的精选推荐',getBoundingClientRect:()=>rect(400)};
 const list={getBoundingClientRect:()=>rect(0),querySelectorAll:()=>cards};
 const context={URL,location:{origin:'https://mobile.yangkeduo.com'},window:{scrollY:0},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),document:{body:{innerText:'SYNTHETIC_SHOP'},querySelectorAll:selector=>selector.startsWith('.waterfall')?[list]:[marker]}};
 const value=vm.runInNewContext('('+readLoadedStoreImages.toString()+')({shopName:"SYNTHETIC_SHOP"})',context);
 assert.deepEqual(JSON.parse(JSON.stringify(value)),{verified:true,images:[{url:urls[0],src:urls[0]}]});
 const recommendationList={getBoundingClientRect:()=>rect(550),querySelectorAll:()=>[card(urls[1],560)]},hiddenList={hidden:true,getBoundingClientRect:()=>rect(10),querySelectorAll:()=>[]};
 context.document.querySelectorAll=selector=>selector.startsWith('.waterfall')?[hiddenList,recommendationList,list]:[marker];
 const withRecommendations=vm.runInNewContext('('+readLoadedStoreImages.toString()+')({shopName:"SYNTHETIC_SHOP"})',context);assert.deepEqual(JSON.parse(JSON.stringify(withRecommendations)),{verified:true,images:[{url:urls[0],src:urls[0]}]});
 const ambiguous={getBoundingClientRect:()=>rect(20),querySelectorAll:()=>[]};context.document.querySelectorAll=selector=>selector.startsWith('.waterfall')?[list,ambiguous,recommendationList]:[marker];
 assert.equal(vm.runInNewContext('('+readLoadedStoreImages.toString()+')({shopName:"SYNTHETIC_SHOP"})',context).verified,false);
 context.document.body.innerText='different shop';assert.equal(vm.runInNewContext('('+readLoadedStoreImages.toString()+')({shopName:"SYNTHETIC_SHOP"})',context).verified,false);
});
test('copies only page-scoped observed GET images, removes temporary bodies and keeps no headers',async()=>fixture(async env=>{
 const report=await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.equal(report.newlySaved,2);assert.equal(report.missing,0);
 const result=await env.cache.flush();assert.equal(result[urls[0]].mime,'image/png');assert.equal(Buffer.from(result[urls[0]].base64,'base64').equals(png),true);assert.ok(!JSON.stringify({report,result}).includes('SYNTHETIC_SECRET'));
 for(const {name,args} of env.calls){assert.equal(args.pageId,7);if(name==='get_network_request'){assert.ok(args.reqid>0);assert.equal(args.requestFilePath,undefined);assert.ok(args.responseFilePath.endsWith('.network-response'));}}
 assert.deepEqual(await fs.readdir(path.join(env.project,'state/local_collection')),[]);
 const before=env.calls.length;await env.cache.flush();await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.equal(env.calls.length,before);
}));
test('approved exact-cache skipUrls prevent network calls without inventing new bytes',async()=>fixture(async env=>{
 const report=await env.cache.collect({shopName:'SYNTHETIC_SHOP',skipUrls:urls});assert.equal(report.reused,2);assert.equal(report.newlySaved,0);assert.equal(report.missing,0);assert.equal(env.calls.length,0);assert.deepEqual(await env.cache.flush(),{});
}));
test('store repair reads only requested original URLs and cannot substitute another loaded card image',async()=>fixture(async env=>{
 const result=await env.cache.collect({shopName:'SYNTHETIC_SHOP',imageUrls:[urls[1]]});
 assert.equal(result.newlySaved,1);assert.deepEqual([...env.cache.images.keys()],[urls[1]]);
 assert.deepEqual(env.calls.filter(call=>call.name==='get_network_request').map(call=>call.args.reqid),[2]);
 const callsBefore=env.calls.length,missing=await env.cache.collect({shopName:'SYNTHETIC_SHOP',imageUrls:[urls[0]+'?different=1']});
 assert.equal(missing.reasons.store_image_not_loaded,1);assert.equal(missing.newlySaved,0);assert.equal(env.calls.length,callsBefore);
}));
test('historically blocked URLs are not fetched or counted as approved cache reuse',async()=>fixture(async env=>{
 const report=await env.cache.collect({shopName:'SYNTHETIC_SHOP',skipUrls:urls,blockedUrls:urls});
 assert.equal(report.reasons.previous_attempt_blocked,2);assert.equal(report.reused,0);assert.equal(report.newlySaved,0);assert.equal(report.missing,2);assert.equal(env.calls.length,0);assert.deepEqual(await env.cache.flush(),{});
}));
test('wrong shop or nonmatching request never triggers response body access',async()=>fixture(async env=>{
 env.dom.verified=false;assert.equal((await env.cache.collect({shopName:'SYNTHETIC_SHOP'})).reasons.shop_dom_unverified,1);assert.equal(env.calls.length,0);
 env.dom.verified=true;env.requests=[{requestId:1,url:urls[0],method:'POST',status:'200'},{requestId:2,url:urls[1],method:'GET',status:'pending'},{requestId:3,url:'https://img.pddpic.com/not-observed.png',method:'GET',status:'200'}];
 assert.equal((await env.cache.collect({shopName:'SYNTHETIC_SHOP'})).missing,2);assert.ok(!env.calls.some(c=>c.name==='get_network_request'));
}));
test('oversized files and wrong magic are rejected after read and cleaned',async()=>fixture(async env=>{
 const original=env.callTool;env.callTool=async(name,args)=>{const value=await original(name,args);if(name==='get_network_request')await fs.writeFile(args.responseFilePath,args.reqid===1?Buffer.alloc(100):Buffer.from('<html>'));return value;};
 const report=await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.equal(report.newlySaved,0);assert.equal(report.reasons.image_size_limit,1);assert.equal(report.reasons.invalid_image_bytes,1);assert.deepEqual(await fs.readdir(path.join(env.project,'state/local_collection')),[]);
},{maxImageBytes:20}));
test('total accepted bytes are bounded and an excessive known size stops before body access',async()=>fixture(async env=>{
 const report=await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.equal(report.newlySaved,1);assert.equal(report.byteCount,png.length);assert.equal(report.missing,1);
},{maxTotalBytes:png.length+1}));
test('known excessive size and MIME mismatch are rejected without substituting image data',async()=>fixture(async env=>{
 env.dom.images[0].knownByteLength=MAX_IMAGE_BYTES+1;const original=env.callTool;env.callTool=async(name,args)=>{const value=await original(name,args);if(name==='get_network_request')value.structuredContent.networkRequest.responseHeaders['content-type']='text/html';return value;};
 const report=await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.equal(report.reasons.image_size_limit,1);assert.equal(report.reasons.image_mime_mismatch,1);assert.equal(env.calls.filter(c=>c.name==='get_network_request').length,1);assert.deepEqual(await env.cache.flush(),{});
}));
test('missing body cannot trigger another body attempt or a HTTP download',async()=>fixture(async env=>{
 const original=env.callTool;env.callTool=async(name,args)=>{if(name==='get_network_request'){env.calls.push({name,args});return {structuredContent:{networkRequest:{responseBody:'not available'}}};}return original(name,args);};
 const first=await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.equal(first.newlySaved,0);const count=env.calls.filter(c=>c.name==='get_network_request').length;await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.equal(env.calls.filter(c=>c.name==='get_network_request').length,count);
}));
test('a returned foreign file path is neither read nor deleted',async()=>fixture(async env=>{
 const outside=path.join(env.project,'do-not-touch.txt');await fs.writeFile(outside,'KEEP');const original=env.callTool;
 env.callTool=async(name,args)=>{const value=await original(name,args);if(name==='get_network_request')value.structuredContent.networkRequest.responseBodyFilePath=outside;return value;};
 const report=await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.equal(report.newlySaved,0);assert.equal(await fs.readFile(outside,'utf8'),'KEEP');assert.deepEqual(await fs.readdir(path.join(env.project,'state/local_collection')),[]);
}));
test('screenshot verifies official .jpeg output then fulfills caller .jpg path',async()=>fixture(async env=>{
 const file=path.join(env.project,'probe','public_page.jpg');
 const result=await saveMcpScreenshot({project:env.project,pageId:7,file,callTool:async(name,args)=>{assert.equal(name,'take_screenshot');assert.equal(args.format,'jpeg');assert.ok(args.filePath.endsWith('.jpeg'));await fs.writeFile(args.filePath,jpeg);return {};}});
 assert.deepEqual(result,{saved:true,reason:null,byteCount:jpeg.length});assert.ok((await fs.readFile(file)).equals(jpeg));assert.deepEqual(await fs.readdir(path.dirname(file)),['public_page.jpg']);
}));
test('screenshot reports missing output or safe failure instead of false success',async()=>fixture(async env=>{
 const options={project:env.project,pageId:7,file:path.join(env.project,'missing.jpg')};
 assert.deepEqual(await saveMcpScreenshot({...options,callTool:async()=>({})}),{saved:false,reason:'screenshot_file_missing'});
 const failed=await saveMcpScreenshot({...options,callTool:async()=>{throw new Error('SYNTHETIC_SECRET_HEADER');}});assert.deepEqual(failed,{saved:false,reason:'screenshot_unavailable'});assert.ok(!JSON.stringify(failed).includes('SYNTHETIC_SECRET'));
 assert.ok(!(await fs.readdir(env.project)).some(name=>name.startsWith('mcp-screenshot-')));
}));

function skuDomFixture(){
 const element=(text='',attrs={})=>({textContent:text,innerText:text,className:'',children:[],parentElement:null,
  getBoundingClientRect:()=>({top:50,bottom:350,width:300,height:300}),getAttribute:name=>attrs[name]??null,
  contains(other){return this===other||this.children.some(child=>child.contains(other));},querySelector:()=>null,querySelectorAll:()=>[]});
 const body=element('SYNTHETIC'),root=element(),group=element(),heading=element('款式'),a=element('A',{'aria-label':'A'}),b=element('B',{'aria-label':'B'}),summary=element('已选：A'),price=element('',{'aria-label':'券后¥4.5'});
 a.className='F7sZG3xe hr353bdX rGqCSAQI';b.className='F7sZG3xe rGqCSAQI';
 const img=Object.assign(element(),{src:urls[0],currentSrc:urls[0],complete:true,naturalWidth:375,naturalHeight:500});
 const outside=Object.assign(element(),{src:urls[1],currentSrc:urls[1],complete:true,naturalWidth:375,naturalHeight:500});
 const attach=(parent,children)=>{parent.children=children;for(const child of children)child.parentElement=parent;};
 attach(body,[root,outside]);attach(root,[group,summary,price,img]);attach(group,[heading,a,b]);
 group.querySelector=()=>heading;group.querySelectorAll=()=>[a,b];
 root.querySelector=selector=>selector==='.Mbx2m60G'?summary:selector==='.ujEqGzEB [aria-label]'?price:null;
 root.querySelectorAll=selector=>selector==='.bIhLWVqm'?[group]:selector==='.O7pEFvHR > img'?[img]:[];
 const context={URL,location:{href:'https://mobile.yangkeduo.com/goods.html?goods_id=123'},innerHeight:850,getComputedStyle:e=>({display:e.hidden?'none':'block',visibility:'visible',opacity:'1'}),document:{body,querySelectorAll:selector=>selector.startsWith('.sku-plus1')?[root]:[]}};
 const read=(args={goodsId:'123',imageUrls:urls})=>JSON.parse(JSON.stringify(vm.runInNewContext('('+readLoadedSkuImages.toString()+')('+JSON.stringify(args)+')',context)));
 return {context,root,group,a,b,summary,price,img,outside,read,element,attach};
}

function skuListDomFixture(){
 const f=skuDomFixture(),{element,attach}=f,shopName='SYNTHETIC_SHOP',title='SYNTHETIC 原卡',token='SYNTHETIC_MODAL_TOKEN';
 const list=element(),card=element(),plus=element(),cardTitle=element(title),cardImage=element('',{'data-src':urls[1],src:urls[1]+'?thumbnail=1'});
 for(const e of [list,card,plus,f.root])e.isConnected=true;
 attach(list,[card]);attach(card,[cardTitle,cardImage,plus]);attach(f.context.document.body,[list,f.root,f.outside]);
 card.querySelector=selector=>selector==='.goodsName_dT8mZSNd'?cardTitle:selector==='.goodsImage_fU27dxbk img'?cardImage:null;
 list.querySelectorAll=selector=>selector==='.goodsItem_R1ok0MpS'?[card]:[];
 f.a.className='skuSpecValue_F7sZG3xe skuSpecValueSelected_oQDDpea9';f.b.className='skuSpecValue_F7sZG3xe';
 f.group.querySelectorAll=selector=>selector==='.skuSpecValueList_s1O5M5fO > [role="button"]'?[f.a,f.b]:[];
 f.root.querySelector=selector=>selector==='.text_Mbx2m60G'?f.summary:selector==='.skuQuantityPriceDesc_ujEqGzEB [aria-label]'?f.price:null;
 f.root.querySelectorAll=selector=>selector==='.skuSpecs_bIhLWVqm'?[f.group]:selector==='.skuSelectorHead_O7pEFvHR > img'?[f.img]:[];
 f.context.document.body.innerText=shopName;
 f.context.document.querySelectorAll=selector=>selector.startsWith('.sku-plus1')?[f.root]:selector==='.waterfall-list-container_egohG8wQ'?[list]:[];
 f.context.location.href='https://mobile.yangkeduo.com/mall_page.html?mall_id=789';
 const binding={token,sourceUrl:f.context.location.href,card,plus,title,imageUrl:urls[1],shopName,modal:f.root,clicked:true,opened:true,invalid:false};
 f.context[Symbol.for('pdd-monitor/sku-modal-binding-v1')]=binding;
 const read=(args={goodsId:null,imageUrls:[urls[0]],listModalToken:token})=>f.read(args);
 return {...f,read,list,card,cardTitle,cardImage,plus,binding,token};
}

function plainCurrentPrice(f,{text='¥18.8',prefixed=true,aliases=false}={}){
 const price=f.element(text),nodes=[price],query=f.root.querySelector,queryAll=f.root.querySelectorAll;
 const selector=prefixed?'.skuQuantityPriceDesc_ujEqGzEB':'.ujEqGzEB';
 f.attach(f.root,[...f.root.children.filter(child=>child!==f.price),price]);
 f.root.querySelector=value=>value==='.ujEqGzEB [aria-label]'||value==='.skuQuantityPriceDesc_ujEqGzEB [aria-label]'?null:query(value);
 f.root.querySelectorAll=value=>value===selector||aliases&&['.ujEqGzEB','.skuQuantityPriceDesc_ujEqGzEB'].includes(value)?nodes:queryAll(value);
 return {price,nodes};
}

function rerenderListPlus(f){
 const replacement=f.element();replacement.isConnected=true;
 f.attach(f.card,[f.cardTitle,f.cardImage,replacement]);f.plus.isConnected=false;f.plus.parentElement=null;
 assert.equal(f.card.contains(f.plus),false);assert.equal(f.binding.plus,f.plus);
 return replacement;
}

function withSkuLists(f,lists,markers=[]){
 const before=f.context.document.querySelectorAll;
 f.context.document.querySelectorAll=selector=>selector==='.waterfall-list-container_egohG8wQ'?lists:selector==='div,p,span'?markers:before(selector);
}

function skuSearchDomFixture(options={}){
 const f=skuListDomFixture(),otherList=f.element(),otherCard=f.element(),otherTitle=f.element('OTHER CARD');
 otherCard.querySelector=selector=>selector==='.goodsName_dT8mZSNd'?otherTitle:selector==='.goodsImage_fU27dxbk img'?f.cardImage:null;
 f.attach(otherList,[otherCard]);otherList.querySelectorAll=selector=>selector==='.goodsItem_R1ok0MpS'?[otherCard]:[];
 const searchLists=[f.list,otherList],markers=[],proof={calls:0,result:true};
 f.context.location=new URL('https://mobile.yangkeduo.com/mall_search_result.html?mall_id=789&search_key=SYNTHETIC');
 f.context.document.body.innerText='搜索结果';f.binding.sourceUrl=f.context.location.href;
 const prior=f.context.document.querySelectorAll;
 f.context.document.querySelectorAll=selector=>selector==='.waterfall-list-container_NLmKnzfN'?searchLists:selector==='.waterfall-list-container_egohG8wQ'?[]:selector==='div,p,span'?markers:prior(selector);
 f.attach(f.context.document.body,[...searchLists,f.root,f.outside]);
 // Cache tests exercise the frozen-proof interface; the arm integration below
 // supplies the real evidence validator and trusted-click binding.
 const evidence=Object.hasOwn(options,'evidence')?options.evidence:Object.freeze({method:'native_store_search'});
 const verifySearch=Object.hasOwn(options,'verifySearch')?options.verifySearch:()=>{proof.calls++;return proof.result;};
 Object.defineProperties(f.binding,{searchEvidence:{value:evidence},verifySearch:{value:verifySearch}});
 return {...f,searchLists,otherList,otherCard,otherTitle,markers,proof};
}

function armedSkuSearchFixture(){
 const f=skuSearchDomFixture(),key=Symbol.for('pdd-monitor/sku-modal-binding-v1'),title=f.binding.title;
 delete f.context[key];f.root.hidden=true;f.context.innerWidth=400;f.context.window={scrollY:0};
 const matchedImage='https://img-2.pddpic.com/SYNTHETIC_B.png',sales=f.element('已拼26件'),listeners=[];
 f.cardImage.getAttribute=name=>['data-src','src'].includes(name)?matchedImage:null;
 f.attach(f.card,[...f.card.children,sales]);f.card.querySelectorAll=selector=>selector==='.salesTip_qUlLfJr3'?[sales]:selector==='.quantityBtnV2_FM92Tcgf'?[f.plus]:[];
 f.plus.querySelector=selector=>selector==='.addV2_TP_S6XAg'?f.element():null;f.plus.scrollIntoView=()=>{};
 f.plus.getBoundingClientRect=()=>({x:40,y:50,top:50,bottom:80,left:40,right:70,width:30,height:30});
 f.context.document.elementFromPoint=()=>f.plus;
 f.context.document.addEventListener=(event,listener,capture)=>{assert.equal(event,'click');assert.equal(capture,true);listeners.push(listener);};
 f.context.document.removeEventListener=()=>{};
 const searchEvidence={method:'native_store_search',source_store_url:'https://mobile.yangkeduo.com/mall_page.html?mall_id=789',query:'SYNTHETIC',search_page_url:f.context.location.href,page_id:7,
  shop_name_verified:true,native_search_opened:true,native_search_submitted:true,same_tab_navigation_verified:true,
  matching_title:title,original_image_url:urls[1],matched_image_url:matchedImage,matched_card_count:1,live_sales_raw:sales.textContent,live_sales_label:'已拼',live_sales_value:26,live_sales_unit:'件'};
 const run=(fn,args)=>JSON.parse(JSON.stringify(vm.runInNewContext(`(${fn.toString()})(${JSON.stringify(args)})`,f.context)));
 assert.equal(run(armListSkuModal,{token:f.token,title,image:urls[1],shopName:'SYNTHETIC_SHOP',sourceUrl:f.context.location.href,searchEvidence,originalSalesValue:20}).ok,true);
 assert.equal(listeners.length,1);listeners[0]({isTrusted:true,target:f.plus});f.root.hidden=false;
 assert.deepEqual(run(checkListSkuModal,{token:f.token,bind:true}),{ok:true});
 const binding=vm.runInNewContext("globalThis[Symbol.for('pdd-monitor/sku-modal-binding-v1')]",f.context);
 return {...f,binding,sales,matchedImage,searchEvidence,run};
}

test('search SKU cache accepts frozen verified binding across two columns without requiring a body shop name',()=>{
 const f=skuSearchDomFixture();assert.equal(f.context.document.body.innerText.includes(f.binding.shopName),false);
 assert.deepEqual(f.read(),{verified:true,images:[{url:urls[0],src:urls[0]}]});assert.equal(f.proof.calls,1);
 assert.deepEqual(f.read({goodsId:null,imageUrls:[urls[1]],listModalToken:f.token}),{verified:true,images:[]});
 assert.equal(Object.isFrozen(f.binding.searchEvidence),true);assert.equal(Object.getOwnPropertyDescriptor(f.binding,'verifySearch').writable,false);
});

test('search SKU cache requires frozen evidence and a successful synchronous verifier',()=>{
 const cases=[{evidence:{method:'native_store_search'}},{evidence:null},{evidence:undefined},{evidence:'native_store_search'},
  {verifySearch:null},{verifySearch:()=>false},{verifySearch:()=>1},{verifySearch:()=>Promise.resolve(true)},{verifySearch:()=>{throw new Error('SYNTHETIC_SECRET_QUERY');}}];
 for(const options of cases){const f=skuSearchDomFixture(options),observed=f.read();assert.equal(observed.reason,'sku_list_binding_unverified');assert.equal(observed.diagnostics.search_evidence_verified,false);assert.equal(JSON.stringify(observed).includes('SYNTHETIC_SECRET'),false);}
 const plain=skuListDomFixture();plain.context.location.href='https://mobile.yangkeduo.com/mall_search_result.html?mall_id=789';plain.binding.sourceUrl=plain.context.location.href;
 assert.equal(plain.read().reason,'sku_list_binding_unverified','search URL cannot use an ordinary list binding');
 const wrongPath=skuSearchDomFixture();wrongPath.context.location=new URL('https://mobile.yangkeduo.com/mall_page.html?mall_id=789');wrongPath.binding.sourceUrl=wrongPath.context.location.href;
 assert.equal(wrongPath.read().reason,'sku_list_binding_unverified','search proof cannot relax the ordinary mall-page path');
});

test('search SKU evidence never replaces original trusted click, token, card or modal continuity',()=>{
 for(const [name,value] of [['clicked',false],['opened',false],['invalid',true],['token','OTHER_TOKEN']]){const f=skuSearchDomFixture();f.binding[name]=value;assert.equal(f.read().reason,'sku_list_binding_unverified');}
 for(const node of ['card','root']){const f=skuSearchDomFixture();f[node].isConnected=false;assert.equal(f.read().reason,'sku_list_binding_unverified');}
 const replaced=skuSearchDomFixture(),prior=replaced.context.document.querySelectorAll;
 replaced.context.document.querySelectorAll=selector=>selector.startsWith('.sku-plus1')?[replaced.element()]:prior(selector);assert.equal(replaced.read().reason,'sku_list_modal_changed');
});

test('search SKU exact-original matching spans all columns and deduplicates repeated DOM references',()=>{
 const duplicate=skuSearchDomFixture();duplicate.otherCard.querySelector=duplicate.card.querySelector;
 const observed=duplicate.read();assert.equal(observed.reason,'sku_list_card_unverified');assert.equal(observed.diagnostics.matching_card_count,2);
 const repeated=skuSearchDomFixture();repeated.otherList.querySelectorAll=()=>[repeated.card,repeated.otherCard,repeated.card];
 assert.equal(repeated.read().verified,true,'one original DOM node repeated by a query is still one card');
 const wrongImage=skuSearchDomFixture();wrongImage.cardImage.getAttribute=name=>name==='data-src'?urls[1]+'?other=1':urls[1];assert.equal(wrongImage.read().reason,'sku_list_card_unverified');
});

test('search SKU cache requires exactly one owner and never swaps an identical-looking replacement card',()=>{
 const orphan=skuSearchDomFixture();orphan.list.children=[];assert.equal(orphan.read().reason,'sku_list_binding_unverified');
 const nested=skuSearchDomFixture(),outer=nested.element();nested.attach(outer,[nested.list]);nested.searchLists.push(outer);assert.equal(nested.read().reason,'sku_list_binding_unverified');
 const replaced=skuSearchDomFixture(),clone=replaced.element();clone.querySelector=replaced.card.querySelector;replaced.attach(replaced.list,[clone]);replaced.list.querySelectorAll=()=>[clone];assert.equal(replaced.read().reason,'sku_list_binding_unverified');
 const changed=skuSearchDomFixture(),clone2=changed.element();clone2.querySelector=changed.card.querySelector;changed.list.querySelectorAll=()=>[clone2];assert.equal(changed.read().reason,'sku_list_card_unverified');
});

test('search SKU cache preserves recommendation, rendered-card, selected-summary and exact-header-image gates',()=>{
 const hidden=skuSearchDomFixture();hidden.card.hidden=true;assert.equal(hidden.read().reason,'sku_list_card_unverified');
 const recommended=skuSearchDomFixture(),marker=recommended.element('其他店铺的精选推荐');marker.getBoundingClientRect=()=>({top:25,width:300,height:20});recommended.markers.push(marker);assert.equal(recommended.read().reason,'sku_list_card_unverified');
 const stale=skuSearchDomFixture();stale.summary.textContent='已选：B';assert.equal(stale.read().reason,'sku_selection_summary_mismatch');
 const image=skuSearchDomFixture();image.img.currentSrc=urls[0]+'?thumbnail=1';assert.deepEqual(image.read().images,[]);image.img.currentSrc=urls[0];image.img.complete=false;assert.deepEqual(image.read().images,[]);
});

test('search SKU cache rechecks exact URL origin and path even when a verifier reports success',()=>{
 for(const href of ['https://mobile.yangkeduo.com/mall_search_result.html?mall_id=456','https://example.org/mall_search_result.html?mall_id=789','https://user:secret@mobile.yangkeduo.com/mall_search_result.html?mall_id=789','https://mobile.yangkeduo.com:8443/mall_search_result.html?mall_id=789','https://mobile.yangkeduo.com/goods.html?goods_id=123']){
  const f=skuSearchDomFixture();f.context.location=new URL(href);if(!href.includes('mall_id=456'))f.binding.sourceUrl=href;assert.equal(f.read().reason,'sku_list_binding_unverified');
 }
});

test('real search arm and trusted popup binding authenticate only the selected loaded header image',()=>{
 const f=armedSkuSearchFixture();assert.equal(Object.isFrozen(f.binding.searchEvidence),true);assert.equal(f.binding.verifySearch(),true);
 assert.equal(f.binding.searchEvidence.original_image_url,urls[1]);assert.equal(f.binding.imageUrl,f.matchedImage);
 assert.deepEqual(f.read(),{verified:true,images:[{url:urls[0],src:urls[0]}]});
 assert.deepEqual(f.read({goodsId:null,imageUrls:[f.matchedImage,urls[1]],listModalToken:f.token}),{verified:true,images:[]});
 // Search proof is SKU-only; even supplied shop text cannot authorize main-image
 // collection from search columns through the ordinary storefront gate.
 f.context.document.body.innerText='SYNTHETIC_SHOP';
 assert.deepEqual(f.run(readLoadedStoreImages,{shopName:'SYNTHETIC_SHOP'}),{verified:false,images:[]});
});

test('real search binding rejects changed source, title, image, raw sales, owner, node and cross-column image aliases',()=>{
 const changes=[
  f=>{f.context.location.searchParams.set('search_key','OTHER');},
  f=>{f.cardTitle.textContent='OTHER CARD';},
  f=>{f.cardImage.getAttribute=()=>urls[1];},
  f=>{f.sales.textContent='已拼27件';},
  f=>{f.list.children=[];f.attach(f.otherList,[f.otherCard,f.card]);},
  f=>{const clone=f.element();clone.querySelector=f.card.querySelector;f.attach(f.list,[clone]);f.list.querySelectorAll=()=>[clone];},
  f=>{const aliasImage=f.element('',{'data-src':urls[1]});f.otherTitle.textContent=f.binding.title;f.otherCard.querySelector=selector=>selector==='.goodsName_dT8mZSNd'?f.otherTitle:selector==='.goodsImage_fU27dxbk img'?aliasImage:null;},
 ];
 for(const change of changes){const f=armedSkuSearchFixture();change(f);assert.equal(f.binding.verifySearch(),false);const observed=f.read();assert.equal(observed.reason,'sku_list_binding_unverified');assert.equal(observed.diagnostics.search_evidence_verified,false);}
});

test('list SKU image gate accepts the exact original card and bound selected prefixed modal',()=>{
 const f=skuListDomFixture();assert.deepEqual(f.read(),{verified:true,images:[{url:urls[0],src:urls[0]}]});
 assert.deepEqual(f.read({imageUrls:[urls[0]],listModalToken:f.token}),{verified:true,images:[{url:urls[0],src:urls[0]}]});
 assert.deepEqual(f.read({goodsId:null,imageUrls:[urls[1]],listModalToken:f.token}),{verified:true,images:[]});
 f.summary.textContent='已选： A ';assert.equal(f.read().verified,true);
 f.summary.textContent='已选：B';assert.equal(f.read().reason,'sku_selection_summary_mismatch');
});

test('bound SKU image gate accepts a single known rendered current price without aria or an original price',()=>{
 for(const prefixed of [false,true])for(const text of ['¥18.8','￥12.26','券后¥4.50','当前价 ¥18.8']){
  const f=skuListDomFixture();plainCurrentPrice(f,{text,prefixed});
  assert.deepEqual(f.read(),{verified:true,images:[{url:urls[0],src:urls[0]}]});
 }
 const f=skuListDomFixture();plainCurrentPrice(f,{aliases:true});
 assert.equal(f.read().verified,true,'one element with both known classes is not two prices');
});

test('plain SKU price gate rejects ranges, promotions, original prices and unrelated price text',()=>{
 for(const text of ['¥18.8-¥20','¥18.8起','18.8','原价¥18.8','券前¥18.8','¥18.8 ¥20','¥18.8 2件9.5折','','SYNTHETIC_PRIVATE_TEXT']){
  const f=skuListDomFixture();plainCurrentPrice(f,{text});
  const result=f.read();assert.equal(result.reason,'sku_price_label_invalid',text);
  assert.deepEqual(result.diagnostics,{price_container_count:1,visible_price_container_count:1});
  assert.ok(!JSON.stringify(result).includes('SYNTHETIC_PRIVATE_TEXT'));
 }
 const f=skuListDomFixture(),{nodes}=plainCurrentPrice(f);nodes.length=0;
 const unrelated=f.element('¥18.8');f.attach(f.root,[...f.root.children,unrelated]);
 assert.deepEqual(f.read(),{verified:false,images:[],reason:'sku_price_label_missing',diagnostics:{price_container_count:0,visible_price_container_count:0}});
});

test('plain SKU price gate rejects hidden, detached or multiple visible current-price containers',()=>{
 for(const mode of ['hidden','detached','duplicate']){
  const f=skuListDomFixture(),{price,nodes}=plainCurrentPrice(f);
  if(mode==='hidden')price.hidden=true;
  if(mode==='detached')f.attach(f.root,f.root.children.filter(child=>child!==price));
  if(mode==='duplicate'){const other=f.element('¥19.8');nodes.push(other);f.attach(f.root,[...f.root.children,other]);}
  const result=f.read();assert.equal(result.reason,mode==='duplicate'?'sku_price_label_ambiguous':'sku_price_label_hidden');
  assert.deepEqual(result.diagnostics,{price_container_count:mode==='duplicate'?2:1,visible_price_container_count:mode==='duplicate'?2:0});
 }
});

test('plain current price cannot bypass stale selection, hidden legacy label or exact header image checks',()=>{
 const f=skuListDomFixture();plainCurrentPrice(f);
 f.summary.textContent='已选：B';assert.equal(f.read().reason,'sku_selection_summary_mismatch');
 f.summary.textContent='已选：A';f.b.className='skuSpecValueSelected_oQDDpea9';assert.equal(f.read().reason,'sku_selected_option_unverified');
 f.b.className='skuSpecValue_F7sZG3xe';f.img.currentSrc=urls[1];assert.deepEqual(f.read().images,[]);
 f.img.currentSrc=urls[0];f.img.complete=false;assert.deepEqual(f.read().images,[]);
 const query=f.root.querySelector;f.price.hidden=true;
 f.root.querySelector=selector=>selector==='.skuQuantityPriceDesc_ujEqGzEB [aria-label]'?f.price:query(selector);
 assert.equal(f.read().reason,'sku_price_label_hidden','a hidden legacy label must not fall through to another node');
});

test('list SKU binding fails closed for absent/wrong token, mixed goods scope and incomplete click binding',()=>{
 const f=skuListDomFixture();
 for(const listModalToken of [null,'','WRONG_TOKEN'])assert.equal(f.read({goodsId:null,imageUrls:[urls[0]],listModalToken}).verified,false);
 assert.equal(f.read({goodsId:'123',imageUrls:[urls[0]],listModalToken:f.token}).reason,'sku_request_invalid');
 assert.equal(f.read({goodsId:null,imageUrls:[urls[0]]}).verified,false);
 for(const field of ['clicked','opened']){f.binding[field]=false;assert.equal(f.read().reason,'sku_list_binding_unverified');f.binding[field]=true;}
 f.binding.invalid=true;assert.equal(f.read().reason,'sku_list_binding_unverified');f.binding.invalid=false;
 delete f.context[Symbol.for('pdd-monitor/sku-modal-binding-v1')];assert.equal(f.read().reason,'sku_list_binding_unverified');
});

test('list SKU binding rejects changed shop URL/name and cannot authenticate a detail page',()=>{
 const cases=['https://mobile.yangkeduo.com/mall_page.html?mall_id=456','http://mobile.yangkeduo.com/mall_page.html?mall_id=789','https://evil.test/mall_page.html?mall_id=789','https://mobile.yangkeduo.com/mall_search_result.html?mall_id=789','https://mobile.yangkeduo.com/goods.html?goods_id=123'];
 for(const href of cases){const f=skuListDomFixture();f.context.location.href=href;assert.equal(f.read().reason,'sku_list_binding_unverified');}
 const f=skuListDomFixture();f.context.document.body.innerText='OTHER_SHOP';assert.equal(f.read().reason,'sku_list_binding_unverified');
});

test('list SKU binding accepts a replaced plus after its recorded trusted click while the original card and modal remain',()=>{
 const f=skuListDomFixture();rerenderListPlus(f);
 const result=f.read();assert.equal(result.verified,true);assert.deepEqual(result.images,[{url:urls[0],src:urls[0]}]);
 for(const [field,value] of [['clicked',false],['opened',false],['invalid',true],['token','OTHER_TOKEN']]){
  const prior=f.binding[field];f.binding[field]=value;assert.equal(f.read().reason,'sku_list_binding_unverified');f.binding[field]=prior;
 }
 assert.equal(f.read({goodsId:null,imageUrls:[urls[0]],listModalToken:'WRONG_TOKEN'}).reason,'sku_list_binding_unverified');
});

test('list SKU binding rejects detached card or modal, replaced modals and duplicate original cards',()=>{
 for(const name of ['card','root']){const f=skuListDomFixture();f[name].isConnected=false;assert.equal(f.read().reason,'sku_list_binding_unverified');}
 for(const count of [0,1,2]){
  const f=skuListDomFixture(),before=f.context.document.querySelectorAll,other=f.element();other.isConnected=true;
  f.context.document.querySelectorAll=selector=>selector.startsWith('.sku-plus1')?(count===0?[]:count===1?[other]:[f.root,other]):before(selector);
  assert.equal(f.read().reason,'sku_list_modal_changed');
 }
 const f=skuListDomFixture(),duplicate=f.element();duplicate.querySelector=f.card.querySelector;
 f.list.querySelectorAll=()=>[f.card,duplicate];assert.equal(f.read().reason,'sku_list_card_unverified');
});

test('list SKU binding selects the unique container owning the bound card despite unrelated hidden and recommendation lists',()=>{
 const f=skuListDomFixture(),hidden=f.element(),recommendations=f.element(),recommendedCard=f.element(),marker=f.element('其他店铺的精选推荐');
 rerenderListPlus(f);hidden.hidden=true;hidden.querySelectorAll=()=>[];
 recommendations.getBoundingClientRect=()=>({top:450,bottom:850,width:300,height:400});recommendedCard.getBoundingClientRect=()=>({top:500,bottom:800,width:300,height:300});
 recommendedCard.querySelector=f.card.querySelector;f.attach(recommendations,[recommendedCard]);recommendations.querySelectorAll=()=>[recommendedCard];
 marker.getBoundingClientRect=()=>({top:400,bottom:420,width:300,height:20});
 withSkuLists(f,[recommendations,hidden,f.list],[marker]);
 const result=f.read();assert.equal(result.verified,true);assert.deepEqual(result.images,[{url:urls[0],src:urls[0]}]);assert.equal(f.binding.card,f.card);
});

test('list SKU binding rejects zero or multiple containing lists and never substitutes an identical replacement card',()=>{
 const detached=skuListDomFixture();rerenderListPlus(detached);detached.list.children=[];detached.card.parentElement=null;
 assert.equal(detached.card.isConnected,true);assert.equal(detached.read().reason,'sku_list_binding_unverified');
 const nested=skuListDomFixture(),outer=nested.element();nested.attach(outer,[nested.list]);withSkuLists(nested,[outer,nested.list]);
 assert.equal(nested.read().reason,'sku_list_binding_unverified');
 const replaced=skuListDomFixture(),sameLooking=replaced.element();sameLooking.isConnected=true;sameLooking.querySelector=replaced.card.querySelector;
 replaced.attach(replaced.list,[sameLooking]);replaced.list.querySelectorAll=()=>[sameLooking];replaced.card.parentElement=null;
 assert.equal(replaced.card.isConnected,true);assert.equal(replaced.read().reason,'sku_list_binding_unverified');
 const retained=skuListDomFixture(),other=retained.element();other.querySelector=retained.card.querySelector;retained.list.querySelectorAll=()=>[other];
 assert.equal(retained.read().reason,'sku_list_card_unverified');
});

test('same-title recommendation cards cannot replace the original or make a valid pre-boundary original ambiguous',()=>{
 const f=skuListDomFixture(),recommended=f.element(),marker=f.element('其他店铺的精选推荐');
 recommended.querySelector=f.card.querySelector;recommended.isConnected=true;recommended.getBoundingClientRect=()=>({top:500,bottom:800,width:300,height:300});
 marker.getBoundingClientRect=()=>({top:400,bottom:420,width:300,height:20});f.attach(f.list,[f.card,recommended]);f.list.querySelectorAll=()=>[f.card,recommended];
 withSkuLists(f,[f.list],[marker]);assert.equal(f.read().verified,true);
 f.card.getBoundingClientRect=()=>({top:500,bottom:800,width:300,height:300});assert.equal(f.read().reason,'sku_list_card_unverified');
});

test('list SKU image binding requires exact original title/image and excludes recommendations',()=>{
 const title=skuListDomFixture();title.cardTitle.textContent='SYNTHETIC原卡';assert.equal(title.read().reason,'sku_list_card_unverified');
 const image=skuListDomFixture();image.cardImage.getAttribute=name=>name==='data-src'?urls[1]+'?thumbnail=1':urls[1];assert.equal(image.read().reason,'sku_list_card_unverified');
 const recommendation=skuListDomFixture(),marker=recommendation.element('其他店铺的精选推荐');
 marker.getBoundingClientRect=()=>({top:25,bottom:45,width:300,height:20});recommendation.list.getBoundingClientRect=()=>({top:0,bottom:300,width:300,height:300});
 const before=recommendation.context.document.querySelectorAll;recommendation.context.document.querySelectorAll=selector=>selector==='div,p,span'?[marker]:before(selector);
 assert.equal(recommendation.read().reason,'sku_list_card_unverified');
});

test('prefixed modal does not infer unknown selected classes or accept unloaded/wrong selected images',()=>{
 const f=skuListDomFixture();f.a.className='skuSpecValue_F7sZG3xe skuSpecValueSelected_UNKNOWN';assert.equal(f.read().reason,'sku_selected_option_unverified');
 f.a.className='skuSpecValueSelected_oQDDpea9';f.b.className='skuSpecValueSelected_oQDDpea9';assert.equal(f.read().reason,'sku_selected_option_unverified');
 f.b.className='skuSpecValue_F7sZG3xe';f.img.complete=false;assert.deepEqual(f.read().images,[]);
 f.img.complete=true;f.img.currentSrc=urls[0]+'?thumbnail=1';assert.deepEqual(f.read().images,[]);
});

test('SKU image gate binds only loaded header image of corroborated current selection',()=>{
 const f=skuDomFixture();assert.deepEqual(f.read(),{verified:true,images:[{url:urls[0],src:urls[0]}]});
 assert.deepEqual(f.read({goodsId:'123',imageUrls:[urls[1]]}),{verified:true,images:[]});
 // No stripping image transformations or guessing the original URL.
 f.img.currentSrc=urls[0]+'?size=375';assert.deepEqual(f.read().images,[]);
 f.img.src=f.img.currentSrc;assert.deepEqual(f.read().images,[]);
 assert.equal(f.read({goodsId:'123',imageUrls:[f.img.currentSrc]}).images.length,1);
});

test('SKU image gate rejects other goods, duplicate ID, foreign URL and shop pages',()=>{
 const f=skuDomFixture();for(const href of ['https://mobile.yangkeduo.com/goods.html?goods_id=456','https://mobile.yangkeduo.com/goods.html?goods_id=123&goods_id=456','https://evil.test/goods.html?goods_id=123','https://mobile.yangkeduo.com/mall_page.html?goods_id=123']){
  f.context.location.href=href;assert.equal(f.read().verified,false);
 }
});

test('SKU image gate rejects stale or ambiguous selection and never uses an outside image',()=>{
 const f=skuDomFixture();f.summary.textContent='已选：B';assert.equal(f.read().verified,false);
 f.summary.textContent='已选：A';f.b.className+=' hr353bdX';assert.equal(f.read().verified,false);
 f.b.className='F7sZG3xe';f.img.hidden=true;assert.deepEqual(f.read().images,[]);
 f.img.hidden=false;f.img.complete=false;assert.deepEqual(f.read().images,[]);
 f.img.complete=true;f.img.naturalWidth=0;assert.deepEqual(f.read().images,[]);
 f.img.naturalWidth=375;f.root.children=f.root.children.filter(child=>child!==f.img);assert.deepEqual(f.read().images,[]);
});

test('SKU gate gives fixed reasons for modal, selection and price-label rejection without page content',()=>{
 const cases=[
  ['sku_modal_hidden',f=>{f.root.hidden=true;}],
  ['sku_modal_outside_viewport',f=>{f.root.getBoundingClientRect=()=>({top:900,bottom:1200,width:300,height:300});}],
  ['sku_selected_option_unverified',f=>{f.a.className='F7sZG3xe';}],
  ['sku_selection_summary_hidden',f=>{f.summary.hidden=true;}],
  ['sku_selection_summary_mismatch',f=>{f.summary.textContent='SYNTHETIC_PRIVATE_PAGE_TEXT';}],
  ['sku_price_label_hidden',f=>{f.price.hidden=true;}],
  ['sku_price_label_invalid',f=>{f.price.getAttribute=()=> 'SYNTHETIC_PRIVATE_PAGE_TEXT';}],
 ];
 for(const [reason,change] of cases){const f=skuDomFixture();change(f);assert.deepEqual(f.read(),{verified:false,images:[],reason});}
 for(const [selector,reason] of [['.Mbx2m60G','sku_selection_summary_missing'],['.ujEqGzEB [aria-label]','sku_price_label_missing']]){
  const f=skuDomFixture(),original=f.root.querySelector;f.root.querySelector=value=>value===selector?null:original(value);
  assert.deepEqual(f.read(),{verified:false,images:[],reason,...(reason==='sku_price_label_missing'?{diagnostics:{price_container_count:0,visible_price_container_count:0}}:{})});
 }
 const identity=skuDomFixture();identity.context.location.href='https://mobile.yangkeduo.com/goods.html?goods_id=456';
 assert.deepEqual(identity.read(),{verified:false,images:[],reason:'sku_goods_identity_unverified'});
});

test('SKU collector requests only selected observed URL using existing page response body',async()=>fixture(async env=>{
 const report=await env.cache.collectSku({goodsId:'123',imageUrls:[urls[1]]});assert.equal(report.newlySaved,1);assert.equal(report.observed,1);
 assert.equal(env.evaluations[0].fn,readLoadedSkuImages);assert.deepEqual(env.evaluations[0].args,{goodsId:'123',imageUrls:[urls[1]]});
 assert.deepEqual(env.calls.filter(c=>c.name==='get_network_request').map(c=>c.args.reqid),[2]);
 assert.deepEqual(Object.keys(await env.cache.flush()),[urls[1]]);assert.ok(!JSON.stringify(await env.cache.flush()).includes('SYNTHETIC_SECRET'));
}));

test('list SKU collector forwards binding token and preserves exact response-only image selection',async()=>fixture(async env=>{
 const args={goodsId:null,imageUrls:[urls[1]],listModalToken:'SYNTHETIC_MODAL_TOKEN'};
 const report=await env.cache.collectSku(args);assert.equal(report.newlySaved,1);
 assert.equal(env.evaluations[0].fn,readLoadedSkuImages);assert.deepEqual(env.evaluations[0].args,args);
 assert.deepEqual(env.calls.filter(c=>c.name==='get_network_request').map(c=>c.args.reqid),[2]);
 assert.deepEqual(Object.keys(await env.cache.flush()),[urls[1]]);
}));

test('rerendered plus and extra lists still save actual selected SKU response bytes after the full DOM gate',async()=>fixture(async env=>{
 const f=skuListDomFixture(),unrelated=f.element();rerenderListPlus(f);unrelated.querySelectorAll=()=>[];withSkuLists(f,[unrelated,f.list]);
 const cache=createLoadedImageCache({project:env.project,getPageId:()=>7,evaluate:async(fn,args)=>{assert.equal(fn,readLoadedSkuImages);return f.read(args);},callTool:(...args)=>env.callTool(...args)});
 const report=await cache.collectSku({goodsId:null,imageUrls:[urls[0]],listModalToken:f.token});
 assert.equal(report.observed,1);assert.equal(report.newlySaved,1);assert.equal(report.missing,0);assert.equal(report.byteCount,png.length);
 assert.deepEqual(env.calls.map(call=>call.name),['list_network_requests','get_network_request']);assert.equal(env.calls[1].args.pageId,7);assert.equal(env.calls[1].args.reqid,1);
 const saved=await cache.flush();assert.deepEqual(Object.keys(saved),[urls[0]]);assert.equal(saved[urls[0]].mime,'image/png');assert.ok(Buffer.from(saved[urls[0]].base64,'base64').equals(png));
 assert.equal(saved[urls[1]],undefined);assert.ok(!JSON.stringify({report,saved}).includes('SYNTHETIC_SECRET'));assert.deepEqual(await fs.readdir(path.join(env.project,'state/local_collection')),[]);
}));

test('real search binding saves exact SKU response bytes then blocks changed evidence before any further network access',async()=>fixture(async env=>{
 const f=armedSkuSearchFixture(),cache=createLoadedImageCache({project:env.project,getPageId:()=>7,evaluate:async(fn,args)=>{assert.equal(fn,readLoadedSkuImages);return f.read(args);},callTool:(...args)=>env.callTool(...args)});
 const args={goodsId:null,imageUrls:[urls[0]],listModalToken:f.token},report=await cache.collectSku(args);
 assert.equal(report.newlySaved,1);assert.equal(report.observed,1);assert.equal(report.missing,0);assert.equal(report.byteCount,png.length);
 assert.deepEqual(env.calls.map(call=>call.name),['list_network_requests','get_network_request']);assert.equal(env.calls[1].args.reqid,1);
 const saved=await cache.flush();assert.deepEqual(Object.keys(saved),[urls[0]]);assert.equal(saved[urls[0]].mime,'image/png');assert.ok(Buffer.from(saved[urls[0]].base64,'base64').equals(png));
 env.calls.length=0;f.sales.textContent='已抢26件';const rejected=await cache.collectSku(args);
 assert.deepEqual(rejected.reasons,{sku_dom_unverified:1,sku_list_binding_unverified:1});assert.equal(rejected.domDiagnostics.search_evidence_verified,false);assert.equal(env.calls.length,0);
 assert.ok(!JSON.stringify({report,saved,rejected}).includes('SYNTHETIC_SECRET'));assert.deepEqual(await fs.readdir(path.join(env.project,'state/local_collection')),[]);
}));

test('plain current-price modal saves only the exact loaded SKU response and rejects ambiguous price before network access',async()=>fixture(async env=>{
 const f=skuListDomFixture(),{nodes}=plainCurrentPrice(f);
 const cache=createLoadedImageCache({project:env.project,getPageId:()=>7,evaluate:async(fn,args)=>{assert.equal(fn,readLoadedSkuImages);return f.read(args);},callTool:(...args)=>env.callTool(...args)});
 const args={goodsId:null,imageUrls:[urls[0]],listModalToken:f.token};
 const result=await cache.collectSku(args);assert.equal(result.newlySaved,1);assert.equal(result.observed,1);
 const saved=await cache.flush();assert.deepEqual(Object.keys(saved),[urls[0]]);assert.ok(Buffer.from(saved[urls[0]].base64,'base64').equals(png));
 assert.deepEqual(env.calls.map(call=>call.name),['list_network_requests','get_network_request']);
 env.calls.length=0;
 const extra=f.element('SYNTHETIC_PRIVATE_TEXT');nodes.push(extra);f.attach(f.root,[...f.root.children,extra]);
 const rejected=await cache.collectSku(args);
 assert.deepEqual(rejected.reasons,{sku_dom_unverified:1,sku_price_label_ambiguous:1});
 assert.deepEqual(rejected.domDiagnostics,{price_container_count:2,visible_price_container_count:2});
 assert.equal(env.calls.length,0);assert.ok(!JSON.stringify(rejected).includes('SYNTHETIC_PRIVATE_TEXT'));
}));

test('list binding failure never reads network bodies and exposes only a fixed reason',async()=>fixture(async env=>{
 env.dom={verified:false,images:[],reason:'sku_list_modal_changed'};
 const report=await env.cache.collectSku({goodsId:null,imageUrls:[urls[0]],listModalToken:'SYNTHETIC_MODAL_TOKEN'});
 assert.deepEqual(report.reasons,{sku_dom_unverified:1,sku_list_modal_changed:1});assert.equal(env.calls.length,0);
}));

test('unverified SKU scope and unobserved image never read a response body',async()=>fixture(async env=>{
 env.dom.verified=false;assert.equal((await env.cache.collectSku({goodsId:'123',imageUrls:[urls[0]]})).reasons.sku_dom_unverified,1);assert.equal(env.calls.length,0);
 env.dom={verified:true,images:[]};assert.equal((await env.cache.collectSku({goodsId:'123',imageUrls:[urls[0]]})).reasons.sku_image_not_loaded,1);assert.equal(env.calls.length,0);
 assert.deepEqual(await env.cache.flush(),{});
}));

test('SKU failure keeps legacy count plus only a whitelisted gate reason',async()=>fixture(async env=>{
 env.dom={verified:false,images:[],reason:'sku_price_label_hidden'};
 const args={goodsId:'123',imageUrls:[urls[0]]};
 assert.deepEqual((await env.cache.collectSku(args)).reasons,{sku_dom_unverified:1,sku_price_label_hidden:1});
 env.dom.reason='SYNTHETIC_SECRET_HTML_OR_COOKIE';const report=await env.cache.collectSku(args);
 assert.deepEqual(report.reasons,{sku_dom_unverified:1});assert.ok(!JSON.stringify(report).includes('SYNTHETIC_SECRET'));assert.equal(env.calls.length,0);
 // A storefront DOM response cannot attach SKU-only diagnostic counters.
 env.dom.reason='sku_price_label_hidden';assert.deepEqual((await env.cache.collect({shopName:'SYNTHETIC_SHOP'})).reasons,{shop_dom_unverified:1});
}));

test('SKU diagnostics expose only fixed boolean and bounded integer fields and never leak into storefront collection',async()=>fixture(async env=>{
 const args={goodsId:null,imageUrls:[urls[0]],listModalToken:'SYNTHETIC_MODAL_TOKEN'};
 env.dom={verified:false,images:[],reason:'sku_list_binding_unverified',diagnostics:{
  binding_present:true,card_connected:false,search_evidence_verified:true,list_count:3,owner_list_count:1,matching_card_count:10000,
  source_matches:'true',token_matches:1,card_rendered:null,original_card_matched:[],modal_opened:{cookie:'SYNTHETIC_SECRET'},
  source_url:'https://private.example/SYNTHETIC_SECRET',token:'SYNTHETIC_SECRET',raw_text:'SYNTHETIC_SECRET',unknown_flag:true,unknown_count:7,
 }};
 const report=await env.cache.collectSku(args);
 assert.deepEqual(report.reasons,{sku_dom_unverified:1,sku_list_binding_unverified:1});
 assert.deepEqual(report.domDiagnostics,{binding_present:true,card_connected:false,search_evidence_verified:true,list_count:3,owner_list_count:1,matching_card_count:10000});
 assert.ok(!JSON.stringify(report).includes('SYNTHETIC_SECRET'));
 for(const value of [-1,10001,0.5,Infinity,NaN,'1',true,null]){
  env.dom.diagnostics.owner_list_count=value;assert.equal((await env.cache.collectSku(args)).domDiagnostics.owner_list_count,undefined);
 }
 env.dom.reason='UNKNOWN_SYNTHETIC_SECRET';assert.equal((await env.cache.collectSku(args)).domDiagnostics,undefined);
 env.dom.reason='sku_list_binding_unverified';
 const storefront=await env.cache.collect({shopName:'SYNTHETIC_SHOP'});assert.deepEqual(storefront.reasons,{shop_dom_unverified:1});assert.equal(storefront.domDiagnostics,undefined);
 assert.ok(!JSON.stringify(storefront).includes('SYNTHETIC_SECRET'));assert.equal(env.calls.length,0);assert.deepEqual(await env.cache.flush(),{});
}));

test('SKU image MIME mismatch is refused without alternate image download or retry',async()=>fixture(async env=>{
 const original=env.callTool;env.callTool=async(name,args)=>{const value=await original(name,args);if(name==='get_network_request')value.structuredContent.networkRequest.responseHeaders['content-type']='image/jpeg';return value;};
 const args={goodsId:'123',imageUrls:[urls[0]]};const report=await env.cache.collectSku(args);assert.equal(report.reasons.image_mime_mismatch,1);assert.equal(report.newlySaved,0);
 await env.cache.collectSku(args);assert.equal(env.calls.filter(c=>c.name==='get_network_request').length,1);assert.deepEqual(await env.cache.flush(),{});
}));

test('discarding persisted image bytes renews bounded cache space for next product',async()=>fixture(async env=>{
 const first=await env.cache.collectSku({goodsId:'123',imageUrls:[urls[0]]});assert.equal(first.newlySaved,1);assert.equal(first.byteCount,png.length);
 const saved=await env.cache.flush();assert.deepEqual(env.cache.discard(),{discarded:true,imageCount:1,byteCount:png.length,reason:null});
 assert.equal(env.cache.images.size,0);assert.deepEqual(await env.cache.flush(),{});assert.equal(saved[urls[0]].base64,png.toString('base64'));
 const second=await env.cache.collectSku({goodsId:'456',imageUrls:[urls[1]]});assert.equal(second.newlySaved,1);assert.equal(second.byteCount,png.length);assert.equal(second.reasons.image_total_limit,undefined);
 assert.deepEqual(Object.keys(await env.cache.flush()),[urls[1]]);assert.equal(env.calls.filter(c=>c.name==='get_network_request').length,2);
},{maxTotalBytes:png.length}));

test('discarding memory does not erase failed body attempts or allow a retry',async()=>fixture(async env=>{
 const original=env.callTool;env.callTool=async(name,args)=>{const value=await original(name,args);if(name==='get_network_request')value.structuredContent.networkRequest.responseHeaders['content-type']='text/html';return value;};
 const args={goodsId:'123',imageUrls:[urls[0]]};assert.equal((await env.cache.collectSku(args)).reasons.image_mime_mismatch,1);
 assert.equal(env.cache.discard().discarded,true);const second=await env.cache.collectSku(args);assert.equal(second.reasons.body_already_attempted,1);assert.equal(env.calls.filter(c=>c.name==='get_network_request').length,1);
 assert.deepEqual(await env.cache.flush(),{});
}));

test('discard refuses during in-flight image collection without changing existing bytes',async()=>fixture(async env=>{
 await env.cache.collectSku({goodsId:'123',imageUrls:[urls[0]]});
 let release,entered;const waiting=new Promise(resolve=>{entered=resolve;}),gate=new Promise(resolve=>{release=resolve;}),original=env.callTool;
 env.callTool=async(name,args)=>{if(name==='list_network_requests'){entered();await gate;}return original(name,args);};
 const running=env.cache.collectSku({goodsId:'456',imageUrls:[urls[1]]});await waiting;
 assert.deepEqual(env.cache.discard(),{discarded:false,imageCount:1,byteCount:png.length,reason:'image_collection_busy'});assert.deepEqual(Object.keys(await env.cache.flush()),[urls[0]]);
 release();assert.equal((await running).newlySaved,1);assert.equal(env.cache.discard().imageCount,2);assert.deepEqual(await env.cache.flush(),{});
}));
