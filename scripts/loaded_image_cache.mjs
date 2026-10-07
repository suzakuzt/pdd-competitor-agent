// Copy only already-loaded, exact-URL storefront images from official MCP.
// No HTTP client, site API, Cookie access or independent image download.
import fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export const MAX_IMAGE_BYTES=10*1024*1024;
export const MAX_TOTAL_IMAGE_BYTES=128*1024*1024;
export function pddImageUrl(raw) {
 try {const u=new URL(raw);return u.protocol==='https:'&&!u.port&&!u.username&&!u.password&&
  (u.hostname==='pddpic.com'||u.hostname.endsWith('.pddpic.com'))&&u.href===raw?u.href:null;}catch{return null;}
}
export function imageMime(bytes) {
 if(bytes.length>=8&&bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return 'image/png';
 if(bytes.length>=3&&bytes[0]===255&&bytes[1]===216&&bytes[2]===255)return 'image/jpeg';
 if(bytes.length>=6&&['GIF87a','GIF89a'].includes(bytes.toString('ascii',0,6)))return 'image/gif';
 if(bytes.length>=12&&bytes.toString('ascii',0,4)==='RIFF'&&bytes.toString('ascii',8,12)==='WEBP')return 'image/webp';
 return null;
}

// Self-contained for evaluate_script. Only rendered shop-list DOM is inspected.
export function readLoadedStoreImages({shopName}) {
 if(location.origin!=='https://mobile.yangkeduo.com'||!shopName||!document.body.innerText.includes(shopName))return {verified:false,images:[]};
 const lists=[...document.querySelectorAll('.waterfall-list-container_egohG8wQ')],y=window.scrollY;
 const shown=element=>{
  const r=element.getBoundingClientRect();if(!(r.width>0&&r.height>0))return false;
  for(let e=element;e;e=e.parentElement){const s=getComputedStyle(e);if(e.hidden||e.getAttribute?.('aria-hidden')==='true'||s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
  return true;
 };
 const markers=[...document.querySelectorAll('div,p,span')].filter(e=>!e.children.length&&['本店暂无更多商品','其他店铺的精选推荐'].includes(e.textContent.trim())&&shown(e)).map(e=>({label:e.textContent.trim(),top:e.getBoundingClientRect().top+y}));
 const recommendationTop=Math.min(Infinity,...markers.filter(m=>m.label==='其他店铺的精选推荐').map(m=>m.top));
 const boundaries=markers.filter(m=>m.label==='本店暂无更多商品'&&m.top<=recommendationTop);
 const cutoff=Math.min(recommendationTop,...boundaries.map(m=>m.top));
 const mainLists=lists.filter(list=>shown(list)&&list.getBoundingClientRect().top+y<cutoff);
 if(mainLists.length!==1)return {verified:false,images:[]};
 const list=mainLists[0];
 const images=[];
 for(const card of list.querySelectorAll('.goodsItem_R1ok0MpS')) {
  if(!shown(card)||card.getBoundingClientRect().top+y>=cutoff)continue;
  const img=card.querySelector('.goodsImage_fU27dxbk img');
  if(!img||!shown(img)||!img.complete||!(img.naturalWidth>0&&img.naturalHeight>0)||!img.currentSrc||img.currentSrc!==img.src)continue;
  try {
   const u=new URL(img.currentSrc);
   if(u.protocol==='https:'&&!u.port&&!u.username&&!u.password&&(u.hostname==='pddpic.com'||u.hostname.endsWith('.pddpic.com')))
    images.push({url:img.currentSrc,src:img.src});
  }catch{}
 }
 return {verified:true,images};
}

// Self-contained rendered-DOM check. Call only after a SKU selection was read;
// matching a loaded image elsewhere on the goods page does not bind it to SKU.
export function readLoadedSkuImages({goodsId,imageUrls,listModalToken}) {
 const reject=(reason,diagnostics)=>({verified:false,images:[],reason,...(diagnostics?{diagnostics}:{})});
 const fromList=listModalToken!==undefined;
 if(!Array.isArray(imageUrls)||!imageUrls.length||imageUrls.length>16||
  (fromList?(typeof listModalToken!=='string'||!listModalToken||goodsId!=null):!/^[1-9]\d*$/.test(String(goodsId))))return reject('sku_request_invalid');
 let current;try{current=new URL(location.href);}catch{return reject('sku_goods_identity_unverified');}
 if(!fromList&&(current.origin!=='https://mobile.yangkeduo.com'||!/^\/goods1?\.html$/.test(current.pathname)||
   current.searchParams.getAll('goods_id').length!==1||current.searchParams.get('goods_id')!==String(goodsId)))return reject('sku_goods_identity_unverified');
 const shown=element=>{
  const r=element.getBoundingClientRect();if(!(r.width>0&&r.height>0))return false;
  for(let e=element;e;e=e.parentElement){const s=getComputedStyle(e);if(e.hidden||e.getAttribute?.('aria-hidden')==='true'||s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
  return true;
 };
 let listBinding=null;
 if(fromList){
  listBinding=globalThis[Symbol.for('pdd-monitor/sku-modal-binding-v1')];
  const fromSearch=!!listBinding&&'searchEvidence' in Object(listBinding);let searchVerified=false;
  if(fromSearch)try{
   searchVerified=!!listBinding.searchEvidence&&typeof listBinding.searchEvidence==='object'&&Object.isFrozen(listBinding.searchEvidence)&&
    typeof listBinding.verifySearch==='function'&&listBinding.verifySearch()===true;
  }catch{}
  const checks={binding_present:!!listBinding,token_matches:listBinding?.token===listModalToken,binding_valid:listBinding?.invalid===false,
   trusted_click_seen:listBinding?.clicked===true,modal_opened:listBinding?.opened===true,
   source_matches:current.origin==='https://mobile.yangkeduo.com'&&!current.username&&!current.password&&!current.port&&current.pathname===(fromSearch?'/mall_search_result.html':'/mall_page.html')&&location.href===listBinding?.sourceUrl,
   ...(fromSearch?{search_evidence_verified:searchVerified}:{shop_name_matches:typeof listBinding?.shopName==='string'&&!!listBinding.shopName.trim()&&document.body.innerText.includes(listBinding.shopName)}),
   card_connected:listBinding?.card?.isConnected===true,modal_connected:listBinding?.modal?.isConnected===true,
   original_plus_recorded:typeof listBinding?.plus?.contains==='function'};
  if(!Object.values(checks).every(Boolean))return reject('sku_list_binding_unverified',checks);
  // A trusted click on the original plus is already recorded by our listener.
  // React may replace that button while keeping this exact card and modal; its
  // old node remaining attached is not continuing SKU provenance evidence.
  const lists=[...document.querySelectorAll(fromSearch?'.waterfall-list-container_NLmKnzfN':'.waterfall-list-container_egohG8wQ')];
  const owners=lists.filter(list=>list.contains(listBinding.card));
  const diagnostics={...checks,list_count:lists.length,owner_list_count:owners.length,
   original_plus_connected:listBinding.plus?.isConnected===true,original_plus_in_card:!!listBinding.plus&&listBinding.card.contains(listBinding.plus)};
  if(owners.length!==1)return reject('sku_list_binding_unverified',diagnostics);
  const list=owners[0];let cutoff=Infinity;
  for(const e of document.querySelectorAll('div,p,span')){
   if(e.children.length||!['本店暂无更多商品','其他店铺的精选推荐'].includes(e.textContent.trim())||!shown(e))continue;
   cutoff=Math.min(cutoff,e.getBoundingClientRect().top);
  }
  const cards=fromSearch?[...new Set(lists.flatMap(list=>[...list.querySelectorAll('.goodsItem_R1ok0MpS')]))]:[...list.querySelectorAll('.goodsItem_R1ok0MpS')];
  const matches=cards.filter(card=>{
   const image=card.querySelector('.goodsImage_fU27dxbk img');
   return shown(card)&&card.getBoundingClientRect().top<cutoff&&card.querySelector('.goodsName_dT8mZSNd')?.textContent.trim()===listBinding.title&&
    (image?.getAttribute('data-src')||image?.getAttribute('src'))===listBinding.imageUrl;
  });
  if(matches.length!==1||matches[0]!==listBinding.card)return reject('sku_list_card_unverified',{...diagnostics,card_rendered:shown(listBinding.card),card_before_boundary:listBinding.card.getBoundingClientRect().top<cutoff,matching_card_count:matches.length,original_card_matched:matches.length===1&&matches[0]===listBinding.card});
 }
 const exactImages=(root,candidates)=>{
  const requested=new Set(imageUrls),images=[];
  for(const img of candidates){
   if(!root.contains(img)||!requested.has(img.currentSrc)||!shown(img)||!img.complete||!(img.naturalWidth>0&&img.naturalHeight>0)||img.currentSrc!==img.src)continue;
   try{const u=new URL(img.currentSrc);if(u.protocol==='https:'&&!u.port&&!u.username&&!u.password&&u.href===img.currentSrc&&(u.hostname==='pddpic.com'||u.hostname.endsWith('.pddpic.com')))images.push({url:img.currentSrc,src:img.src});}catch{}
  }
  return {verified:true,images};
 };
 // Observed mobile SKU sheet, isolated from product recommendations and from
 // installed extension overlays. Selection text corroborates the CSS state.
 const modalNodes=[...document.querySelectorAll('.sku-plus1 [role="dialog"][aria-modal="true"]')],modals=modalNodes.filter(shown);
 if(fromList&&(modals.length!==1||modals[0]!==listBinding.modal))return reject('sku_list_modal_changed');
 if(modalNodes.length&&!modals.length)return reject('sku_modal_hidden');
 if(modals.length){
  if(modals.length!==1)return reject('sku_modal_ambiguous');
  const root=modals[0],bounds=root.getBoundingClientRect();
  if(bounds.bottom<=0||bounds.top>=innerHeight)return reject('sku_modal_outside_viewport');
  const oldGroups=[...root.querySelectorAll('.bIhLWVqm')];
  const groups=(oldGroups.length?oldGroups:[...root.querySelectorAll('.skuSpecs_bIhLWVqm')]).filter(shown);
  if(!groups.length||groups.length>4)return reject('sku_groups_unverified');
  const labels=[];
  for(const group of groups){
   if(!group.querySelector('.sku-specs-key')?.textContent.trim())return reject('sku_group_name_missing');
   const oldOptions=[...group.querySelectorAll('.s1O5M5fO > [role="button"]')];
   const chosen=(oldOptions.length?oldOptions:[...group.querySelectorAll('.skuSpecValueList_s1O5M5fO > [role="button"]')]).filter(e=>shown(e)&&String(e.className).split(/\s+/).some(name=>['hr353bdX','skuSpecValueSelected_oQDDpea9'].includes(name)));
   if(chosen.length!==1)return reject('sku_selected_option_unverified');
   const label=chosen[0].getAttribute('aria-label')?.trim();if(!label)return reject('sku_selected_label_missing');labels.push(label);
  }
  const selection=root.querySelector('.Mbx2m60G')||root.querySelector('.text_Mbx2m60G'),price=root.querySelector('.ujEqGzEB [aria-label]')||root.querySelector('.skuQuantityPriceDesc_ujEqGzEB [aria-label]');
  if(!selection)return reject('sku_selection_summary_missing');
  if(!shown(selection))return reject('sku_selection_summary_hidden');
  if(selection.textContent.replace(/\s+/g,'')!=='已选：'+labels.join('').replace(/\s+/g,''))return reject('sku_selection_summary_mismatch');
  if(price){
   if(!shown(price))return reject('sku_price_label_hidden');
   if(!/[¥￥]/.test(price.getAttribute('aria-label')||''))return reject('sku_price_label_invalid');
  }else{
   // The same observed SKU sheet also renders a plain current-price container
   // without an aria-label child. Match only the two selectors read by readSpecs;
   // prices elsewhere in the modal (original price, promotions) cannot qualify.
   const currentPrices=[...new Set([...root.querySelectorAll('.ujEqGzEB'),...root.querySelectorAll('.skuQuantityPriceDesc_ujEqGzEB')])];
   const visiblePrices=currentPrices.filter(e=>root.contains(e)&&shown(e));
   const diagnostics={price_container_count:currentPrices.length,visible_price_container_count:visiblePrices.length};
   if(!currentPrices.length)return reject('sku_price_label_missing',diagnostics);
   if(!visiblePrices.length)return reject('sku_price_label_hidden',diagnostics);
   if(visiblePrices.length!==1)return reject('sku_price_label_ambiguous',diagnostics);
   const text=visiblePrices[0].innerText.replace(/\s+/g,'');
   if(!/^(?:券后价?|当前价)?[¥￥](?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(text))return reject('sku_price_label_invalid',diagnostics);
  }
  const oldImages=[...root.querySelectorAll('.O7pEFvHR > img')];
  return exactImages(root,oldImages.length?oldImages:root.querySelectorAll('.skuSelectorHead_O7pEFvHR > img'));
 }
 const optionSelectors='[class*="sku-spec-value"],[class*="skuItem"],[class*="sku-item"],[class*="specItem"],[class*="spec-item"],[role="option"]';
 const options=[...document.querySelectorAll(optionSelectors)].filter(e=>shown(e)&&e.textContent.trim()&&e.textContent.trim().length<200);
 const leaves=options.filter(e=>!options.some(other=>other!==e&&e.contains(other)));
 if(!leaves.length)return reject('sku_options_missing');
 const parents=[...new Set(leaves.map(e=>e.parentElement))];
 const selected=e=>e.getAttribute('aria-selected')==='true'||e.getAttribute('aria-checked')==='true'||/(?:^|[\s_-])(selected|checked|active)(?:[\s_-]|$)/i.test(String(e.className));
 if(parents.length>4||parents.some(parent=>leaves.filter(e=>e.parentElement===parent&&selected(e)).length!==1))return reject('sku_selected_option_unverified');
 let root=parents[0];
 while(root?.parentElement&&root!==document.body&&(!leaves.every(e=>root.contains(e))||!/[¥￥]/.test(root.innerText)))root=root.parentElement;
 if(!root||root===document.body||!shown(root)||!leaves.every(e=>root.contains(e))||!/[¥￥]/.test(root.innerText))return reject('sku_common_modal_unverified');
 const bounds=root.getBoundingClientRect();if(bounds.bottom<=0||bounds.top>=innerHeight)return reject('sku_modal_outside_viewport');
 return exactImages(root,[...root.querySelectorAll('img')].filter(img=>!leaves.some(option=>option.contains(img)&&!selected(option))));
}

function samePath(a,b){return path.resolve(a).toLowerCase()===path.resolve(b).toLowerCase();}
function inside(root,file){const relative=path.relative(path.resolve(root),path.resolve(file));return !!relative&&!relative.startsWith('..')&&!path.isAbsolute(relative);}
async function removeOwned(io,file,directory) {
 if(file&&inside(directory,file))try{await io.unlink(file);}catch{}
}
async function readBoundedImage(io,file,maxBytes) {
 const stat=await io.lstat(file);
 if(!stat.isFile()||stat.isSymbolicLink())throw new Error('invalid_file');
 if(stat.size<=0)throw new Error('empty_image');
 if(stat.size>maxBytes)throw new Error('image_size_limit');
 const bytes=await io.readFile(file);
 if(bytes.length!==stat.size||bytes.length>maxBytes)throw new Error('image_size_limit');
 const mime=imageMime(bytes);if(!mime)throw new Error('invalid_image_bytes');
 return {bytes,mime};
}
const safeFileReasons=new Set(['empty_image','image_size_limit','invalid_image_bytes','invalid_file']);
const safeSkuDomReasons=new Set(['sku_request_invalid','sku_goods_identity_unverified','sku_list_binding_unverified','sku_list_card_unverified','sku_list_modal_changed','sku_modal_hidden','sku_modal_ambiguous','sku_modal_outside_viewport','sku_groups_unverified','sku_group_name_missing','sku_selected_option_unverified','sku_selected_label_missing','sku_selection_summary_missing','sku_selection_summary_hidden','sku_selection_summary_mismatch','sku_price_label_missing','sku_price_label_hidden','sku_price_label_invalid','sku_price_label_ambiguous','sku_options_missing','sku_common_modal_unverified']);
const safeSkuDomBooleans=new Set(['binding_present','token_matches','binding_valid','trusted_click_seen','modal_opened','source_matches','shop_name_matches','search_evidence_verified','card_connected','modal_connected','original_plus_recorded','original_plus_connected','original_plus_in_card','card_rendered','card_before_boundary','original_card_matched']);
const safeSkuDomCounts=new Set(['list_count','owner_list_count','matching_card_count','price_container_count','visible_price_container_count']);

export function createLoadedImageCache({project,getPageId,evaluate,callTool,io=fs,
 maxImageBytes=MAX_IMAGE_BYTES,maxTotalBytes=MAX_TOTAL_IMAGE_BYTES,timeBudgetMs=20000}={}) {
 const images=new Map(),attempted=new Set();let totalBytes=0,busy=false;
 async function collectObserved(readDom,domArgs,unverifiedReason,{skipUrls=[],blockedUrls=[],requestedUrls=null}={}) {
  const counts={observed:0,newlySaved:0,reused:0,cached:images.size,byteCount:totalBytes,missing:0,reasons:{},limitTiming:'after_response_buffer'};
  const reason=name=>{counts.reasons[name]=(counts.reasons[name]||0)+1;};
  if(busy){reason('image_collection_busy');return counts;}
  busy=true;let directory;const allowedUrls=new Set(),reusedUrls=new Set(),blocked=new Set();
  try {
   const pageId=getPageId();if(!Number.isSafeInteger(pageId)){reason('page_unavailable');return counts;}
   const dom=await evaluate(readDom,domArgs);
   if(!dom?.verified||!Array.isArray(dom.images)){
    reason(unverifiedReason);
    if(readDom===readLoadedSkuImages&&safeSkuDomReasons.has(dom?.reason)){
     reason(dom.reason);
     const diagnostics=Object.fromEntries(Object.entries(dom.diagnostics||{}).filter(([name,value])=>safeSkuDomBooleans.has(name)&&typeof value==='boolean'||safeSkuDomCounts.has(name)&&Number.isSafeInteger(value)&&value>=0&&value<=10000));
     if(Object.keys(diagnostics).length)counts.domDiagnostics=diagnostics;
    }
    return counts;
   }
   const requested=requestedUrls?new Set(requestedUrls):null;
   const allowed=new Map(dom.images.filter(item=>pddImageUrl(item.url)&&item.src===item.url&&(!requested||requested.has(item.url))).map(item=>[item.url,item]));
   if(requested)for(const url of requested)if(!allowed.has(url))reason(readDom===readLoadedSkuImages?'sku_image_not_loaded':'store_image_not_loaded');
   counts.observed=allowed.size;
   for(const url of allowed.keys())allowedUrls.add(url);
   for(const url of Array.isArray(blockedUrls)?blockedUrls.slice(0,10000):[])if(allowed.has(url)&&pddImageUrl(url))blocked.add(url);
   for(const url of blocked)reason('previous_attempt_blocked');
   // Caller supplies only URLs whose exact-original cache evidence is approved.
   for(const url of Array.isArray(skipUrls)?skipUrls.slice(0,10000):[])if(allowed.has(url)&&!blocked.has(url)&&pddImageUrl(url))reusedUrls.add(url);
   counts.reused=reusedUrls.size;
   const pending=new Set([...allowed.keys()].filter(url=>!images.has(url)&&!reusedUrls.has(url)&&!blocked.has(url)));
   if(!pending.size)return counts;
   const requests=new Map();
   for(let pageIdx=0;pageIdx<10;pageIdx++) {
    const result=await callTool('list_network_requests',{pageId,resourceTypes:['image'],includePreservedRequests:false,pageSize:100,pageIdx});
    for(const request of result.structuredContent?.networkRequests||[]) {
     if(Number.isSafeInteger(request.requestId)&&request.requestId>0&&request.method==='GET'&&String(request.status)==='200'&&pending.has(request.url))requests.set(request.url,request);
    }
    if(!result.structuredContent?.pagination?.hasNextPage)break;
   }
   const started=Date.now();
   for(const url of pending) {
    if(Date.now()-started>=timeBudgetMs){reason('image_time_budget');break;}
    if(totalBytes>=maxTotalBytes){reason('image_total_limit');break;}
    const request=requests.get(url);
    if(!request){reason('loaded_request_unavailable');continue;}
    const key=pageId+':'+request.requestId;
    if(attempted.has(key)){reason('body_already_attempted');continue;}
    attempted.add(key);
    // The official brief list currently has no byte size. Reject an explicitly
    // known excessive size if supplied; do not infer size from dimensions.
    const knownBytes=allowed.get(url).knownByteLength;
    if(Number.isFinite(knownBytes)&&knownBytes>Math.min(maxImageBytes,maxTotalBytes-totalBytes)){reason('image_size_limit');continue;}
    if(!directory){const base=path.join(project,'state/local_collection');await io.mkdir(base,{recursive:true});directory=await io.mkdtemp(path.join(base,'mcp-images-'));}
    const file=path.join(directory,randomUUID()+'.network-response');
    try {
     // MCP reads its existing response buffer; this does not fetch url again.
     // The official API buffers the whole body before writing: these are
     // acceptance limits AFTER that read, not claimed transfer/memory limits.
     const result=await callTool('get_network_request',{pageId,reqid:request.requestId,responseFilePath:file});
     const item=result.structuredContent?.networkRequest;
     if(!item||item.requestId!==request.requestId||item.url!==url||item.method!=='GET'||String(item.status)!=='200'||
       typeof item.responseBodyFilePath!=='string'||!samePath(item.responseBodyFilePath,file)){reason('response_body_unavailable');continue;}
     const {bytes,mime}=await readBoundedImage(io,file,Math.min(maxImageBytes,maxTotalBytes-totalBytes));
     // Only MIME is inspected; no headers, raw MCP response or request data are
     // retained in cache, results, error logs, screenshots or collection output.
     const declared=String(item.responseHeaders?.['content-type']||'').split(';')[0].trim().toLowerCase();
     if(declared&&declared!==mime){reason('image_mime_mismatch');continue;}
     images.set(url,{mime,base64:bytes.toString('base64')});totalBytes+=bytes.length;counts.newlySaved++;
    }catch(error) {
     reason(safeFileReasons.has(error.message)?error.message:error.code==='connection_lost'||error.code==='connection_timeout'||error.reason==='connection_required'?'connection_required':'response_body_unavailable');
     if(error.code==='connection_lost'||error.code==='connection_timeout'||error.reason==='connection_required')break;
    }finally{await removeOwned(io,file,directory);}
   }
   return counts;
  }catch(error){reason(error.reason==='connection_required'?'connection_required':'image_collection_unavailable');return counts;}
  finally {
   counts.cached=images.size;counts.byteCount=totalBytes;counts.missing=[...allowedUrls].filter(url=>!images.has(url)&&!reusedUrls.has(url)).length;
   if(directory)try{await io.rmdir(directory);}catch{}
   busy=false;
  }
 }
 const collect=({shopName,skipUrls=[],blockedUrls=[],imageUrls=null}={})=>collectObserved(readLoadedStoreImages,{shopName},'shop_dom_unverified',{skipUrls,blockedUrls,requestedUrls:Array.isArray(imageUrls)?imageUrls:null});
 const collectSku=({goodsId,imageUrls=[],listModalToken}={})=>collectObserved(readLoadedSkuImages,{goodsId,imageUrls,...(listModalToken!==undefined?{listModalToken}:{})},'sku_dom_unverified',{requestedUrls:Array.isArray(imageUrls)?imageUrls:[]});
 function discard(){
  const imageCount=images.size,byteCount=totalBytes;
  if(busy)return {discarded:false,imageCount,byteCount,reason:'image_collection_busy'};
  // Caller has already persisted the product. Release only this Node cache;
  // keep request-attempt evidence so a failed image cannot be retried by reset.
  images.clear();totalBytes=0;
  return {discarded:true,imageCount,byteCount,reason:null};
 }
 return {images,collect,collectSku,discard,flush:async()=>Object.fromEntries(images)};
}

export async function saveMcpScreenshot({project,pageId,file,callTool,io=fs}) {
 if(!Number.isSafeInteger(pageId))return {saved:false,reason:'page_unavailable'};
 const absolute=path.resolve(file),ext=path.extname(absolute).toLowerCase();
 if(!inside(project,absolute)||!['.jpg','.jpeg','.png'].includes(ext))return {saved:false,reason:'screenshot_path_rejected'};
 const format=ext==='.png'?'png':'jpeg';let directory,temporary;
 try {
  await io.mkdir(path.dirname(absolute),{recursive:true});directory=await io.mkdtemp(path.join(path.dirname(absolute),'mcp-screenshot-'));
  temporary=path.join(directory,'capture.'+format);
  await callTool('take_screenshot',{pageId,filePath:temporary,format,...(format==='jpeg'?{quality:70}:{}),fullPage:false});
  // MCP forces .jpeg, even if the requested destination used .jpg.
  const {bytes,mime}=await readBoundedImage(io,temporary,MAX_IMAGE_BYTES);
  if(mime!=='image/'+format)return {saved:false,reason:'screenshot_invalid_image'};
  await io.writeFile(absolute,bytes);return {saved:true,reason:null,byteCount:bytes.length};
 }catch(error){return {saved:false,reason:error.code==='ENOENT'?'screenshot_file_missing':error.reason==='connection_required'?'connection_required':'screenshot_unavailable'};}
 finally{if(directory){await removeOwned(io,temporary,directory);try{await io.rmdir(directory);}catch{}}}
}
