import {delay,guard,readPageGuard,checkPageGuard,ReviewError} from './local_browser.mjs';
import {waitForStorefront,readStorefrontState} from './local_storefront.mjs';
import {shopIdentityFromUrl} from './live_capture_driver.mjs';
import {randomUUID} from 'node:crypto';
import {armListSkuModal,checkListSkuModal,releaseListSkuModal} from './list_sku_modal.mjs';
import {locateSkuBySearch,returnFromSkuSearch} from './local_sku_search.mjs';

export function combinations(groups,limit=300){let out=[[]];for(const group of groups){if(!group.options.length)throw new ReviewError('规格组没有可读选项。','manual_review','sku_catalog_incomplete');if(out.length*group.options.length>limit)throw new ReviewError('规格组合超过300项；此商品已停止，需人工选择范围。','manual_review','sku_combination_limit');out=out.flatMap(prefix=>group.options.map(option=>[...prefix,{name:group.name,value:option.label}]));}return out;}

// Conservative rendered-DOM adapter. Unsupported/ambiguous layouts stop for review.
export function readSpecs({includeRawText=true}={}){
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
 const point=e=>{const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,inViewport:r.top>=0&&r.bottom<=innerHeight};};
 // Verified on the ordinary Chrome mobile-web SKU dialog, 2026-10-06.
 // Only rendered choices and selected price labels; never app state/site APIs.
 const dialogs=[...document.querySelectorAll('.sku-plus1 [role="dialog"][aria-modal="true"]')].filter(visible);
 if(dialogs.length){
  if(dialogs.length!==1)return {supported:false,reason:'多个规格弹层，无法确定当前规格'};
  const root=dialogs[0],sections=[...new Set([...root.querySelectorAll('.bIhLWVqm'),...root.querySelectorAll('.skuSpecs_bIhLWVqm')])];
  const clickPoint=e=>{const r=e.getBoundingClientRect();for(const yf of [.5,.15,.85])for(const xf of [.5,.15,.85]){const p={x:r.x+r.width*xf,y:r.y+r.height*yf};if(p.x>=0&&p.x<innerWidth&&p.y>=0&&p.y<innerHeight&&e.contains(document.elementFromPoint(p.x,p.y)))return {...p,inViewport:true};}return {...point(e),inViewport:false};};
  const groups=sections.map(e=>({name:e.querySelector('.sku-specs-key')?.textContent.trim(),options:[...new Set([...e.querySelectorAll('.s1O5M5fO > [role="button"]'),...e.querySelectorAll('.skuSpecValueList_s1O5M5fO > [role="button"]')])].map(o=>({label:o.getAttribute('aria-label')||o.textContent.trim(),point:clickPoint(o),selected:o.classList.contains('hr353bdX')||o.classList.contains('skuSpecValueSelected_oQDDpea9')||o.getAttribute('aria-selected')==='true',disabled:o.getAttribute('aria-disabled')==='true'||o.hasAttribute('disabled')||/disabled|soldout|sold-out/i.test(o.className),image:o.querySelector('img')?.currentSrc||null,rendered:visible(o)}))}));
  if(!groups.length||groups.length>4||new Set(groups.map(g=>g.name)).size!==groups.length||groups.some(g=>!g.name||!g.options.length||g.options.some(o=>!o.label)||new Set(g.options.map(o=>o.label)).size!==g.options.length))return {supported:false,reason:'规格目录结构或名称无法核对'};
  const selectedText=(root.querySelector('.Mbx2m60G')||root.querySelector('.text_Mbx2m60G'))?.textContent.trim()||'';
  const selectionSummaryVerified=/^已选[：:]/.test(selectedText)&&groups.every(g=>g.options.filter(o=>o.selected).length===1)&&selectedText.replace(/^已选[：:]\s*/,'').replace(/\s+/g,' ')===groups.map(g=>g.options.find(o=>o.selected)?.label).join(' ').replace(/\s+/g,' ');
  const currentElements=[...new Set([...root.querySelectorAll('.ujEqGzEB'),...root.querySelectorAll('.skuQuantityPriceDesc_ujEqGzEB')])].filter(visible);
  const current=currentElements.length===1?currentElements[0].innerText.replace(/\s+/g,''):null;
  const originalElements=[...new Set([...root.querySelectorAll('.mhHA_CEU,del,s'),...root.querySelectorAll('.skuQuantityTipTxt_mhHA_CEU')])].filter(e=>visible(e)&&/[¥￥]/.test(e.innerText));
  const original=originalElements.length===1?originalElements[0]:null,originalRaw=original?.innerText.replace(/\s+/g,'')||null;
  const evidence=/^券前/.test(originalRaw||'')?'explicit_before_coupon_price':/原价/.test(originalRaw||'')?'explicit_original_price_label':original&&(['DEL','S'].includes(original.tagName)||getComputedStyle(original).textDecorationLine?.includes('line-through'))?'struck_through_price':null;
  const headerImage=root.querySelector('.O7pEFvHR > img')||root.querySelector('.skuSelectorHead_O7pEFvHR > img');
  const imageReady=headerImage&&visible(headerImage)&&headerImage.complete&&headerImage.naturalWidth>0&&headerImage.naturalHeight>0&&headerImage.currentSrc===headerImage.src;
  return {supported:true,adapter:'pdd_sku_plus1_v1',groups,price_raw:current,current_price_raw:current,current_price_label:/券后/.test(current||'')?'券后价':'当前价',original_price_raw:evidence?originalRaw:null,original_price_evidence:evidence,original_price_label:evidence==='explicit_before_coupon_price'?'券前价':evidence==='struck_through_price'?'划线价':'原价',image_url:selectionSummaryVerified&&imageReady?headerImage.currentSrc:null,image_pending:!!(headerImage&&visible(headerImage)&&headerImage.src&&!imageReady),selection_summary_verified:selectionSummaryVerified,raw_text:includeRawText?root.innerText.slice(0,16000):null,option_catalog_verified:groups.every(g=>g.options.every(o=>o.rendered))&&!/展开全部|更多规格|加载中|查看更多规格/.test(root.innerText)};
 }
 const selectors='[class*="sku-spec-value"],[class*="skuItem"],[class*="sku-item"],[class*="specItem"],[class*="spec-item"],[role="option"]';
 const options=[...document.querySelectorAll(selectors)].filter(e=>visible(e)&&e.textContent.trim()&&e.textContent.trim().length<200);
 const leaves=options.filter(e=>!options.some(child=>child!==e&&e.contains(child)));
 if(!leaves.length)return {supported:false,reason:'未识别规格选项结构'};
 const parents=[...new Set(leaves.map(e=>e.parentElement))];
 const groups=parents.map((parent,i)=>{let heading=parent.previousElementSibling?.textContent.trim();if(!heading||heading.length>60)heading=parent.getAttribute('aria-label')||`规格组${i+1}`;
  return {name:heading,options:leaves.filter(e=>e.parentElement===parent).map(e=>({label:e.textContent.trim(),point:point(e),selected:e.getAttribute('aria-selected')==='true'||e.getAttribute('aria-checked')==='true'||/(?:^|[\s_-])(selected|checked|active)(?:[\s_-]|$)/i.test(e.className),disabled:e.getAttribute('aria-disabled')==='true'||e.hasAttribute('disabled')||/disabled|soldout|sold-out/.test(e.className),image:e.querySelector('img')?.currentSrc||null}))};});
 if(groups.length>4||new Set(groups.map(g=>g.name)).size!==groups.length||groups.some(g=>new Set(g.options.map(o=>o.label)).size!==g.options.length))return {supported:false,reason:'规格组或选项存在歧义'};
 let root=parents[0];while(root.parentElement&&root!==document.body&&(!groups.every(g=>leaves.filter(e=>e.parentElement===parents[groups.indexOf(g)]).every(e=>root.contains(e)))||!/[¥￥]/.test(root.innerText)))root=root.parentElement;
 if(root===document.body)return {supported:false,reason:'无法定位独立规格弹层，价格来源未知'};
 const prices=[...root.querySelectorAll('[class*="price"],[class*="Price"]')].filter(e=>visible(e)&&/[¥￥]/.test(e.innerText)).filter((e,i,all)=>!all.some(other=>other!==e&&e.contains(other)));
 const priceRaw=prices.length===1?prices[0].innerText.replace(/\s+/g,''):null;
 // Unknown layouts cannot establish that a large header image belongs to this
 // choice. Only an explicitly selected option's own loaded image can qualify.
 const selectedImages=groups.flatMap(g=>g.options.filter(o=>o.selected&&o.image).map(o=>o.image)).filter(url=>leaves.some(o=>{const img=o.querySelector('img');return img&&img.currentSrc===url&&visible(img)&&img.complete&&img.naturalWidth>0&&img.naturalHeight>0&&img.currentSrc===img.src;}));
 const incomplete=[...root.querySelectorAll(selectors)].some(e=>!visible(e))||/展开全部|更多规格|加载中|查看更多规格/.test(root.innerText)||parents.some(p=>/virtual|recycler/i.test(p.className));
 return {supported:true,groups,price_raw:priceRaw,image_url:selectedImages.length===1?selectedImages[0]:null,raw_text:includeRawText?root.innerText.slice(0,16000):null,option_catalog_verified:!incomplete&&groups.every(g=>g.options.length>0)};
}

const selectionMatches=(specs,combination)=>specs.selection_summary_verified!==false&&specs.groups.every(g=>g.options.filter(o=>o.selected).length===1)&&combination.every(s=>specs.groups.find(g=>g.name===s.name)?.options.some(o=>o.label===s.value&&o.selected));
const valueKey=specs=>JSON.stringify([specs.price_raw,specs.current_price_raw,specs.original_price_raw,specs.original_price_evidence,specs.image_url]);
const checkCancelled=async cancelled=>{if(await cancelled())throw new ReviewError('已取消SKU读取。','cancelled','operator_cancelled');};
// Stop polling as soon as two observations agree across a short render window.
// Missing fields remain unknown; a changing price/image must never be assigned
// just because a fixed sleep expired. Dependencies are injectable for tests.
export async function waitForStableSku(read,combination,{cancelled=async()=>false,sleep=delay,now=Date.now,timeoutMs=1600,stableMs=100,intervalMs=100,previousState=null,valueUpdateObserved=false}={}){
 const started=now(),previousKey=previousState?valueKey(previousState):null;let prior=null,stableSince=started,specs,updated=!previousState||valueUpdateObserved;
 for(;;){
  await checkCancelled(cancelled);
  specs=await read();await checkCancelled(cancelled);const selected=selectionMatches(specs,combination);
  if(previousState&&(specs.image_pending||valueKey(specs)!==previousKey))updated=true;
  const noValues=!specs.price_raw&&!specs.current_price_raw&&!specs.original_price_raw&&!specs.image_url;
  const key=selected?JSON.stringify([specs.price_raw,specs.current_price_raw,specs.original_price_raw,specs.original_price_evidence,specs.image_url,specs.image_pending]):null;
  if(key===null||key!==prior){prior=key;stableSince=now();}
  if(selected&&(updated||noValues)&&!specs.image_pending&&now()-stableSince>=stableMs)return {...specs,value_stability_verified:true};
  if(now()-started>=timeoutMs){
   if(!selected)throw new ReviewError('未能核对所有已选规格，不能归属当前价格。','manual_review','sku_selection_unverified');
   // Retain independently stable prices when only an image is still loading.
   const stable=(updated||noValues)&&now()-stableSince>=stableMs;
   return {...specs,...(!stable?{price_raw:null,current_price_raw:null,original_price_raw:null,original_price_evidence:null}:{}),image_url:null,value_stability_verified:stable};
  }
  await sleep(intervalMs);
 }
}

// Click targets must be unobscured. Extensions can cover the purchase bar.
export function skuEntryPoint(){
 const candidates=[...document.querySelectorAll('button,a,[role="button"],div,span')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e),t=e.textContent.trim();return r.width>20&&r.height>10&&r.bottom>0&&r.top<innerHeight&&s.display!=='none'&&s.visibility!=='hidden'&&t.length<120&&/选择规格|请选择规格|单独购买|立即购买|发起拼单/.test(t)&&!/支付|提交订单|确认订单|优惠券|去拼单/.test(t)&&!e.closest('.sku-plus1');});
 const controls=candidates.filter(e=>e.matches('button,a,[role="button"]'));
 for(const e of [...controls,...candidates]){const r=e.getBoundingClientRect();for(const yf of [.5,.15,.85])for(const xf of [.5,.15,.85]){const p={x:r.x+r.width*xf,y:r.y+r.height*yf};if(p.x>0&&p.x<innerWidth&&p.y>0&&p.y<innerHeight&&e.contains(document.elementFromPoint(p.x,p.y)))return p;}}
 return null;
}

export function locateCard({title,image,move=false}){
 const list=document.querySelector('.waterfall-list-container_egohG8wQ');if(!list)return {count:0};
 let boundary=Infinity;for(const e of document.querySelectorAll('div,p,span'))if(!e.children.length&&['本店暂无更多商品','其他店铺的精选推荐'].includes(e.textContent.trim())){const r=e.getBoundingClientRect();if(r.width>0&&r.height>0)boundary=Math.min(boundary,r.top+scrollY);}
 const matches=[...list.querySelectorAll('.goodsItem_R1ok0MpS')].filter(e=>{const i=e.querySelector('.goodsImage_fU27dxbk img'),r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.top+scrollY<boundary&&e.querySelector('.goodsName_dT8mZSNd')?.textContent.trim()===title&&(i?.getAttribute('data-src')||i?.getAttribute('src'))===image;});
 if(matches.length!==1)return {count:matches.length};const e=matches[0];if(move)e.scrollIntoView({block:'center',behavior:'instant'});
 const r=e.getBoundingClientRect();for(const yf of [.3,.5,.7])for(const xf of [.5,.3,.7]){const p={x:r.x+r.width*xf,y:r.y+r.height*yf};if(p.x>0&&p.x<innerWidth&&p.y>0&&p.y<innerHeight&&e.contains(document.elementFromPoint(p.x,p.y)))return {count:1,point:{...p,inViewport:true}};}
 return {count:1,point:{x:r.x+r.width/2,y:r.y+r.height/2,inViewport:false}};
}
export function revealSkuOption({name,value}){
 const modal=document.querySelector('.sku-plus1 [role="dialog"][aria-modal="true"]');if(!modal)return false;
 const groups=[...new Set([...modal.querySelectorAll('.bIhLWVqm'),...modal.querySelectorAll('.skuSpecs_bIhLWVqm')])].filter(e=>e.querySelector('.sku-specs-key')?.textContent.trim()===name);if(groups.length!==1)return false;
 const options=[...new Set([...groups[0].querySelectorAll('.s1O5M5fO > [role="button"]'),...groups[0].querySelectorAll('.skuSpecValueList_s1O5M5fO > [role="button"]')])].filter(e=>(e.getAttribute('aria-label')||e.textContent.trim())===value);if(options.length!==1)return false;
 options[0].scrollIntoView({block:'center',behavior:'instant'});return true;
}
// Small rendered-only failure receipt: no page HTML, credentials or app state.
export function diagnoseSkuOption({name,value}){
 const root=document.querySelector('.sku-plus1 [role="dialog"][aria-modal="true"]');
 const group=[...(root?.querySelectorAll('.bIhLWVqm')||[]),...(root?.querySelectorAll('.skuSpecs_bIhLWVqm')||[])].find(e=>e.querySelector('.sku-specs-key')?.textContent.trim()===name);
 const option=[...(group?.querySelectorAll('.s1O5M5fO > [role="button"]')||[]),...(group?.querySelectorAll('.skuSpecValueList_s1O5M5fO > [role="button"]')||[])].find(e=>(e.getAttribute('aria-label')||e.textContent.trim())===value);
 const describe=e=>{if(!e)return null;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {tag:e.tagName,class:String(e.className).slice(0,120),rect:{x:r.x,y:r.y,width:r.width,height:r.height},display:s.display,visibility:s.visibility,opacity:s.opacity,ariaHidden:e.getAttribute('aria-hidden')};};
 const ancestors=[];for(let e=option;e&&ancestors.length<10;e=e.parentElement)ancestors.push(describe(e));
 const r=option?.getBoundingClientRect(),hit=r?document.elementFromPoint(r.x+r.width/2,r.y+r.height/2):null;
 return {viewport:{width:innerWidth,height:innerHeight,visual:window.visualViewport?{width:visualViewport.width,height:visualViewport.height,offsetTop:visualViewport.offsetTop,offsetLeft:visualViewport.offsetLeft,scale:visualViewport.scale}:null},root:describe(root),ancestors,hit:describe(hit),hitInside:!!option?.contains(hit)};
}
function sameShop(actual,expected){try{const a=shopIdentityFromUrl(actual),e=shopIdentityFromUrl(expected);return a.origin===e.origin&&(e.identity_kind==='mall_id'?a.source_mall_id:a.source_mall_sn)===e.stable_identifier;}catch{return false;}}
function assertProductPage(page,goodsId){
 let url;try{url=new URL(page.url);}catch{}
 if(!url||url.origin!=='https://mobile.yangkeduo.com'||!/^\/goods1?\.html$/.test(url.pathname)||url.searchParams.getAll('goods_id').length!==1||url.searchParams.get('goods_id')!==goodsId)throw new ReviewError('读取规格期间商品身份变化，已停止，保留之前已核对的规格。','manual_review','sku_identity_changed');
}

export async function prepareSkuShop(browser,request,cancelled){
 let current=request.reuse_shop||Number.isSafeInteger(browser.tab?.id)?await guard(browser):null;
 if(current&&sameShop(current.url,request.shop.source_url)){
  // A single original card was already verified in this exact existing page.
  // Its identity is independent of list order; the locator and trusted plus
  // binding still recheck it before clicking. Sorting again would discard the
  // loaded list and force a full scroll solely to find the same original card.
  if(browser.canPreserveSkuStorefront?.({sourceUrl:request.shop.source_url,currentUrl:current.url,title:request.observation?.title,image:request.observation?.image_url,shopName:request.shop.shop_name}))return;
  const state=await browser.evaluate(readStorefrontState,{shopName:request.shop.shop_name});
  if(state.shopVerified&&state.cards>0&&state.latestSelected)return;
 }
 if(!current||!sameShop(current.url,request.shop.source_url))await browser.goto(request.entry_url||request.shop.source_url);
 const preflight={shopName:request.shop.shop_name,sourceUrl:request.shop.source_url,cancelled};
 const ready=await waitForStorefront(browser,preflight);if(!ready.latestSelected)await browser.click(ready.latestPoint);
 await waitForStorefront(browser,{...preflight,requireSelected:true});
}

export async function captureSku(browser,request,cancelled,progress){
 const row=request.observation,ref=row.verified_product_ref;let basis=row.goods_id?'recorded_public_goods_link':'current_unique_card_candidate',locator=null;
 const expectedId=String(row.goods_id||ref?.goods_id||''),directUrl=row.goods_id&&row.goods_url?row.goods_url:ref?.goods_url;
 if(ref){let u;try{u=new URL(ref.goods_url);}catch{}if(!u||u.origin!=='https://mobile.yangkeduo.com'||!/^\/goods1?\.html$/.test(u.pathname)||u.searchParams.getAll('goods_id').length!==1||u.searchParams.get('goods_id')!==String(ref.goods_id)||row.goods_id&&String(row.goods_id)!==String(ref.goods_id))throw new ReviewError('已保存的商品链接身份不一致，已停止。','manual_review','sku_identity_changed');}
 if(!row.goods_id&&(row.current_unique_card_candidate_count??1)!==1)throw new ReviewError('本轮有完全相同标题和主图的多张原卡，SKU 单独留待核对。','manual_review','sku_card_ambiguous');
 if(directUrl&&expectedId&&!(request.shop?.source_url&&row.image_url)){locator={method:row.goods_id?'recorded_link':'saved_verified_link',capture_id:ref?.capture_id||null};await progress({phase:'直接打开已核实商品链接',locator:locator.method});await browser.goto(directUrl);}
 else{
  const searchEvidence=await locateSkuBySearch(browser,request,cancelled,progress);
  locator={method:'store_search',entry:'card_plus',query:searchEvidence.query};
  let result=null,failure=null;
  try{
   const token=randomUUID(),armed=await browser.evaluate(armListSkuModal,{token,title:row.title,image:row.image_url,shopName:request.shop.shop_name,sourceUrl:searchEvidence.search_page_url,searchEvidence,originalSalesValue:row.sales_value});
   if(!armed.ok)throw new ReviewError('搜索商品的加号入口未能核对，已停止。','manual_review',armed.reason==='obscured'?'sku_card_obscured':armed.reason==='no_plus'?'sku_entry_unavailable':'sku_identity_changed');
   result=await captureListSkuModal(browser,request,cancelled,progress,{token,point:armed.point,locator,searchEvidence});
  }catch(error){failure=error;}
  if(result?.stop_reason!=='sku_modal_cleanup_failed'&&failure?.reason!=='sku_modal_cleanup_failed'){
   if(result)searchEvidence.modal_closed=true;
   try{await progress({phase:'关闭搜索，返回原店铺',locator:'store_search'});Object.assign(searchEvidence,await returnFromSkuSearch(browser,request,{...searchEvidence,modal_closed:true}));}
   catch(error){if(result)Object.assign(result,{status:'partial',all_combinations_visited:false,stop_status:error.status||'manual_review',stop_reason:'sku_search_return_failed',stop_cause_reason:error.reason||null,stop_message:error.message});else failure ||= error;}
  }
  if(failure)throw failure;
  result.search_evidence=searchEvidence;return result;
 }
 let page;for(let i=0;i<15;i++){await delay(200);page=await guard(browser);if(/goods_id=[1-9]\d*/.test(page.url)&&page.text.includes(row.title))break;}
 const actual=new URL(page.url),goodsId=actual.searchParams.get('goods_id');
 if(!goodsId||expectedId&&expectedId!==goodsId)throw new ReviewError('实际商品链接与原卡ID不一致，已停止。','manual_review','sku_identity_changed');
 assertProductPage(page,goodsId);
 if(page.text.includes(row.title)&&/前往APP查看价格/.test(page.text)&&!await browser.evaluate(skuEntryPoint))throw new ReviewError('已按标题和主图定位到商品，但网页提示前往 APP 查看价格，暂未提供可读取的 SKU 入口；已跳过此商品。','manual_review','sku_web_unavailable');
 if(!page.text.includes(row.title)||request.shop&&!page.text.includes(request.shop.shop_name))throw new ReviewError('商品详情标题或店名与所选原卡不一致，需复核。','manual_review','sku_identity_changed');
 const goodsUrl=actual.origin+actual.pathname+'?goods_id='+goodsId;
 return captureSkuChoices(browser,request,cancelled,progress,{goodsId,goodsUrl,basis,locator});
}

export async function captureListSkuModal(browser,request,cancelled,progress,{token,point,locator,searchEvidence=null}){
 let result=null,failure=null,cleanupFailure=null;
 try{
  await progress({phase:'点击本商品加号，读取规格',locator:searchEvidence?'store_search_plus':'store_scroll_plus'});
  await browser.click(point,{settleMs:0});let opened;
  const openDeadline=Date.now()+1600;
  do{
   await guard(browser,searchEvidence?null:request.shop.shop_name);opened=await browser.evaluate(checkListSkuModal,{token,bind:true});
   await checkCancelled(cancelled);
   if(opened.ok||opened.reason!=='not_open_yet'||Date.now()>=openDeadline)break;
   await delay(60);
  }while(true);
  if(!opened.ok)throw new ReviewError('加号未打开可核对的本商品规格弹窗。','manual_review','sku_identity_changed');
  result=await captureSkuChoices(browser,request,cancelled,progress,{goodsId:null,goodsUrl:null,basis:'current_unique_card_modal',locator,listModalToken:token});
 }catch(error){failure=error;}
 try{
  const close=await browser.evaluate(checkListSkuModal,{token,closing:true});
  let closed=false;
  if(close.ok){
   await progress({phase:failure?'关闭当前规格弹窗':'规格已读取，关闭当前规格弹窗'});
   await browser.click(close.point,{settleMs:0});const deadline=Date.now()+1600;
   do{const state=await browser.evaluate(releaseListSkuModal,{token,probe:true});closed=state.closed===true;if(closed||Date.now()>=deadline)break;await delay(60);}while(true);
  }
  if(!closed)cleanupFailure=new ReviewError('规格已读取，但弹窗未确认关闭，已暂停后续商品。','manual_review','sku_modal_cleanup_failed');
 }catch(error){cleanupFailure=error;}
 finally{
  try{const released=await browser.evaluate(releaseListSkuModal,{token});if(!released.closed&&!cleanupFailure)cleanupFailure=new ReviewError('规格弹窗仍可见，已暂停后续商品。','manual_review','sku_modal_cleanup_failed');}
  catch(error){cleanupFailure ||= error;}
 }
 if(failure){if(cleanupFailure){cleanupFailure.cause_reason=failure.reason||'sku_read_failed';throw cleanupFailure;}throw failure;}
 if(cleanupFailure){if(!result)throw cleanupFailure;Object.assign(result,{status:'partial',all_combinations_visited:false,stop_status:cleanupFailure.status||'manual_review',stop_reason:'sku_modal_cleanup_failed',stop_cause_reason:cleanupFailure.reason||null,stop_message:cleanupFailure.message});}
 return result;
}

async function captureSkuChoices(browser,request,cancelled,progress,{goodsId,goodsUrl,basis,locator,listModalToken=null}){
 const row=request.observation;
 const readState=async({includeRawText=false}={})=>{
  const readers=[[readPageGuard,{includeText:false}],...(listModalToken?[[checkListSkuModal,{token:listModalToken}]]:[]),[readSpecs,{includeRawText}]];
  const values=browser.evaluateMany?await browser.evaluateMany(readers):await (async()=>{const out=[];for(const [fn,arg] of readers)out.push(await browser.evaluate(fn,arg));return out;})();
  const page=checkPageGuard(values[0]);
  if(!listModalToken)assertProductPage(page,goodsId);
  else if(!values[1].ok)throw new ReviewError('规格弹窗或对应商品发生变化，已停止，保留已核对规格。','manual_review','sku_identity_changed');
  return values.at(-1);
 };
 let specs=await readState();
 if(!specs.supported){
  if(listModalToken)throw new ReviewError('加号弹窗的规格结构尚未识别，未读取其他商品。','manual_review','sku_layout_unrecognized');
  const button=await browser.evaluate(skuEntryPoint);
  if(!button)throw new ReviewError('未找到可点击的规格入口。','manual_review','sku_entry_unavailable');
  await browser.click(button);specs=await readState();
 }
 if(!specs.supported)throw new ReviewError(specs.reason+'；请保留规格窗口供复核。','manual_review','sku_layout_unrecognized');
 if(!specs.option_catalog_verified)throw new ReviewError('规格目录有折叠或未加载选项，不能声明完整。','manual_review','sku_catalog_incomplete');
 const catalog=specs.groups.map(g=>({name:g.name,options:g.options.map(o=>({label:o.label}))})),choices=combinations(catalog),variants=[];
 const catalogKey=s=>JSON.stringify(s.groups.map(g=>({name:g.name,options:g.options.map(o=>o.label)}))),initialKey=catalogKey(specs);
 const readVerified=async options=>{const state=await readState(options);if(!state.supported||catalogKey(state)!==initialKey||!state.option_catalog_verified)throw new ReviewError('规格目录在读取过程中变化，已停止。','manual_review','sku_catalog_changed');return state;};
 let stopped=null;
 try{for(const combination of choices){
  if(await cancelled())throw new ReviewError('已取消SKU读取。','cancelled','operator_cancelled');
  specs=await readVerified();const previousState=specs;let available=true,changed=false,valueUpdateObserved=false;
  for(const selected of combination){
   await checkCancelled(cancelled);
   const option=specs.groups.find(g=>g.name===selected.name)?.options.find(o=>o.label===selected.value);
   if(!option)throw new ReviewError('规格选项消失，已停止。','manual_review','sku_option_missing');
   if(option.disabled){available=false;break;}
   // Already-selected options need no pointer target. The complete selection
   // summary and current goods ID are still verified before recording a SKU.
   if(option.selected)continue;
   let target=option;
   if(!target.point.inViewport){
    const moved=await browser.evaluate(revealSkuOption,selected);if(moved){specs=await readVerified();target=specs.groups.find(g=>g.name===selected.name)?.options.find(o=>o.label===selected.value);}
   }
   if(!target?.point.inViewport){const error=new ReviewError('规格选项被遮挡或不可见，请在 Chrome 复核。','manual_review','sku_option_obscured');error.sku_diagnostics=await browser.evaluate(diagnoseSkuOption,selected);throw error;}
   if(!target.selected){
    await checkCancelled(cancelled);changed=true;
    await browser.click(target.point,{settleMs:0});const deadline=Date.now()+1600;
    do{
     if(await cancelled())throw new ReviewError('已取消SKU读取。','cancelled','operator_cancelled');
     specs=await readVerified();
     await checkCancelled(cancelled);
     if(specs.image_pending||valueKey(specs)!==valueKey(previousState))valueUpdateObserved=true;
     if(specs.groups.find(g=>g.name===selected.name)?.options.some(o=>o.label===selected.value&&o.selected))break;
     if(Date.now()>=deadline)throw new ReviewError('未能核对所有已选规格，不能归属当前价格。','manual_review','sku_selection_unverified');
     await delay(60);
    }while(true);
   }
  }
  if(available)specs=await waitForStableSku(readVerified,combination,{cancelled,...(changed?{previousState,valueUpdateObserved}:{})});
  const verified=available&&selectionMatches(specs,combination);
  if(available&&!verified)throw new ReviewError('未能核对所有已选规格，不能归属当前价格。','manual_review','sku_selection_unverified');
  const variant={specs:combination,sku_id:null,price_raw:verified?specs.price_raw:null,current_price_raw:verified?(specs.current_price_raw??specs.price_raw):null,current_price_label:specs.current_price_label||'当前价',original_price_raw:verified?specs.original_price_raw||null:null,original_price_evidence:verified?specs.original_price_evidence||null:null,original_price_label:specs.original_price_label||'原价',image_url:verified?specs.image_url:null,available,selection_verified:verified,observed_at:new Date().toISOString(),raw_text:specs.raw_text};
  await checkCancelled(cancelled);
  if(variant.image_url&&browser.collectSkuImages)variant.image_capture=await browser.collectSkuImages({goodsId,imageUrls:[variant.image_url],...(listModalToken?{listModalToken}:{})});
  const finalState=await readVerified({includeRawText:true});
  await checkCancelled(cancelled);
  if(verified&&(!selectionMatches(finalState,combination)||['price_raw','original_price_raw','image_url'].some(key=>variant[key]!=null&&variant[key]!==finalState[key])))throw new ReviewError('读取图片期间已选规格或价格发生变化，已停止。','manual_review','sku_selection_unverified');
  variant.raw_text=finalState.raw_text;
  variants.push(variant);await progress({phase:'逐项读取 SKU 价格和图片',variants:variants.length,total:choices.length});
 }}catch(error){if(!variants.length)throw error;stopped=error;}
 if(!variants.length)throw new ReviewError('规格读取已取消，未保存空结果。','cancelled');
 return {goods_id:goodsId,goods_url:goodsUrl,observed_at:new Date().toISOString(),identity_basis:basis,locator,...(listModalToken?{capture_source:'shop_card_modal',list_modal_evidence:{shop_source_url:request.shop.source_url,shop_name:request.shop.shop_name,title:row.title,image_url:row.image_url,view_order:row.view_order,observation_id:row.observation_id,matched_card_count:1,opened_from_verified_plus:true,modal_continuity_verified:true}}:{}),source_card_evidence:{title:row.title,image_url:row.image_url,view_order:row.view_order,shop_source_url:request.shop?.source_url},status:!stopped&&variants.length===choices.length?'complete':'partial',all_combinations_visited:!stopped&&variants.length===choices.length,option_catalog_verified:true,expected_combinations:choices.length,stop_status:stopped?(stopped.status||'manual_review'):null,stop_reason:stopped?(stopped.reason||'sku_read_failed'):null,stop_message:stopped instanceof ReviewError?stopped.message:null,...(stopped?.sku_diagnostics?{stop_diagnostics:stopped.sku_diagnostics}:{}),variants};
}
