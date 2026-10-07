// Synthetic rendered nodes and trusted-event fixtures only. No browser or database.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {captureListSkuModal} from '../scripts/local_sku_capture.mjs';
import {captureSkuBatch} from '../scripts/local_sku_batch.mjs';
import {armListSkuModal,checkListSkuModal} from '../scripts/list_sku_modal.mjs';

let modalAttempt=0;
// This file exercises the production modal reader after a real DOM arm. The
// search entry/return driver has its own tests and does not belong in this fixture.
async function captureSku(browser,request,cancelled,progress){
 const token=`synthetic-modal-${++modalAttempt}`,row=request.observation;
 const armed=await browser.evaluate(armListSkuModal,{token,title:row.title,image:row.image_url,shopName:request.shop.shop_name,sourceUrl:request.shop.source_url});
 assert.equal(armed.ok,true);
 return captureListSkuModal(browser,request,cancelled,progress,{token,point:armed.point,locator:{method:'store_scroll',entry:'card_plus'}});
}

const shop={shop_name:'SYNTHETIC店',source_url:'https://mobile.yangkeduo.com/mall_page.html?mall_id=123'};
const mainImage='https://img.pddpic.com/SYNTHETIC_MAIN.png';
const skuImages=Array.from({length:5},(_,i)=>`https://img.pddpic.com/SYNTHETIC_SKU_${i+1}.png`);
const request={shop,observation:{observation_id:31,view_order:17,title:'SYNTHETIC商品',image_url:mainImage,
 goods_id:null,goods_url:null,current_unique_card_candidate_count:1,
 sales_label:'已抢',sales_value:5,sales_unit:'件',sales_precision:'exact_display'}};

function fixture(){
 const state={opened:false,selected:0,title:request.observation.title,layoutFailure:false,replaceModal:false,
  closeBehavior:'remove',modalHidden:false,closeMarker:'aria',closeRequested:false,closeProbeCount:0,closeDelayReads:3,extraClose:null,closeBlocked:false,
  clicks:[],progress:[],imageCalls:[],tokens:[],visits:[],listeners:new Set(),afterOption:null};
 function node(kind,x=20,y=300,width=100,height=40){
  const value={kind,tagName:'DIV',isConnected:true,children:[],parentElement:null,hidden:false,className:'',
   getBoundingClientRect:()=>({x,y,width,height,top:y,bottom:y+height,left:x,right:x+width}),
   contains(other){return value===other||value.children.some(child=>child.contains(other));},
   querySelector:()=>null,querySelectorAll:()=>[],getAttribute:()=>null,hasAttribute:()=>false,
   scrollIntoView(){},closest:()=>null,textContent:'',innerText:''};
  return value;
 }
 const attach=(parent,children)=>{parent.children=children;for(const child of children)child.parentElement=parent;};
 const body=node('body',0,0,1280,850);body.innerText=shop.shop_name;
 const list=node('list'),card=node('card',50,50,220,240),title=node('title');
 Object.defineProperty(title,'textContent',{get:()=>state.title});
 const image=node('main_image');image.getAttribute=name=>['src','data-src'].includes(name)?mainImage:null;
 const plus=node('plus',200,220,40,40),plusMark=node('plus_mark');attach(plus,[plusMark]);
 plus.querySelector=selector=>selector==='.addV2_TP_S6XAg'?plusMark:null;
 card.querySelector=selector=>selector==='.goodsName_dT8mZSNd'?title:selector==='.goodsImage_fU27dxbk img'?image:null;
 card.querySelectorAll=selector=>selector==='.quantityBtnV2_FM92Tcgf'?[plus]:[];
 attach(card,[title,image,plus]);attach(list,[card]);
 list.querySelectorAll=selector=>selector==='.goodsItem_R1ok0MpS'?[card]:[];
 const modal=node('modal',10,290,500,430),replacement=node('replacement_modal',10,290,500,430),group=node('group');
 const modalBounds=modal.getBoundingClientRect;
 modal.getBoundingClientRect=()=>state.modalHidden?{x:0,y:0,width:0,height:0,top:0,bottom:0,left:0,right:0}:modalBounds();
 const heading=node('heading');heading.textContent='款式';
 const options=Array.from({length:5},(_,i)=>{
  const option=node('option',30+i*85,430,70,40);option.index=i;option.textContent=`款式${i+1}`;
  option.getAttribute=name=>name==='aria-label'?option.textContent:null;
  Object.defineProperty(option,'className',{get:()=>state.selected===i?'hr353bdX':''});
  option.classList={contains:name=>name==='hr353bdX'&&state.selected===i};
  option.closest=selector=>selector.includes('.s1O5M5fO > [role="button"]')?option:null;
  return option;
 });
 group.querySelector=selector=>selector==='.sku-specs-key'?heading:null;
 group.querySelectorAll=selector=>selector==='.s1O5M5fO > [role="button"]'?options:[];
 attach(group,[heading,...options]);
 const current=node('current_price'),original=node('original_price'),summary=node('summary'),skuImage=node('sku_image');
 Object.defineProperty(current,'innerText',{get:()=>`券后¥${state.selected+4}.50`});
 Object.defineProperty(original,'innerText',{get:()=>`券前¥${state.selected+9}.50`});
 Object.defineProperty(summary,'textContent',{get:()=>`已选：款式${state.selected+1}`});
 Object.assign(skuImage,{complete:true,naturalWidth:375,naturalHeight:500});
 Object.defineProperty(skuImage,'currentSrc',{get:()=>skuImages[state.selected]});
 Object.defineProperty(skuImage,'src',{get:()=>skuImages[state.selected]});
 const close=node('close',455,300,40,40),quantity=node('quantity',40,620,60,40),confirm=node('confirm',150,650,200,40);
 Object.defineProperty(close,'textContent',{get:()=>state.closeMarker==='glyph'?'×':''});
 close.getAttribute=name=>name==='aria-label'?(state.closeMarker==='aria'?'关闭弹窗':state.closeMarker==='label'?'关闭':null):name==='class'&&state.closeMarker==='class'?'skuSelectorClose_paleIcon':null;
 close.closest=selector=>selector==='[role="button"][aria-label="关闭弹窗"]'?close:null;
 modal.querySelector=selector=>selector==='.Mbx2m60G'?summary:selector==='.O7pEFvHR > img'?skuImage:null;
 modal.querySelectorAll=selector=>selector==='.bIhLWVqm'?[group]:selector==='.ujEqGzEB'?[current]:
  selector==='.mhHA_CEU,del,s'?[original]:selector==='[role="button"][aria-label="关闭弹窗"]'?(state.closeMarker==='aria'?[close]:[]):selector.startsWith('button,')?[close,...(state.extraClose?[state.extraClose]:[])]:[];
 Object.defineProperty(modal,'innerText',{get:()=>`${current.innerText} ${original.innerText} ${summary.textContent}`});
 // A same-looking replacement must fail by DOM continuity, not merely by a
 // changed catalog. Its layout and selected fields are otherwise identical.
 replacement.querySelector=modal.querySelector;replacement.querySelectorAll=modal.querySelectorAll;
 Object.defineProperty(replacement,'innerText',{get:()=>modal.innerText});
 attach(modal,[group,current,original,summary,skuImage,close,quantity,confirm]);attach(body,[list,modal]);
 const document={body,querySelector:selector=>selector==='.waterfall-list-container_egohG8wQ'?list:null,
  querySelectorAll:selector=>selector==='.sku-plus1 [role="dialog"][aria-modal="true"]'&&state.opened?[state.replaceModal?replacement:modal]:[],
  addEventListener(type,listener,capture){assert.equal(type,'click');assert.equal(capture,true);state.listeners.add(listener);},
  removeEventListener(type,listener,capture){assert.equal(type,'click');assert.equal(capture,true);state.listeners.delete(listener);},
  elementFromPoint(x,y){const hit=(state.opened&&!state.modalHidden?[close,...options,quantity,confirm]:[plus]).find(e=>{const r=e.getBoundingClientRect();return x>=r.x&&x<r.right&&y>=r.y&&y<r.bottom;})||body;return state.closeBlocked&&hit===close?confirm:hit;}};
 const context=vm.createContext({document,location:{href:shop.source_url,origin:'https://mobile.yangkeduo.com',pathname:'/mall_page.html'},
  innerHeight:850,innerWidth:1280,getComputedStyle:()=>({display:'block',visibility:'visible',textDecorationLine:'none'})});
 const run=(fn,args)=>JSON.parse(JSON.stringify(vm.runInContext(`(${fn.toString()})(${JSON.stringify(args)})`,context)));
 const dispatch=(target,isTrusted=true)=>{for(const listener of state.listeners)listener({target,isTrusted});};
 const browser={
  async goto(url){assert.equal(url,shop.source_url);state.visits.push(url);},
  async submitShopSearch(){assert.fail('list SKU must not search');},
  async scroll(){assert.fail('unique card is already visible');},
  async evaluate(fn,args){
   if(fn.name==='readPageGuard')return {url:shop.source_url,text:shop.shop_name,login:false,challenge:false};
   if(fn.name==='readStorefrontState')return {url:shop.source_url,shopVerified:true,cards:1,latestSelected:true,latestPoint:{x:10,y:10}};
   if(fn.name==='readRenderedDom')return {pageUrl:shop.source_url,endBoundaryObserved:false,scrollTop:0,viewportHeight:850};
   if(fn.name==='locateCard'){assert.equal(args.title,state.title);assert.equal(args.image,mainImage);return {count:1,point:{x:100,y:100,inViewport:true}};}
   if(fn.name==='armListSkuModal')state.tokens.push(args.token);
   if(fn.name==='readSpecs'&&state.layoutFailure)return {supported:false,reason:'SYNTHETIC unsupported layout'};
   if(fn.name==='releaseListSkuModal'&&args.probe){
    state.closeProbeCount++;assert.equal(state.listeners.size,1,'binding remains armed while closing');
    if(state.closeRequested&&state.closeBehavior==='delayed'&&state.closeProbeCount>=state.closeDelayReads)state.modalHidden=true;
   }
   return run(fn,args);
  },
  async click(point){
   const target=document.elementFromPoint(point.x,point.y);state.clicks.push(target.kind);
   assert.ok(['plus','option','close'].includes(target.kind),'never click quantity, confirmation, or another card');
   dispatch(target);
   if(target===plus){state.opened=true;state.modalHidden=false;modal.isConnected=true;}
   if(target.kind==='option'){state.selected=target.index;state.afterOption?.();}
   if(target===close){
    state.closeRequested=true;
    if(state.closeBehavior==='hide')state.modalHidden=true;
    else if(state.closeBehavior==='remove'){state.opened=false;modal.isConnected=false;}
   }
  },
  async collectSkuImages(args){
   assert.equal(args.goodsId,null);assert.equal(args.listModalToken,state.tokens.at(-1));
   assert.deepEqual(run(checkListSkuModal,{token:args.listModalToken}),{ok:true});
   assert.deepEqual(args.imageUrls,[skuImages[state.selected]]);state.imageCalls.push(args);return {newlySaved:1};
  },
  async flush(){return {};},
 };
 return {state,browser,dispatch,quantity,context,modal,close,node};
}

test('upper-right pale X works with a short close label, a glyph or a close icon class',async()=>{
 for(const marker of ['label','glyph','class']){
  const f=fixture();f.state.closeMarker=marker;
  const result=await captureSku(f.browser,request,async()=>false,async()=>{});
  assert.equal(result.status,'complete',marker);assert.equal(f.state.opened,false);assert.equal(f.state.listeners.size,0);
  assert.equal(f.state.clicks.filter(x=>x==='close').length,1);
 }
});

test('close animation is polled until hidden, keeping the original modal binding until then',async()=>{
 const f=fixture();f.state.closeMarker='glyph';f.state.closeBehavior='delayed';
 const result=await captureSku(f.browser,request,async()=>false,async()=>{});
 assert.equal(result.status,'complete');assert.equal(f.state.closeProbeCount,3);
 assert.equal(f.state.modalHidden,true);assert.equal(f.state.listeners.size,0);
 assert.equal(f.state.clicks.filter(x=>x==='close').length,1,'animation never causes another click');
});

test('unmarked, obscured, off-corner or ambiguous controls cannot substitute for the close X',async()=>{
 for(const kind of ['unmarked','obscured','off_corner','ambiguous']){
  const f=fixture();f.state.layoutFailure=true;
  if(kind==='unmarked')f.state.closeMarker='none';
  if(kind==='obscured')f.state.closeBlocked=true;
  if(kind==='off_corner')f.close.getBoundingClientRect=()=>({x:20,y:640,width:40,height:40});
  if(kind==='ambiguous'){const other=f.node('other_close',400,300,30,30);other.textContent='×';f.state.extraClose=other;f.modal.children.push(other);}
  await assert.rejects(captureSku(f.browser,request,async()=>false,async()=>{}),e=>e.reason==='sku_modal_cleanup_failed'&&e.cause_reason==='sku_layout_unrecognized',kind);
  assert.deepEqual(f.state.clicks,['plus']);assert.equal(f.state.opened,true);assert.equal(f.state.listeners.size,0);
 }
});

test('a marked outer group cannot widen a close X hit to a neighboring quantity control',async()=>{
 const f=fixture(),group=f.node('close_group',420,300,80,40);
 group.className='closeGroup';group.children=[f.close,f.quantity];f.state.extraClose=group;
 const result=await captureSku(f.browser,request,async()=>false,async()=>{});
 assert.equal(result.status,'complete');assert.equal(f.state.clicks.at(-1),'close');
 assert.ok(!f.state.clicks.includes('quantity'));
});

test('exact list-card plus reads five selected SKU prices and images with null public ID',async()=>{
 const f=fixture(),result=await captureSku(f.browser,request,async()=>false,async p=>f.state.progress.push(p));
 assert.equal(result.status,'complete');assert.equal(result.identity_basis,'current_unique_card_modal');
 assert.equal(result.capture_source,'shop_card_modal');assert.equal(result.goods_id,null);assert.equal(result.goods_url,null);
 assert.equal(result.expected_combinations,5);assert.equal(result.locator.entry,'card_plus');
 assert.deepEqual(result.variants.map(v=>v.current_price_raw),['券后¥4.50','券后¥5.50','券后¥6.50','券后¥7.50','券后¥8.50']);
 assert.deepEqual(result.variants.map(v=>v.original_price_raw),['券前¥9.50','券前¥10.50','券前¥11.50','券前¥12.50','券前¥13.50']);
 assert.deepEqual(result.variants.map(v=>v.image_url),skuImages);assert.ok(result.variants.every(v=>v.selection_verified));
 assert.equal(f.state.imageCalls.length,5);assert.equal(new Set(f.state.imageCalls.map(v=>v.listModalToken)).size,1);
 assert.deepEqual(result.source_card_evidence,{title:request.observation.title,image_url:mainImage,view_order:17,shop_source_url:shop.source_url});
 assert.deepEqual(result.list_modal_evidence,{shop_source_url:shop.source_url,shop_name:shop.shop_name,title:request.observation.title,
  image_url:mainImage,view_order:17,observation_id:31,matched_card_count:1,opened_from_verified_plus:true,modal_continuity_verified:true});
 assert.deepEqual(f.state.clicks,['plus','option','option','option','option','close']);
 assert.equal(f.state.opened,false);assert.equal(f.state.listeners.size,0);
 assert.deepEqual(f.state.progress.filter(p=>p.variants).map(p=>p.variants),[1,2,3,4,5]);
});

test('production batch-reader path groups identity and specs, keeping full evidence only for each final SKU',async()=>{
 const f=fixture(),read=f.browser.evaluate;let batches=0,fullEvidence=0;
 f.browser.evaluateMany=async readers=>{
  batches++;assert.deepEqual(readers.map(([fn])=>fn.name),['readPageGuard','checkListSkuModal','readSpecs']);
  assert.deepEqual(readers[0][1],{includeText:false});
  if(readers[2][1].includeRawText)fullEvidence++;
  const values=[];for(const [fn,arg] of readers)values.push(await read(fn,arg));return values;
 };
 const result=await captureSku(f.browser,request,async()=>false,async()=>{});
 assert.equal(result.status,'complete');assert.equal(fullEvidence,5);
 assert.ok(batches>=25&&batches<=30,`bounded combined reads: ${batches}`);
 assert.ok(result.variants.every(v=>v.raw_text.includes('已选：')));
});

test('another popup or a click outside specification controls prevents saving the changed SKU',async()=>{
 for(const change of ['replacement','outside']){
  const f=fixture();let result;
  f.state.selected=4;
  f.state.afterOption=()=>{if(change==='replacement')f.state.replaceModal=true;else f.dispatch(f.quantity);};
  await assert.rejects(captureSku(f.browser,request,async()=>false,async p=>f.state.progress.push(p)).then(v=>{result=v;}),
   e=>e.reason==='sku_modal_cleanup_failed'&&e.cause_reason==='sku_identity_changed',change);
  assert.equal(result,undefined);assert.equal(f.state.imageCalls.length,0);
  assert.equal(f.state.progress.filter(p=>p.variants).length,0);assert.equal(f.state.listeners.size,0);
 }
});

test('unsupported local popup is closed and released before the next original card can run',async()=>{
 const f=fixture();f.state.layoutFailure=true;
 await assert.rejects(captureSku(f.browser,request,async()=>false,async()=>{}),e=>e.reason==='sku_layout_unrecognized');
 assert.equal(f.state.opened,false);assert.equal(f.state.listeners.size,0);assert.deepEqual(f.state.clicks,['plus','close']);
 f.state.layoutFailure=false;f.state.title='SYNTHETIC下一原卡';
 const next={...request,observation:{...request.observation,observation_id:32,view_order:18,title:f.state.title}};
 const result=await captureSku(f.browser,next,async()=>false,async()=>{});
 assert.equal(result.status,'complete');assert.equal(result.variants.length,5);assert.equal(result.list_modal_evidence.observation_id,32);
 assert.equal(result.source_card_evidence.title,f.state.title);assert.notEqual(f.state.tokens[0],f.state.tokens[1]);
 assert.equal(f.state.opened,false);assert.equal(f.state.listeners.size,0);
});

test('zero-size retained popup is closed successfully and the next original card can complete',async()=>{
 const f=fixture();f.state.closeBehavior='hide';
 const first=await captureSku(f.browser,request,async()=>false,async()=>{});
 assert.equal(first.status,'complete');assert.equal(f.state.opened,true);assert.equal(f.modal.isConnected,true);
 assert.equal(f.modal.getBoundingClientRect().width,0);assert.equal(f.modal.getBoundingClientRect().height,0);
 assert.equal(f.state.listeners.size,0);
 f.state.title='SYNTHETIC下一原卡';
 const next={...request,observation:{...request.observation,observation_id:32,view_order:18,title:f.state.title}};
 const second=await captureSku(f.browser,next,async()=>false,async()=>{});
 assert.equal(second.status,'complete');assert.equal(second.list_modal_evidence.observation_id,32);
 assert.equal(first.variants.length,5);assert.equal(second.variants.length,5);assert.equal(f.state.imageCalls.length,10);
 assert.equal(f.state.modalHidden,true);assert.equal(f.state.listeners.size,0);assert.notEqual(f.state.tokens[0],f.state.tokens[1]);
});

test('a still-visible popup halts the batch, including after an earlier local layout failure',async()=>{
 for(const layoutFailure of [false,true]){
  const f=fixture(),saved=[],attempted=[];f.state.closeBehavior='fail';f.state.layoutFailure=layoutFailure;
  const batch=await captureSkuBatch(f.browser,{shop,observations:[request.observation,{...request.observation,observation_id:32}]},{
   cancelled:async()=>false,progress:async()=>{},checkpoint:async()=>{},
   capture:async(...args)=>{attempted.push(args[1].observation.observation_id);return captureSku(...args);},
   saveItem:async value=>{saved.push(value);return 'SYNTHETIC_CAPTURE.json';},
  });
  assert.equal(batch.status,'manual_review');assert.equal(batch.reason,'sku_modal_cleanup_failed');
  assert.equal(batch.remaining,1);assert.deepEqual(attempted,[31]);assert.equal(f.state.opened,true);
  assert.equal(f.state.modalHidden,false);assert.equal(f.state.listeners.size,0);
  assert.equal(saved.length,layoutFailure?0:1);
  if(!layoutFailure){assert.equal(saved[0].sku.status,'partial');assert.equal(saved[0].sku.variants.length,5);}
 }
 const f=fixture();f.state.closeBehavior='fail';f.state.layoutFailure=true;
 await assert.rejects(captureSku(f.browser,request,async()=>false,async()=>{}),
  e=>e.reason==='sku_modal_cleanup_failed'&&e.cause_reason==='sku_layout_unrecognized');
});
