// Public DOM evidence for a new, explicitly supplied storefront. No product
// capture, guessed shop name, hidden API, or screenshot is used for identity.
import {ReviewError,guard,checkPageGuard,delay} from './local_browser.mjs';

function stopped(message,reason='intake_unverified',status='manual_review'){
 return new ReviewError(message,status,reason);
}

export function intakeStoreIdentity(raw,{allowShare=false}={}){
 let url;try{url=new URL(raw);}catch{throw stopped('店铺来源无效。','source_changed');}
 if(url.origin!=='https://mobile.yangkeduo.com'||url.username||url.password||url.pathname!=='/mall_page.html'||url.hash){
  throw stopped('店铺页面来源发生变化，请复核分享链接。','source_changed');
 }
 const keys=['mall_sn','mall_id'].filter(key=>url.searchParams.has(key));
 if(keys.length===0&&allowShare&&url.searchParams.size===1&&url.searchParams.getAll('ps').length===1&&/^[A-Za-z0-9_-]{1,128}$/.test(url.searchParams.get('ps')||''))return null;
 if(keys.length!==1)throw stopped('未读到唯一店铺来源标识。','identity_unverified');
 const kind=keys[0],values=url.searchParams.getAll(kind),value=values[0]||'';
 if(values.length!==1||!(kind==='mall_id'?/^[1-9][0-9]{0,31}$/:/^[A-Za-z0-9_+=/-]{1,512}$/).test(value)){
  throw stopped('店铺来源标识无效或重复。','identity_unverified');
 }
 return {kind,value};
}

// Self-contained because the browser adapter serializes this function.
export function readStorefrontIntake(){
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth&&s.display!=='none'&&s.visibility!=='hidden';};
 const headers=[...document.querySelectorAll('[class^="mallName_"],[class*=" mallName_"]')].filter(visible);
 const header=headers.length===1?headers[0].innerText.trim():'';
 const lists=[...document.querySelectorAll('.waterfall-list-container_egohG8wQ')].filter(visible);
 const sorts=[...document.querySelectorAll('#rc-opt-list')].filter(visible);
 const options=sorts.length===1?[...sorts[0].querySelectorAll('li')].filter(visible):[];
 const latest=options.filter(e=>e.innerText.trim()==='上新');
 const selected=options.filter(e=>e.getAttribute('aria-selected')==='true'||String(e.className||'').split(/\s+/).some(c=>/^(?:current|active)(?:_|$)/i.test(c)));
 const control=latest.length===1?latest[0]:null,r=control?.getBoundingClientRect();
 const text=document.body.innerText;
 return {url:location.href,title:document.title,header,headerCount:headers.length,listCount:lists.length,sortCount:sorts.length,
  latestCount:latest.length,latestPoint:r?{x:r.x+r.width/2,y:r.y+r.height/2}:null,
  latestSelected:!!control&&selected.length===1&&selected[0]===control,
  selectedMarkup:control&&selected.length===1&&selected[0]===control?control.outerHTML:'',
  login:/login\.html|login\.yangkeduo|请先登录|登录后查看|验证码登录|短信登录|手机号码登录/.test(location.href+' '+text),
  challenge:/滑动.*验证|请完成.*验证|安全验证|访问受限|操作过于频繁/.test(text)};
}

export async function verifyStorefrontIntake(browser,{userShareUrl,cancelled=async()=>false,progress=async()=>{},sleep=delay,maxPolls=40,pollMs=500}={}){
 const expected=intakeStoreIdentity(userShareUrl,{allowShare:true});
 let pinned=null,clicked=false;
 const cancel=async()=>{if(await cancelled())throw stopped('店铺核验已取消。','operator_cancelled','cancelled');};
 await cancel();
 await progress({phase:'正在读取店铺名称和来源'});
 await browser.goto(userShareUrl);
 for(let i=0;i<maxPolls;i++){
  await cancel();
  await guard(browser);
  const state=await browser.evaluate(readStorefrontIntake);
  checkPageGuard(state);
  const actual=intakeStoreIdentity(state.url,{allowShare:state.url===userShareUrl&&!expected});
  if(actual&&expected&&(expected.kind!==actual.kind||expected.value!==actual.value))throw stopped('分享入口与实际店铺来源不一致。','identity_mismatch');
  if(pinned&&(!actual||pinned.kind!==actual.kind||pinned.value!==actual.value))throw stopped('店铺核验期间来源发生变化。','identity_mismatch');
  if(state.headerCount>1||state.listCount>1||state.sortCount>1||state.latestCount>1)throw stopped('页面存在多个店铺标题或上新控件，需人工复核。','intake_ambiguous');
  if(pinned&&state.header&&state.header!==pinned.name)throw stopped('店铺核验期间名称发生变化。','identity_mismatch');
  const name=state.header;
  const ready=actual&&state.headerCount===1&&typeof name==='string'&&name.length>0&&name.length<=120&&!/[\r\n\x00-\x1f\x7f]/.test(name)
   &&state.title===name&&state.listCount===1&&state.sortCount===1&&state.latestCount===1&&state.latestPoint;
  if(ready){
   if(!clicked){
    pinned={...actual,name};
    await cancel();
    await progress({phase:'正在确认店铺上新列表'});
    await browser.click(state.latestPoint);
    clicked=true;
   }else if(state.latestSelected&&typeof state.selectedMarkup==='string'&&state.selectedMarkup.length>0&&state.selectedMarkup.length<=8192){
    await cancel();
    return {userShareUrl,shopName:name,sourceUrl:state.url,header:name,selectedMarkup:state.selectedMarkup,observedAt:new Date().toISOString()};
   }
  }
  if(i+1<maxPolls)await sleep(pollMs);
 }
 throw stopped(clicked?'未确认上新已选中，请在 Chrome 检查页面。':'未读到可核对的店铺名称、来源和商品列表，请检查分享链接。',clicked?'sort_unverified':'intake_unverified');
}
