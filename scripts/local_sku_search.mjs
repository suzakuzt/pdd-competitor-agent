// Fixed, rendered-only in-store search. The search mall_id never replaces the
// recorded canonical shop identifier or a card's original image reference.
import {ReviewError,guard,delay} from './local_browser.mjs';
import {verifyStoreIdentity} from './local_storefront.mjs';

const cancelledCheck=async cancelled=>{if(await cancelled())throw new ReviewError('已取消SKU读取。','cancelled','operator_cancelled');};
const sameShop=(a,b)=>{try{return verifyStoreIdentity(a,b);}catch{return false;}};
export function skuSearchQuery(title){
 if(typeof title!=='string'||!title.trim())throw new ReviewError('商品原始标题缺失。','manual_review','sku_card_not_found');
 // A deterministic title prefix is cheaper and less restrictive than all of
 // the seller's marketing suffix. Identity still uses the complete title.
 return title.trim().slice(0,18);
}
export function readSkuSearchEntry({shopName}){
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 const u=new URL(location.href);
 if(u.origin!=='https://mobile.yangkeduo.com'||u.pathname!=='/mall_page.html'||!document.body.innerText.includes(shopName))return {ok:false,reason:'source'};
 if([...document.querySelectorAll('.sku-plus1 [role="dialog"][aria-modal="true"]')].some(visible))return {ok:false,reason:'old_modal'};
 const entries=[...document.querySelectorAll('.mall-search-bar_rsdSZQxR')].filter(visible);
 if(entries.length!==1||!entries[0].textContent.includes('搜索店铺商品'))return {ok:false,reason:'entry'};
 const e=entries[0];e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect(),point={x:r.x+r.width/2,y:r.y+r.height/2};
 if(point.x<0||point.x>=innerWidth||point.y<0||point.y>=innerHeight||!e.contains(document.elementFromPoint(point.x,point.y)))return {ok:false,reason:'covered'};
 return {ok:true,url:location.href,point};
}
export function readSkuSearchResults({query,title,image,oldSalesValue}){
 const u=new URL(location.href),result={url:location.href,routeReady:false,matched_card_count:0};
 if(u.origin!=='https://mobile.yangkeduo.com'||u.pathname!=='/mall_search_result.html'||u.username||u.password||u.port||u.hash||u.searchParams.getAll('mall_id').length!==1||!(/^[1-9]\d{0,24}$/).test(u.searchParams.get('mall_id')||'')||u.searchParams.getAll('search_key').length!==1||u.searchParams.get('search_key')!==query)return result;
 result.routeReady=true;
 const shown=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
 const normalized=url=>{try{if(typeof url!=='string'||/[\x00-\x20\x7f]|%(?![0-9a-fA-F]{2})/.test(url))return null;const p=new URL(url);if(p.protocol!=='https:'||p.username||p.password||p.port||p.hash||!['img.pddpic.com','img-2.pddpic.com'].includes(p.hostname))return null;for(const piece of p.search.slice(1).split(/[&=]/))decodeURIComponent(piece.replace(/\+/g,' '));return JSON.stringify([p.pathname,[...p.searchParams].map(([k,v])=>[!v?k.replace(/^imageMogr2\/format\/webp\//,'imageMogr2/'):k,v])]);}catch{return null;}};
 const original=normalized(image);if(!original)return {...result,reason:'image_source'};
 const lists=[...document.querySelectorAll('.waterfall-list-container_NLmKnzfN')];
 let cutoff=Infinity;for(const e of document.querySelectorAll('div,span,p'))if(!e.children.length&&shown(e)&&['没有更多商品了','其他店铺的精选推荐'].includes(e.textContent.trim()))cutoff=Math.min(cutoff,e.getBoundingClientRect().top+scrollY);
 const cards=[...new Set(lists.flatMap(e=>[...e.querySelectorAll('.goodsItem_R1ok0MpS')]))].filter(e=>shown(e)&&e.getBoundingClientRect().top+scrollY<cutoff);
 result.card_count=cards.length;result.finished=Number.isFinite(cutoff);result.loading=/正在加载中|加载中\.\.\./.test(document.body.innerText);
 const matches=cards.filter(e=>e.querySelector('.goodsName_dT8mZSNd')?.textContent.trim()===title&&normalized(e.querySelector('.goodsImage_fU27dxbk img')?.getAttribute('data-src')||e.querySelector('.goodsImage_fU27dxbk img')?.getAttribute('src'))===original);
 result.matched_card_count=matches.length;if(matches.length!==1)return result;
 const card=matches[0],owners=lists.filter(e=>e.contains(card));if(owners.length!==1)return {...result,reason:'owner'};
 const sales=[...card.querySelectorAll('.salesTip_qUlLfJr3')].filter(shown);
 const raw=sales.length===1?sales[0].textContent.trim():null,m=/^(已拼|已抢)(\d+)件$/.exec(raw||'');
 if(!m||!Number.isSafeInteger(Number(m[2]))||Number(m[2])<=0||!Number.isSafeInteger(oldSalesValue)||oldSalesValue<=0||Number(m[2])<oldSalesValue)return {...result,reason:'sales'};
 const imageUrl=card.querySelector('.goodsImage_fU27dxbk img')?.getAttribute('data-src')||card.querySelector('.goodsImage_fU27dxbk img')?.getAttribute('src');
 return {...result,matching_title:title,original_image_url:image,matched_image_url:imageUrl,live_sales_raw:raw,live_sales_label:m[1],live_sales_value:Number(m[2]),live_sales_unit:'件'};
}
export function readSkuSearchCancel({searchUrl}){
 if(location.href!==searchUrl||location.pathname!=='/mall_search_result.html')return {ok:false};
 const shown=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 if([...document.querySelectorAll('.sku-plus1 [role="dialog"][aria-modal="true"]')].some(shown))return {ok:false};
 const controls=[...document.querySelectorAll('.mall-cancel-button_T0cRxs3m')].filter(e=>shown(e)&&e.textContent.trim()==='取消');
 if(controls.length!==1)return {ok:false};const e=controls[0],r=e.getBoundingClientRect(),point={x:r.x+r.width/2,y:r.y+r.height/2};
 return point.x>=0&&point.x<innerWidth&&point.y>=0&&point.y<innerHeight&&e.contains(document.elementFromPoint(point.x,point.y))?{ok:true,point}:{ok:false};
}
export async function locateSkuBySearch(browser,request,cancelled,progress,{sleep=delay,maxPolls=60}={}){
 await cancelledCheck(cancelled);
 let current=Number.isSafeInteger(browser.tab?.id)?await guard(browser):null;
 if(!current||!sameShop(request.shop.source_url,current.url)){await browser.goto(request.entry_url||request.shop.source_url);current=await guard(browser);}
 let entry;
 for(let i=0;i<maxPolls;i++){
  await cancelledCheck(cancelled);await guard(browser);entry=await browser.evaluate(readSkuSearchEntry,{shopName:request.shop.shop_name});
  if(entry.ok){verifyStoreIdentity(request.shop.source_url,entry.url);break;}
  if(entry.reason==='old_modal')throw new ReviewError('已有规格弹窗未关闭，先完成当前商品。','manual_review','sku_modal_cleanup_failed');
  if(i+1<maxPolls)await sleep(100);
 }
 if(!entry?.ok)throw new ReviewError('本店搜索入口尚未就绪，已停止。','manual_review','sku_search_entry_unavailable');
 await progress({phase:'店内搜索定位商品，核对主图和销量',locator:'store_search',steps:0});
 const pageId=browser.tab.id,query=skuSearchQuery(request.observation.title);
 await browser.click(entry.point,{settleMs:0});
 await browser.fillSearch({selector:'input.search-box-view-main_Y_ywSOkD[type="search"]',expectedPlaceholder:'输入商品名称',sourceUrl:entry.url},query);
 await cancelledCheck(cancelled);await browser.pressSearchEnter();
 const evidence={method:'native_store_search',source_store_url:entry.url,query,page_id:pageId,shop_name_verified:true,native_search_opened:true,native_search_submitted:true,same_tab_navigation_verified:true,modal_closed:false,return_verified:false,return_store_url:null};
 let result;
 try{for(let i=0;i<maxPolls;i++){
  await cancelledCheck(cancelled);const page=await guard(browser);
  if(browser.tab.id!==pageId)throw new ReviewError('搜索标签发生变化，已停止。','manual_review','sku_identity_changed');
  result=await browser.evaluate(readSkuSearchResults,{query,title:request.observation.title,image:request.observation.image_url,oldSalesValue:request.observation.sales_value});
  if(result.routeReady&&result.reason)throw new ReviewError('搜索结果的商品主图或销量未能核对，已停止。','manual_review','sku_identity_changed');
  if(result.matched_card_count>1)throw new ReviewError('搜索结果存在多张完全相同标题和主图，暂不能区分。','manual_review','sku_card_ambiguous');
  if(result.matched_card_count===1&&result.live_sales_value>0)break;
  if(result.routeReady&&result.finished&&!result.loading)break;
  if(!result.routeReady&&page.url!==entry.url&&new URL(page.url).pathname!=='/mall_search_result.html')throw new ReviewError('搜索跳转来源不符，已停止。','manual_review','sku_identity_changed');
  if(i+1<maxPolls)await sleep(100);
 }
 if(result?.matched_card_count!==1||!result.live_sales_value)throw new ReviewError('店内搜索未找到同主图原卡，本次未绑定其他同名款。','manual_review','sku_card_not_found');
 }catch(error){
  // Before any plus click, a verified empty/ambiguous result can safely cancel
  // search. Never navigate or click through a login/challenge/changed source.
  if(result?.routeReady&&browser.tab.id===pageId&&!['access_restricted','login_required','sku_identity_changed'].includes(error.reason)){
   try{await returnFromSkuSearch(browser,request,{...evidence,search_page_url:result.url,modal_closed:true},{sleep});}catch{}
  }
  throw error;
 }
 Object.assign(evidence,{search_page_url:result.url,...Object.fromEntries(['matching_title','original_image_url','matched_image_url','matched_card_count','live_sales_raw','live_sales_label','live_sales_value','live_sales_unit'].map(k=>[k,result[k]]))});
 return evidence;
}
export async function returnFromSkuSearch(browser,request,evidence,{sleep=delay,maxPolls=40}={}){
 const control=await browser.evaluate(readSkuSearchCancel,{searchUrl:evidence.search_page_url});
 if(!control.ok)throw new ReviewError('规格已读取，但搜索页取消入口未能核对。','manual_review','sku_search_return_failed');
 await browser.click(control.point,{settleMs:0});
 for(let i=0;i<maxPolls;i++){
  const page=await guard(browser);
  if(browser.tab.id!==evidence.page_id)break;
  if(sameShop(request.shop.source_url,page.url)&&page.text.includes(request.shop.shop_name))return {...evidence,return_verified:true,return_store_url:page.url};
  if(page.url!==evidence.search_page_url&&!sameShop(request.shop.source_url,page.url))break;
  if(i+1<maxPolls)await sleep(100);
 }
 throw new ReviewError('规格已读取，但尚未确认返回原店铺，已暂停后续商品。','manual_review','sku_search_return_failed');
}
