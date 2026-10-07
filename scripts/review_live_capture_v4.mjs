// Pure Node mocks only. No browser and no database interaction.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {createCapture,shopIdentityFromUrl} from './live_capture_driver.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
const cli={};for(let i=2;i<process.argv.length;i+=2){if(!['--project-root','--output'].includes(process.argv[i])||!process.argv[i+1])throw new Error('Usage: node scripts/review_live_capture_v4.mjs [--project-root PATH] [--output PATH]');cli[process.argv[i]]=process.argv[i+1];}
const projectRoot=path.resolve(cli['--project-root']||path.join(root,'..'));
const output=cli['--output']?path.resolve(cli['--output']):await fs.mkdtemp(path.join(os.tmpdir(),'pdd_capture_v4_review_'));
await fs.mkdir(output,{recursive:true});
const syntheticRoot=await fs.mkdtemp(path.join(output,'SYNTHETIC_ONLY_'));
const options={sourceUrl:'https://example.invalid/mall?mall_id=900001',shopName:'SYNTHETIC STORE',checkpointReference:'sources/SYNTHETIC_ONLY',attemptId:'SYNTHETIC_ONLY'};
const at=n=>new Date(Date.UTC(2026,9,4,8,0,n)).toISOString();
const row=(index,overrides={})=>({title:'SYNTHETIC SAME TITLE',salesRaw:'已拼11件',priceRaw:'券后¥4.5',imageUrl:'https://example.invalid/image.png',goodsUrl:null,goodsId:null,goodsIdEvidence:'No verified visible goods link or ID on DOM card',domIndex:index,domColumn:0,domRow:index,position:{top:index*200,left:0,viewportTop:index*200,viewportBottom:index*200+180},...overrides});
const page=(n=0,overrides={})=>({at:at(n),pageUrl:options.sourceUrl,shopVerified:true,listPresent:true,storeSalesRaw:'本店已拼123件',scrollTop:0,viewportHeight:600,viewportWidth:1000,scrollHeight:2000,loadedCardCount:2,columnLayoutVerified:true,columnCounts:[overrides.loadedCardCount??2],endBoundaryObserved:false,boundaries:[],cards:[row(0),row(1)],...overrides});
function mockTab(pages,fail={}){
 const calls={evaluate:0,scroll:[],press:[],ax:0};
 return {id:'SYNTHETIC_TAB',calls,playwright:{evaluate:async()=>{calls.evaluate++;const value=pages.shift();if(value instanceof Error)throw value;if(!value)throw new Error('SYNTHETIC unexpected read');return structuredClone(value);},locator:selector=>({press:async key=>{calls.press.push([selector,key]);throw new Error('PageDown must not be used by step');}})},
  scroll:async(...args)=>{calls.scroll.push(args);if(fail.scroll)throw new Error('SYNTHETIC scroll failure');},
  getAXState:async()=>{calls.ax++;if(fail.ax)throw new Error('SYNTHETIC AX timeout');return 'SYNTHETIC';}};
}
let number=0;
async function fresh(pages,fail,settings=options){
 const directory=path.join(syntheticRoot,String(++number).padStart(3,'0'));
 const tab=mockTab(pages,fail),capture=await createCapture(tab,directory,settings);
 return {directory,tab,capture};
}
const checks=[];
async function test(name,fn){try{await fn();checks.push({name,ok:true});}catch(error){checks.push({name,ok:false,error:error.stack||String(error)});}}

await test('verified_parameters_and_fresh_attempt_directory_required',async()=>{
 await assert.rejects(createCapture({},path.join(syntheticRoot,'no-options')),/required/);
 await assert.rejects(createCapture({},path.join(syntheticRoot,'bad-url'),{...options,sourceUrl:'javascript:bad'}),/HTTP/);
 const {capture,tab,directory}=await fresh([page()]);await capture.capture();
 await assert.rejects(createCapture(tab,directory,options),/fresh attempt/);
});
await test('step_before_initial_top_capture_has_clear_error_without_scrolling',async()=>{
 const {capture,tab}=await fresh([page()]);await assert.rejects(capture.step(),/Call capture/);
 assert.equal(tab.calls.scroll.length,0);assert.equal(tab.calls.evaluate,0);
});
await test('same_title_image_and_missing_ids_preserve_separate_original_cards',async()=>{
 const {capture}=await fresh([page()]);await capture.capture();const snapshot=await capture.snapshot();
 assert.equal(snapshot.rows.length,2);assert.equal(new Set(snapshot.rows.map(r=>r.recordKey)).size,2);
 assert(snapshot.rows.every(r=>r.goodsId===null));assert.equal(snapshot.status,'partial');
 assert.equal(snapshot.sourceUrl,options.sourceUrl);assert.equal(snapshot.collectionEvidence.attemptId,options.attemptId);
 assert.equal(snapshot.collectionEvidence.tabId,'SYNTHETIC_TAB');assert.equal(snapshot.storeSalesRaw,'本店已拼123件');
});
await test('complete_requires_top_start_visible_boundary_and_clean_sequence',async()=>{
 const {capture,tab}=await fresh([page(),page(1,{scrollTop:450,cards:[row(2)],loadedCardCount:3,endBoundaryObserved:true})]);
 await capture.capture();await capture.step();const snapshot=await capture.snapshot();
 assert.equal(snapshot.status,'complete');assert.equal(snapshot.endBoundaryObserved,true);assert.equal(snapshot.rows.length,3);
 assert.deepEqual(tab.calls.scroll,[[[500,500],'down',0.8]]);
 assert.equal(snapshot.observedFrom,at(0));assert.equal(snapshot.observedTo,at(1));
});
await test('not_started_at_top_never_claims_complete',async()=>{
 const {capture}=await fresh([page(0,{scrollTop:100,endBoundaryObserved:true})]);
 await capture.capture();assert.equal(capture.status().stop,'scan_not_started_at_top');
 assert.equal(capture.status().cards,0);await assert.rejects(capture.snapshot(),/No observed cards/);
});
await test('overlap_reads_preserve_first_observation_time_and_raw_sales',async()=>{
 const {capture}=await fresh([page(),page(1,{scrollTop:100,cards:[row(1,{salesRaw:'已拼12件'})]})]);
 await capture.capture();await capture.step();const snapshot=await capture.snapshot();
 assert.equal(snapshot.rows.length,2);assert.equal(snapshot.rows[1].salesRaw,'已拼11件');
 assert.equal(snapshot.rows[1].observedAt,at(0));assert.equal(snapshot.rows[1].observedAtPrecision,'batch_read');
});
await test('forward_gap_is_partial_and_uncertain_batch_cannot_add_cards',async()=>{
 const {capture,directory}=await fresh([page(),page(1,{scrollTop:603,cards:[row(3)],loadedCardCount:4,endBoundaryObserved:true})]);
 await capture.capture();await capture.step();const snapshot=await capture.snapshot();
 assert.equal(snapshot.status,'partial');assert.equal(capture.status().stop,'unobserved_scroll_gap');assert.equal(snapshot.rows.length,2);
 const raw=JSON.parse(await fs.readFile(path.join(directory,'batch_0002.json'),'utf8'));
 assert.equal(raw.cards.length,1);assert.equal(raw.added,0);
});
await test('viewport_changes_and_reverse_scroll_stop_the_attempt',async()=>{
 for(const [override,reason] of [[{viewportWidth:900},'viewport_changed'],[{scrollTop:-20},'unexpected_reverse_scroll']]){
  const {capture}=await fresh([page(),page(1,override)]);await capture.capture();await capture.step();assert.equal(capture.status().stop,reason);
 }
});
await test('three_normal_no_new_scrolls_stop_even_when_scrollTop_is_unchanged',async()=>{
 const {capture,tab}=await fresh([page(),page(1),page(2),page(3)]);await capture.capture();
 await capture.step();await capture.step();await capture.step();assert.equal(capture.status().stop,'three_normal_scrolls_without_new_cards');
 assert.equal(tab.calls.scroll.length,3);assert.equal(capture.status().noNew,3);
 await capture.capture();await capture.step();assert.equal(tab.calls.evaluate,4);assert.equal(tab.calls.scroll.length,3);
});
await test('manual_repeat_reads_do_not_masquerade_as_three_normal_scrolls',async()=>{
 const {capture}=await fresh([page(),page(1),page(2),page(3)]);
 for(let i=0;i<4;i++)await capture.capture();assert.equal(capture.status().noNew,0);assert.equal(capture.status().stop,null);
});
await test('visible_boundary_wins_over_third_no_new_scroll_but_terminal_state_is_sticky',async()=>{
 const {capture,tab}=await fresh([page(),page(1),page(2),page(3,{endBoundaryObserved:true})]);
 await capture.capture();await capture.step();await capture.step();await capture.step();
 assert.equal((await capture.snapshot()).status,'complete');await capture.capture();await capture.step();assert.equal(tab.calls.evaluate,4);
 assert.equal((await capture.snapshot('operator_stop')).status,'partial');
});
await test('read_timeout_retains_valid_prefix_and_cannot_scroll_again',async()=>{
 const {capture,tab}=await fresh([page(),new Error('SYNTHETIC timeout')]);await capture.capture();
 await assert.rejects(capture.step(),/timeout/);await capture.step();
 assert.equal(tab.calls.scroll.length,1);assert.equal(capture.status().stop,'page_read_failed');
 const snapshot=await capture.snapshot();assert.equal(snapshot.rows.length,2);assert.equal(snapshot.observedTo,at(0));assert.equal(snapshot.status,'partial');
});
await test('step_reads_fresh_DOM_after_scroll_without_redundant_AX_call',async()=>{
 const {capture,tab}=await fresh([page(),page(1,{scrollTop:450,cards:[row(2)],loadedCardCount:3,endBoundaryObserved:true})],{ax:true});
 await capture.capture();await capture.step();
 assert.equal(tab.calls.ax,0);assert.equal(tab.calls.scroll.length,1);assert.equal(tab.calls.evaluate,2);
 assert.equal(capture.status().stop,null);assert.equal((await capture.snapshot()).rows.length,3);
 assert.equal((await capture.snapshot()).observedTo,at(1));
});
await test('checkpoint_collision_is_not_counted_as_saved_progress',async()=>{
 const {capture,directory}=await fresh([page()]);await fs.writeFile(path.join(directory,'batch_0001.json'),'SYNTHETIC COLLISION');
 await assert.rejects(capture.capture(),/EEXIST/);assert.equal(capture.status().batches,0);assert.equal(capture.status().cards,0);
 assert.equal(capture.status().stop,'checkpoint_write_failed');assert.equal(await fs.readFile(path.join(directory,'batch_0001.json'),'utf8'),'SYNTHETIC COLLISION');
});
await test('layout_shift_or_virtualized_replacement_cannot_duplicate_or_merge_cards',async()=>{
 for(const [override,reason] of [[{cards:[row(1,{position:{top:220,left:0}})]},'document_layout_changed'],[{loadedCardCount:1,cards:[row(0)]},'dom_list_replaced_or_virtualized'],[{cards:[row(1,{title:'SYNTHETIC different'})]},'position_content_conflict']]){
  const {capture}=await fresh([page(),page(1,override)]);await capture.capture();await capture.step();
  assert.equal(capture.status().stop,reason);assert.equal((await capture.snapshot()).rows.length,2);
 }
});
await test('two_DOM_cards_at_one_position_stop_instead_of_silent_map_merging',async()=>{
 const {capture,directory}=await fresh([page(),page(1,{cards:[row(2),row(3,{position:{...row(2).position}})],loadedCardCount:4})]);
 await capture.capture();await capture.step();assert.equal(capture.status().stop,'duplicate_document_position');
 assert.equal((await capture.snapshot()).rows.length,2);
 const batch=JSON.parse(await fs.readFile(path.join(directory,'batch_0002.json'),'utf8'));
 assert.equal(batch.cards.length,2);assert.equal(batch.added,0);
});
await test('wrong_shop_or_missing_list_never_adds_cards_or_finishes',async()=>{
 for(const [override,reason] of [[{shopVerified:false},'shop_state_changed'],[{listPresent:false},'target_list_missing']]){
  const {capture}=await fresh([page(),page(1,{...override,endBoundaryObserved:true,cards:[row(2)],loadedCardCount:3})]);
  await capture.capture();await capture.step();assert.equal(capture.status().stop,reason);assert.equal((await capture.snapshot()).rows.length,2);assert.equal(capture.status().ended,false);
 }
});
await test('backwards_or_invalid_clock_does_not_replace_last_good_observation_time',async()=>{
 for(const time of ['bad',at(0)]){
  const {capture}=await fresh([page(2),page(3,{at:time})]);await capture.capture();await assert.rejects(capture.step(),/invalid_capture_timestamp/);
  assert.equal((await capture.snapshot()).observedTo,at(2));assert.equal(capture.status().batches,1);
 }
});
await test('concurrent_operations_are_rejected_before_double_scroll_or_snapshot',async()=>{
 const directory=path.join(syntheticRoot,'concurrent');let resolve;const tab=mockTab([]);
 tab.playwright.evaluate=()=>new Promise(done=>{resolve=done;});const capture=await createCapture(tab,directory,options),pending=capture.capture();
 await assert.rejects(capture.step(),/already in progress/);await assert.rejects(capture.capture(),/already in progress/);await assert.rejects(capture.snapshot(),/active capture/);
 resolve(page());await pending;assert.equal(capture.status().cards,2);
});

// Run the actual evaluate callback in a minimal DOM model, not just canned pages.
function domTab({cardSpecs,boundarySpecs=[],scrollTop=0,listTop=0,body='SYNTHETIC STORE\n本店已拼222件',pageUrl=options.sourceUrl,nestedCards=false,extraChild=false}){
 const rect=(top,height=180)=>({top,bottom:top+height,left:0,right:200,width:200,height});
 const cards=cardSpecs.map((spec,index)=>({children:[],matches:selector=>selector==='.goodsItem_R1ok0MpS',innerText:spec.title||'SYNTHETIC SAME TITLE',
  checkVisibility:()=>!spec.hidden,getBoundingClientRect:()=>({...rect(spec.top??index*100),left:spec.left??(spec.column??0)*250}),
  querySelector:selector=>selector.includes('goodsName')?{textContent:spec.title||'SYNTHETIC SAME TITLE'}:
   selector.includes('salesTip')?{textContent:spec.tip??'已拼11件'}:
   selector.includes('goodsImage')?{getAttribute:key=>key==='src'?'https://example.invalid/a.png':null}:
   selector==='a[href]'?(spec.href?{href:spec.href}:null):null,
  querySelectorAll:selector=>selector.includes('priceDesc')?[{textContent:'券后¥4.50'}]:[],
  closest:()=>null}));
 const boundaries=boundarySpecs.map(spec=>({children:[],textContent:'本店暂无更多商品',checkVisibility:()=>!spec.hidden,getBoundingClientRect:()=>rect(spec.top,spec.height??20)}));
 const columnIds=[...new Set(cardSpecs.map(spec=>spec.column??0))].sort((a,b)=>a-b);
 const columns=columnIds.map(column=>({children:cards.filter((card,index)=>(cardSpecs[index].column??0)===column)}));
 for(const column of columns)for(const card of column.children)card.parentElement=column;
 const orderedCards=columns.flatMap(column=>column.children);
 if(nestedCards)for(const column of columns){
  const wrapper={children:column.children,matches:()=>false,getBoundingClientRect:()=>rect(0),checkVisibility:()=>true};
  for(const card of wrapper.children)card.parentElement=wrapper;
  column.children=[wrapper];
 }
 if(extraChild)columns[0].children.push({children:[],matches:()=>false,getBoundingClientRect:()=>rect(10),checkVisibility:()=>true});
 const list={children:columns,querySelectorAll:()=>orderedCards,getBoundingClientRect:()=>rect(listTop,1200)};
 const document={scrollingElement:{scrollTop,scrollHeight:2000},body:{innerText:body},querySelector:()=>list,querySelectorAll:()=>boundaries};
 const DateMock=class extends Date{constructor(){super(at(0));}};
 return {id:'SYNTHETIC_DOM_TAB',scroll:async()=>{},getAXState:async()=>'',playwright:{evaluate:async(fn,arg)=>vm.runInNewContext('('+fn.toString()+')('+JSON.stringify(arg)+')',{document,location:{href:pageUrl},innerHeight:600,innerWidth:1000,URL,Date:DateMock,getComputedStyle:()=>({visibility:'visible',display:'block'})})}};
}
async function fromDOM(spec){const tab=domTab(spec);return createCapture(tab,path.join(syntheticRoot,'DOM_'+(++number)),options);}
await test('actual_DOM_hidden_end_template_does_not_cut_off_visible_cards_or_finish',async()=>{
 const capture=await fromDOM({cardSpecs:[{top:100},{top:300}],boundarySpecs:[{top:0,height:0,hidden:true}]});
 await capture.capture();const snapshot=await capture.snapshot();assert.equal(snapshot.rows.length,2);assert.equal(snapshot.status,'partial');
});
await test('actual_DOM_visible_end_excludes_recommended_cards_after_boundary',async()=>{
 const capture=await fromDOM({cardSpecs:[{top:100},{top:450}],boundarySpecs:[{top:350}]});
 await capture.capture();const snapshot=await capture.snapshot();assert.equal(snapshot.rows.length,1);assert.equal(snapshot.status,'complete');
});
await test('actual_DOM_offscreen_end_does_not_claim_complete_and_hidden_cards_are_excluded',async()=>{
 const capture=await fromDOM({cardSpecs:[{top:100},{top:300,hidden:true}],boundarySpecs:[{top:800}]});
 await capture.capture();const snapshot=await capture.snapshot();assert.equal(snapshot.rows.length,1);assert.equal(snapshot.status,'partial');
});
await test('actual_DOM_ID_requires_one_positive_decimal_in_visible_HTTP_link',async()=>{
 const hrefs=['https://example.invalid/goods?goods_id=123','https://example.invalid/goods?goods_id=0','https://example.invalid/goods?goods_id=1&goods_id=2','javascript:?goods_id=99','https://example.invalid/goods?goods_id=001',null];
 const capture=await fromDOM({cardSpecs:hrefs.map((href,index)=>({top:index*60,href}))});await capture.capture();const snapshot=await capture.snapshot();
 assert.deepEqual(snapshot.rows.map(row=>row.goodsId),['123',null,null,null,null,null]);
 assert(snapshot.rows[0].goodsIdEvidence.includes('Visible goods link'));assert.equal(snapshot.rows[1].goodsUrl,hrefs[1]);
 assert.equal(snapshot.rows[5].goodsUrl,null);assert(snapshot.rows.every(row=>row.observedAtPrecision==='batch_read'));
});
await test('actual_DOM_retains_missing_sales_and_other_labels_without_invention',async()=>{
 const capture=await fromDOM({cardSpecs:[{top:10,tip:'已抢3件'},{top:210,tip:'收藏7人'},{top:410,tip:'已拼10+件'}]});
 await capture.capture();const snapshot=await capture.snapshot();
 assert.deepEqual(snapshot.rows.map(row=>row.salesRaw),['已抢3件',null,'已拼10+件']);assert.equal(snapshot.rows[1].displayTipRaw,'收藏7人');
});

await test('explicit_shop_name_and_stable_source_identity_required',async()=>{
 await assert.rejects(createCapture({},path.join(syntheticRoot,'no-name'),{...options,shopName:undefined}),/explicit shopName/);
 await assert.rejects(createCapture({},path.join(syntheticRoot,'no-key'),{...options,sourceUrl:'https://example.invalid/mall'}),/Stable/);
 for(const sourceUrl of ['https://example.invalid/mall?mall_id=1&mall_id=2','https://example.invalid/mall?mall_id=','https://example.invalid/mall?mall_id=01','https://example.invalid/mall?mall_sn=A&mall_sn=B']){
  await assert.rejects(createCapture({},path.join(syntheticRoot,'ambiguous-'+(++number)),{...options,sourceUrl}));
 }
 assert.equal(shopIdentityFromUrl('https://example.invalid/mall?mall_id=900001&mall_sn=opaque-A').stable_identifier,'900001');
});
await test('same_display_name_different_actual_store_never_adds_cards',async()=>{
 const {capture}=await fresh([page(0,{pageUrl:'https://example.invalid/mall?mall_id=900002',shopVerified:true,endBoundaryObserved:true})]);
 const result=await capture.capture();assert.equal(result.stop,'shop_identity_changed');assert.equal(result.seen,0);
 await assert.rejects(capture.snapshot(),/No observed cards/);
});
await test('mid_scan_store_switch_seals_only_verified_prefix_and_identity_receipt',async()=>{
 const {capture,directory}=await fresh([page(),page(1,{pageUrl:'https://example.invalid/mall?mall_id=900002',shopVerified:true,
  cards:[row(2)],loadedCardCount:3,scrollTop:450,endBoundaryObserved:true,storeSalesRaw:'本店已拼999件'})]);
 await capture.capture();await capture.step();const snapshot=await capture.snapshot();
 assert.equal(snapshot.status,'partial');assert.equal(snapshot.endBoundaryObserved,false);assert.equal(snapshot.rows.length,2);
 assert.equal(snapshot.sourceUrl,options.sourceUrl);assert.equal(snapshot.mallId,'900001');assert.equal(snapshot.storeSalesRaw,'本店已拼123件');
 assert.equal(snapshot.collectionEvidence.stopReason,'shop_identity_changed');
 assert.equal(snapshot.collectionEvidence.driverVersion,4);
 assert.deepEqual(snapshot.collectionEvidence.shopIdentityChecks.map(value=>value.verified),[true,false]);
 assert.equal(snapshot.shopIdentityEvidence.stable_identifier,'900001');
 const raw=JSON.parse(await fs.readFile(path.join(directory,'batch_0002.json'),'utf8'));
 assert.equal(raw.observedShopIdentity.stable_identifier,'900002');assert.equal(raw.added,0);
});
await test('missing_key_duplicate_key_and_changed_origin_stop_each_batch',async()=>{
 for(const [pageUrl,reason] of [[null,'shop_identity_unverified'],['https://example.invalid/mall','shop_identity_unverified'],
  ['https://example.invalid/mall?mall_id=900001&mall_id=900002','shop_identity_unverified'],
  ['https://different.invalid/mall?mall_id=900001','shop_identity_changed']]){
  const {capture}=await fresh([page(),page(1,{pageUrl})]);await capture.capture();await capture.step();
  assert.equal(capture.status().stop,reason);assert.equal((await capture.snapshot()).rows.length,2);
 }
});
await test('opaque_tokens_are_compared_as_opaque_and_not_numeric_aliases',async()=>{
 const sourceUrl='https://example.invalid/mall?mall_sn=opaque-A';
 const valid=await fresh([page(0,{pageUrl:sourceUrl})],undefined,{...options,sourceUrl});await valid.capture.capture();
 assert.equal((await valid.capture.snapshot()).shopIdentityEvidence.identity_kind,'mall_sn');
 const wrong=await fresh([page(0,{pageUrl:'https://example.invalid/mall?mall_sn=900001'})]);await wrong.capture.capture();
 assert.equal(wrong.capture.status().stop,'shop_identity_changed');
});
await test('actual_DOM_location_not_caller_label_is_checked',async()=>{
 const capture=await fromDOM({cardSpecs:[{top:100}],pageUrl:'https://example.invalid/mall?mall_id=900002'});
 await capture.capture();assert.equal(capture.status().stop,'shop_identity_changed');
 await assert.rejects(capture.snapshot(),/No observed cards/);
});

// Private historical-store replay is omitted from the public source distribution.

await test('end_boundary_with_unobserved_column_slot_stays_partial_and_records_gap',async()=>{
 const left=row(0),right=row(2,{domColumn:1,domRow:0,position:{top:0,left:250}});
 const {capture}=await fresh([page(0,{cards:[left,right],loadedCardCount:4,columnCounts:[2,2]}),
  page(1,{cards:[row(1)],loadedCardCount:4,columnCounts:[2,2],scrollTop:100,endBoundaryObserved:true})]);
 await capture.capture();await capture.step();
 assert.equal(capture.status().stop,'coverage_incomplete_at_boundary');assert.equal(capture.status().ended,false);
 const snapshot=await capture.snapshot();assert.equal(snapshot.status,'partial');assert.equal(snapshot.endBoundaryObserved,false);
 assert.equal(snapshot.rows.length,3); // Valid visible cards survive; missing slot is never fabricated.
});

await test('same_title_and_image_four_column_slots_remain_four_original_cards',async()=>{
 const cards=[row(0),row(1),row(2,{domColumn:1,domRow:0,position:{top:0,left:250}}),row(3,{domColumn:1,domRow:1,position:{top:200,left:250}})];
 const {capture}=await fresh([page(0,{cards,loadedCardCount:4,columnCounts:[2,2],endBoundaryObserved:true})]);
 await capture.capture();const snapshot=await capture.snapshot();
 assert.equal(snapshot.rows.length,4);assert.equal(new Set(snapshot.rows.map(row=>row.title)).size,1);
 assert.equal(new Set(snapshot.rows.map(row=>row.imageUrl)).size,1);assert.equal(new Set(snapshot.rows.map(row=>row.recordKey)).size,4);
 assert.equal(snapshot.status,'complete');
});

await test('global_index_is_diagnostic_but_column_local_position_remains_guarded',async()=>{
 const {capture}=await fresh([page(),page(1,{cards:[row(0,{domIndex:900}),row(1,{domIndex:901})]})]);
 await capture.capture();await capture.step();assert.equal(capture.status().stop,null);assert.equal(capture.status().cards,2);
 assert.deepEqual((await capture.snapshot()).rows.map(row=>row.domIndex),[0,1]);
});

await test('column_count_change_or_shrinking_column_stops_without_accepting_new_cards',async()=>{
 for(const columnCounts of [[1,1],[1,3]]){
  const {capture}=await fresh([page(0,{columnCounts:[2,2],loadedCardCount:4}),page(1,{columnCounts,loadedCardCount:4,cards:[],endBoundaryObserved:true})]);
  await capture.capture();await capture.step();assert.equal(capture.status().stop,'column_list_replaced_or_virtualized');
  assert.equal((await capture.snapshot()).status,'partial');assert.equal(capture.status().cards,2);
 }
 const changed=await fresh([page(),page(1,{columnCounts:[2,1],loadedCardCount:3,cards:[]})]);
 await changed.capture.capture();await changed.capture.step();assert.equal(changed.capture.status().stop,'column_list_replaced_or_virtualized');
});

await test('unverified_layout_or_invalid_or_duplicate_slot_cannot_add_cards',async()=>{
 for(const [override,reason] of [
  [{columnLayoutVerified:false},'unrecognized_column_layout'],
  [{cards:[row(0,{domColumn:-1})]},'invalid_or_duplicate_column_slot'],
  [{cards:[row(0,{domRow:2})]},'invalid_or_duplicate_column_slot'],
  [{cards:[row(0),row(1,{domRow:0})]},'invalid_or_duplicate_column_slot'],
  [{cards:[row(0,{domRow:0.5})]},'invalid_or_duplicate_column_slot']
 ]){
  const {capture}=await fresh([page(0,override)]);await capture.capture();
  assert.equal(capture.status().stop,reason);assert.equal(capture.status().cards,0);
  await assert.rejects(capture.snapshot(),/No observed cards/);
 }
});

await test('normal_scroll_error_is_terminal_and_preserves_prefix',async()=>{
 const {capture,tab}=await fresh([page(),page(1)],{scroll:true});await capture.capture();
 await assert.rejects(capture.step(),/scroll failure/);assert.equal(capture.status().stop,'scroll_failed');
 await capture.step();assert.equal(tab.calls.scroll.length,1);assert.equal(tab.calls.evaluate,1);
 assert.equal((await capture.snapshot()).rows.length,2);assert.equal((await capture.snapshot()).status,'partial');
});

await test('actual_DOM_callback_extracts_column_local_slots_and_column_major_indices',async()=>{
 const capture=await fromDOM({cardSpecs:[{top:100,column:0},{top:300,column:0},{top:100,column:1},{top:300,column:1}],boundarySpecs:[{top:500}]});
 await capture.capture();const snapshot=await capture.snapshot();
 assert.equal(snapshot.status,'complete');assert.equal(snapshot.rows.length,4);
 assert.deepEqual(snapshot.rows.map(row=>[row.domColumn,row.domRow,row.domIndex]),[[0,0,0],[1,0,2],[0,1,1],[1,1,3]]);
 assert(snapshot.rows.every(row=>row.goodsId===null));
});

await test('actual_DOM_callback_append_changes_global_indices_but_completes_all_slots',async()=>{
 const makeSpecs=(count,scrollTop)=>Array.from({length:count*2},(_,index)=>({column:index<count?0:1,top:(index%count)*200-scrollTop}));
 const dom0=domTab({cardSpecs:makeSpecs(2,0)});
 const dom1=domTab({cardSpecs:makeSpecs(4,300),scrollTop:300,listTop:-300,boundarySpecs:[{top:500}]});
 const tab=mockTab([]);let reads=0;
 tab.playwright.evaluate=(fn,arg)=>[dom0,dom1][reads++].playwright.evaluate(fn,arg);
 const capture=await createCapture(tab,path.join(syntheticRoot,'ACTUAL_DOM_APPEND'),options);
 await capture.capture();assert.equal(capture.status().cards,4);
 const next=await capture.step();assert.equal(next.added,4);assert.equal(next.stop,null);
 const snapshot=await capture.snapshot();assert.equal(snapshot.rows.length,8);assert.equal(snapshot.status,'complete');
 assert.equal(snapshot.rows.find(row=>row.domColumn===1&&row.domRow===1).domIndex,3);
 assert.equal(snapshot.rows.find(row=>row.domColumn===1&&row.domRow===2).domIndex,6);
 assert.equal(new Set(snapshot.rows.map(row=>`${row.domColumn}:${row.domRow}`)).size,8);
});

await test('actual_DOM_nested_cards_or_noncard_column_children_are_not_silently_accepted',async()=>{
 for(const flags of [{nestedCards:true},{extraChild:true}]){
  const capture=await fromDOM({cardSpecs:[{top:100},{top:300}],...flags,boundarySpecs:[{top:500}]});
  await capture.capture();assert.equal(capture.status().stop,'unrecognized_column_layout');
  assert.equal(capture.status().cards,0);await assert.rejects(capture.snapshot(),/No observed cards/);
 }
});

const driverPath=path.join(root,'live_capture_driver.mjs');
const result={checked_at:new Date().toISOString(),mode:'Node mocks and actual DOM callback in vm; no real browser/network/database',
 driver_path:driverPath,driver_sha256:crypto.createHash('sha256').update(await fs.readFile(driverPath)).digest('hex'),
 checks,passed:checks.filter(row=>row.ok).length,failed:checks.filter(row=>!row.ok).length,ok:checks.every(row=>row.ok),
 synthetic_fixture_directory:syntheticRoot,
 usage_limits:[
  'The operator must verify the actual shop, active 上新 tab, top-of-page start and selector compatibility before this driver is used.',
  'Class selectors remain website-specific. Mocks do not prove the current website end boundary, virtual-list behavior or actual browser control compatibility.',
  'This driver reads only the observed DOM. It cannot obtain IDs absent from visible links and never searches hidden state or fetches URLs.',
  'Global traversal index changes alone may continue only with stable column-local slots and positions. Reflow, column replacement, changed viewport or missed coverage still stop as partial.',
  'A read/scroll/checkpoint error ends this attempt. Recovery belongs to the caller; start a fresh attempt directory and do not merge times from a new page load.',
  'Checkpoints preserve raw batches; uncertain batches are not appended to snapshot cards. No completeness claim follows from lack of new cards.',
  'No images are downloaded and no database writes, schedules, browser recovery or live collection were verified.'
 ]};
await fs.writeFile(path.join(output,'live_capture_review.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({ok:result.ok,passed:result.passed,failed:result.failed,failures:checks.filter(row=>!row.ok)},null,2));
if(!result.ok)process.exitCode=1;
