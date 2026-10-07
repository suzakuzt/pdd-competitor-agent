// Fixed DOM-only preflight for a user-supplied replacement storefront entry.
// The entry URL never replaces canonical shop identity or rewrites history.
import {ReviewError,guard,delay} from './local_browser.mjs';
import {shopIdentityFromUrl} from './live_capture_driver.mjs';

export function readStorefrontState({shopName}){
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
 const list=document.querySelector('.waterfall-list-container_egohG8wQ');
 const cards=list?[...list.querySelectorAll('.goodsItem_R1ok0MpS')].filter(visible):[];
 const candidates=[...document.querySelectorAll('button,a,[role="button"],[role="tab"],li,div,span')].filter(e=>visible(e)&&e.textContent.trim()==='上新');
 const latest=candidates.find(e=>!candidates.some(child=>child!==e&&e.contains(child)));
 const r=latest?.getBoundingClientRect();
 const current=e=>e&&(e.getAttribute('aria-selected')==='true'||/(?:^|\s|_)current(?:\s|_|$)|(?:^|\s|_)active(?:\s|_|$)/i.test(e.className||''));
 const text=document.body.innerText;
 return {url:location.href,shopVerified:text.includes(shopName),cards:cards.length,
  latestPoint:r&&r.bottom>0&&r.top<innerHeight?{x:r.x+r.width/2,y:r.y+r.height/2}:null,
  latestSelected:!!(current(latest)||current(latest?.parentElement)),
  empty:text.includes('本店暂无更多商品')&&cards.length===0};
}

export function verifyStoreIdentity(sourceUrl,actualUrl){
 let expected,actual;
 try{expected=shopIdentityFromUrl(sourceUrl);actual=shopIdentityFromUrl(actualUrl);}
 catch{throw new ReviewError('新链接尚未显示可核对的店铺，请重新粘贴本店分享链接。','needs_url','entry_unavailable');}
 const key=expected.identity_kind==='mall_id'?'source_mall_id':'source_mall_sn';
 if(actual.origin!==expected.origin||actual[key]!==expected.stable_identifier){
  throw new ReviewError('新链接与当前店铺不一致，请确认后粘贴本店链接；原数据保留。','needs_url','identity_mismatch');
 }
 return true;
}

export async function waitForStorefront(browser,{shopName,sourceUrl,cancelled=async()=>false,
 sleep=delay,maxPolls=40,pollMs=500,requireSelected=false}={}){
 let state;
 for(let i=0;i<maxPolls;i++){
  if(await cancelled())throw new ReviewError('已取消，原数据保留。','cancelled','operator_cancelled');
  await guard(browser);
  state=await browser.evaluate(readStorefrontState,{shopName});
  // Do not reject an intermediate short URL before redirect/hydration completes.
  if(state.shopVerified&&state.cards>0&&state.latestPoint){
   verifyStoreIdentity(sourceUrl,state.url);
   if(!requireSelected||state.latestSelected)return state;
  }
  if(i+1<maxPolls)await sleep(pollMs);
 }
 if(state?.shopVerified){
  verifyStoreIdentity(sourceUrl,state.url);
  if(state.cards===0)throw new ReviewError('页面没有读到商品，请粘贴本店最新分享链接后重试；已有数据保留。','needs_url','zero_products');
  throw new ReviewError('未确认上新列表，请在 Chrome 检查页面后重试；已有数据保留。','manual_review','sort_unverified');
 }
 throw new ReviewError('店铺链接未显示本店商品，请粘贴最新分享链接后重试；已有数据保留。','needs_url','entry_unavailable');
}
