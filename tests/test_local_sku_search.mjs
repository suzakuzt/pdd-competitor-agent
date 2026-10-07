import test from 'node:test';
import assert from 'node:assert/strict';
import {readSkuSearchResults,skuSearchQuery,locateSkuBySearch,readSkuSearchEntry,readSkuSearchCancel,returnFromSkuSearch} from '../scripts/local_sku_search.mjs';
import {readPageGuard} from '../scripts/local_browser.mjs';
const store='https://mobile.yangkeduo.com/mall_page.html?mall_sn=SYNTHETIC_A',query='SYNTHETIC title',title=query+' long',original='https://img-2.pddpic.com/synthetic.png?imageMogr2/format/webp/quality/90/thumbnail/500x9999%3E',actual='https://img.pddpic.com/synthetic.png?imageMogr2/quality/90/thumbnail/500x9999%3E';
const search=()=>`https://mobile.yangkeduo.com/mall_search_result.html?mall_id=123&search_key=${encodeURIComponent(query)}`;
function node(text=''){return {textContent:text,children:[],getBoundingClientRect:()=>({x:20,y:80,width:100,height:40,top:80,bottom:120}),contains(other){return other===this;}};}
function fixture({image=actual,sales='已拼598件',duplicate=false,route=search(),extraImage='https://img.pddpic.com/other.png'}={}){
 const make=(img,raw)=>{const card=node(),i={getAttribute:k=>k==='src'?img:null},label=node(raw);card.querySelector=s=>s==='.goodsName_dT8mZSNd'?node(title):s==='.goodsImage_fU27dxbk img'?i:null;card.querySelectorAll=s=>s==='.salesTip_qUlLfJr3'?[label]:[];return card;};
 const cards=[make(extraImage,'已拼7件'),make(image,sales)],lists=[{querySelectorAll:()=>cards.slice(0,1),contains:e=>e===cards[0]},{querySelectorAll:()=>cards.slice(1),contains:e=>cards.slice(1).includes(e)}];
 if(duplicate)cards.push(make(image,sales));
 globalThis.location=new URL(route);globalThis.scrollY=0;globalThis.getComputedStyle=()=>({display:'block',visibility:'visible'});globalThis.document={body:{innerText:'SYNTHETIC_RESULTS_ONLY'},querySelectorAll:s=>s==='.waterfall-list-container_NLmKnzfN'?lists:[]};
 return {cards,lists};
}
const args={query,title,image:original,oldSalesValue:594};
test('search matches original image file across observed display encoding, never another same-title image',()=>{
 fixture();const r=readSkuSearchResults(args);assert.equal(r.matched_card_count,1);assert.equal(r.live_sales_value,598);assert.equal(r.matched_image_url,actual);assert.equal(r.original_image_url,original);assert.equal(r.card_count,2);
});
test('search rejects wrong shop-result route, query and duplicate identifiers',()=>{
 for(const route of [store,search().replace('123','abc'),search()+'&mall_id=456',search()+'&search_key=other',search().replace(encodeURIComponent(query),'other'),'https://example.com/mall_search_result.html?mall_id=123&search_key=x']){fixture({route});assert.equal(readSkuSearchResults(args).routeReady,false);}
});
test('different design or image transform is not the original card; ambiguous exact cards remain unbound',()=>{
 for(const image of [actual.replace('synthetic.png','other-design.png'),actual.replace('/90/','/80/'),actual.replace('500x','250x'),actual.replace('img.pddpic.com','evil.example')]){fixture({image});assert.equal(readSkuSearchResults(args).matched_card_count,0);}
 fixture({duplicate:true});assert.equal(readSkuSearchResults(args).matched_card_count,2);
});
test('exact live sales corroborate image; missing, fuzzy, foreign unit and regression refuse binding',()=>{
 for(const sales of ['已拼593件','已拼0件','已拼598+件','总售598件','已拼598单','','已拼999999999999999999件']){fixture({sales});assert.equal(readSkuSearchResults(args).reason,'sales');}
 fixture({sales:'已抢599件'});assert.equal(readSkuSearchResults(args).live_sales_value,599);
});
test('query is bounded deterministic title prefix; full title used independently for identity',()=>{
 assert.equal(skuSearchQuery('  abcdefghijklmnopqrstuvwxyz  '),'abcdefghijklmnopqr');assert.throws(()=>skuSearchQuery(''));
});
function fakeBrowser({duplicate=false,returnMismatch=false}={}){
 const current={url:store,text:'SYNTHETIC_STORE'},operations=[];
 const browser={tab:{id:17},evaluate:async(fn,arg)=>{
  if(fn===readPageGuard)return {...current,login:false,challenge:false};
  if(fn===readSkuSearchEntry)return {ok:true,url:store,point:{x:50,y:100}};
  if(fn===readSkuSearchResults)return {url:search(),routeReady:true,matched_card_count:duplicate?2:1,matching_title:title,original_image_url:original,matched_image_url:actual,live_sales_raw:'已拼598件',live_sales_value:598,live_sales_label:'已拼',live_sales_unit:'件'};
  if(fn===readSkuSearchCancel)return {ok:true,point:{x:400,y:20}};
  throw Error('Unexpected reader');
 },click:async p=>{operations.push('native_click');if(current.url!==store){current.url=returnMismatch?store.replace('SYNTHETIC_A','SYNTHETIC_B'):store;current.text='SYNTHETIC_STORE';}},fillSearch:async(options,text)=>{operations.push('native_fill');assert.equal(options.sourceUrl,store);assert.equal(text,query);},pressSearchEnter:async()=>{operations.push('native_enter');current.url=search();current.text='SYNTHETIC_RESULTS';},scroll:()=>{throw Error('Whole-shop scroll forbidden');},goto:()=>{throw Error('Unexpected reload');}};
 return {browser,operations};
}
const request={shop:{source_url:store,shop_name:'SYNTHETIC_STORE'},observation:{title:query,image_url:original,sales_value:594}};
test('same-tab fixed native search and cancel return use no whole-shop paging or reload',async()=>{
 const f=fakeBrowser(),e=await locateSkuBySearch(f.browser,request,async()=>false,async()=>{});
 assert.equal(e.method,'native_store_search');assert.equal(e.page_id,17);assert.equal(e.matched_card_count,1);assert.deepEqual(f.operations,['native_click','native_fill','native_enter']);
 const returned=await returnFromSkuSearch(f.browser,request,{...e,modal_closed:true});assert.equal(returned.return_verified,true);assert.equal(returned.return_store_url,store);assert.equal(returned.modal_closed,true);
});
test('ambiguous result stops before plus, and cross-store cancel return never validates',async()=>{
 let f=fakeBrowser({duplicate:true});await assert.rejects(()=>locateSkuBySearch(f.browser,request,async()=>false,async()=>{}),e=>e.reason==='sku_card_ambiguous');
 f=fakeBrowser({returnMismatch:true});const e=await locateSkuBySearch(f.browser,request,async()=>false,async()=>{});await assert.rejects(()=>returnFromSkuSearch(f.browser,request,e,{maxPolls:1}),e=>e.reason==='sku_search_return_failed');
});
