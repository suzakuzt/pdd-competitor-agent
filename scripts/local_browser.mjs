// Ordinary Chrome via official autoConnect. No profile, UA or viewport changes.
import fs from 'node:fs/promises';
import path from 'node:path';
import {startChromeMcp,ChromeMcpError} from './chrome_mcp_client.mjs';
import {ensureNormalChrome} from './normal_chrome.mjs';
import {createLoadedImageCache,saveMcpScreenshot} from './loaded_image_cache.mjs';
import {shopIdentityFromUrl} from './live_capture_driver.mjs';
export const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export class ReviewError extends Error {constructor(message,status='manual_review',reason=null){super(message);this.status=status;this.reason=reason;}}
const connectionDetails=new Set(['connection_timeout','connection_lost','connection_required','connection_busy','runtime_unavailable','unsupported_server','invalid_response','protocol_error','browser_tool_failed','unsupported_tool']);
const connectionTools=new Set(['list_pages','select_page','new_page','navigate_page','evaluate_script','click_at','take_snapshot','fill','press_key','take_screenshot','close_page','list_network_requests','get_network_request']);
function connectionError(cause,{initial=false,operation=null}={}){
 const code=cause instanceof ChromeMcpError?cause.code:cause instanceof ReviewError?cause.connection_detail:null;
 const detail=connectionDetails.has(code)?code:'connection_required';
 const tool=operation||cause?.connection_diagnostics?.tool;
 const message=detail==='connection_busy'?'上一次采集连接仍在使用或尚未安全结束，已停止新任务；请先检查原采集任务。':initial
  ?detail==='connection_timeout'?'等待 Chrome 连接已超时；如果已开启远程调试，请检查 Chrome 中本次连接的“允许”提示，确认后重新采集。':'请在你平时使用的 Chrome 打开 chrome://inspect/#remote-debugging，启用远程调试并允许本次连接，然后重新采集。'
  :detail==='connection_timeout'?'Chrome 页面操作已超时，本轮已停止，已保存的数据保留。请检查当前页面后重新开始采集。'
   :detail==='connection_lost'?'与 Chrome 的连接已中断，本轮已停止，已保存的数据保留。请检查当前 Chrome 后重新开始采集。'
    :'Chrome 页面操作连接未完成，本轮已停止，已保存的数据保留。请检查当前 Chrome 后重新开始采集。';
 const error=new ReviewError(message,'needs_browser','connection_required');error.connection_detail=detail;error.connection_stage=initial?'initial':'operation';
 error.connection_diagnostics=Object.freeze({tool:connectionTools.has(tool)?tool:null,detail});return error;
}
const operationError=()=>new ReviewError('Chrome 页面操作未完成；已停止本轮，请检查当前店铺页面后重试。','manual_review','browser_operation_failed');
const searchInputError=()=>new ReviewError('店内搜索框未能保持唯一、可操作和同页绑定，已停止本轮。','manual_review','sku_search_input_unverified');
const searchSelector='input.search-box-view-main_Y_ywSOkD[type="search"]',searchPlaceholder='输入商品名称';
// Only the public input DOM is inspected. Native MCP fill/press perform edits.
function checkSearchInput({selector,expectedPlaceholder,sourceUrl,text,checkValue=false,checkElement=false},element){
 if(location.href!==sourceUrl||location.origin!=='https://mobile.yangkeduo.com')return false;
 const inputs=[...document.querySelectorAll(selector)];if(inputs.length!==1)return false;
 const input=inputs[0];
 if(input.tagName!=='INPUT'||input.type!=='search'||input.getAttribute('placeholder')!==expectedPlaceholder||!input.isConnected||input.disabled||input.readOnly||input.getAttribute('aria-disabled')==='true')return false;
 if(checkElement&&input!==element)return false;
 for(let e=input;e;e=e.parentElement){const s=getComputedStyle(e);if(e.hidden||e.getAttribute('aria-hidden')==='true'||s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
 const r=input.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
 if(!(r.width>0&&r.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight)||document.elementFromPoint(x,y)!==input)return false;
 return !checkValue||(input.value===text&&document.activeElement===input);
}
function searchInputUid(snapshot){
 const pending=[snapshot],inputs=[];let visited=0;
 while(pending.length){
  const node=pending.pop();if(!node||typeof node!=='object'||Array.isArray(node)||++visited>20000)throw searchInputError();
  if(['searchbox','textbox'].includes(node.role))inputs.push(node);
  if(node.children!==undefined){if(!Array.isArray(node.children)||node.children.length+pending.length+visited>20000)throw searchInputError();pending.push(...node.children);}
 }
 const named=inputs.filter(node=>node.name===searchPlaceholder),candidates=named.length?named:inputs;
 if(candidates.length!==1||typeof candidates[0].id!=='string'||!/^\d+_\d+$/.test(candidates[0].id)||candidates[0].id.length>80)throw searchInputError();
 return candidates[0].id;
}
function publicPageUrl(raw){
 let url;try{url=new URL(raw);}catch{throw new ReviewError('请输入本店公开分享链接。','needs_url','entry_unavailable');}
 if(url.protocol!=='https:'||url.hostname!=='mobile.yangkeduo.com'||url.port||url.username||url.password||!/^\/(mall_page|goods|goods1)\.html$/.test(url.pathname))throw new ReviewError('拒绝非拼多多商品或店铺地址。','needs_url','entry_unavailable');return url.href;
}
function pagesFrom(result){const pages=result?.structuredContent?.pages;if(!Array.isArray(pages)||pages.some(p=>!Number.isSafeInteger(p.id)||typeof p.url!=='string'))throw operationError();return pages;}
function storefrontIdentity(raw){
 try{const url=new URL(publicPageUrl(raw));return url.pathname==='/mall_page.html'?shopIdentityFromUrl(url.href):null;}catch{return null;}
}
const sameStore=(actual,expected)=>!!actual&&!!expected&&actual.origin===expected.origin&&actual.identity_kind===expected.identity_kind&&actual.stable_identifier===expected.stable_identifier;
function publicStorefrontProbeRedirect(requested,actual){
 // Resolving an opaque share is only public-page inspection. It does not
 // establish a shop identity or authorize product collection on that page.
 const entry=new URL(requested);
 return entry.pathname==='/mall_page.html'&&!entry.hash&&entry.searchParams.size===1&&entry.searchParams.has('ps')
  &&/^[A-Za-z0-9_-]{1,128}$/.test(entry.searchParams.get('ps'))&&!!storefrontIdentity(actual);
}
const skuProbeReasons=new Set(['qualified','verified_storefront_resume','invalid_url','source_mismatch','identity_ambiguous','identity_mismatch','original_card_missing','document_unavailable','shop_name_mismatch','login_required','access_restricted','visible_sku_modal','no_storefront_list','card_not_found','original_image_mismatch','card_hidden','card_after_boundary','card_ambiguous','non_primary_list','source_list_ambiguous','source_list_missing','source_sort_unverified']);
// Serialized DOM-only probe, before selecting an existing single-SKU tab. It
// neither scrolls nor touches site state, and never reads an unrelated shop.
export function readSkuStorefrontCandidate({title,image,shopName,identity}){
 const diagnostics={matchingCardCount:null,visibleModalCount:null,listCount:null,sourceVerified:false,latestSelected:null,loadedCardCount:null};
 const reject=reason=>({verified:false,url:location.href,reason,...diagnostics});let url;
 try{url=new URL(location.href);}catch{return reject('invalid_url');}
 if(!identity||url.origin!==identity.origin||url.origin!=='https://mobile.yangkeduo.com'||url.username||url.password||url.port||url.pathname!=='/mall_page.html')return reject('source_mismatch');
 const values={};
 for(const key of ['mall_id','mall_sn']){const all=url.searchParams.getAll(key);if(all.some(value=>!value||value!==value.trim())||new Set(all).size>1)return reject('identity_ambiguous');values[key]=all[0]??null;}
 const kind=values.mall_id!==null?'mall_id':'mall_sn';
 if(kind!==identity.identity_kind||values[kind]!==identity.stable_identifier)return reject('identity_mismatch');
 if([title,image,shopName].some(value=>typeof value!=='string'||!value.trim()))return reject('original_card_missing');
 const text=document.body?.innerText;if(typeof text!=='string')return reject('document_unavailable');
 if(!text.includes(shopName))return reject('shop_name_mismatch');
 if(/login\.html|login\.yangkeduo|请先登录|登录后查看|验证码登录|短信登录|手机号码登录/.test(url.href+' '+text))return reject('login_required');
 if(/滑动.*验证|请完成.*验证|安全验证|访问受限|操作过于频繁/.test(text))return reject('access_restricted');
 const rendered=element=>{
  const r=element.getBoundingClientRect();if(!(r.width>0&&r.height>0))return false;
  for(let e=element;e;e=e.parentElement){const s=getComputedStyle(e);if(e.hidden||e.getAttribute?.('aria-hidden')==='true'||s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
  return true;
 };
 diagnostics.visibleModalCount=[...document.querySelectorAll('.sku-plus1 [role="dialog"][aria-modal="true"]')].filter(rendered).length;
 if(diagnostics.visibleModalCount)return reject('visible_sku_modal');
 let cutoff=Infinity;
 for(const e of document.querySelectorAll('div,p,span'))if(!e.children.length&&['本店暂无更多商品','其他店铺的精选推荐'].includes(e.textContent.trim())&&rendered(e))cutoff=Math.min(cutoff,e.getBoundingClientRect().top);
 const lists=[...document.querySelectorAll('.waterfall-list-container_egohG8wQ')],matches=[];let titles=0,exact=0,visible=0;
 diagnostics.listCount=lists.length;diagnostics.matchingCardCount=0;if(!lists.length)return reject('no_storefront_list');
 for(const list of lists)for(const card of list.querySelectorAll('.goodsItem_R1ok0MpS')){
  const img=card.querySelector('.goodsImage_fU27dxbk img');
  if(card.querySelector('.goodsName_dT8mZSNd')?.textContent.trim()!==title)continue;titles++;
  if((img?.getAttribute('data-src')||img?.getAttribute('src'))!==image)continue;exact++;
  if(!rendered(card))continue;visible++;
  if(card.getBoundingClientRect().top<cutoff)matches.push({list,card});
 }
 // The subsequent fixed locator uses the first storefront list. Require that
 // same original container here, with no duplicate matching card before cutoff.
 diagnostics.matchingCardCount=matches.length;
 if(!matches.length){
  // A missing original card is not a failed shop identity check. A strictly
  // verified, already loaded latest list may continue scrolling, but this
  // weaker proof must never authorize SKU reading or bypass sorting checks.
  if(exact)return reject(visible?'card_after_boundary':'card_hidden');
  const owners=lists.filter(list=>rendered(list)&&list.getBoundingClientRect().top<cutoff);
  if(owners.length!==1||owners[0]!==lists[0])return reject('source_list_ambiguous');
  diagnostics.loadedCardCount=[...lists[0].querySelectorAll('.goodsItem_R1ok0MpS')].filter(card=>rendered(card)&&card.getBoundingClientRect().top<cutoff).length;
  if(!diagnostics.loadedCardCount)return reject('source_list_missing');
  const controls=[...document.querySelectorAll('button,a,[role="button"],[role="tab"],li,div,span')].filter(e=>rendered(e)&&e.textContent.trim()==='上新');
  const leaves=controls.filter(e=>!controls.some(child=>child!==e&&e.contains(child)));
  const current=e=>!!e&&(e.getAttribute('aria-selected')==='true'||/(?:^|\s|_)current(?:\s|_|$)|(?:^|\s|_)active(?:\s|_|$)/i.test(String(e.className||'')));
  diagnostics.latestSelected=leaves.length===1&&(current(leaves[0])||current(leaves[0].parentElement));
  if(!diagnostics.latestSelected)return reject('source_sort_unverified');
  diagnostics.sourceVerified=true;return reject('verified_storefront_resume');
 }
 if(matches.length!==1)return reject('card_ambiguous');
 if(matches[0].list!==lists[0])return reject('non_primary_list');
 return {verified:true,url:location.href,reason:'qualified',...diagnostics,sourceVerified:true};
}
export function scriptResult(result){
 const message=result?.structuredContent?.message;const match=typeof message==='string'&&/^Script ran on page and returned:\n```json\n([\s\S]*)\n```$/.exec(message);
 if(!match)throw operationError();if(match[1]==='undefined')return undefined;try{return JSON.parse(match[1]);}catch{throw operationError();}
}
export async function createBrowserAdapter(client,project,{entryUrl,verifiedStorefrontUrl,verifiedOriginalCard,publicProbe=false,sleep=delay}={}){
 let pageId=null,owned=false,failed=false,closed=false,connectionFailure=null,searchProof=null;
 const entry=entryUrl?publicPageUrl(entryUrl):null;
 async function call(name,args={},optional=false,timeoutMs=45000){
  if(failed||closed)throw connectionFailure||connectionError();
  try{const result=await client.callTool(name,args,timeoutMs);if(result.structuredContent?.reconnected)throw new ChromeMcpError('connection_lost');if(result.structuredContent?.dialog)throw new ReviewError('Chrome 页面有待处理对话框，请手动处理后重新采集。','manual_review','browser_dialog');return result;}
  catch(error){
   // Optional image/screenshot failures do not invalidate already read cards.
   // A transport failure still closes the connection; never reconnect/retry.
   if(!optional||error instanceof ReviewError||error instanceof ChromeMcpError&&error.code!=='browser_tool_failed')failed=true;
   if(error instanceof ReviewError)throw error;
   if(error instanceof ChromeMcpError&&error.code!=='browser_tool_failed'){connectionFailure ||= connectionError(error,{operation:name});throw connectionFailure;}
   throw operationError();
  }
 }
 // Only first connection waits for the user's Chrome consent; page work stays bounded.
 let pages;try{pages=pagesFrom(await call('list_pages',{},false,120000));}catch(error){await client.close();throw connectionError(error,{initial:true,operation:'list_pages'});}
 // The verified storefront also binds legitimate redirects from share entries.
 // Only single-SKU requests supply the original-card proof used to preserve a
 // loaded list. Full-shop callers still navigate and run fresh shop preflight.
 // Bind the origin AND identity type/value, never titles.
 // Unresolved share links have no typed identity and keep exact-URL behavior.
 const expectedStore=verifiedStorefrontUrl?storefrontIdentity(verifiedStorefrontUrl):null;
 const candidates=expectedStore?pages.filter(page=>{
  if(page.isolatedContext)return false;const actual=storefrontIdentity(page.url);
  return sameStore(actual,expectedStore);
 }):entry?pages.filter(p=>p.url===entry&&!p.isolatedContext):[];
 const probeCard=expectedStore&&verifiedOriginalCard&&['title','image','shopName'].every(key=>typeof verifiedOriginalCard[key]==='string'&&!!verifiedOriginalCard[key].trim());
 let qualified=candidates;const probes=[],resumeCandidates=[];
 if(probeCard){
  qualified=[];const args={title:verifiedOriginalCard.title,image:verifiedOriginalCard.image,shopName:verifiedOriginalCard.shopName,identity:{origin:expectedStore.origin,identity_kind:expectedStore.identity_kind,stable_identifier:expectedStore.stable_identifier}};
  for(const page of candidates){
   const observed=scriptResult(await call('evaluate_script',{pageId:page.id,function:`() => (${readSkuStorefrontCandidate.toString()})(${JSON.stringify(args)})`,waitForStableDom:false,dialogAction:'dismiss'}));
   const verified=observed?.verified===true&&sameStore(storefrontIdentity(observed.url),expectedStore);
   const count=name=>Number.isSafeInteger(observed?.[name])&&observed[name]>=0&&observed[name]<=1000000?observed[name]:null;
   const loadedCardCount=count('loadedCardCount'),sourceVerified=observed?.verified===false&&observed?.sourceVerified===true&&observed?.latestSelected===true&&observed?.reason==='verified_storefront_resume'&&count('matchingCardCount')===0&&count('visibleModalCount')===0&&count('listCount')>0&&loadedCardCount>0&&sameStore(storefrontIdentity(observed.url),expectedStore);
   probes.push(Object.freeze({pageId:page.id,verified,sourceVerified:verified||sourceVerified,latestSelected:typeof observed?.latestSelected==='boolean'?observed.latestSelected:null,loadedCardCount,reason:verified?'qualified':observed?.verified===true?'identity_mismatch':skuProbeReasons.has(observed?.reason)?observed.reason:'probe_unverified',matchingCardCount:count('matchingCardCount'),visibleModalCount:count('visibleModalCount'),listCount:count('listCount')}));
   if(verified)qualified.push(page);
   else if(sourceVerified)resumeCandidates.push({page,loadedCardCount});
  }
 }
 const selected=qualified.filter(p=>p.selected===true);
 const original=qualified.length===1?qualified[0]:selected.length===1?selected[0]:probeCard&&qualified.length?qualified.reduce((latest,page)=>page.id>latest.id?page:latest):null;
 const resume=!original&&resumeCandidates.length?resumeCandidates.reduce((best,item)=>item.loadedCardCount>best.loadedCardCount||item.loadedCardCount===best.loadedCardCount&&item.page.id>best.page.id?item:best).page:null;
 const existing=original||resume;
 if(existing){pageId=existing.id;await call('select_page',{pageId,bringToFront:true});}
 const originalCardVerified=!!(probeCard&&original),verifiedCard=originalCardVerified?Object.freeze({title:verifiedOriginalCard.title,image:verifiedOriginalCard.image,shopName:verifiedOriginalCard.shopName,pageId:original.id}):null;
 const pageSelection=Object.freeze({strategy:probeCard?'original_card':expectedStore?'typed_storefront':'exact_url',candidateCount:candidates.length,qualifiedCount:qualified.length,resumeCandidateCount:resumeCandidates.length,selectedPageId:existing?.id??null,decision:existing?'reused':'fresh_required',selectionReason:originalCardVerified?'original_card':resume?'verified_storefront_resume':existing?expectedStore?'typed_storefront':'exact_url':'fresh_required',originalCardVerified,sortPolicy:originalCardVerified?'preserve_verified_original_card':'require_latest',probes:Object.freeze(probes)});
 const executionEvidence=Object.freeze({transport:'chrome-devtools-mcp',version:'1.10.1',connection:'autoConnect',persistentConnection:client.persistent===true,scroll:'dom-window-scrollBy',preserveViewport:true,preserveUserAgent:true,imageAcquisition:'loaded-exact-url-response-and-existing-cache',imageLimits:'acceptance-after-response-buffer',networkHeaders:'redacted-and-not-retained',pageSelection});
 function canPreserveSkuStorefront({sourceUrl,currentUrl,title,image,shopName}={}){
  return !failed&&!closed&&!!verifiedCard&&pageId===verifiedCard.pageId&&sameStore(storefrontIdentity(sourceUrl),expectedStore)&&sameStore(storefrontIdentity(currentUrl),expectedStore)&&title===verifiedCard.title&&image===verifiedCard.image&&shopName===verifiedCard.shopName;
 }
 async function confirmEntryAddress(id,url){
  const requestedStore=new URL(url).pathname==='/mall_page.html'?(expectedStore||storefrontIdentity(url)):null;
  for(let attempt=0;attempt<25;attempt++){
   const address=scriptResult(await call('evaluate_script',{pageId:id,function:'() => ({url:location.href,origin:location.origin})',waitForStableDom:false,dialogAction:'dismiss'}));
   const inspectedShare=publicProbe===true&&!verifiedStorefrontUrl&&!verifiedOriginalCard&&url===entry&&publicStorefrontProbeRedirect(url,address?.url);
   if(address?.origin==='https://mobile.yangkeduo.com'&&(address.url===url||sameStore(storefrontIdentity(address.url),requestedStore)||inspectedShare))return address;
   const transientLogin=address?.origin==='https://mobile.yangkeduo.com'&&new URL(address.url).pathname==='/login.html';
   if(!transientLogin){failed=true;throw operationError();}
   if(attempt<24){await sleep(400);continue;}
   failed=true;throw new ReviewError('网站仍停在登录页，请在原 Chrome 完成登录后继续；已保存数据保留。','needs_login','login_required');
  }
 }
 async function createPage(url){
  const before=new Set(pages.map(p=>p.id)),result=await call('new_page',{url,background:false,timeout:25000});
  const after=pagesFrom(result),created=after.filter(p=>!before.has(p.id)&&!p.isolatedContext);
  if(created.length!==1){failed=true;throw operationError();}
  // selected is MCP connection-local metadata, not Chrome's foreground page.
  // Bind the unique newly created ordinary page only after reading its address.
  await confirmEntryAddress(created[0].id,url);
  pageId=created[0].id;owned=true;pages=after;
 }
 async function evaluateSource(expression){
  if(!Number.isSafeInteger(pageId))throw operationError();
  const source=`async () => { if (location.origin !== 'https://mobile.yangkeduo.com') return {__pddSourceChanged:true}; return await (${expression}); }`;
  const value=scriptResult(await call('evaluate_script',{pageId,function:source,waitForStableDom:false,dialogAction:'dismiss'}));
  if(value?.__pddSourceChanged){failed=true;throw new ReviewError('网页来源发生变化；需要人工复核。','manual_review','source_changed');}return value;
 }
 async function evaluate(fn,arg){
  if(typeof fn!=='function')throw operationError();let serialized;try{serialized=JSON.stringify(arg);}catch{throw operationError();}
  return evaluateSource(`(${fn.toString()})(${serialized??''})`);
 }
 // Trusted, synchronous DOM readers in one page turn; argument data stays JSON.
 // SKU identity, selection and price therefore describe the same observation.
 async function evaluateMany(readers){
  if(!Array.isArray(readers)||!readers.length||readers.length>4||readers.some(([fn])=>typeof fn!=='function'))throw operationError();
  let expressions;try{expressions=readers.map(([fn,arg])=>`(${fn.toString()})(${JSON.stringify(arg)??''})`);}catch{throw operationError();}
  return evaluateSource(`[${expressions.join(',')}]`);
 }
 async function goto(raw){
  searchProof=null;
  const url=publicPageUrl(raw);if(pageId===null)await createPage(url);
  else{const result=await call('navigate_page',{pageId,type:'url',url,timeout:25000,handleBeforeUnload:'dismiss'});if(/Unable to navigate|Failed to navigate/i.test(result.structuredContent?.message||'')){failed=true;throw operationError();}}
  await sleep(1200);await confirmEntryAddress(pageId,url);
 }
 async function back(){
  searchProof=null;
  if(!Number.isSafeInteger(pageId))throw operationError();
  const result=await call('navigate_page',{pageId,type:'back',timeout:25000,handleBeforeUnload:'dismiss'});
  if(/Unable to navigate|Failed to navigate/i.test(result.structuredContent?.message||'')){failed=true;throw operationError();}
  await sleep(1200);await evaluate(()=>({origin:location.origin}));
 }
 async function click(point,{settleMs=700}={}){
  searchProof=null;
  if(!Number.isFinite(settleMs)||settleMs<0||settleMs>700)throw operationError();
  if(!point||!Number.isFinite(point.x)||!Number.isFinite(point.y))throw operationError();const bounds=await evaluate(()=>({width:innerWidth,height:innerHeight}));
  if(point.x<0||point.y<0||point.x>=bounds.width||point.y>=bounds.height)throw new ReviewError('待点击入口不在当前视口，请检查页面后重新采集。');
  await call('click_at',{pageId,x:point.x,y:point.y,includeSnapshot:false});if(settleMs)await sleep(settleMs);
 }
 async function scroll(position,direction,pages){
  searchProof=null;
  if(!['up','down'].includes(direction)||!Number.isFinite(pages)||pages<0||pages>1000||!Array.isArray(position)||position.length!==2||!position.every(Number.isFinite))throw operationError();
  // Fixed DOM scroll, measured in the actual viewport. Driver verifies coverage.
  await evaluate(({direction,pages})=>{const height=window.innerHeight;if(!Number.isFinite(height)||height<=0)throw new Error('Invalid viewport');const pixels=(direction==='up'?-1:1)*height*pages;window.scrollBy({top:pixels,left:0,behavior:'instant'});return {requestedPixels:pixels,height};},{direction,pages});await sleep(900);
 }
 async function verifySearch(proof,{uid=null,checkValue=false}={}){
  if(!Number.isSafeInteger(pageId)||proof.pageId!==pageId)throw searchInputError();
  const args={selector:searchSelector,expectedPlaceholder:searchPlaceholder,sourceUrl:proof.sourceUrl,text:proof.text,checkValue,checkElement:uid!==null};
  const value=uid===null?await evaluate(checkSearchInput,args):scriptResult(await call('evaluate_script',{pageId,function:`(element) => (${checkSearchInput.toString()})(${JSON.stringify(args)},element)`,args:[uid],waitForStableDom:false,dialogAction:'dismiss'}));
  if(value!==true)throw searchInputError();
 }
 async function fillSearch(options={},text){
  searchProof=null;
  const {selector,expectedPlaceholder,sourceUrl}=options||{};
  if(selector!==searchSelector||expectedPlaceholder!==searchPlaceholder||typeof sourceUrl!=='string'||!storefrontIdentity(sourceUrl)||typeof text!=='string'||!text.trim()||text.length>300||/[\x00-\x1f\x7f]/.test(text))throw searchInputError();
  const proof={pageId,sourceUrl,text};await verifySearch(proof);
  const uid=searchInputUid((await call('take_snapshot',{pageId,verbose:false})).structuredContent?.snapshot);
  await verifySearch(proof,{uid});
  await call('fill',{pageId,uid,value:text,includeSnapshot:false});
  await verifySearch(proof,{uid,checkValue:true});
  searchProof=Object.freeze({...proof,uid});
 }
 async function pressSearchEnter(...args){
  const proof=searchProof;searchProof=null;
  if(args.length||!proof)throw searchInputError();
  await verifySearch(proof,{uid:proof.uid,checkValue:true});
  await call('press_key',{pageId,key:'Enter',includeSnapshot:false});
 }
 const tab={get id(){return pageId;},playwright:{evaluate},scroll,executionEvidence};
 const imageCache=createLoadedImageCache({project,getPageId:()=>pageId,evaluate,callTool:(name,args)=>call(name,args,true)});
 return {tab,evaluate,evaluateMany,click,scroll,goto,back,fillSearch,pressSearchEnter,canPreserveSkuStorefront,executionEvidence,images:imageCache.images,collectImages:imageCache.collect,collectSkuImages:imageCache.collectSku,discardImages:imageCache.discard,flush:imageCache.flush,
  async screenshot(file){if(failed||closed)return {saved:false,reason:'connection_required'};return saveMcpScreenshot({project,pageId,file,callTool:(name,args)=>call(name,args,true)});},
  async close(success=false,{keepPage=false}={}){searchProof=null;if(closed)return;if(success&&owned&&!failed&&keepPage!==true)try{await call('close_page',{pageId});}catch{}closed=true;await client.close();}
 };
}
export async function openBrowser(project,options={}){
 const policyFile=path.join(project,'state/local_collection/browser_policy.json');let policy;try{policy=JSON.parse(await fs.readFile(policyFile,'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
 if(policy&&policy.mode!=='local_chrome')throw new ReviewError('当前浏览器采集方式未启用普通 Chrome。');const entryUrl=options.entryUrl?publicPageUrl(options.entryUrl):undefined;let client;
 try{await ensureNormalChrome(project,entryUrl);client=await startChromeMcp(project);return await createBrowserAdapter(client,project,{entryUrl,verifiedStorefrontUrl:options.verifiedStorefrontUrl,verifiedOriginalCard:options.verifiedOriginalCard,publicProbe:options.publicProbe});}catch(error){if(client)await client.close();if(error instanceof ReviewError)throw error;throw connectionError(error,{initial:true});}
}
export function visibleTextPoint(text){
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&s.display!=='none'&&s.visibility!=='hidden';};
 const options=[...document.querySelectorAll('button,a,[role="button"],[role="tab"],li,div,span')].filter(e=>visible(e)&&e.textContent.trim()===text);const leaf=options.find(e=>!options.some(other=>other!==e&&e.contains(other)));if(!leaf)return null;const r=leaf.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};
}
export function readPageGuard({includeText=true}={}){const text=document.body.innerText;return {url:location.href,text:includeText?text.slice(0,16000):'',login:/login\.html|login\.yangkeduo|请先登录|登录后查看|验证码登录|短信登录|手机号码登录/.test(location.href+' '+text),challenge:/滑动.*验证|请完成.*验证|安全验证|访问受限|操作过于频繁/.test(text)};}
export function visiblePurchasePoint(){
 const candidates=[...document.querySelectorAll('button,a,[role="button"],div,span')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e),text=e.textContent.trim();return r.width>30&&r.height>15&&r.top>innerHeight*.6&&r.bottom<=innerHeight+2&&s.visibility!=='hidden'&&s.display!=='none'&&text.length<120&&/选择规格|立即购买|单独购买|发起拼单/.test(text)&&!/支付|提交订单|确认订单|优惠券|去拼单/.test(text);});const leaf=candidates.find(e=>!candidates.some(child=>child!==e&&e.contains(child)));if(!leaf)return null;const r=leaf.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};
}
export async function guard(browser,shopName=null){
 const page=await browser.evaluate(readPageGuard);
 return checkPageGuard(page,shopName);
}
export function checkPageGuard(page,shopName=null){
 if(page.challenge)throw new ReviewError('页面要求人工验证或限制访问；请在 Chrome 查看提示，处理后再采集。','manual_review','access_restricted');
 if(page.login)throw new ReviewError('网站要求登录，请在原 Chrome 完成拼多多登录，再点开始采集；后续将复用该浏览器的登录状态。','needs_login','login_required');
 if(new URL(page.url).hostname!=='mobile.yangkeduo.com')throw new ReviewError('网页来源发生变化；需要人工复核。','manual_review','source_changed');
 if(shopName&&!page.text.includes(shopName))throw new ReviewError('未核对到当前店铺，请更新本店分享链接；已有数据保留。','needs_url','entry_unavailable');return page;
}
