// Authorized current DOM only. No HTTP, hidden application state, reload or login.
import * as fs from 'node:fs/promises';
import { shopIdentityFromUrl } from './live_capture_driver.mjs';

// Self-contained so cua can serialize this function into the current document.
export function readRenderedDom({shopName}) {
 const y=document.scrollingElement.scrollTop,h=innerHeight,w=innerWidth;
 const list=document.querySelector('.waterfall-list-container_egohG8wQ');
 const listTop=list?list.getBoundingClientRect().top+y:Infinity;
 const nonhidden=(element,rect)=>{
  if(!rect||![rect.width,rect.height,rect.top,rect.left].every(Number.isFinite)||rect.width<=0||rect.height<=0)return false;
  for(let current=element;current;current=current.parentElement){
   const style=getComputedStyle(current);
   if(current.hidden||current.getAttribute?.('aria-hidden')==='true'||style.display==='none'||style.visibility==='hidden'||style.visibility==='collapse'||Number(style.opacity)===0)return false;
  }
  return typeof element.checkVisibility==='function'?element.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}):true;
 };
 const leaves=Array.from(document.querySelectorAll('div,p,span')).filter(e=>e.children.length===0);
 const points=label=>leaves.filter(e=>e.textContent.trim()===label).map(e=>{
  const r=e.getBoundingClientRect();return {documentTop:r.top+y,top:r.top,bottom:r.bottom,rendered:nonhidden(e,r),visible:nonhidden(e,r)&&r.bottom>0&&r.top<h};
 }).filter(point=>point.rendered&&point.documentTop>=listTop);
 const recommendations=points('其他店铺的精选推荐');
 const recommendationTop=recommendations.length?Math.min(...recommendations.map(p=>p.documentTop)):Infinity;
 const boundaries=points('本店暂无更多商品').filter(p=>p.documentTop<=recommendationTop);
 const boundaryTop=boundaries.length?Math.min(...boundaries.map(p=>p.documentTop)):Infinity;
 const cutoff=Math.min(boundaryTop,recommendationTop);
 const columns=Array.from(list?.children||[]),columnCards=columns.map(c=>Array.from(c.children));
 const nodes=Array.from(list?.querySelectorAll('.goodsItem_R1ok0MpS')||[]);
 const columnLayoutVerified=columns.length>0&&columnCards.flat().length===nodes.length&&
  columnCards.every(rows=>rows.every(child=>child.matches('.goodsItem_R1ok0MpS')))&&nodes.every(node=>columns.includes(node.parentElement));
 const manifest=[],cards=[];
 const validUrl=raw=>{try{const u=new URL(raw,location.href);return ['http:','https:'].includes(u.protocol)?u.href:null;}catch{return null;}};
 const placeholderTitle=title=>!title||/^(?:加载中|正在加载|商品加载中|loading)[.…\s]*$/i.test(title);
 for(let column=0;column<columnCards.length;column++)for(let row=0;row<columnCards[column].length;row++){
  const e=columnCards[column][row],r=e.getBoundingClientRect(),slot=`${column}:${row}`;
  const position={top:Math.round(r.top+y),left:Math.round(r.left),bottom:Math.round(r.bottom+y),viewportTop:Math.round(r.top),viewportBottom:Math.round(r.bottom)};
  if(!nonhidden(e,r)){manifest.push({slot,domColumn:column,domRow:row,state:'excluded_hidden',reason:'no rendered nonhidden box'});continue;}
  if(r.top+y>=cutoff){manifest.push({slot,domColumn:column,domRow:row,state:'excluded_after_boundary',position});continue;}
  const title=e.querySelector('.goodsName_dT8mZSNd')?.textContent?.trim()||'';
  const tip=e.querySelector('.salesTip_qUlLfJr3')?.textContent?.trim()||null;
  const image=e.querySelector('.goodsImage_fU27dxbk img');
  const rawImageUrl=image?.getAttribute('data-src')||image?.getAttribute('src')||null;
  const imageUrl=rawImageUrl?validUrl(rawImageUrl):null;
  const anchor=e.querySelector('a[href]')||e.closest('a[href]');
  const goodsUrl=anchor?.href?validUrl(anchor.href):null;
  let goodsId=null;
  try{const ids=goodsUrl?new URL(goodsUrl).searchParams.getAll('goods_id'):[];if(ids.length===1&&/^[1-9]\d*$/.test(ids[0]))goodsId=ids[0];}catch{}
  const priceNodes=Array.from(e.querySelectorAll('.priceDesc_Rt9mZDhD,.priceIcon_vjoyxmSd,.price_af78GRrq,.fraction_EQGvgNnL'));
  const priceRaw=priceNodes.map(n=>n.textContent).join('')||null;
  const pendingReasons=[];
  if(placeholderTitle(title))pendingReasons.push('title_not_ready');
  if(!imageUrl)pendingReasons.push('original_image_url_not_ready');
  if(priceNodes.length&&!priceRaw?.trim())pendingReasons.push('price_element_not_ready');
  if(e.getAttribute('aria-busy')==='true')pendingReasons.push('card_marked_busy');
  const state=pendingReasons.length?'pending':'ready';
  const card={slot,domColumn:column,domRow:row,title,salesRaw:tip&&/^(已拼|已抢|总售|已售|售出)/.test(tip)?tip:null,
   displayTipRaw:tip,priceRaw,imageUrl,rawImageUrl,goodsUrl,goodsId,
   goodsIdEvidence:goodsId?'Public goods link on rendered DOM card':'No verified public goods link or ID on rendered DOM card',
   rawText:e.innerText,discountRaw:e.querySelector('.goodsTags_yRLrsv4G')?.innerText||null,
   shippingRaw:title.match(/【[^】]*(?:发货|发完)[^】]*】/)?.[0]||null,
   position,inViewport:r.bottom>0&&r.top<h,ready:state==='ready',pendingReasons,
   identityTitle:placeholderTitle(title)?null:title};
  // cua's transport marks any repeated object reference as [Circular], even
  // when it is an acyclic alias. Each returned branch must own its objects.
  cards.push(card);manifest.push({slot,domColumn:column,domRow:row,state,position:{...position},pendingReasons:[...pendingReasons]});
 }
 const text=document.body.innerText;
 return {at:new Date().toISOString(),pageUrl:location.href,shopVerified:text.includes(shopName),listPresent:!!list,
  storeSalesRaw:text.match(/本店(?:已拼|总售)[^\n]*/)?.[0]||null,
  scrollTop:y,viewportHeight:h,viewportWidth:w,scrollHeight:document.scrollingElement.scrollHeight,
  columnLayoutVerified,columnSlotCounts:columnCards.map(rows=>rows.length),loadedCardCount:nodes.length,
  slotManifest:manifest,cards,readCoverageBottom:cards.length?Math.max(...cards.map(c=>c.position.bottom)):null,
  endBoundaryObserved:boundaries.some(b=>b.visible),boundaries,recommendations,
  recommendationCutoff:Number.isFinite(cutoff)?cutoff:null};
}

const finite=value=>typeof value==='number'&&Number.isFinite(value);
const validPosition=p=>p&&[p.top,p.left,p.bottom,p.viewportTop,p.viewportBottom].every(finite)&&p.bottom>p.top;
const identityFields=['identityTitle','imageUrl','goodsUrl'];
const relevant=slot=>slot.state==='ready'||slot.state==='pending';

export async function createRenderedCapture(tab,directory,{
 sourceUrl,checkpointReference,attemptId=null,shopName,browserName='Codex In-app Browser',browserTool='mcp__cua_repl',maxScrollViewports=3,
 resumeV4=null,
}={}){
 if(resumeV4!==null)throw new Error('v4 resume unsupported: same-document continuity was not recorded; seal v4 separately and start a fresh v5 scan');
 if(!['mcp__cua_repl','pdd_local_chrome_worker'].includes(browserTool))throw new Error('Unsupported browser evidence tool');
 if(!sourceUrl||!checkpointReference||typeof shopName!=='string'||!shopName.trim())throw new Error('Verified sourceUrl, explicit shopName and durable checkpoint reference are required');
 if(!finite(maxScrollViewports)||maxScrollViewports<=0||maxScrollViewports>100)throw new Error('maxScrollViewports must be >0 and <=100; scroll remains bounded by previously read coverage');
 const expectedIdentity=shopIdentityFromUrl(sourceUrl);
 await fs.mkdir(directory,{recursive:true});
 if((await fs.readdir(directory)).some(name=>/^batch_\d+\.json$/.test(name)||name==='snapshot.json'))throw new Error('Fresh empty checkpoint directory required');
 let slots=new Map(),rows=new Map(),positionAnchors=new Map(),batches=[],verifiedBatches=[],stop=null,ended=false,busy=false,dimensions=null,noProgress=0;
 let storeSalesRaw=null;
 const status=()=>({driverVersion:5,batches:batches.length,verifiedBatches:verifiedBatches.length,cards:rows.size,
  pending:[...slots.values()].filter(s=>s.state==='pending').length,noProgressReads:noProgress,ended,stop,busy,last:verifiedBatches.at(-1)?.scrollTop,
  readCoverageBottom:verifiedBatches.at(-1)?.readCoverageBottom??null});
 const terminal=()=>({stopped:true,...status()});

 async function captureInternal(scrollEvidence=null){
  let page;
  try{page=await tab.playwright.evaluate(readRenderedDom,{shopName});}catch(error){stop='page_read_failed';throw error;}
  let failure=null;const fail=reason=>{failure ||= reason;};
  const previous=verifiedBatches.at(-1),timestamp=Date.parse(page?.at);
  if(!finite(timestamp)||(previous&&timestamp<Date.parse(previous.at))) {stop='invalid_capture_timestamp';throw new Error(stop);}
  let observedIdentity=null,identityVerified=false;
  try{observedIdentity=shopIdentityFromUrl(page.pageUrl);const actual=expectedIdentity.identity_kind==='mall_id'?observedIdentity.source_mall_id:observedIdentity.source_mall_sn;
   identityVerified=observedIdentity.origin===expectedIdentity.origin&&actual===expectedIdentity.stable_identifier;
   if(!identityVerified)fail('shop_identity_changed');
  }catch{fail('shop_identity_unverified');}
  if(!page.shopVerified)fail('shop_state_changed');
  if(!page.listPresent)fail('target_list_missing');
  if(![page.scrollTop,page.viewportWidth,page.viewportHeight,page.scrollHeight].every(finite)||page.viewportWidth<=0||page.viewportHeight<=0)fail('invalid_viewport');
  if(!dimensions){if(page.scrollTop>2)fail('scan_not_started_at_top');}
  else if(dimensions[0]!==page.viewportWidth||dimensions[1]!==page.viewportHeight)fail('viewport_changed');
  if(previous&&page.scrollTop<previous.scrollTop-2)fail('unexpected_reverse_scroll');
  if(previous&&page.scrollTop>previous.scrollTop+2&&!scrollEvidence)fail('unrecorded_scroll');
  if(scrollEvidence&&page.scrollTop>scrollEvidence.maxAllowedScrollTop+2)fail('scroll_beyond_read_coverage');
  const counts=page.columnSlotCounts,manifest=page.slotManifest,cards=page.cards;
  if(!page.columnLayoutVerified)fail('unrecognized_column_layout');
  if(!Array.isArray(counts)||!counts.length||counts.some(n=>!Number.isInteger(n)||n<0)||
     !Array.isArray(manifest)||!Array.isArray(cards)){stop='unrecognized_column_layout';throw new Error(stop);}
  const boundaryEvidence=Array.isArray(page.boundaries)?page.boundaries:[];
  const visibleBoundary=boundaryEvidence.some(b=>b.rendered===true&&b.visible===true&&[b.top,b.bottom,b.documentTop].every(finite)&&
   b.bottom>0&&b.top<page.viewportHeight&&Math.abs(b.documentTop-(b.top+page.scrollTop))<=2);
  if(typeof page.endBoundaryObserved!=='boolean'||page.endBoundaryObserved!==visibleBoundary)fail('inconsistent_boundary_evidence');
  const cutoffs=[...boundaryEvidence,...(Array.isArray(page.recommendations)?page.recommendations:[])].filter(b=>b.rendered===true&&finite(b.documentTop)).map(b=>b.documentTop);
  const cutoff=cutoffs.length?Math.min(...cutoffs):Infinity;
  if(counts.reduce((a,b)=>a+b,0)!==page.loadedCardCount||manifest.length!==page.loadedCardCount)fail('incomplete_slot_manifest');
  if(previous&&(counts.length!==previous.columnSlotCounts.length||counts.some((n,i)=>n<previous.columnSlotCounts[i])))fail('column_list_replaced_or_virtualized');
  const manifestMap=new Map(),cardMap=new Map(),positionKeys=new Set();
  for(const entry of manifest){
   const {domColumn:column,domRow:row}=entry;
   if(!Number.isInteger(column)||!Number.isInteger(row)||column<0||column>=counts.length||row<0||row>=counts[column]||entry.slot!==`${column}:${row}`||manifestMap.has(entry.slot)||
      !['ready','pending','excluded_hidden','excluded_after_boundary'].includes(entry.state))fail('invalid_or_duplicate_slot');
   manifestMap.set(entry.slot,entry);
  }
  for(const card of cards){
   const entry=manifestMap.get(card.slot);
   if(!entry||!relevant(entry)||cardMap.has(card.slot)||card.domColumn!==entry.domColumn||card.domRow!==entry.domRow||!validPosition(card.position))fail('invalid_card_manifest');
   if(card.ready!==(entry?.state==='ready')||(card.ready&&(!card.identityTitle||!card.imageUrl||card.pendingReasons?.length)))fail('invalid_readiness');
   if(card.position?.top>=cutoff)fail('card_after_shop_boundary_or_recommendations');
   if(entry?.position&&(entry.position.top!==card.position?.top||entry.position.left!==card.position?.left))fail('manifest_position_conflict');
   const key=`position_${card.position?.top}_${card.position?.left}`;
   if(positionKeys.has(key))fail('duplicate_document_position');positionKeys.add(key);
   cardMap.set(card.slot,card);
  }
  if([...manifestMap.values()].filter(relevant).some(entry=>!cardMap.has(entry.slot)))fail('card_manifest_incomplete');
  const computedBottom=cards.length?Math.max(...cards.map(card=>card.position?.bottom)):null;
  if(page.readCoverageBottom!==computedBottom)fail('read_coverage_mismatch');
  for(const [slot,prior] of slots){
   const current=manifestMap.get(slot),card=cardMap.get(slot);
   if(!current)fail('previous_slot_missing');
   if(relevant(prior)){
    if(!current||!relevant(current)||!card)fail('previous_rendered_slot_hidden_or_removed');
    else{
     // Compare to the first rendered slot, never the previous rounded position.
     // This admits one CSS-pixel rounding jitter without permitting cumulative drift.
     const anchor=positionAnchors.get(slot);
     if(!anchor||['top','left'].some(key=>Math.abs(anchor[key]-card.position[key])>1))fail('document_layout_changed');
     for(const field of identityFields)if(prior.identity[field]!==null&&prior.identity[field]!==card[field])fail('slot_content_conflict');
     if(prior.state==='ready'&&!card.ready)fail('ready_card_became_pending');
    }
   }
  }
  let nextSlots=new Map(slots),nextRows=new Map(rows),nextPositionAnchors=new Map(positionAnchors),added=0,pending=0,newRelevantSlots=0;
  if(!failure)for(const entry of manifest){
   const prior=slots.get(entry.slot),card=cardMap.get(entry.slot);
   if(relevant(entry)){
    if(!nextPositionAnchors.has(entry.slot))nextPositionAnchors.set(entry.slot,{top:card.position.top,left:card.position.left});
    if(!prior||!relevant(prior))newRelevantSlots++;
    const firstRenderedAt=prior?.firstRenderedAt||page.at;
    nextSlots.set(entry.slot,{...entry,identity:Object.fromEntries(identityFields.map(field=>[field,card[field]??null])),firstRenderedAt});
    if(!card.ready){pending++;continue;}
    const oldRow=rows.get(entry.slot);
    if(!oldRow)added++;
    nextRows.set(entry.slot,{...card,recordKey:`position_${card.position.top}_${card.position.left}`,
     firstRenderedAt,firstObservedAt:oldRow?.firstObservedAt||page.at,
     observedAt:page.at,observedAtPrecision:'batch_read',firstObservedAtPrecision:'batch_read',
     observationTimeMeaning:'latest actual rendered DOM batch read; not listing time',latestBatch:batches.length+1});
   }else nextSlots.set(entry.slot,{...entry});
  }
  const required=[...manifestMap.values()].filter(relevant);
  const coverageReady=page.endBoundaryObserved&&required.length>0&&required.every(s=>s.state==='ready'&&nextRows.has(s.slot));
  let nextNoProgress=added||newRelevantSlots?0:noProgress;
  if(scrollEvidence){
   const reachedFrontier=page.scrollTop+page.viewportHeight>=(previous?.readCoverageBottom??Infinity)-2;
   const stuck=previous&&Math.abs(page.scrollTop-previous.scrollTop)<=2;
   const waitingAtBoundary=page.endBoundaryObserved&&!coverageReady;
   nextNoProgress=added||newRelevantSlots?0:(reachedFrontier||stuck||waitingAtBoundary?noProgress+1:0);
   if(nextNoProgress>=3&&!coverageReady)fail(waitingAtBoundary?'three_boundary_reads_without_ready_progress':'three_frontier_reads_without_ready_progress');
  }
  const complete=coverageReady&&!failure;
  const batch={batch:batches.length+1,driverVersion:5,...page,shopIdentityVerified:identityVerified,
   observedShopIdentity:observedIdentity,expectedShopIdentity:expectedIdentity,scrollEvidence,
   added:failure?0:added,seen:failure?rows.size:nextRows.size,pending:failure?status().pending:pending,
   stop:failure,complete,noProgressReads:nextNoProgress,mode:'rendered_nonhidden_dom_including_offviewport'};
  try{const handle=await fs.open(`${directory}/batch_${String(batch.batch).padStart(4,'0')}.json`,'wx');
   try{await handle.writeFile(JSON.stringify(batch,null,2));await handle.sync();}finally{await handle.close();}
  }catch(error){stop='checkpoint_write_failed';throw error;}
  batches.push(batch);
  if(failure){stop=failure;noProgress=nextNoProgress;return terminal();}
  slots=nextSlots;rows=nextRows;positionAnchors=nextPositionAnchors;noProgress=nextNoProgress;verifiedBatches.push(batch);
  dimensions ||= [page.viewportWidth,page.viewportHeight];storeSalesRaw=page.storeSalesRaw||storeSalesRaw;ended=complete;
  return {batch:batch.batch,at:page.at,top:page.scrollTop,loaded:page.loadedCardCount,added,seen:rows.size,pending,end:ended,stop};
 }
 async function capture(){
  if(busy)throw new Error('Capture operation already in progress');if(stop||ended)return terminal();
  busy=true;try{return await captureInternal();}finally{busy=false;}
 }
 async function step(){
  if(busy)throw new Error('Capture operation already in progress');if(stop||ended)return terminal();
  const previous=verifiedBatches.at(-1);if(!previous)throw new Error('Call capture() at the top before step()');
  const maxAllowedScrollTop=Math.max(previous.scrollTop,Math.min(previous.readCoverageBottom??previous.scrollTop,previous.scrollHeight-previous.viewportHeight));
  const delta=Math.max(0,Math.min(previous.viewportHeight*maxScrollViewports,maxAllowedScrollTop-previous.scrollTop));
  const evidence={kind:delta>0?'normal_scroll':'stationary_read',from:previous.scrollTop,readCoverageBottom:previous.readCoverageBottom,maxAllowedScrollTop,
   requestedPixels:delta,requestedViewportUnits:delta/previous.viewportHeight};
  busy=true;
  try{const x=Math.max(1,Math.min(500,dimensions[0]-50)),y=Math.max(1,Math.min(500,dimensions[1]-80));
   if(delta>0)try{await tab.scroll([x,y],'down',evidence.requestedViewportUnits);}catch(error){stop='scroll_failed';throw error;}
   return await captureInternal(evidence);
  }finally{busy=false;}
 }
 async function snapshot(reason=null){
  if(busy)throw new Error('Wait for the active operation before sealing');
  if(!verifiedBatches.length||!rows.size)throw new Error('No ready verified cards; record failed attempt instead of an empty run');
  const sorted=[...rows.values()].sort((a,b)=>a.position.top-b.position.top||a.position.left-b.position.left).map((row,i)=>({...row,viewOrder:i+1}));
  const complete=ended&&!stop&&!reason;
  return {shopName,sourceUrl,...(expectedIdentity.identity_kind==='mall_id'?{mallId:expectedIdentity.stable_identifier}:{}),
   shopIdentityEvidence:{...expectedIdentity,basis:'operator-verified target URL and matching storefront origin/key on every accepted rendered DOM batch'},
   sort:'上新',observedFrom:verifiedBatches[0].at,observedTo:verifiedBatches.at(-1).at,
   status:complete?'complete':'partial',endBoundaryObserved:verifiedBatches.at(-1).endBoundaryObserved,storeSalesRaw,
   collectionEvidence:{tool:browserTool,browser:browserName,tabId:tab.id,attemptId,driverVersion:5,
    ...(tab.executionEvidence?{browserAdapter:{...tab.executionEvidence}}:{}),
    mode:'fresh page, all rendered nonhidden shop-list DOM including offviewport; normal scroll bounded by inspected list coverage',
    stopReason:reason||stop,batchCount:batches.length,verifiedBatchCount:verifiedBatches.length,
    pendingSlotCount:status().pending,pendingSlots:[...slots.values()].filter(s=>s.state==='pending'),
    cardIdentity:'column-local actual DOM slot and document top/left; no title/image merging',
    timestampPrecision:'batch_read',rowTimeMeaning:'latest actual batch read; firstObservedAt retained separately; neither is listing time',
    checkpointDirectory:checkpointReference,imagesDownloaded:false,maxScrollViewports,
    completionRule:'viewport-visible shop end marker plus all current nonhidden pre-boundary slots ready and covered',
    lastAttemptAt:batches.at(-1)?.at,
    shopIdentityChecks:batches.map(b=>({at:b.at,pageUrl:b.pageUrl,verified:b.shopIdentityVerified,stop:b.stop}))},
   scrolls:verifiedBatches.map(b=>({at:b.at,count:b.seen,pending:b.pending,scrollTop:b.scrollTop,end:b.endBoundaryObserved})),rows:sorted};
 }
 return {capture,step,snapshot,status};
}

export const createCapture=createRenderedCapture;
