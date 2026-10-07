// Repair only missing exact-original shop images already rendered in Chrome.
// No navigation, search, HTTP request, API, credentials, or image substitution.
import {guard,ReviewError,delay} from './local_browser.mjs';
import {verifyStoreIdentity} from './local_storefront.mjs';
import {pddImageUrl} from './loaded_image_cache.mjs';

export function exactDisplayedSales(raw){
 const match=typeof raw==='string'&&/^(已拼|已抢)\s*([0-9]+(?:,[0-9]{3})*)\s*(件|个)$/.exec(raw.trim());
 if(!match)return null;
 const value=Number(match[2].replace(/,/g,''));
 return Number.isSafeInteger(value)?{value,unit:match[3],label:match[1]}:null;
}
const originalImage=row=>row.rawImageUrl||row.imageUrl||row.image_url||null;
const salesRaw=row=>row.salesRaw??row.sales_raw??null;
const goodsId=row=>/^[1-9]\d*$/.test(String(row.goodsId??row.goods_id??''))?String(row.goodsId??row.goods_id):null;

// Allocate bounded time to actionable missing cards, not the whole shop size.
export function imageRepairBudgetMs({rows=[],skipUrls=[],blockedUrls=[],cachedImages=null}={}){
 const approved=new Set(skipUrls),blocked=new Set(blockedUrls);
 const missing=rows.filter(row=>{
  const url=originalImage(row);
  return pddImageUrl(url)&&!blocked.has(url)&&!approved.has(url)&&!cachedImages?.has(url);
 }).length;
 return Math.min(600000,Math.max(120000,missing*6000));
}

// Sales is a within-this-shop disambiguator, never permission to change an
// original image, merge cards, or claim a cross-day product identity.
export function matchRepairCard(row,cards){
 const image=originalImage(row),title=row.title;
 const candidates=cards.filter(card=>card.title===title&&card.imageUrl===image);
 const base={candidate_count:candidates.length};
 if(!image)return {...base,ok:false,reason:'source_image_missing'};
 if(!candidates.length)return {...base,ok:false,reason:'original_card_not_found'};
 const id=goodsId(row);
 if(id&&candidates.some(card=>goodsId(card)&&goodsId(card)!==id))return {...base,ok:false,reason:'goods_id_conflict'};
 if(candidates.length===1)return {...base,ok:true,candidate:candidates[0],match_basis:'exact_title_original_image',sales_distance:null};
 const targetSales=exactDisplayedSales(salesRaw(row)),numbers=candidates.map(card=>exactDisplayedSales(salesRaw(card)));
 if(!targetSales||numbers.some(value=>!value||value.unit!==targetSales.unit))return {...base,ok:false,reason:'sales_unknown_or_incomparable'};
 const distances=numbers.map(value=>Math.abs(value.value-targetSales.value)),minimum=Math.min(...distances);
 if(distances.filter(value=>value===minimum).length!==1)return {...base,ok:false,reason:'nearest_sales_tied'};
 return {...base,ok:true,candidate:candidates[distances.indexOf(minimum)],match_basis:'exact_title_original_image_unique_nearest_sales',sales_distance:minimum};
}

// Main-image loading alone may reveal any exact shared image reference. This
// does not identify a product/card or authorize copying its sales or goods ID.
// Keep matchRepairCard strict for every caller that requires card identity.
export function matchRepairImageReference(row,cards){
 const match=matchRepairCard(row,cards);
 if(match.ok||!['sales_unknown_or_incomparable','nearest_sales_tied'].includes(match.reason))return match;
 const image=originalImage(row),candidates=cards.filter(card=>card.title===row.title&&card.imageUrl===image);
 if(!pddImageUrl(image))return {...match,reason:'invalid_original_image_url'};
 const ids=new Set(candidates.map(goodsId).filter(Boolean));
 if(ids.size>1)return {...match,reason:'goods_id_conflict'};
 if(candidates.some(card=>!Number.isSafeInteger(card.index)||card.index<0)||new Set(candidates.map(card=>card.index)).size!==candidates.length)return {...match,reason:'image_reference_index_invalid'};
 const candidate=candidates.reduce((first,card)=>card.index<first.index?card:first);
 return {ok:true,candidate_count:candidates.length,candidate,image_reference_only:true,match_basis:'shared_exact_original_image_reference',sales_distance:null};
}

// Self-contained public DOM function, serialized by the ordinary Chrome
// adapter. A revealed target is checked against its whole candidate group.
export function readRepairStoreDom({shopName,reveal=null}){
 const shown=element=>{
  const r=element.getBoundingClientRect();if(!(r.width>0&&r.height>0))return false;
  for(let e=element;e;e=e.parentElement){const s=getComputedStyle(e);if(e.hidden||e.getAttribute?.('aria-hidden')==='true'||s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
  return true;
 };
 const result={url:location.href,shopVerified:document.body.innerText.includes(shopName),latestSelected:false,boundaryVerified:false,cards:[],reveal:null};
 const lists=[...document.querySelectorAll('.waterfall-list-container_egohG8wQ')];
 result.listCount=lists.length;result.mainListCount=0;
 if(location.origin!=='https://mobile.yangkeduo.com'||location.pathname!=='/mall_page.html'||!shopName||!result.shopVerified)return result;
 const y=window.scrollY;
 const markers=[...document.querySelectorAll('div,p,span')].filter(e=>!e.children.length&&['本店暂无更多商品','其他店铺的精选推荐'].includes(e.textContent.trim())&&shown(e)).map(e=>({label:e.textContent.trim(),top:e.getBoundingClientRect().top+y}));
 const recommendationTop=Math.min(Infinity,...markers.filter(m=>m.label==='其他店铺的精选推荐').map(m=>m.top));
 const boundaries=markers.filter(m=>m.label==='本店暂无更多商品'&&m.top<=recommendationTop);
 const cutoff=Math.min(recommendationTop,...boundaries.map(m=>m.top));
 // Recommendations can mount a second list at the bottom. Only the unique
 // rendered list before the shop/recommendation boundary is eligible.
 const mainLists=lists.filter(list=>shown(list)&&list.getBoundingClientRect().top+y<cutoff);
 result.mainListCount=mainLists.length;
 if(mainLists.length!==1)return result;
 const list=mainLists[0],listTop=list.getBoundingClientRect().top+y;
 const tabs=[...document.querySelectorAll('button,a,[role="button"],[role="tab"],li,div,span')].filter(e=>shown(e)&&e.textContent.trim()==='上新');
 const latest=tabs.find(e=>!tabs.some(child=>child!==e&&e.contains(child)));
 const selected=e=>e&&(e.getAttribute('aria-selected')==='true'||/(?:^|\s|_)current(?:\s|_|$)|(?:^|\s|_)active(?:\s|_|$)/i.test(e.className||''));
 result.latestSelected=!!(selected(latest)||selected(latest?.parentElement));
 result.boundaryVerified=boundaries.some(marker=>marker.top>=listTop);
 const nodes=[...list.querySelectorAll('.goodsItem_R1ok0MpS')].filter(card=>shown(card)&&card.getBoundingClientRect().top+y<cutoff);
 result.cards=nodes.map((card,index)=>{
  const img=card.querySelector('.goodsImage_fU27dxbk img'),anchor=card.querySelector('a[href]')||card.closest('a[href]');
  let goodsId=null;try{const url=anchor?new URL(anchor.href):null,ids=url?.searchParams.getAll('goods_id');if(url?.origin==='https://mobile.yangkeduo.com'&&/^\/goods1?\.html$/.test(url.pathname)&&ids.length===1&&/^[1-9]\d*$/.test(ids[0]))goodsId=ids[0];}catch{}
  return {index,title:card.querySelector('.goodsName_dT8mZSNd')?.textContent.trim()||'',imageUrl:img?.getAttribute('data-src')||img?.getAttribute('src')||null,salesRaw:card.querySelector('.salesTip_qUlLfJr3')?.textContent.trim()||null,goodsId,
   actualSrc:img?.currentSrc||null,loaded:!!(img?.complete&&img.naturalWidth>0&&img.naturalHeight>0)};
 });
 if(reveal&&result.latestSelected&&result.boundaryVerified){
  const group=result.cards.filter(card=>card.title===reveal.title&&card.imageUrl===reveal.imageUrl);
  const signature=JSON.stringify(group.map(({index,title,imageUrl,salesRaw,goodsId})=>({index,title,imageUrl,salesRaw,goodsId})));
  const card=result.cards.find(card=>card.index===reveal.index);
  if(signature!==reveal.signature||!card||card.title!==reveal.title||card.imageUrl!==reveal.imageUrl)result.reveal={ok:false,reason:'candidate_changed'};
  else{
   nodes[card.index].scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
   const r=nodes[card.index].getBoundingClientRect();
   result.reveal={ok:r.bottom>0&&r.top<innerHeight,reason:r.bottom>0&&r.top<innerHeight?null:'card_outside_viewport'};
  }
 }
 return result;
}

function signature(row,cards){return JSON.stringify(cards.filter(card=>card.title===row.title&&card.imageUrl===originalImage(row)).map(({index,title,imageUrl,salesRaw,goodsId})=>({index,title,imageUrl,salesRaw,goodsId})));}
function verifyPage(shop,page){
 verifyStoreIdentity(shop.source_url,page.url);
 const url=new URL(page.url);
 if(url.origin!=='https://mobile.yangkeduo.com'||url.pathname!=='/mall_page.html'||!page.shopVerified)throw new ReviewError('补图期间店铺来源变化，已停止。','manual_review','image_repair_source_changed');
 if(!page.latestSelected||!page.boundaryVerified)throw new ReviewError('补图未确认完整的本店上新列表，已停止。','manual_review','image_repair_list_unverified');
}

export async function repairStoreImages(browser,{shop,rows,skipUrls=[],blockedUrls=[]},{cancelled=async()=>false,progress=async()=>{},sleep=delay,now=Date.now,maxDurationMs=120000,maxAttempts=2}={}){
 if(!shop?.shop_name||!shop?.source_url||!Array.isArray(rows)||rows.length>10000||!Number.isFinite(maxDurationMs)||maxDurationMs<=0||!Number.isInteger(maxAttempts)||maxAttempts<1||maxAttempts>2)throw new ReviewError('补图任务参数无效。','manual_review','image_repair_invalid_request');
 const approved=new Set(skipUrls),blocked=new Set(blockedUrls),started=now(),sharedReferences=new Map();
 const hasImage=url=>!!url&&!blocked.has(url)&&(approved.has(url)||browser.images?.has(url));
 const items=rows.map((row,index)=>({view_order:row.viewOrder??row.view_order??index+1,title:row.title,image_url:originalImage(row),state:hasImage(originalImage(row))?'complete':'missing',reason:hasImage(originalImage(row))?null:blocked.has(originalImage(row))?'previous_attempt_blocked':!originalImage(row)?'source_image_missing':'not_checked',attempts:0}));
 const initialComplete=items.filter(item=>item.state==='complete').length;
 let checked=0,timedOut=false;
 const report=()=>({status:items.every(item=>item.state==='complete')?'complete':'partial',total:items.length,saved:items.filter(item=>item.state==='complete').length,complete:items.filter(item=>item.state==='complete').length,missing:items.filter(item=>item.state!=='complete').length,blocked:items.filter(item=>item.reason==='previous_attempt_blocked').length,repaired:items.filter(item=>item.state==='complete').length-initialComplete,checked,duration_ms:now()-started,timed_out:timedOut,items});
 const emit=()=>{const value=report();return progress({phase:'检查并补齐商品图片',stage:'images',image_total:value.total,image_saved:value.saved,image_missing:value.missing,image_attempted:checked,image_blocked:value.blocked});};
 await emit();
 for(let index=0;index<rows.length;index++){
  const row=rows[index],item=items[index],url=item.image_url;
  if(item.state==='complete')continue;
  if(await cancelled())throw new ReviewError('补图已取消，已采数据保留。','cancelled','operator_cancelled');
  if(blocked.has(url)||!url){checked++;continue;}
  if(!pddImageUrl(url)){item.reason='invalid_original_image_url';checked++;continue;}
  if(now()-started>=maxDurationMs){timedOut=true;item.reason='image_repair_time_budget';continue;}
  if(hasImage(url)){
   const shared=sharedReferences.get(url);
   if(shared?.title===row.title&&goodsId(row)&&shared.goods_ids.some(id=>id!==goodsId(row))){item.reason='goods_id_conflict';checked++;continue;}
   item.state='complete';item.reason=null;
   // Reuse already verified exact-URL bytes while retaining each original row.
   // The receipt describes only a shared image reference, never another card.
   if(shared?.title===row.title)Object.assign(item,{image_reference_only:true,match_basis:'shared_exact_original_image_reference',candidate_count:shared.candidate_count,sales_distance:null,matched_sales_raw:null,matched_goods_id:null});
   checked++;continue;
  }
  let stableSignature=null;
  for(let attempt=0;attempt<maxAttempts;attempt++){
   if(await cancelled())throw new ReviewError('补图已取消，已采数据保留。','cancelled','operator_cancelled');
   if(now()-started>=maxDurationMs){timedOut=true;item.reason='image_repair_time_budget';break;}
   await guard(browser,shop.shop_name);
   const page=await browser.evaluate(readRepairStoreDom,{shopName:shop.shop_name});verifyPage(shop,page);
   const match=matchRepairImageReference(row,page.cards);
   Object.assign(item,{candidate_count:match.candidate_count,match_basis:match.match_basis||null,sales_distance:match.sales_distance??null,...(match.image_reference_only?{image_reference_only:true}:{})});
   if(!match.ok){item.reason=match.reason;break;}
   const currentSignature=signature(row,page.cards);
   if(stableSignature&&stableSignature!==currentSignature)throw new ReviewError('补图候选商品变化，已停止并保留已采数据。','manual_review','image_repair_candidate_changed');
   stableSignature=currentSignature;
   item.matched_sales_raw=match.image_reference_only?null:match.candidate.salesRaw;item.matched_goods_id=match.image_reference_only?null:match.candidate.goodsId;item.actual_src=match.candidate.actualSrc;
   const revealed=await browser.evaluate(readRepairStoreDom,{shopName:shop.shop_name,reveal:{title:row.title,imageUrl:url,index:match.candidate.index,signature:stableSignature}});verifyPage(shop,revealed);
   if(!revealed.reveal?.ok){if(revealed.reveal?.reason==='candidate_changed')throw new ReviewError('补图候选商品变化，已停止并保留已采数据。','manual_review','image_repair_candidate_changed');item.reason=revealed.reveal?.reason||'card_not_revealed';break;}
   item.attempts++;
   let ready=false;const waitUntil=Math.min(started+maxDurationMs,now()+5000);
   for(let poll=0;poll<20;poll++){
    if(await cancelled())throw new ReviewError('补图已取消，已采数据保留。','cancelled','operator_cancelled');
    if(now()>=waitUntil){if(now()-started>=maxDurationMs){timedOut=true;item.reason='image_repair_time_budget';}break;}
    // Wait in place for this image; never trigger another page or list scroll.
    await sleep(Math.min(250,Math.max(0,waitUntil-now())));
    if(await cancelled())throw new ReviewError('补图已取消，已采数据保留。','cancelled','operator_cancelled');
    if(now()-started>=maxDurationMs){timedOut=true;item.reason='image_repair_time_budget';break;}
    await guard(browser,shop.shop_name);
    const confirmed=await browser.evaluate(readRepairStoreDom,{shopName:shop.shop_name});verifyPage(shop,confirmed);
    if(signature(row,confirmed.cards)!==stableSignature)throw new ReviewError('补图候选商品变化，已停止并保留已采数据。','manual_review','image_repair_candidate_changed');
    const target=confirmed.cards.find(card=>card.index===match.candidate.index);
    item.actual_src=target?.actualSrc||null;
    if(target?.loaded&&target.actualSrc===url){ready=true;break;}
   }
   if(!ready){if(item.reason!=='image_repair_time_budget')item.reason='store_image_not_loaded';break;}
   const images=await browser.collectImages({shopName:shop.shop_name,skipUrls,blockedUrls,imageUrls:[url]});
   if(images?.reasons?.connection_required)throw new ReviewError('补图时 Chrome 连接中断，已停止。','needs_browser','connection_required');
   if(images?.reasons?.shop_dom_unverified)throw new ReviewError('补图时店铺图片来源未通过校验，已停止。','manual_review','image_repair_source_changed');
   item.image_capture=images;
   if(hasImage(url)){item.state='complete';item.reason=null;if(match.image_reference_only)sharedReferences.set(url,{title:row.title,candidate_count:match.candidate_count,goods_ids:[...new Set(page.cards.filter(card=>card.title===row.title&&card.imageUrl===url).map(goodsId).filter(Boolean))]});break;}
   item.reason=Object.keys(images?.reasons||{})[0]||'image_not_saved';
   // Revisit only images not loaded yet. A rejected body, blocked request or
   // accepted-size limit is final; never try another request or URL for it.
   if(!['store_image_not_loaded','loaded_request_unavailable'].includes(item.reason))break;
  }
  checked++;await emit();
 }
 await emit();return report();
}
