import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {exactDisplayedSales,matchRepairCard,matchRepairImageReference,readRepairStoreDom,repairStoreImages,imageRepairBudgetMs} from '../scripts/local_image_repair.mjs';

const url='https://img.pddpic.com/SYNTHETIC_ORIGINAL.png',other='https://img.pddpic.com/SYNTHETIC_OTHER.png';
const shop={shop_name:'SYNTHETIC_SHOP',source_url:'https://mobile.yangkeduo.com/mall_page.html?mall_id=123'};
const row={title:'SYNTHETIC_PRODUCT',imageUrl:url,salesRaw:'已拼425件',viewOrder:7,goodsId:null};
const candidate=(sales='已拼425件',image=url,index=0)=>({index,title:row.title,imageUrl:image,salesRaw:sales,goodsId:null});

test('production image-repair budget scales by missing cards and stays within 120 to 600 seconds',()=>{
 const rows=Array.from({length:138},(_,index)=>({image_url:`https://img.pddpic.com/SYNTHETIC_${index}.png`}));
 for(const [count,expected] of [[0,120000],[1,120000],[20,120000],[21,126000],[99,594000],[100,600000],[138,600000]])assert.equal(imageRepairBudgetMs({rows:rows.slice(0,count)}),expected);
});

test('budget excludes approved cache, this-session bytes, blocked originals and invalid/missing image URLs',()=>{
 const rows=Array.from({length:60},(_,index)=>({rawImageUrl:`https://img.pddpic.com/SYNTHETIC_${index}.png`}));
 const skipUrls=rows.slice(0,10).map(row=>row.rawImageUrl),cachedImages=new Map(rows.slice(10,20).map(row=>[row.rawImageUrl,{mime:'image/png'}])),blockedUrls=rows.slice(20,30).map(row=>row.rawImageUrl);
 const withInvalid=[...rows,{image_url:null},{image_url:'https://foreign.example/image.png'}];
 assert.equal(imageRepairBudgetMs({rows:withInvalid,skipUrls,cachedImages,blockedUrls}),180000);
 assert.equal(imageRepairBudgetMs({rows,skipUrls:rows.map(row=>row.rawImageUrl)}),120000);
 assert.equal(imageRepairBudgetMs({rows,blockedUrls:rows.map(row=>row.rawImageUrl)}),120000);
});
function fixture(cards=[candidate()]){
 const env={cards,scrolls:[],collects:[],evaluations:[],sleeps:0,progress:[],url:shop.source_url,marker:'本店暂无更多商品',markerTop:10000,selected:true,text:shop.shop_name,scrollY:0};
 const style={display:'block',visibility:'visible',opacity:'1'};
 const element=(text='',top=20)=>({textContent:text,children:[],className:'',getAttribute(){return null;},getBoundingClientRect:()=>({top:top-env.scrollY,bottom:top+80-env.scrollY,width:100,height:80}),contains(other){return this===other;}});
 const tab=element('上新');tab.getAttribute=key=>key==='aria-selected'&&env.selected?'true':null;
 const list=element('',0);list.querySelectorAll=()=>env.cards.map((data,index)=>{
  const card=element('',data.top??index*100+100);card.title=element(data.title);card.sales=element(data.salesRaw||'');
  card.image={...element('',data.top??index*100+100),currentSrc:data.actualSrc||data.imageUrl,complete:data.loaded!==false,naturalWidth:100,naturalHeight:100,getAttribute:key=>['data-src','src'].includes(key)?data.imageUrl:null};
  card.querySelector=selector=>selector.startsWith('.goodsImage')?card.image:selector.startsWith('.goodsName')?card.title:selector.startsWith('.salesTip')?card.sales:selector==='a[href]'&&data.goodsId?{href:`https://mobile.yangkeduo.com/goods.html?goods_id=${data.goodsId}`}:null;
  card.closest=()=>null;card.scrollIntoView=()=>{env.scrolls.push(index);env.scrollY=(data.top??index*100+100)-400;};return card;
 });
 env.extraLists=[];env.extraMarkers=[];env.makeElement=element;
 const context={URL,innerHeight:1000,getComputedStyle:()=>style,window:{get scrollY(){return env.scrollY;}},location:{get href(){return env.url;},get origin(){return new URL(env.url).origin;},get pathname(){return new URL(env.url).pathname;}},document:{body:{get innerText(){return env.text;}},querySelectorAll:selector=>selector.startsWith('.waterfall')?[list,...env.extraLists]:selector==='div,p,span'?[element(env.marker,env.markerTop),...env.extraMarkers]:[tab]}};
 env.browser={images:new Map(),async evaluate(fn,args){env.evaluations.push(fn.name);return JSON.parse(JSON.stringify(vm.runInNewContext(`(${fn.toString()})(${JSON.stringify(args)??''})`,context)));},async collectImages(args){env.collects.push(args);env.browser.images.set(args.imageUrls[0],{mime:'image/png',base64:'synthetic'});return {newlySaved:1,reasons:{}};}};
 env.options={sleep:async()=>{env.sleeps++;},progress:async value=>env.progress.push(value)};
 env.run=(rows=[row],patch={},options={})=>repairStoreImages(env.browser,{shop,rows,...patch},{...env.options,...options});return env;
}

test('exact 已拼 and 已抢 counts share a business metric; fuzzy or foreign counts remain unknown',()=>{
 assert.deepEqual(exactDisplayedSales('已抢425件'),{value:425,unit:'件',label:'已抢'});
 assert.equal(exactDisplayedSales('已拼1,234件').value,1234);
 for(const raw of ['已拼425+件','已拼1万件','总售425件','已拼3-5件','已拼0.5件','已拼425','已抢12,34件',null])assert.equal(exactDisplayedSales(raw),null);
});
test('three same-title different-image cards require the original main image',()=>{
 const cards=[candidate('已拼11件',other),candidate('已抢426件',url,1),candidate('已拼425件',other,2)];
 const result=matchRepairCard(row,cards);assert.equal(result.ok,true);assert.equal(result.candidate.index,1);assert.equal(result.candidate_count,1);
 assert.equal(matchRepairCard({...row,imageUrl:null},cards).reason,'source_image_missing');
 assert.equal(matchRepairCard({...row,imageUrl:url+'?size=2'},cards).reason,'original_card_not_found');
});
test('same-image candidates select unique nearest exact sales without summing',()=>{
 const result=matchRepairCard(row,[candidate('已拼11件'),candidate('已抢427件',url,1),candidate('已拼450件',url,2)]);
 assert.equal(result.ok,true);assert.equal(result.candidate.index,1);assert.equal(result.sales_distance,2);assert.equal(result.candidate_count,3);
 assert.equal(result.match_basis,'exact_title_original_image_unique_nearest_sales');
});
test('ties, unknown sales, incompatible unit and reliable ID conflict never guess',()=>{
 assert.equal(matchRepairCard(row,[candidate('已拼424件'),candidate('已抢426件')]).reason,'nearest_sales_tied');
 assert.equal(matchRepairCard(row,[candidate('已拼425件'),candidate(null)]).reason,'sales_unknown_or_incomparable');
 assert.equal(matchRepairCard(row,[candidate('已拼425件'),candidate('已拼430个')]).reason,'sales_unknown_or_incomparable');
 assert.equal(matchRepairCard({...row,goodsId:'88'},[{...candidate(),goodsId:'89'}]).reason,'goods_id_conflict');
 assert.equal(matchRepairCard({...row,goodsId:'88'},[{...candidate(),goodsId:'88'},{...candidate('已拼900件'),goodsId:'89'}]).reason,'goods_id_conflict');
});

test('image-reference matching preserves successful strict original-card and nearest-sales results',()=>{
 for(const cards of [[candidate()],[candidate('已拼11件'),candidate('已抢427件',url,1),candidate('已拼450件',url,2)]]){
  assert.deepEqual(matchRepairImageReference(row,cards),matchRepairCard(row,cards));
 }
});

test('shared exact-image fallback reveals the smallest unique DOM index without claiming card identity or sales',()=>{
 const inputs=[
  [row,[candidate('已拼424件',url,9),candidate('已抢426件',url,2)],'nearest_sales_tied'],
  [{...row,salesRaw:null},[candidate('已拼424件',url,9),candidate('已抢426件',url,2)],'sales_unknown_or_incomparable'],
  [row,[candidate(null,url,9),candidate('已抢426件',url,2)],'sales_unknown_or_incomparable'],
  [row,[candidate('已拼425件',url,9),candidate('已拼425个',url,2)],'sales_unknown_or_incomparable'],
 ];
 for(const [source,cards,reason] of inputs){
  const before=JSON.stringify({source,cards});Object.freeze(source);cards.forEach(Object.freeze);Object.freeze(cards);
  assert.equal(matchRepairCard(source,cards).reason,reason);const result=matchRepairImageReference(source,cards);
  assert.equal(result.ok,true);assert.equal(result.candidate.index,2);assert.equal(result.candidate_count,2);assert.equal(result.image_reference_only,true);assert.equal(result.match_basis,'shared_exact_original_image_reference');assert.equal(result.sales_distance,null);
  assert.equal(JSON.stringify({source,cards}),before);
 }
});

test('shared image fallback rejects missing, negative, fractional, unsafe and duplicate DOM indices',()=>{
 for(const index of [undefined,null,-1,0.5,'1',Number.MAX_SAFE_INTEGER+1]){
  const result=matchRepairImageReference(row,[{...candidate('已拼424件'),index},candidate('已拼426件',url,3)]);
  assert.equal(result.ok,false,`invalid index ${String(index)} must not reveal another card`);assert.equal(result.reason,'image_reference_index_invalid');
 }
 assert.equal(matchRepairImageReference(row,[candidate('已拼424件',url,3),candidate('已拼426件',url,3)]).reason,'image_reference_index_invalid');
});

test('shared image fallback never crosses conflicting reliable goods IDs even when the source has no ID',()=>{
 const cards=[{...candidate('已拼424件',url,0),goodsId:'88'},{...candidate('已拼426件',url,1),goodsId:'89'}];
 for(const goodsId of [null,'88','89'])assert.equal(matchRepairImageReference({...row,goodsId},cards).reason,'goods_id_conflict');
 const sameId=cards.map(card=>({...card,goodsId:'88'}));assert.equal(matchRepairImageReference({...row,goodsId:'88'},sameId).image_reference_only,true);
 const unknownId=[sameId[0],{...sameId[1],goodsId:null}];assert.equal(matchRepairImageReference({...row,goodsId:'88'},unknownId).image_reference_only,true);
 assert.equal(matchRepairImageReference({...row,goodsId:'88'},[{...candidate(),goodsId:'89'}]).reason,'goods_id_conflict');
});

test('shared references require the exact title and original image URL including all parameters',()=>{
 for(const imageUrl of [other,url+'?size=375',url.replace('img.pddpic.com','img-2.pddpic.com')]){
  const cards=[candidate('已拼424件',imageUrl,0),candidate('已拼426件',imageUrl,1)];
  assert.equal(matchRepairImageReference(row,cards).reason,'original_card_not_found');
 }
 assert.equal(matchRepairImageReference(row,[{...candidate(),title:row.title+' EXTRA'}]).reason,'original_card_not_found');
 assert.equal(matchRepairImageReference({...row,imageUrl:null},[candidate()]).reason,'source_image_missing');
 const source={...row,rawImageUrl:url,imageUrl:other},cards=[candidate('已拼424件',url,5),candidate('已拼426件',url,3),{...candidate(null,other,-1),goodsId:'999'}];
 const result=matchRepairImageReference(source,cards);assert.equal(result.ok,true);assert.equal(result.candidate.imageUrl,url);assert.equal(result.candidate_count,2);assert.equal(result.candidate.index,3);
});
test('real DOM adapter excludes recommendation cards and requires end-of-shop boundary',async()=>{
 const env=fixture([{...candidate(),top:100},{...candidate('已拼424件'),top:700}]);env.markerTop=500;
 const page=await env.browser.evaluate(readRepairStoreDom,{shopName:shop.shop_name});assert.equal(page.cards.length,1);assert.equal(page.boundaryVerified,true);
 env.marker='其他店铺的精选推荐';assert.equal((await env.browser.evaluate(readRepairStoreDom,{shopName:shop.shop_name})).boundaryVerified,false);
 await assert.rejects(env.run(),error=>error.reason==='image_repair_list_unverified');assert.equal(env.collects.length,0);
});

test('an extra recommendation list after the real end marker does not reject the sole shop list',async()=>{
 const env=fixture();env.markerTop=500;env.extraMarkers.push(env.makeElement('其他店铺的精选推荐',600));env.extraLists.push(env.makeElement('',650));
 const hidden=env.makeElement('',100);hidden.hidden=true;env.extraLists.push(hidden);
 const page=await env.browser.evaluate(readRepairStoreDom,{shopName:shop.shop_name});assert.equal(page.listCount,3);assert.equal(page.mainListCount,1);assert.equal(page.boundaryVerified,true);assert.equal(page.latestSelected,true);assert.equal(page.cards.length,1);
 const result=await env.run();assert.equal(result.repaired,1);assert.equal(env.collects.length,1);
});

test('two visible pre-boundary lists remain ambiguous and an end marker after recommendations is not accepted',async()=>{
 const ambiguous=fixture();ambiguous.extraLists.push(ambiguous.makeElement('',50));await assert.rejects(ambiguous.run(),error=>error.reason==='image_repair_list_unverified');assert.equal(ambiguous.collects.length,0);
 const wrongBoundary=fixture();wrongBoundary.markerTop=700;wrongBoundary.extraMarkers.push(wrongBoundary.makeElement('其他店铺的精选推荐',500));
 const page=await wrongBoundary.browser.evaluate(readRepairStoreDom,{shopName:shop.shop_name});assert.equal(page.boundaryVerified,false);await assert.rejects(wrongBoundary.run(),error=>error.reason==='image_repair_list_unverified');assert.equal(wrongBoundary.collects.length,0);
});
test('normal scroll and exact loaded image capture record per-card evidence and progress',async()=>{
 const env=fixture([candidate('已拼11件'),candidate('已抢426件',url,1),candidate('已拼999件',url,2)]);
 const result=await env.run();assert.equal(result.status,'complete');assert.equal(result.saved,1);assert.equal(result.repaired,1);assert.equal(result.missing,0);
 assert.deepEqual(env.scrolls,[1]);assert.equal(env.collects.length,1);assert.deepEqual(env.collects[0].imageUrls,[url]);assert.equal(env.collects[0].shopName,shop.shop_name);
 assert.equal(result.items[0].sales_distance,1);assert.equal(result.items[0].matched_sales_raw,'已抢426件');assert.equal(result.items[0].attempts,1);
 assert.equal(env.progress.at(-1).stage,'images');assert.equal(env.progress.at(-1).image_saved,1);assert.equal(env.progress.at(-1).image_missing,0);
});
test('approved exact cache skips work but blocked and missing source never count as saved',async()=>{
 const env=fixture();const result=await env.run([row,{...row,viewOrder:8,imageUrl:other},{...row,viewOrder:9,imageUrl:null}],{skipUrls:[url,other],blockedUrls:[other]});
 assert.equal(result.saved,1);assert.equal(result.missing,2);assert.equal(result.blocked,1);assert.equal(result.items[1].reason,'previous_attempt_blocked');assert.equal(result.items[2].reason,'source_image_missing');assert.equal(env.evaluations.length,0);
});
test('a different actual src cannot satisfy bounded lazy-image wait or substitute the original',async()=>{
 const env=fixture([{...candidate(),actualSrc:url+'?size=1'}]);env.browser.collectImages=async args=>{env.collects.push(args);return {reasons:{store_image_not_loaded:1}};};
 const result=await env.run();assert.equal(result.status,'partial');assert.equal(env.collects.length,0);assert.equal(result.items[0].attempts,1);assert.equal(result.items[0].actual_src,url+'?size=1');assert.equal(result.items[0].reason,'store_image_not_loaded');assert.equal(env.sleeps,20);assert.equal(env.scrolls.length,1);
});

test('a slow lazy image is awaited in place until loaded before any image response copy',async()=>{
 const env=fixture([{...candidate(),loaded:false}]);let polls=0;
 const result=await env.run([row],{},{sleep:async()=>{polls++;assert.equal(env.collects.length,0);if(polls===4)env.cards[0].loaded=true;}});
 assert.equal(polls,4);assert.equal(env.scrolls.length,1);assert.equal(env.collects.length,1);assert.equal(result.repaired,1);
});

test('lazy-image waiting respects the whole-job budget and cancellation without extra scrolls',async()=>{
 const env=fixture([{...candidate(),loaded:false}]);let clock=0;
 const result=await env.run([row],{},{now:()=>clock,maxDurationMs:600,sleep:async ms=>{clock+=ms;}});assert.equal(result.timed_out,true);assert.equal(result.items[0].reason,'image_repair_time_budget');assert.equal(env.collects.length,0);assert.equal(env.scrolls.length,1);
 const cancelled=fixture([{...candidate(),loaded:false}]);let stop=false;await assert.rejects(cancelled.run([row],{},{cancelled:async()=>stop,sleep:async()=>{stop=true;}}),error=>error.status==='cancelled');assert.equal(cancelled.collects.length,0);assert.equal(cancelled.scrolls.length,1);
});
test('body failure and size limits end this image without a second attempt',async()=>{
 for(const reason of ['response_body_unavailable','image_size_limit','previous_attempt_blocked','body_already_attempted']){
  const env=fixture();env.browser.collectImages=async args=>{env.collects.push(args);return {reasons:{[reason]:1}};};const result=await env.run();assert.equal(result.items[0].reason,reason);assert.equal(env.collects.length,1);
 }
});
test('sales ties repair the shared original image while the strict card matcher still rejects identity ambiguity',async()=>{
 const cards=[candidate('已拼424件'),candidate('已拼426件',url,1)],env=fixture(cards);assert.equal(matchRepairCard(row,cards).reason,'nearest_sales_tied');
 const result=await env.run();assert.equal(result.status,'complete');assert.equal(result.missing,0);assert.equal(env.collects.length,1);assert.deepEqual(env.collects[0].imageUrls,[url]);assert.deepEqual(env.scrolls,[0]);
 assert.equal(result.items[0].image_reference_only,true);assert.equal(result.items[0].match_basis,'shared_exact_original_image_reference');assert.equal(result.items[0].matched_sales_raw,null);assert.equal(result.items[0].matched_goods_id,null);assert.equal(result.items[0].sales_distance,null);
 const missing=fixture([candidate('已拼425件',other)]),unmatched=await missing.run();assert.equal(unmatched.status,'partial');assert.equal(unmatched.items[0].reason,'original_card_not_found');assert.equal(missing.collects.length,0);
});

test('two independent original rows share only image bytes and keep separate receipt entries with no borrowed sales or ID',async()=>{
 const rows=[Object.freeze({...row,viewOrder:7}),Object.freeze({...row,viewOrder:19,salesRaw:null})],before=JSON.stringify(rows),cards=[candidate('已拼424件'),candidate('已拼426件',url,1)],env=fixture(cards);
 Object.freeze(rows);const result=await env.run(rows);assert.equal(result.status,'complete');assert.equal(result.total,2);assert.equal(result.saved,2);assert.equal(result.repaired,2);assert.equal(result.missing,0);assert.equal(result.items.length,2);
 assert.deepEqual(result.items.map(item=>item.view_order),[7,19]);assert.deepEqual(result.items.map(item=>item.image_url),[url,url]);assert.equal(env.collects.length,1);assert.equal(env.browser.images.size,1);assert.deepEqual([...env.browser.images.keys()],[url]);
 for(const item of result.items){assert.equal(item.state,'complete');assert.equal(item.image_reference_only,true);assert.equal(item.match_basis,'shared_exact_original_image_reference');assert.equal(item.matched_sales_raw,null);assert.equal(item.matched_goods_id,null);assert.equal(item.sales_distance,null);}
 assert.equal(JSON.stringify(rows),before);assert.deepEqual(cards.map(card=>card.salesRaw),['已拼424件','已拼426件']);assert.equal(matchRepairCard(row,cards).reason,'nearest_sales_tied');
 assert.deepEqual(result.items.map(item=>item.attempts),[1,0]);assert.deepEqual(result.items.map(item=>item.candidate_count),[2,2]);
});

test('shared-image cache reuse still rejects the second original row when its reliable goods ID conflicts',async()=>{
 const rows=[{...row,viewOrder:7},{...row,viewOrder:19,goodsId:'99'}],before=JSON.stringify(rows),env=fixture([{...candidate('已拼424件'),goodsId:'88'},{...candidate('已拼426件',url,1),goodsId:'88'}]);
 const result=await env.run(rows);assert.equal(result.status,'partial');assert.equal(result.saved,1);assert.equal(result.missing,1);assert.deepEqual(result.items.map(item=>item.view_order),[7,19]);
 assert.equal(result.items[0].image_reference_only,true);assert.equal(result.items[0].matched_sales_raw,null);assert.equal(result.items[0].matched_goods_id,null);
 assert.equal(result.items[1].state,'missing');assert.equal(result.items[1].reason,'goods_id_conflict');assert.equal(result.items[1].attempts,0);
 assert.equal(env.collects.length,1);assert.equal(env.browser.images.size,1);assert.equal(JSON.stringify(rows),before);
});

test('shared-image repair refuses reliable-ID conflict and never substitutes an alternate original URL',async()=>{
 const conflicts=fixture([{...candidate('已拼424件'),goodsId:'88'},{...candidate('已拼426件',url,1),goodsId:'89'}]);
 const conflict=await conflicts.run();assert.equal(conflict.status,'partial');assert.equal(conflict.items[0].reason,'goods_id_conflict');assert.equal(conflicts.collects.length,0);assert.equal(conflicts.browser.images.size,0);
 const different=fixture([candidate('已拼424件',url+'?size=375'),candidate('已拼426件',url+'?size=375',1)]),result=await different.run();assert.equal(result.status,'partial');assert.equal(result.items[0].image_url,url);assert.equal(different.collects.length,0);assert.equal(different.browser.images.size,0);
});

test('shared-image reference requires the actual loaded source to equal the original and leaves both rows incomplete otherwise',async()=>{
 const env=fixture([{...candidate('已拼424件'),actualSrc:url+'?size=1'},{...candidate('已拼426件',url,1),actualSrc:url+'?size=1'}]);
 const result=await env.run([row,{...row,viewOrder:8}]);assert.equal(result.status,'partial');assert.equal(result.saved,0);assert.equal(result.missing,2);assert.equal(env.collects.length,0);assert.equal(env.browser.images.size,0);
 for(const item of result.items){assert.equal(item.reason,'store_image_not_loaded');assert.equal(item.image_url,url);assert.equal(item.image_reference_only,true);assert.equal(item.matched_sales_raw,null);assert.equal(item.matched_goods_id,null);}
});

test('shared-image body failures never fabricate a completed row or cached original',async()=>{
 for(const reason of ['response_body_unavailable','image_size_limit','invalid_image_bytes','body_already_attempted']){
  const env=fixture([candidate('已拼424件'),candidate('已拼426件',url,1)]);env.browser.collectImages=async args=>{env.collects.push(args);return {newlySaved:0,reasons:{[reason]:1}};};
  const result=await env.run([row,{...row,viewOrder:8}]);assert.equal(result.status,'partial');assert.equal(result.saved,0);assert.equal(result.missing,2);assert.equal(env.browser.images.size,0);assert.ok(env.collects.length<=2);
  for(const item of result.items){assert.equal(item.reason,reason);assert.equal(item.state,'missing');assert.equal(item.image_reference_only,true);assert.equal(item.matched_sales_raw,null);assert.equal(item.matched_goods_id,null);}
 }
});

test('shared-image candidate signature or shop changes during reveal stop before any response copy',async()=>{
 const mutations=[env=>{env.cards[1].salesRaw='已拼999件';},env=>{env.cards[1].goodsId='99';},env=>{env.cards[1].imageUrl=other;},env=>{env.cards.reverse();},env=>{env.url=shop.source_url.replace('123','456');}];
 for(const mutate of mutations){const env=fixture([candidate('已拼424件'),candidate('已拼426件',url,1)]);await assert.rejects(env.run([row],{},{sleep:async()=>mutate(env)}));assert.equal(env.collects.length,0);assert.equal(env.browser.images.size,0);}
});
test('candidate change during lazy-load wait stops before copying unrelated response',async()=>{
 const env=fixture();await assert.rejects(env.run([row],{},{sleep:async()=>{env.cards[0].salesRaw='已拼999件';}}),error=>error.reason==='image_repair_candidate_changed');assert.equal(env.collects.length,0);
});
test('source, sort, login, restriction and connection changes stop globally',async()=>{
 for(const mutate of [env=>{env.url=shop.source_url.replace('123','456');},env=>{env.selected=false;},env=>{env.text+=' 请先登录';},env=>{env.text+=' 安全验证';}]){
  const env=fixture();mutate(env);await assert.rejects(env.run());assert.equal(env.collects.length,0);
 }
 const env=fixture();env.browser.collectImages=async args=>{env.collects.push(args);return {reasons:{connection_required:1}};};await assert.rejects(env.run(),error=>error.reason==='connection_required');assert.equal(env.collects.length,1);
});
test('budget and cancellation stop work without discarding already accepted image bytes',async()=>{
 const env=fixture();let clock=0;const result=await env.run([row],{},{now:()=>clock+=100,maxDurationMs:100});assert.equal(result.timed_out,true);assert.equal(result.items[0].reason,'image_repair_time_budget');assert.equal(env.collects.length,0);
 env.browser.images.set(other,{base64:'saved'});await assert.rejects(env.run([row],{},{cancelled:async()=>true}),error=>error.status==='cancelled');assert.equal(env.browser.images.has(other),true);
});
