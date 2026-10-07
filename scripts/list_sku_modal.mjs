// Our own short-lived DOM references, never site application state or login data.
// A trusted click on the exact original card establishes the popup's provenance.
export function armListSkuModal({token,title,image,shopName,sourceUrl,searchEvidence,originalSalesValue}){
 const key=Symbol.for('pdd-monitor/sku-modal-binding-v1');
 const searching=searchEvidence!==undefined;
 if(globalThis[key]||typeof token!=='string'||!token||token.length>200||location.href!==sourceUrl||location.origin!=='https://mobile.yangkeduo.com'||(!searching&&(location.pathname!=='/mall_page.html'||!document.body.innerText.includes(shopName))))return {ok:false,reason:'identity'};
 const shown=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);if(!(r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'))return false;if(searching)for(let node=e;node;node=node.parentElement){const style=getComputedStyle(node);if(node.hidden||node.getAttribute?.('aria-hidden')==='true'||style.display==='none'||['hidden','collapse'].includes(style.visibility)||Number(style.opacity)===0)return false;}return true;};
 const sameImage=(original,actual)=>{
  if(typeof original!=='string'||typeof actual!=='string'||!original||!actual)return false;if(original===actual)return true;
  try{
   const urls=[original,actual].map(raw=>{if(!/^https:\/\/(?:img|img-2)\.pddpic\.com\//.test(raw)||/[\x00-\x20\x7f]|%(?![0-9a-fA-F]{2})/.test(raw))throw Error();const u=new URL(raw);if(u.protocol!=='https:'||!['img.pddpic.com','img-2.pddpic.com'].includes(u.host)||u.username||u.password||u.hash||!u.pathname)throw Error();return u;});
   const query=u=>[...u.searchParams].map(([key,value])=>[!value&&key.startsWith('imageMogr2/format/webp/')?'imageMogr2/'+key.slice('imageMogr2/format/webp/'.length):key,value]);
   for(const u of urls)for(const piece of u.search.slice(1).split(/[&=]/))decodeURIComponent(piece.replace(/\+/g,' '));
   return urls[0].pathname===urls[1].pathname&&JSON.stringify(query(urls[0]))===JSON.stringify(query(urls[1]));
  }catch{return false;}
 };
 let search=null;
 const validSearch=()=>{
  try{
   const e=search;if(!e||e.method!=='native_store_search'||!Number.isSafeInteger(e.page_id)||e.page_id<1||!Number.isSafeInteger(originalSalesValue)||originalSalesValue<=0||e.matched_card_count!==1||e.matching_title!==title||e.original_image_url!==image||!sameImage(image,e.matched_image_url))return false;
   if(['shop_name_verified','native_search_opened','native_search_submitted','same_tab_navigation_verified'].some(key=>e[key]!==true)||typeof e.query!=='string'||!e.query.trim()||e.query!==e.query.trim()||e.query.length>120||/[\x00-\x1f\x7f]/.test(e.query))return false;
   const page=(raw,pathname)=>{if(typeof raw!=='string'||!/^https:\/\/mobile\.yangkeduo\.com\//.test(raw)||/[\x00-\x20\x7f]|%(?![0-9a-fA-F]{2})/.test(raw))throw Error();const u=new URL(raw);if(u.href!==raw||u.origin!=='https://mobile.yangkeduo.com'||u.username||u.password||u.port||u.pathname!==pathname||u.hash)throw Error();for(const piece of u.search.slice(1).split(/[&=]/))decodeURIComponent(piece.replace(/\+/g,' '));return u;};
   const store=page(e.source_store_url,'/mall_page.html'),current=page(e.search_page_url,'/mall_search_result.html');
   const one=(u,key)=>{const all=u.searchParams.getAll(key);if(all.length>1||all.some(value=>!value||value!==value.trim()))throw Error();return all[0]??null;};
   const id=one(store,'mall_id'),sn=one(store,'mall_sn'),searchId=one(current,'mall_id');
   if(id!==null&&!/^[1-9]\d{0,24}$/.test(id)||id===null&&sn===null||!searchId||!/^[1-9]\d{0,24}$/.test(searchId)||id!==null&&id!==searchId||one(current,'search_key')!==e.query||one(current,'mall_sn')!==null&&(id!==null||one(current,'mall_sn')!==sn))return false;
   if(location.href!==sourceUrl||sourceUrl!==e.search_page_url||location.pathname!=='/mall_search_result.html')return false;
   const sales=typeof e.live_sales_raw==='string'&&/^(已拼|已抢)\s*([0-9]+)\s*(件)$/.exec(e.live_sales_raw.trim());
   return !!sales&&sales[2].length<=18&&Number.isSafeInteger(e.live_sales_value)&&e.live_sales_value>0&&e.live_sales_value>=originalSalesValue&&Number(sales[2])===e.live_sales_value&&sales[1]===e.live_sales_label&&sales[3]===e.live_sales_unit;
  }catch{return false;}
 };
 if(searching){
  if(!searchEvidence||typeof searchEvidence!=='object'||Array.isArray(searchEvidence))return {ok:false,reason:'identity'};
  search=Object.freeze({...searchEvidence});if(!validSearch())return {ok:false,reason:'identity'};
 }
 if([...document.querySelectorAll('.sku-plus1 [role="dialog"][aria-modal="true"]')].some(shown))return {ok:false,reason:'old_modal'};
 const lists=searching?[...document.querySelectorAll('.waterfall-list-container_NLmKnzfN')]:[document.querySelector('.waterfall-list-container_egohG8wQ')].filter(Boolean);
 const cardImage=card=>card.querySelector('.goodsImage_fU27dxbk img')?.getAttribute('data-src')||card.querySelector('.goodsImage_fU27dxbk img')?.getAttribute('src');
 const beforeBoundary=card=>{let cutoff=Infinity;for(const e of document.querySelectorAll('div,p,span'))if(!e.children.length&&['本店暂无更多商品','其他店铺的精选推荐'].includes(e.textContent.trim())&&shown(e))cutoff=Math.min(cutoff,e.getBoundingClientRect().top);return card.getBoundingClientRect().top<cutoff;};
 const matchingCards=()=>[...new Set(lists.flatMap(list=>[...list.querySelectorAll('.goodsItem_R1ok0MpS')]))].filter(card=>shown(card)&&card.querySelector('.goodsName_dT8mZSNd')?.textContent.trim()===title&&(searching?beforeBoundary(card)&&sameImage(image,cardImage(card)):cardImage(card)===image));
 const cards=matchingCards();
 if(cards.length!==1)return {ok:false,reason:'ambiguous'};
 const card=cards[0],owners=lists.filter(list=>list.contains(card));
 const liveSalesMatch=()=>{const sales=[...card.querySelectorAll('.salesTip_qUlLfJr3')];return sales.length===1&&shown(sales[0])&&sales[0].textContent.trim()===search.live_sales_raw;};
 if(searching&&(owners.length!==1||cardImage(card)!==search.matched_image_url||!liveSalesMatch()))return {ok:false,reason:'identity'};
 const owner=owners[0],pluses=[...card.querySelectorAll('.quantityBtnV2_FM92Tcgf')].filter(e=>shown(e)&&e.querySelector('.addV2_TP_S6XAg'));
 if(pluses.length!==1)return {ok:false,reason:'no_plus'};
 const plus=pluses[0];plus.scrollIntoView({block:'center',behavior:'instant'});
 const r=plus.getBoundingClientRect();let point=null;
 for(const xf of [.5,.3,.7]){const p={x:r.x+r.width*xf,y:r.y+r.height/2};if(p.x>0&&p.x<innerWidth&&p.y>0&&p.y<innerHeight&&plus.contains(document.elementFromPoint(p.x,p.y))){point=p;break;}}
 if(!point)return {ok:false,reason:'obscured'};
 const binding={token,sourceUrl,card,plus,title,imageUrl:searching?search.matched_image_url:image,shopName,modal:null,clicked:false,opened:false,invalid:false};
 if(searching)Object.defineProperties(binding,{searchEvidence:{value:search,enumerable:true},verifySearch:{value:()=>{
  if(!validSearch()||!card.isConnected||cardImage(card)!==search.matched_image_url||!liveSalesMatch())return false;
  const currentLists=[...document.querySelectorAll('.waterfall-list-container_NLmKnzfN')],currentOwners=currentLists.filter(list=>list.contains(card));
  if(currentOwners.length!==1||currentOwners[0]!==owner||currentLists.length!==lists.length||currentLists.some((list,index)=>list!==lists[index]))return false;
  const matches=matchingCards();return matches.length===1&&matches[0]===card;
 }}});
 binding.onClick=e=>{
  if(!e.isTrusted){binding.invalid=true;return;}
  if(!binding.clicked){if(plus.contains(e.target)){binding.clicked=true;return;}binding.invalid=true;return;}
  const option=e.target?.closest?.('.s1O5M5fO > [role="button"],.skuSpecValueList_s1O5M5fO > [role="button"]');
  if(binding.opened&&option&&binding.modal?.contains(option))return;
  if(binding.closing&&binding.modal?.contains(e.target)&&binding.closeTarget?.contains(e.target))return;
  binding.invalid=true;
 };
 document.addEventListener('click',binding.onClick,true);globalThis[key]=binding;
 return {ok:true,point};
}

export function checkListSkuModal({token,bind=false,closing=false}){
 const binding=globalThis[Symbol.for('pdd-monitor/sku-modal-binding-v1')];
 if(!binding||binding.token!==token||binding.invalid||!binding.clicked||location.href!==binding.sourceUrl||location.origin!=='https://mobile.yangkeduo.com'||!binding.card.isConnected)return {ok:false};
 const searching=Object.hasOwn(binding,'searchEvidence');
 if(searching){try{if(!Object.isFrozen(binding.searchEvidence)||typeof binding.verifySearch!=='function'||binding.verifySearch()!==true)return {ok:false};}catch{return {ok:false};}}
 else if(location.pathname!=='/mall_page.html'||!document.body.innerText.includes(binding.shopName))return {ok:false};
 const lists=searching?[...document.querySelectorAll('.waterfall-list-container_NLmKnzfN')]:[document.querySelector('.waterfall-list-container_egohG8wQ')].filter(Boolean);
 const cards=[...new Set(lists.flatMap(list=>[...list.querySelectorAll('.goodsItem_R1ok0MpS')]))].filter(e=>e.querySelector('.goodsName_dT8mZSNd')?.textContent.trim()===binding.title&&(e.querySelector('.goodsImage_fU27dxbk img')?.getAttribute('data-src')||e.querySelector('.goodsImage_fU27dxbk img')?.getAttribute('src'))===binding.imageUrl);
 if(cards.length!==1||cards[0]!==binding.card)return {ok:false};
 const modals=[...document.querySelectorAll('.sku-plus1 [role="dialog"][aria-modal="true"]')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';});
 if(modals.length!==1)return {ok:false,...(!modals.length&&!binding.opened?{reason:'not_open_yet'}:{})};
 if(bind&&!binding.opened){binding.modal=modals[0];binding.opened=true;}
 if(!binding.opened||binding.modal!==modals[0]||!binding.modal.isConnected)return {ok:false};
 if(closing){
  const bounds=binding.modal.getBoundingClientRect();
  // The pale X can be a span/div/SVG, without the old exact aria-label. Require
  // both a close marker and its small, upper-right location in this bound modal.
  // Never guess a coordinate or treat a purchase/quantity control as a close.
  const exact=[...binding.modal.querySelectorAll('[role="button"][aria-label="关闭弹窗"]')];
  const candidates=[...new Set([...exact,...binding.modal.querySelectorAll('button,[role="button"],[aria-label],[title],[class*="close"],[class*="Close"],svg,span,div')])].filter(e=>{
   const r=e.getBoundingClientRect(),s=getComputedStyle(e),text=(e.textContent||'').trim(),label=(e.getAttribute('aria-label')||e.getAttribute('title')||'').trim();
   const marked=exact.includes(e)||/^(?:关闭(?:弹窗|弹层|规格)?|close(?: dialog| modal)?|[×✕✖xX])$/i.test(label)||/^[×✕✖xX]$/.test(text)||/close/i.test(e.getAttribute('class')||String(e.className||''));
   return marked&&!/确定|确认|购买|支付|订单|数量/.test(label+' '+text)&&r.width>=6&&r.width<=80&&r.height>=6&&r.height<=80&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0'&&r.x+r.width/2>=bounds.x+bounds.width-Math.min(140,bounds.width/3)&&r.y>=bounds.y-4&&r.y+r.height/2<=bounds.y+120;
  });
  // Choose the marked leaf, not a container that also wraps quantity controls.
  // Two marked sibling branches stay ambiguous even if an outer class says close.
  const buttons=candidates.filter(e=>!candidates.some(child=>child!==e&&e.contains(child)));
  if(buttons.length!==1)return {ok:false,reason:'close_ambiguous'};
  const e=buttons[0],r=e.getBoundingClientRect();let point=null;
  if([...e.querySelectorAll('button,[role="button"],input,a,[tabindex]')].some(child=>child!==e&&!candidates.includes(child)))return {ok:false,reason:'close_ambiguous'};
  for(const xf of [.5,.3,.7]){const p={x:r.x+r.width*xf,y:r.y+r.height/2};if(p.x>0&&p.x<innerWidth&&p.y>0&&p.y<innerHeight&&e.contains(document.elementFromPoint(p.x,p.y))){point=p;break;}}
  if(!point)return {ok:false,reason:'close_obscured'};
  binding.closing=true;binding.closeTarget=e;return {ok:true,point};
 }
 return {ok:true};
}

export function releaseListSkuModal({token,probe=false}){
 const key=Symbol.for('pdd-monitor/sku-modal-binding-v1'),binding=globalThis[key];
 if(!binding||binding.token!==token)return {released:false};
 // This site hides the popup without removing its DOM after closing it.
 // A different visible popup still blocks the next product.
 const visible=[...document.querySelectorAll('.sku-plus1 [role="dialog"][aria-modal="true"]')].some(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.isConnected&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';});
 if(probe)return {released:false,closed:!visible&&!binding.invalid&&binding.closing===true&&location.href===binding.sourceUrl};
 document.removeEventListener('click',binding.onClick,true);delete globalThis[key];
 return {released:true,closed:!visible};
}
