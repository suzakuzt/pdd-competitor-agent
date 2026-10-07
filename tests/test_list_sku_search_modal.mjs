import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {armListSkuModal,checkListSkuModal,releaseListSkuModal} from '../scripts/list_sku_modal.mjs';

const origin='https://mobile.yangkeduo.com',store=origin+'/mall_page.html?mall_sn=SYNTHETIC',query='SYNTHETIC商品';
const searchUrl=origin+'/mall_search_result.html?mall_id=123&search_key='+encodeURIComponent(query);
const original='https://img-2.pddpic.com/SYNTHETIC.jpg?imageMogr2%2Fformat%2Fwebp%2Fquality%2F80';
const actual='https://img.pddpic.com/SYNTHETIC.jpg?imageMogr2/quality/80';
const evidence=()=>({method:'native_store_search',source_store_url:store,query,search_page_url:searchUrl,page_id:42,
 shop_name_verified:true,native_search_opened:true,native_search_submitted:true,same_tab_navigation_verified:true,
 matching_title:query,original_image_url:original,matched_image_url:actual,matched_card_count:1,
 live_sales_raw:'已拼598件',live_sales_label:'已拼',live_sales_value:598,live_sales_unit:'件'});

function fixture(){
 const state={opened:false,title:query,image:actual,sales:'已拼598件',boundary:Infinity},listeners=new Set();
 function node(text='',top=50){return {textContent:text,children:[],parentElement:null,isConnected:true,hidden:false,
  getBoundingClientRect:()=>({x:20,y:top,top,bottom:top+40,width:100,height:40}),getAttribute:()=>null,
  querySelector:()=>null,querySelectorAll:()=>[],scrollIntoView(){},contains(other){return this===other||this.children.some(child=>child.contains(other));}};}
 const body=node('搜索结果'),left=node(),right=node(),card=node(),title=node(),image=node(),sales=node(),plus=node(),mark=node();
 const attach=(parent,children)=>{parent.children=children;children.forEach(child=>child.parentElement=parent);};
 Object.defineProperty(title,'textContent',{get:()=>state.title});Object.defineProperty(sales,'textContent',{get:()=>state.sales});
 image.getAttribute=name=>['data-src','src'].includes(name)?state.image:null;
 plus.querySelector=selector=>selector==='.addV2_TP_S6XAg'?mark:null;
 card.querySelector=selector=>selector==='.goodsName_dT8mZSNd'?title:selector==='.goodsImage_fU27dxbk img'?image:null;
 card.querySelectorAll=selector=>selector==='.quantityBtnV2_FM92Tcgf'?[plus]:selector==='.salesTip_qUlLfJr3'?[sales]:[];
 attach(plus,[mark]);attach(card,[title,image,sales,plus]);attach(right,[card]);attach(body,[left,right]);
 for(const list of [left,right])list.querySelectorAll=selector=>selector==='.goodsItem_R1ok0MpS'?list.children:[];
 const modal=node('',300),boundary=node('其他店铺的精选推荐');boundary.getBoundingClientRect=()=>({top:state.boundary,width:100,height:40});
 const document={body,querySelectorAll:selector=>selector==='.waterfall-list-container_NLmKnzfN'?[left,right]:selector==='.sku-plus1 [role="dialog"][aria-modal="true"]'?state.opened?[modal]:[]:selector==='div,p,span'?Number.isFinite(state.boundary)?[boundary]:[]:[],
  querySelector:()=>null,elementFromPoint:()=>plus,addEventListener(type,listener){listeners.add(listener);},removeEventListener(type,listener){listeners.delete(listener);}};
 const context=vm.createContext({URL,document,location:new URL(searchUrl),innerWidth:1280,innerHeight:850,getComputedStyle:e=>({display:e.hidden?'none':'block',visibility:'visible',opacity:'1'})});
 const run=(fn,args)=>JSON.parse(JSON.stringify(vm.runInContext(`(${fn.toString()})(${JSON.stringify(args)})`,context)));
 const arm=(patch={})=>run(armListSkuModal,{token:'SYNTHETIC_TOKEN',title:query,image:original,shopName:'ORIGINAL_SHOP',sourceUrl:searchUrl,originalSalesValue:594,searchEvidence:evidence(),...patch});
 const binding=()=>vm.runInContext('globalThis[Symbol.for("pdd-monitor/sku-modal-binding-v1")]',context);
 const open=()=>{for(const listener of listeners)listener({isTrusted:true,target:plus});state.opened=true;return run(checkListSkuModal,{token:'SYNTHETIC_TOKEN',bind:true});};
 return {state,left,right,card,plus,modal,context,run,arm,binding,open,listeners,node,attach};
}

test('native search binds the unique original in either column with actual image and increasing sales',()=>{
 const f=fixture();assert.equal(f.arm().ok,true);assert.deepEqual(f.open(),{ok:true});
 const binding=f.binding();assert.equal(binding.imageUrl,actual);assert.equal(binding.searchEvidence.original_image_url,original);
 assert.equal(Object.isFrozen(binding.searchEvidence),true);assert.equal(Object.getOwnPropertyDescriptor(binding,'searchEvidence').writable,false);assert.equal(Object.getOwnPropertyDescriptor(binding,'verifySearch').writable,false);assert.equal(binding.verifySearch(),true);
 assert.equal(f.context.document.body.textContent.includes('ORIGINAL_SHOP'),false);
 assert.deepEqual(f.run(releaseListSkuModal,{token:'SYNTHETIC_TOKEN'}),{released:true,closed:false});assert.equal(f.listeners.size,0);
});

test('search evidence refuses missing native flags, source/query conflicts, ambiguous identity and wrong raw sales',()=>{
 const patches=[{method:'other'},{source_store_url:origin+'/mall_page.html?ps=ONLY'},{source_store_url:origin+'/mall_page.html?mall_id=999'},
  {source_store_url:store+'&mall_sn=OTHER'},{query:'other'},{query:' '+query},{query:'x'.repeat(121)},{page_id:0},{page_id:true},{matched_card_count:2},
  {shop_name_verified:false},{native_search_opened:false},{native_search_submitted:false},{same_tab_navigation_verified:false},
  {matching_title:'OTHER'},{original_image_url:actual},{live_sales_raw:'已拼598+件'},{live_sales_label:'已抢'},{live_sales_value:0},{live_sales_value:593},{live_sales_unit:'人'},
  {search_page_url:searchUrl+'&mall_id=123'},{search_page_url:searchUrl+'&search_key='+encodeURIComponent(query)},{search_page_url:searchUrl+'&mall_sn=OTHER'}];
 for(const patch of patches){const f=fixture();assert.equal(f.arm({searchEvidence:{...evidence(),...patch}}).ok,false,JSON.stringify(patch));assert.equal(f.listeners.size,0);}
 for(const originalSalesValue of [undefined,0,-1,599,1.5])assert.equal(fixture().arm({originalSalesValue}).ok,false);
});

test('only the observed image host/encoding/format prefix variation can bind the original',()=>{
 const forbidden=['https://example.org/SYNTHETIC.jpg?imageMogr2/quality/80','https://img.pddpic.com/OTHER.jpg?imageMogr2/quality/80',
  'https://img.pddpic.com/SYNTHETIC.jpg?imageMogr2/quality/79','https://img.pddpic.com/SYNTHETIC.jpg?imageMogr2/quality/80&extra=1',
  'https://img.pddpic.com/SYNTHETIC.jpg?imageMogr2/quality/80#fragment','https://img.pddpic.com/SYNTHETIC.jpg?%FF'];
 for(const matched_image_url of forbidden){const f=fixture();f.state.image=matched_image_url;assert.equal(f.arm({searchEvidence:{...evidence(),matched_image_url}}).ok,false);}
 const f=fixture();f.state.image=original;assert.equal(f.arm({searchEvidence:{...evidence(),matched_image_url:original}}).ok,true);
});

test('another original in either search column is ambiguous including equivalent image encodings',()=>{
 for(const alias of [actual,original]){const f=fixture(),other=f.node(),otherTitle=f.node(query),otherImage=f.node();otherImage.getAttribute=()=>alias;other.querySelector=selector=>selector==='.goodsName_dT8mZSNd'?otherTitle:selector==='.goodsImage_fU27dxbk img'?otherImage:null;f.attach(f.left,[other]);assert.equal(f.arm().reason,'ambiguous');}
 for(const kind of ['boundary','hidden','old_modal']){const f=fixture();if(kind==='boundary')f.state.boundary=40;if(kind==='hidden')f.right.hidden=true;if(kind==='old_modal')f.state.opened=true;assert.equal(f.arm().ok,false,kind);}
});

test('each modal read rechecks exact search URL, original card, owner, actual image and raw live sales',()=>{
 const changes=[f=>f.context.location=new URL(searchUrl+'&changed=1'),f=>f.state.title='OTHER',f=>f.state.image=original,
  f=>f.state.sales='已拼599件',f=>f.card.isConnected=false,f=>f.right.hidden=true,f=>f.state.boundary=40,
  f=>{f.right.children=[];f.attach(f.left,[f.card]);},f=>{f.attach(f.left,[f.card]);}];
 for(const mutate of changes){const f=fixture();assert.equal(f.arm().ok,true);assert.equal(f.open().ok,true);mutate(f);assert.equal(f.run(checkListSkuModal,{token:'SYNTHETIC_TOKEN'}).ok,false);}
});

test('search evidence is frozen from caller changes and never waives trusted click or modal continuity',()=>{
 const f=fixture(),input=evidence();assert.equal(f.arm({searchEvidence:input}).ok,true);input.live_sales_value=1;assert.equal(f.binding().searchEvidence.live_sales_value,598);
 assert.equal(f.run(checkListSkuModal,{token:'SYNTHETIC_TOKEN',bind:true}).ok,false);assert.equal(f.open().ok,true);
 f.binding().modal=f.node();assert.equal(f.run(checkListSkuModal,{token:'SYNTHETIC_TOKEN'}).ok,false);
 const untrusted=fixture();assert.equal(untrusted.arm().ok,true);for(const listener of untrusted.listeners)listener({isTrusted:false,target:untrusted.plus});untrusted.state.opened=true;assert.equal(untrusted.run(checkListSkuModal,{token:'SYNTHETIC_TOKEN',bind:true}).ok,false);
});

test('search route with absent or malformed search proof cannot use ordinary mall-page binding',()=>{
 for(const searchEvidence of [undefined,null,{},[]]){const f=fixture();assert.equal(f.arm({searchEvidence}).ok,false);assert.equal(f.listeners.size,0);}
});
