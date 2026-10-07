// Use only from an authorized cua_repl session. No HTTP, cookies or hidden state.
// Before creating an attempt, the operator verifies this shop and active 上新 sort.
// This driver reads DOM evidence; it is not a browser-login or retry mechanism.
import * as fs from 'node:fs/promises';

export function shopIdentityFromUrl(value) {
 const url=new URL(value);
 if(!['http:','https:'].includes(url.protocol))throw new Error('sourceUrl must be the verified HTTP(S) shop URL');
 function one(key){
  const values=url.searchParams.getAll(key);
  if(!values.length)return null;
  if(values.some(value=>!value||value!==value.trim())||new Set(values).size!==1)throw new Error(`Ambiguous or empty storefront ${key}`);
  return values[0];
 }
 const mallId=one('mall_id'),mallSn=one('mall_sn');
 if(mallId!==null&&!/^[1-9]\d*$/.test(mallId))throw new Error('mall_id must be a positive integer');
 if(mallId===null&&mallSn===null)throw new Error('Stable mall_id or mall_sn storefront identity is required');
 return {identity_kind:mallId!==null?'mall_id':'mall_sn',stable_identifier:mallId??mallSn,
  source_mall_id:mallId,source_mall_sn:mallSn,origin:url.origin,source_url:url.href};
}

export async function createCapture(tab, directory, {
 sourceUrl, checkpointReference, attemptId=null, shopName, browserName='Codex In-app Browser',
}={}) {
 if(!sourceUrl || !checkpointReference || typeof shopName!=='string'||!shopName.trim()) throw new Error('Verified sourceUrl, explicit shopName and durable checkpoint reference are required');
 const expectedIdentity=shopIdentityFromUrl(sourceUrl);
 await fs.mkdir(directory,{recursive:true});
 const files=(await fs.readdir(directory)).filter(n=>/^batch_\d+\.json$/.test(n));
 if(files.length) throw new Error('Start a fresh attempt directory; never mix a new page load into old observation times');
 const batches=[]; let cards=new Map(),ended=false,stop=null,noNew=0,dimensions=null,storeSalesRaw=null,busy=false;
 const status=()=>({batches:batches.length,cards:cards.size,ended,stop,last:batches.at(-1)?.scrollTop,noNew,busy});
 const terminal=()=>({stopped:true,ended,stop});

 async function readPage(){
  return tab.playwright.evaluate(({shopName})=>{
   const y=document.scrollingElement.scrollTop,h=innerHeight,w=innerWidth;
   const list=document.querySelector('.waterfall-list-container_egohG8wQ');
   const rendered=(element,rect)=>rect.width>0&&rect.height>0&&(
    typeof element.checkVisibility==='function'?element.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}):
    getComputedStyle(element).visibility!=='hidden'&&getComputedStyle(element).display!=='none');
   const boundaryNodes=Array.from(document.querySelectorAll('div,p,span')).filter(e=>e.children.length===0&&e.textContent.trim()==='本店暂无更多商品');
   const listTop=list?list.getBoundingClientRect().top+y:Infinity;
   const boundaries=boundaryNodes.map(e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,documentTop:r.top+y,rendered:rendered(e,r),visible:rendered(e,r)&&r.bottom>0&&r.top<h};});
   // Hidden templates and boundaries before the target list are not evidence.
   const usable=boundaries.filter(b=>b.rendered&&b.documentTop>=listTop);
   const cutoff=usable.length?Math.min(...usable.map(b=>b.documentTop)):Infinity;
   const nodes=Array.from(list?.querySelectorAll('.goodsItem_R1ok0MpS')||[]);
   // The storefront appends to each waterfall column. A global traversal index
   // changes for every later column when the first column grows. Each column's
   // own DOM slot plus its document position is the within-scan address.
   const columns=Array.from(list?.children||[]);
   const columnCards=columns.map(column=>Array.from(column.children));
   const columnLayoutVerified=columns.length>0&&nodes.every(node=>{
    const columnIndex=columns.indexOf(node.parentElement);
    return columnIndex>=0&&columnCards[columnIndex].every(child=>child.matches('.goodsItem_R1ok0MpS'));
   });
   const columnCounts=columnCards.map(rows=>rows.filter(e=>{const r=e.getBoundingClientRect();return rendered(e,r)&&r.top+y<cutoff;}).length);
   const visible=nodes.map((e,index)=>({e,index,r:e.getBoundingClientRect()})).filter(({e,r})=>rendered(e,r)&&r.bottom>0&&r.top<h&&r.top+y<cutoff);
   const text=document.body.innerText;
   return {at:new Date().toISOString(),pageUrl:location.href,shopVerified:text.includes(shopName),listPresent:!!list,
    storeSalesRaw:text.match(/本店(?:已拼|总售)[^\n]*/)?.[0]||null,scrollTop:y,viewportHeight:h,viewportWidth:w,
    scrollHeight:document.scrollingElement.scrollHeight,loadedCardCount:nodes.length,columnLayoutVerified,columnCounts,
    endBoundaryObserved:usable.some(b=>b.visible),boundaries,recommendationText:text.includes('其他店铺的精选推荐'),
    cards:visible.map(({e,index,r})=>{
     const title=e.querySelector('.goodsName_dT8mZSNd')?.textContent?.trim()||'';
     const tip=e.querySelector('.salesTip_qUlLfJr3')?.textContent?.trim()||null;
     const image=e.querySelector('.goodsImage_fU27dxbk img');
     const anchor=e.querySelector('a[href]')||e.closest('a[href]');
     const goodsUrl=anchor?.href||null;
     let goodsId=null;
     try {const url=goodsUrl?new URL(goodsUrl):null,ids=url?.searchParams.getAll('goods_id');
      if(url&&['http:','https:'].includes(url.protocol)&&ids.length===1&&/^[1-9]\d*$/.test(ids[0]))goodsId=ids[0];
     } catch{}
     return {title,salesRaw:tip&&/^(已拼|已抢|总售|已售|售出)/.test(tip)?tip:null,displayTipRaw:tip,
      priceRaw:Array.from(e.querySelectorAll('.priceDesc_Rt9mZDhD,.priceIcon_vjoyxmSd,.price_af78GRrq,.fraction_EQGvgNnL')).map(n=>n.textContent).join('')||null,
      imageUrl:image?.getAttribute('data-src')||image?.getAttribute('src')||null,goodsUrl,goodsId,
      goodsIdEvidence:goodsId?'Visible goods link on DOM card':'No verified visible goods link or ID on DOM card',
      rawText:e.innerText,discountRaw:e.querySelector('.goodsTags_yRLrsv4G')?.innerText||null,
      shippingRaw:title.match(/【[^】]*(?:发货|发完)[^】]*】/)?.[0]||null,domIndex:index,
      domColumn:columns.indexOf(e.parentElement),domRow:columnCards[columns.indexOf(e.parentElement)]?.indexOf(e)??-1,
      position:{top:Math.round(r.top+y),left:Math.round(r.left),viewportTop:Math.round(r.top),viewportBottom:Math.round(r.bottom)}};
    })};
  },{shopName});
 }

 async function captureInternal(afterScroll=false){
  let page;
  try{page=await readPage();}catch(error){stop='page_read_failed';throw error;}
  let nextStop=stop;
  const fail=reason=>{nextStop ||= reason;};
  const prev=batches.at(-1),timestamp=Date.parse(page.at);
  if(!Number.isFinite(timestamp)||(prev&&timestamp<Date.parse(prev.at))){stop='invalid_capture_timestamp';throw new Error(stop);}
  let observedIdentity=null,identityVerified=false;
  try{
   observedIdentity=shopIdentityFromUrl(page.pageUrl);
   const observedKey=expectedIdentity.identity_kind==='mall_id'?observedIdentity.source_mall_id:observedIdentity.source_mall_sn;
   identityVerified=observedIdentity.origin===expectedIdentity.origin&&observedKey===expectedIdentity.stable_identifier;
   if(!identityVerified)fail('shop_identity_changed');
  }catch{fail('shop_identity_unverified');}
  if(!page.shopVerified)fail('shop_state_changed');
  if(!page.listPresent)fail('target_list_missing');
  if(![page.scrollTop,page.viewportWidth,page.viewportHeight,page.scrollHeight].every(Number.isFinite)||page.viewportWidth<=0||page.viewportHeight<=0)fail('invalid_viewport');
  if(!dimensions){if(page.scrollTop>2)fail('scan_not_started_at_top');}
  else if(dimensions[0]!==page.viewportWidth||dimensions[1]!==page.viewportHeight)fail('viewport_changed');
  if(prev&&page.scrollTop-prev.scrollTop>Math.min(page.viewportHeight,prev.viewportHeight)+2)fail('unobserved_scroll_gap');
  if(prev&&page.scrollTop<prev.scrollTop-2)fail('unexpected_reverse_scroll');
  if(prev&&page.loadedCardCount<prev.loadedCardCount)fail('dom_list_replaced_or_virtualized');
  if(!page.columnLayoutVerified||!Array.isArray(page.columnCounts)||!page.columnCounts.length||page.columnCounts.some(n=>!Number.isInteger(n)||n<0))fail('unrecognized_column_layout');
  if(prev&&(prev.columnCounts.length!==page.columnCounts?.length||prev.columnCounts.some((n,i)=>page.columnCounts[i]<n)))fail('column_list_replaced_or_virtualized');
  const nextCards=new Map(cards),slotKeys=new Map([...cards.values()].map(row=>[`${row.domColumn}:${row.domRow}`,row.recordKey])),batchKeys=new Set(),batchSlots=new Set();
  for(const row of page.cards){
   const key=`position_${row.position.top}_${row.position.left}`,prior=cards.get(key),slot=`${row.domColumn}:${row.domRow}`,slotKey=slotKeys.get(slot);
   if(batchKeys.has(key))fail('duplicate_document_position');
   batchKeys.add(key);
   if(!Number.isInteger(row.domColumn)||!Number.isInteger(row.domRow)||row.domColumn<0||row.domColumn>=page.columnCounts?.length||row.domRow<0||row.domRow>=page.columnCounts[row.domColumn]||batchSlots.has(slot))fail('invalid_or_duplicate_column_slot');
   batchSlots.add(slot);
   if(slotKey&&slotKey!==key)fail('document_layout_changed');
   if(prior&&(prior.title!==row.title||prior.imageUrl!==row.imageUrl||prior.goodsUrl!==row.goodsUrl||prior.domColumn!==row.domColumn||prior.domRow!==row.domRow))fail('position_content_conflict');
   if(!row.title)fail('card_title_missing');
  }
  // A structurally uncertain batch stays in the checkpoint but cannot add or
  // merge observations into the verified prefix of the scan.
  let added=0;
  if(!nextStop)for(const row of page.cards){
   const key=`position_${row.position.top}_${row.position.left}`;
   if(!nextCards.has(key)){nextCards.set(key,{...row,recordKey:key,observedAt:page.at,observedAtPrecision:'batch_read'});added++;}
  }
  let nextNoNew=added?0:noNew;
  if(afterScroll&&!added)nextNoNew++;
  if(nextNoNew>=3&&!page.endBoundaryObserved)fail('three_normal_scrolls_without_new_cards');
  if(page.endBoundaryObserved&&!nextStop){
   const seenSlots=new Set([...nextCards.values()].map(row=>`${row.domColumn}:${row.domRow}`));
   if(page.columnCounts.some((count,column)=>Array.from({length:count},(_,row)=>`${column}:${row}`).some(slot=>!seenSlots.has(slot))))fail('coverage_incomplete_at_boundary');
  }
  const batch={batch:batches.length+1,...page,shopIdentityVerified:identityVerified,observedShopIdentity:observedIdentity,
   expectedShopIdentity:expectedIdentity,added,seen:nextCards.size,stop:nextStop,afterNormalScroll:afterScroll};
  try{
   const handle=await fs.open(`${directory}/batch_${String(batch.batch).padStart(4,'0')}.json`,'wx');
   try{await handle.writeFile(JSON.stringify(batch,null,2));await handle.sync();}finally{await handle.close();}
  }
  catch(error){stop='checkpoint_write_failed';throw error;}
  // Commit in-memory progress only after the immutable checkpoint is durable.
  batches.push(batch);cards=nextCards;noNew=nextNoNew;stop=nextStop;
  dimensions ||= [page.viewportWidth,page.viewportHeight];
  if(page.shopVerified&&identityVerified)storeSalesRaw=page.storeSalesRaw||storeSalesRaw;
  ended=page.endBoundaryObserved&&!stop&&cards.size>0;
  return {batch:batch.batch,at:page.at,top:page.scrollTop,loaded:page.loadedCardCount,added,seen:cards.size,end:ended,stop};
 }
 async function capture(){
  if(busy)throw new Error('Capture operation already in progress');
  if(ended||stop)return terminal();
  busy=true;try{return await captureInternal();}finally{busy=false;}
 }
 async function step(){
  if(busy)throw new Error('Capture operation already in progress');
  if(ended||stop)return terminal();
  if(!dimensions)throw new Error('Call capture() at the top before step()');
  busy=true;
  try{
   const x=Math.max(1,Math.min(500,dimensions[0]-50)),y=Math.max(1,Math.min(500,dimensions[1]-80));
   try{await tab.scroll([x,y],'down',0.8);}catch(error){stop='scroll_failed';throw error;}
   return await captureInternal(true);
  }finally{busy=false;}
 }
 async function snapshot(reason=null){
  if(busy)throw new Error('Wait for the active capture operation before sealing a snapshot');
  if(!batches.length||!cards.size)throw new Error('No observed cards: write a failed attempt receipt, not an empty run');
  const rows=Array.from(cards.values()).sort((a,b)=>a.position.top-b.position.top||a.position.left-b.position.left).map((r,i)=>({...r,viewOrder:i+1}));
  const complete=ended&&!stop&&!reason;
  return {shopName,sourceUrl,...(expectedIdentity.identity_kind==='mall_id'?{mallId:expectedIdentity.stable_identifier}:{}),
   shopIdentityEvidence:{...expectedIdentity,basis:'operator-verified target URL plus same stable key and origin on every accepted visible DOM batch'},
   sort:'上新',observedFrom:batches[0].at,observedTo:batches.at(-1).at,
   status:complete?'complete':'partial',endBoundaryObserved:complete,storeSalesRaw,
   collectionEvidence:{tool:'mcp__cua_repl',browser:browserName,tabId:tab.id,attemptId,driverVersion:4,
    mode:'fresh page, visible DOM cards, sequential normal overlapping viewport scrolls (keyboard or wheel)',stopReason:reason||stop,
    batchCount:batches.length,cardIdentity:'document position and column-local DOM slot; global index diagnostic only; no title/image merging',
    timestampPrecision:'batch_read',checkpointDirectory:checkpointReference,imagesDownloaded:false,
    shopIdentityChecks:batches.map(batch=>({at:batch.at,pageUrl:batch.pageUrl,verified:batch.shopIdentityVerified,
     observedIdentity:batch.observedShopIdentity,stop:batch.stop}))},
   scrolls:batches.map(b=>({at:b.at,count:b.seen,scrollTop:b.scrollTop,end:b.endBoundaryObserved})),rows};
 }
 return {capture,step,snapshot,status};
}
