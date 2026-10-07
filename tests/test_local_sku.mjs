// Synthetic spec sheets only; never opens a browser or writes business records.
import test from 'node:test';
import assert from 'node:assert/strict';
import {combinations,readSpecs,captureSku} from '../scripts/local_sku_capture.mjs';
import {visibleTextPoint,readPageGuard} from '../scripts/local_browser.mjs';

test('shop sort locator includes rendered list-item controls',()=>{
 globalThis.innerHeight=850;globalThis.getComputedStyle=()=>({display:'block',visibility:'visible'});
 globalThis.document={querySelectorAll(selector){assert.ok(selector.split(',').includes('li'));return [{textContent:'上新',contains:()=>false,getBoundingClientRect:()=>({x:20,y:120,width:80,height:40,top:120,bottom:160})}];}};
 assert.deepEqual(visibleTextPoint('上新'),{x:60,y:140});
});

test('cartesian combinations retain every dimension and reject unbounded work',()=>{
 const groups=[{name:'款式',options:[{label:'A'},{label:'B'}]},{name:'尺寸',options:[{label:'小'},{label:'大'}]}];
 assert.equal(combinations(groups).length,4);
 assert.deepEqual(combinations(groups)[3],[{name:'款式',value:'B'},{name:'尺寸',value:'大'}]);
 assert.throws(()=>combinations(groups,3));assert.throws(()=>combinations([{name:'x',options:[]}]))
});

function element(text='',className=''){
 const node={textContent:text,innerText:text,className,children:[],parentElement:null,previousElementSibling:null,
  getBoundingClientRect:()=>({x:20,y:200,width:100,height:50,top:200,bottom:250}),
  getAttribute:()=>null,hasAttribute:()=>false,
  querySelector:()=>null,querySelectorAll:()=>[],contains(other){return this===other||this.children.some(child=>child.contains(other));}};
 return node;
}
function fixture({duplicates=false,multiplePrices=false,hidden=false}={}){
 const body=element('SYNTHETIC_ONLY'),root=element('款式A B ¥4.50'),group=element('', 'sku-specs');root.parentElement=body;body.children=[root];group.parentElement=root;root.children=[group];group.previousElementSibling=element('款式');
 const options=[element('A','sku-item selected_test'),element(duplicates?'A':'B','sku-item')];group.children=options;for(const o of options)o.parentElement=group;
 const price=element('¥4.50','sku-price'),image=element('');image.currentSrc='https://img.pddpic.com/synthetic_only.png';
 const prices=multiplePrices?[price,element('¥6.50')]:[price];
 root.querySelectorAll=selector=>selector.includes('price')?prices:selector==='img'?[image]:options;
 globalThis.document={body,querySelectorAll:selector=>selector.startsWith('.sku-plus1')?[]:options};globalThis.innerHeight=850;globalThis.getComputedStyle=e=>({display:hidden&&e===options[1]?'none':'block',visibility:'visible'});
 return {root,options};
}
test('rendered selected options bind one modal price and its image',()=>{
 fixture();const read=readSpecs();assert.equal(read.supported,true);assert.equal(read.price_raw,'¥4.50');assert.equal(read.groups[0].options[0].selected,true);assert.equal(read.option_catalog_verified,true);
 assert.equal(read.image_url,null,'an unbound large header image cannot fill a selected SKU');
});
test('ambiguous options stop; multiple prices remain unknown; hidden catalog cannot complete',()=>{
 fixture({duplicates:true});assert.equal(readSpecs().supported,false);
 fixture({multiplePrices:true});assert.equal(readSpecs().price_raw,null);
 fixture({hidden:true});assert.equal(readSpecs().option_catalog_verified,false);
});

// Synthetic DOM shape modelled after the observed sku-plus1 dialog. No report,
// actual product identifier, downloaded HTML, browser or network is needed.
function knownModalFixture(){
 const attach=(parent,children)=>{parent.children=children;for(const child of children)child.parentElement=parent;};
 const body=element('SYNTHETIC_ONLY'),root=element('券后¥4.5 券前¥9.5 已选：款式1 10cm立牌/活动价 款式 型号');
 const current=element('券后¥4.5','ujEqGzEB'),original=element('券前¥9.5','mhHA_CEU'),summary=element('已选：款式1 10cm立牌/活动价','Mbx2m60G');
 original.tagName='DIV';
 const image=Object.assign(element(''),{complete:true,naturalWidth:375,naturalHeight:500,currentSrc:'https://img.pddpic.com/SYNTHETIC_SKU.png'});
 image.src=image.currentSrc;
 const buttons=[];
 const sections=[['款式',['款式1','款式2']],['型号',['10cm立牌/活动价']]].map(([name,labels],g)=>{
  const section=element('','bIhLWVqm'),heading=element(name,'sku-specs-key'),choices=labels.map((label,index)=>{
   const button=element(label,'F7sZG3xe '+(index===0?'hr353bdX':'')+' rGqCSAQI');
   button.classList={contains:value=>button.className.split(/\s+/).includes(value)};
   button.getAttribute=name=>name==='aria-label'?label:null;
   button.getBoundingClientRect=()=>({x:30+index*180,y:300+g*100,width:150,height:50,top:300+g*100,bottom:350+g*100});
   buttons.push(button);return button;
  });
  section.querySelector=selector=>selector==='.sku-specs-key'?heading:null;
  section.querySelectorAll=selector=>selector==='.s1O5M5fO > [role="button"]'?choices:[];
  attach(section,[heading,...choices]);return section;
 });
 const values={sections,current:[current],original:[original]};
 root.querySelectorAll=selector=>selector==='.bIhLWVqm'?values.sections:selector==='.ujEqGzEB'?values.current:selector==='.mhHA_CEU,del,s'?values.original:[];
 root.querySelector=selector=>selector==='.Mbx2m60G'?summary:selector==='.O7pEFvHR > img'?image:null;
 attach(root,[...sections,current,original,summary,image]);attach(body,[root]);
 globalThis.innerHeight=850;globalThis.innerWidth=1280;globalThis.getComputedStyle=e=>({display:e.hidden?'none':'block',visibility:'visible',textDecorationLine:'none'});
 globalThis.document={body,querySelectorAll:selector=>selector==='.sku-plus1 [role="dialog"][aria-modal="true"]'?[root]:[],elementFromPoint:(x,y)=>buttons.find(e=>{const r=e.getBoundingClientRect();return x>=r.x&&x<r.x+r.width&&y>=r.y&&y<r.bottom;})||body};
 return {values,root,summary,image,sections,buttons};
}

test('known SKU dialog binds each selected specification, coupon price and exact image',()=>{
 const f=knownModalFixture(),read=readSpecs();
 assert.equal(read.supported,true);assert.equal(read.adapter,'pdd_sku_plus1_v1');assert.equal(read.option_catalog_verified,true);
 assert.deepEqual(read.groups.map(g=>({name:g.name,labels:g.options.map(o=>o.label)})),[{name:'款式',labels:['款式1','款式2']},{name:'型号',labels:['10cm立牌/活动价']}]);
 assert.equal(read.current_price_raw,'券后¥4.5');assert.equal(read.original_price_raw,'券前¥9.5');assert.equal(read.original_price_label,'券前价');
 assert.equal(read.original_price_evidence,'explicit_before_coupon_price');assert.equal(read.selection_summary_verified,true);assert.equal(read.image_url,f.image.currentSrc);
 assert.ok(read.groups.every(g=>g.options.every(o=>o.point.inViewport)));
});

test('compact repeated SKU reads keep gates and prices without retransmitting full evidence text',()=>{
 const f=knownModalFixture(),compact=readSpecs({includeRawText:false});
 assert.equal(compact.raw_text,null);assert.equal(compact.current_price_raw,'券后¥4.5');assert.equal(compact.image_url,f.image.currentSrc);
 globalThis.location={href:'https://mobile.yangkeduo.com/goods.html?goods_id=123'};
 globalThis.document.body.innerText='SYNTHETIC 需要安全验证';
 const page=readPageGuard({includeText:false});assert.equal(page.text,'');assert.equal(page.challenge,true);
});

test('known SKU stale summary cannot attribute image and missing group metadata stops reading',()=>{
 let f=knownModalFixture();f.summary.textContent='已选：款式2 10cm立牌/活动价';let read=readSpecs();
 assert.equal(read.selection_summary_verified,false);assert.equal(read.image_url,null);
 f=knownModalFixture();f.sections[0].querySelector=()=>null;assert.equal(readSpecs().supported,false);
 f=knownModalFixture();f.values.sections=[];assert.equal(readSpecs().supported,false);
});

test('known SKU multiple prices stay unknown, and hidden or incomplete images are not replaced',()=>{
 let f=knownModalFixture();f.values.current.push(element('券后¥7.5'));f.values.original.push(element('券前¥12.5'));
 let read=readSpecs();assert.equal(read.current_price_raw,null);assert.equal(read.price_raw,null);assert.equal(read.original_price_raw,null);assert.equal(read.original_price_evidence,null);
 f=knownModalFixture();f.image.complete=false;assert.equal(readSpecs().image_url,null);
 f.image.complete=true;f.image.hidden=true;assert.equal(readSpecs().image_url,null);
 f.image.hidden=false;f.buttons[1].hidden=true;assert.equal(readSpecs().option_catalog_verified,false);
 f=knownModalFixture();f.image.src='https://img.pddpic.com/SYNTHETIC_NEXT.png';read=readSpecs();assert.equal(read.image_url,null);assert.equal(read.image_pending,true);
 f=knownModalFixture();f.image.naturalHeight=0;assert.equal(readSpecs().image_url,null);
});

function mockBrowser({mutation=false,refuseSelection=false,disabled=false}={}){
 const selected=[null,null],clicks=[];let reads=0;
 const read=()=>({supported:true,option_catalog_verified:true,groups:['款式','尺寸'].map((name,g)=>({name,options:['A','B'].map((label,o)=>({label:mutation&&reads>3&&o===1?'CHANGED':label,selected:selected[g]===o,disabled:disabled&&g===0&&o===1,point:{x:g*10+o,y:1,inViewport:true}}))})),price_raw:selected.every(v=>v!==null)?`¥${3+selected[0]*2+selected[1]}.50`:null,image_url:'https://img.pddpic.com/synthetic_only.png',raw_text:'SYNTHETIC_ONLY selected specs'});
 const browser={async goto(){},async evaluate(fn){if(fn.name==='readPageGuard')return {url:'https://mobile.yangkeduo.com/goods.html?goods_id=123',text:'SYNTHETIC_ONLY 商品',login:false,challenge:false};if(fn.name==='readSpecs'){reads++;return read();}throw new Error('Unexpected DOM action');},async click(p){clicks.push(p);if(!refuseSelection)selected[Math.floor(p.x/10)]=p.x%10;},async scroll(){throw new Error('Unexpected scroll');}};
 return {browser,clicks,selected};
}
const request={observation:{goods_id:'123',goods_url:'https://mobile.yangkeduo.com/goods.html?goods_id=123',title:'SYNTHETIC_ONLY 商品'}};
test('each combination receives its own selected price; no order action',async()=>{
 const {browser,clicks}=mockBrowser();const progress=[];
 const result=await captureSku(browser,request,async()=>false,async value=>progress.push(value));
 assert.equal(result.status,'complete');assert.equal(result.variants.length,4);assert.deepEqual(result.variants.map(v=>v.price_raw),['¥3.50','¥4.50','¥5.50','¥6.50']);assert.ok(result.variants.every(v=>v.selection_verified));assert.equal(progress.at(-1).variants,4);assert.ok(clicks.every(p=>p.y===1));
});
test('disabled combinations keep unknown price; cancellation preserves partial work',async()=>{
 const {browser}=mockBrowser({disabled:true});const result=await captureSku(browser,request,async()=>false,async()=>{});assert.equal(result.variants[2].available,false);assert.equal(result.variants[2].price_raw,null);
 let cancelled=false;const other=mockBrowser();const partial=await captureSku(other.browser,request,async()=>cancelled,async p=>{if(p.variants===2)cancelled=true;});
 assert.equal(partial.status,'partial');assert.equal(partial.variants.length,2);assert.equal(partial.stop_status,'cancelled');assert.equal(partial.stop_reason,'operator_cancelled');assert.equal(partial.all_combinations_visited,false);
});
test('selection refusal or changed option catalog stops instead of attributing price',async()=>{
 const one=mockBrowser({refuseSelection:true});await assert.rejects(captureSku(one.browser,request,async()=>false,async()=>{}),/核对所有已选规格/);
 const two=mockBrowser({mutation:true});await assert.rejects(captureSku(two.browser,request,async()=>false,async()=>{}),/目录.*变化/);
});

test('cancellation before the first SKU never fabricates an empty completed capture',async()=>{
 const {browser,clicks}=mockBrowser();
 await assert.rejects(captureSku(browser,request,async()=>true,async()=>{}),e=>e.status==='cancelled'&&e.reason==='operator_cancelled');
 assert.equal(clicks.length,0);
});

test('a changed public goods ID stops and preserves only previously verified SKU data',async()=>{
 const {browser}=mockBrowser(),original=browser.evaluate,imageCalls=[];let goodsId='123';
 browser.evaluate=async(fn,arg)=>{const value=await original(fn,arg);return fn.name==='readPageGuard'?{...value,url:'https://mobile.yangkeduo.com/goods.html?goods_id='+goodsId}:value;};
 browser.collectSkuImages=async args=>{imageCalls.push(args);return {newlySaved:1};};
 const result=await captureSku(browser,request,async()=>false,async p=>{if(p.variants===1)goodsId='456';});
 assert.equal(result.status,'partial');assert.equal(result.stop_status,'manual_review');assert.equal(result.stop_reason,'sku_identity_changed');assert.equal(result.goods_id,'123');
 assert.equal(result.all_combinations_visited,false);assert.equal(result.variants.length,1);assert.equal(result.variants[0].price_raw,'¥3.50');
 assert.deepEqual(imageCalls.map(call=>call.goodsId),['123']);
});

test('a goods ID switch before the first save rejects price and image attribution entirely',async()=>{
 const {browser}=mockBrowser(),evaluate=browser.evaluate,click=browser.click;let goodsId='123';
 browser.evaluate=async(fn,arg)=>{const value=await evaluate(fn,arg);return fn.name==='readPageGuard'?{...value,url:'https://mobile.yangkeduo.com/goods.html?goods_id='+goodsId}:value;};
 browser.click=async point=>{await click(point);goodsId='456';};
 browser.collectSkuImages=async()=>assert.fail('wrong-product image must not be collected');
 await assert.rejects(captureSku(browser,request,async()=>false,async()=>{}),e=>e.reason==='sku_identity_changed');
});

test('missing SKU image or price stays unknown while later specification combinations continue',async()=>{
 const {browser,selected}=mockBrowser(),evaluate=browser.evaluate,imageCalls=[],progress=[];
 const imageB='https://img.pddpic.com/SYNTHETIC_B.png',imageC='https://img.pddpic.com/SYNTHETIC_C.png';
 const fields=[
  {price_raw:null,current_price_raw:null,image_url:null,original_price_raw:null},
  {price_raw:'¥4.50',current_price_raw:'¥4.50',image_url:imageB,original_price_raw:null},
  {price_raw:null,current_price_raw:null,image_url:imageC,original_price_raw:null},
  {price_raw:'¥6.50',current_price_raw:'¥6.50',image_url:null,original_price_raw:'券前¥9.50',original_price_evidence:'explicit_before_coupon_price'},
 ];
 browser.evaluate=async(fn,arg)=>{const value=await evaluate(fn,arg);if(fn.name==='readSpecs'&&selected.every(v=>v!==null))return {...value,...fields[selected[0]*2+selected[1]]};return value;};
 browser.collectSkuImages=async args=>{imageCalls.push(args);return {newlySaved:1};};
 const result=await captureSku(browser,{...request,observation:{...request.observation,price_raw:'¥0.99'}},async()=>false,async value=>progress.push(value));
 assert.equal(result.status,'complete');assert.equal(result.all_combinations_visited,true);assert.equal(result.variants.length,4);assert.equal(progress.at(-1).variants,4);
 assert.deepEqual(result.variants.map(v=>v.current_price_raw),[null,'¥4.50',null,'¥6.50']);
 assert.deepEqual(result.variants.map(v=>v.image_url),[null,imageB,imageC,null]);
 assert.deepEqual(result.variants.map(v=>v.original_price_raw),[null,null,null,'券前¥9.50']);
 assert.ok(result.variants.every(v=>v.selection_verified));assert.deepEqual(imageCalls.flatMap(c=>c.imageUrls),[imageB,imageC]);
});

test('selected offscreen options need no click or scroll but final selection remains verified',async()=>{
 const {browser,selected,clicks}=mockBrowser(),evaluate=browser.evaluate;selected[0]=0;selected[1]=0;
 let completed=0,reveals=0;
 browser.evaluate=async(fn,arg)=>{
  if(fn.name==='revealSkuOption'){reveals++;assert.fail('already selected option must not be scrolled');}
  const value=await evaluate(fn,arg);
  if(fn.name==='readSpecs')return {...value,selection_summary_verified:true,groups:value.groups.map(group=>({...group,options:group.options.map(option=>({...option,point:{...option.point,inViewport:false}}))}))};
  return value;
 };
 const result=await captureSku(browser,request,async()=>completed===1,async p=>{completed=p.variants;});
 assert.equal(result.variants.length,1);assert.equal(result.variants[0].selection_verified,true);assert.equal(result.variants[0].price_raw,'¥3.50');
 assert.equal(clicks.length,0);assert.equal(reveals,0);assert.equal(result.stop_reason,'operator_cancelled');
});

test('offscreen unselected option still requires a verified hit target before any click',async()=>{
 const {browser,selected,clicks}=mockBrowser(),evaluate=browser.evaluate;selected[0]=1;selected[1]=0;let reveals=0;
 browser.evaluate=async(fn,arg)=>{
  if(fn.name==='revealSkuOption'){reveals++;return true;}
  if(fn.name==='diagnoseSkuOption')return {hitInside:false};
  const value=await evaluate(fn,arg);
  if(fn.name==='readSpecs')return {...value,groups:value.groups.map(group=>({...group,options:group.options.map(option=>({...option,point:{...option.point,inViewport:false}}))}))};
  return value;
 };
 await assert.rejects(captureSku(browser,request,async()=>false,async()=>{}),e=>e.reason==='sku_option_obscured');
 assert.equal(reveals,1);assert.equal(clicks.length,0);
});

test('skipping a selected offscreen option does not bypass the final selection summary',async()=>{
 const {browser,selected,clicks}=mockBrowser(),evaluate=browser.evaluate;selected[0]=0;selected[1]=0;
 browser.evaluate=async(fn,arg)=>{
  assert.notEqual(fn.name,'revealSkuOption');const value=await evaluate(fn,arg);
  if(fn.name==='readSpecs')return {...value,selection_summary_verified:false,groups:value.groups.map(group=>({...group,options:group.options.map(option=>({...option,point:{...option.point,inViewport:false}}))}))};
  return value;
 };
 await assert.rejects(captureSku(browser,request,async()=>false,async()=>{}),e=>e.reason==='sku_selection_unverified');assert.equal(clicks.length,0);
});

test('saved verified link navigates directly while original missing goods ID stays missing',async()=>{
 const {browser}=mockBrowser(),visited=[];
 browser.goto=async url=>visited.push(url);
 const original={title:request.observation.title,goods_id:null,goods_url:null,
  verified_product_ref:{goods_id:'123',goods_url:request.observation.goods_url,capture_id:'collect_'+'a'.repeat(32)}};
 const result=await captureSku(browser,{observation:original},async()=>false,async()=>{});
 assert.deepEqual(visited,[request.observation.goods_url]);assert.equal(original.goods_id,null);assert.equal(original.goods_url,null);
 assert.equal(result.goods_id,'123');assert.equal(result.locator.method,'saved_verified_link');assert.equal(result.identity_basis,'current_unique_card_candidate');
});
test('invalid saved URL or conflicting ID is rejected before any browser action',async()=>{
 for(const ref of [
  {goods_id:'999',goods_url:request.observation.goods_url},
  {goods_id:'123',goods_url:'https://other.invalid/goods.html?goods_id=123'},
  {goods_id:'123',goods_url:request.observation.goods_url+'&goods_id=123'},
  {goods_id:'999',goods_url:'https://mobile.yangkeduo.com/goods.html?goods_id=999'},
 ]){
  const browser={goto:async()=>assert.fail('invalid reference must not navigate'),evaluate:async()=>assert.fail('invalid reference must not inspect')};
  await assert.rejects(captureSku(browser,{observation:{...request.observation,verified_product_ref:ref}},async()=>false,async()=>{}),e=>e.reason==='sku_identity_changed');
 }
});
test('saved reference expected ID is enforced against the actual product detail',async()=>{
 const {browser}=mockBrowser();let specReads=0;const evaluate=browser.evaluate;
 browser.evaluate=async(fn,arg)=>{if(fn.name==='readSpecs')specReads++;return evaluate(fn,arg);};
 const observation={title:request.observation.title,goods_id:null,goods_url:null,
  verified_product_ref:{goods_id:'999',goods_url:'https://mobile.yangkeduo.com/goods.html?goods_id=999',capture_id:'collect_'+'b'.repeat(32)}};
 await assert.rejects(captureSku(browser,{observation},async()=>false,async()=>{}),e=>e.reason==='sku_identity_changed');assert.equal(specReads,0);
});

test('a card without a verified link uses native shop search, verified plus, modal close and cancel return without paging',async()=>{
 const {browser}=mockBrowser(),evaluate=browser.evaluate,click=browser.click,actions=[],progress=[];
 const shop={shop_name:'SYNTHETIC店',source_url:'https://mobile.yangkeduo.com/mall_page.html?mall_id=123'};
 const title=request.observation.title,image='https://img.pddpic.com/SYNTHETIC_MAIN.png',searchUrl='https://mobile.yangkeduo.com/mall_search_result.html?mall_id=123&search_key='+encodeURIComponent(title);
 let phase='shop';browser.tab={id:29};browser.goto=async()=>assert.fail('Existing shop must not reload');
 browser.fillSearch=async(options,text)=>{assert.equal(options.sourceUrl,shop.source_url);assert.equal(text,title);actions.push('fill');};
 browser.pressSearchEnter=async()=>{phase='search';actions.push('enter');};
 browser.evaluate=async(fn,arg)=>{
  if(fn.name==='readPageGuard')return {url:phase==='shop'?shop.source_url:searchUrl,text:phase==='shop'?shop.shop_name:'SYNTHETIC_RESULTS',login:false,challenge:false};
  if(fn.name==='readSkuSearchEntry')return {ok:true,url:shop.source_url,point:{x:10,y:10}};
  if(fn.name==='readSkuSearchResults')return {routeReady:true,url:searchUrl,matched_card_count:1,matching_title:title,original_image_url:image,matched_image_url:image,live_sales_raw:'已拼12件',live_sales_value:12,live_sales_label:'已拼',live_sales_unit:'件'};
  if(fn.name==='armListSkuModal'){assert.equal(arg.sourceUrl,searchUrl);assert.equal(arg.originalSalesValue,11);assert.equal(arg.searchEvidence.source_store_url,shop.source_url);return {ok:true,point:{x:99,y:9}};}
  if(fn.name==='checkListSkuModal')return arg.closing?{ok:true,point:{x:98,y:9}}:{ok:true};
  if(fn.name==='releaseListSkuModal')return {closed:true,released:true};
  if(fn.name==='readSkuSearchCancel')return {ok:true,point:{x:97,y:9}};
  return evaluate(fn,arg);
 };
 browser.scroll=async()=>assert.fail('Whole-shop paging forbidden');
 browser.click=async point=>{
  if(point.x===10&&point.y===10){actions.push('search_entry');return;}
  if(point.x===99&&point.y===9){actions.push('plus');return;}
  if(point.x===98&&point.y===9){actions.push('close_modal');return;}
  if(point.x===97&&point.y===9){actions.push('cancel_search');phase='shop';return;}
  return click(point);
 };
 const result=await captureSku(browser,{shop,observation:{title,image_url:image,sales_value:11,view_order:45,observation_id:8,current_unique_card_candidate_count:1}},async()=>false,async value=>progress.push(value));
 assert.equal(result.status,'complete');assert.equal(result.locator.method,'store_search');assert.equal(result.goods_id,null);assert.equal(result.identity_basis,'current_unique_card_modal');
 assert.deepEqual(actions,['search_entry','fill','enter','plus','close_modal','cancel_search']);
 assert.equal(result.search_evidence.return_verified,true);assert.equal(result.search_evidence.modal_closed,true);assert.equal(result.search_evidence.return_store_url,shop.source_url);
 assert.equal(result.variants.length,4);assert.ok(result.variants.every(v=>v.selection_verified));assert.equal(progress.at(-1).locator,'store_search');
});

test('verified product with APP-only price and no web SKU entry is locally unavailable, not wrong-shop data',async()=>{
 const entries=[],browser={
  async goto(){},
  async evaluate(fn){
   if(fn.name==='readPageGuard')return {url:request.observation.goods_url,text:request.observation.title+' 前往APP查看价格',login:false,challenge:false};
   if(fn.name==='skuEntryPoint'){entries.push(fn.name);return null;}
   assert.fail('APP-only page must not read or invent SKU data');
  },
  async click(){assert.fail('APP-only page must not click another control');},
 };
 const observation={title:request.observation.title,goods_id:null,goods_url:null,
  verified_product_ref:{goods_id:'123',goods_url:request.observation.goods_url,capture_id:'collect_'+'c'.repeat(32)}};
 await assert.rejects(captureSku(browser,{shop:{shop_name:'SYNTHETIC店'},observation},async()=>false,async()=>{}),
  e=>e.status==='manual_review'&&e.reason==='sku_web_unavailable');
 assert.equal(entries.length,1);
});

test('APP price hint cannot hide a cached ID mismatch or invalid actual product route',async()=>{
 for(const actualUrl of ['https://mobile.yangkeduo.com/goods.html?goods_id=999',
  'https://mobile.yangkeduo.com/goods.html?goods_id=123&goods_id=123',
  'https://mobile.yangkeduo.com/mall_page.html?goods_id=123']){
  const browser={async goto(){},async evaluate(fn){
   assert.equal(fn.name,'readPageGuard','identity must be checked before classifying APP availability');
   return {url:actualUrl,text:request.observation.title+' 前往APP查看价格',login:false,challenge:false};
  }};
  const observation={title:request.observation.title,goods_id:null,
   verified_product_ref:{goods_id:'123',goods_url:request.observation.goods_url,capture_id:'collect_'+'d'.repeat(32)}};
  await assert.rejects(captureSku(browser,{observation},async()=>false,async()=>{}),e=>e.reason==='sku_identity_changed');
 }
});
