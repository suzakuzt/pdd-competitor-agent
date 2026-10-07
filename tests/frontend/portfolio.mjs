// Portable synthetic model assertions; historical production counts are intentionally outside this suite.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {scopeShopData,availableShops,applyDrilldown,drilldownProducts,makeDrilldown,makeShopRequest,shopScope,dimensionLabel} from '../../dashboard/src/content/dashboard/portfolio-model.js';
import {EMPTY_FILTERS,filterCards,eligible,chooseDefaultRun} from '../../dashboard/src/content/dashboard/pdd-model.js';
import {arrivalQueueState,scopedArrivals} from '../../dashboard/src/content/dashboard/new-arrivals-model.js';
const base=path.dirname(fileURLToPath(import.meta.url)),stage=path.join(base,'portfolio_ui'),content=path.join(stage,'src/content/dashboard');
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const checks=[];function check(name,fn){fn();checks.push({name,status:'passed'});}
const query=rows=>({rows});
const fixtures={runs:query([{shop_id:'a',shop_name:'同名店铺',run_id:'a-old',status:'complete',end_boundary_observed:true,observed_to_epoch:1},{shop_id:'a',shop_name:'同名店铺',run_id:'a-new',status:'partial',end_boundary_observed:false,observed_to_epoch:3},{shop_id:'b',shop_name:'同名店铺',run_id:'b-old',status:'complete',end_boundary_observed:true,observed_to_epoch:2}]),observations:query([{observation_id:1,run_id:'a-old',title:'同标题',sales_value:12,sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display'},{observation_id:2,run_id:'a-old',title:'同标题',sales_value:1,sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display'},{observation_id:3,run_id:'a-old',title:'同标题',sales_value:null,sales_precision:'missing'},{observation_id:4,run_id:'a-new'},{observation_id:5,run_id:'b-old'}]),run_history:query([{run_id:'a-old'},{run_id:'a-new'},{run_id:'b-old'}]),comparison_summaries:query([{comparison_id:'ca',target_run_id:'a-new',baseline_run_id:'a-old'},{comparison_id:'cb',target_run_id:'b-old',baseline_run_id:null},{comparison_id:'cross-shop-invalid',target_run_id:'a-new',baseline_run_id:'b-old'}]),comparison_items:query([{comparison_id:'ca',comparison_item_id:'i-a'},{comparison_id:'cb',comparison_item_id:'i-b'},{comparison_id:'cross-shop-invalid',comparison_item_id:'invalid'}]),new_arrival_summary:query([{shop_id:'a',tracking_id:'fixed-a',state:'no_post_baseline_run',baseline_run_ids:['a-old','a-new'],post_baseline_run_count:0},{shop_id:'b',tracking_id:null,state:'not_configured'}]),new_arrival_items:query([{shop_id:'a',tracking_id:'fixed-a',run_id:'a-new'},{shop_id:'b',run_id:'b-old'}]),collection_attempts:query([{attempt_id:'a',run_id:'a-new'},{attempt_id:'b',run_id:'b-old'},{attempt_id:'explicit-a',shop_id:'a',run_id:null},{attempt_id:'unassigned-global',run_id:null}])};
check('same-named shops remain independent by shop_id',()=>{
 assert.equal(availableShops(fixtures).length,2);
 assert.deepEqual(scopeShopData(fixtures,'a').runs.map(row=>row.run_id),['a-old','a-new']);
 assert.deepEqual(scopeShopData(fixtures,'b').observations.map(row=>row.observation_id),[5]);
 assert.equal(scopeShopData(fixtures,'unknown').observations.length,0);
});
check('reference complete fallback is evaluated inside each shop',()=>{
 assert.equal(chooseDefaultRun(scopeShopData(fixtures,'a').runs).run_id,'a-old');
 assert.equal(chooseDefaultRun(scopeShopData(fixtures,'b').runs).run_id,'b-old');
});
check('comparison target and baseline both belong to selected shop',()=>{
 const scope=scopeShopData(fixtures,'a');
 assert.deepEqual(scope.histories.map(row=>row.run_id),['a-old','a-new']);
 assert.deepEqual(scope.summaries.map(row=>row.comparison_id),['ca']);
 assert.deepEqual(scope.items.map(row=>row.comparison_item_id),['i-a']);
});
check('fixed origin and unconfigured shop are selected separately',()=>{
 const a=scopeShopData(fixtures,'a'),b=scopeShopData(fixtures,'b');
 assert.equal(arrivalQueueState(a.arrivalSummary,0,0),'waiting');
 assert.equal(arrivalQueueState(b.arrivalSummary,0,0),'not_configured');
 assert.equal(scopedArrivals(a.arrivalItems,a.arrivalSummary).length,0);
 assert.equal(scopedArrivals(b.arrivalItems,b.arrivalSummary).length,0);
});
check('attempts only include selected shop while unknown/global unassigned records are not misattributed',()=>{
 assert.deepEqual(scopeShopData(fixtures,'a').attempts.map(row=>row.attempt_id),['a','explicit-a']);
 assert.deepEqual(scopeShopData(fixtures,'b').attempts.map(row=>row.attempt_id),['b']);
});
check('dimension drilldown includes low and missing observations with exact shop/run/ID population',()=>{
 const dimension={dimension_id:'dim',shop_id:'a',run_id:'a-old',observation_ids:[1,'2',3]};
 const drill=makeDrilldown(dimension,'全部同标题线索','competitor_dimensions');
 const products=fixtures.observations.rows.map(row=>({...row,shop_id:row.run_id==='b-old'?'b':'a'}));
 assert.equal(drilldownProducts(products,drill).length,3);
 const runRows=scopeShopData(fixtures,'a').observations.filter(row=>row.run_id==='a-old');
 const matching=applyDrilldown(filterCards(runRows,EMPTY_FILTERS,new Map()),drill,'a','a-old');
 assert.deepEqual(matching.map(row=>row.observation_id),[1,2,3]);
 assert.equal(matching.filter(eligible).length,1);
 assert.equal(matching[2].sales_value,null);
});
check('drilldown copies ID array, never merges same titles, and selected scope preserves identity',()=>{
 const dimension={dimension_id:'dim',shop_id:'a',run_id:'a-old',observation_ids:[1,2,3]};
 const before=JSON.stringify(dimension),drill=makeDrilldown(dimension,'标题线索','competitor_dimensions');
 assert.notEqual(drill.observation_ids,dimension.observation_ids);
 assert.equal(JSON.stringify(dimension),before);assert.equal(shopScope(drill.shop_id)[0].value,'a');
});
check('new shop request is only plain text with explicit unregistered/uncaptured status',()=>{
 assert.equal(makeShopRequest('  ',' '),'');
 const text=makeShopRequest('同名店铺','https://example.test/?q=<script>');
 assert.ok(text.includes('尚未登记、尚未采集'));assert.ok(text.includes('先核实店铺身份'));
 assert.ok(!text.includes('competitor-add'));assert.ok(!text.includes('powershell'));
});
check('human dimension labels distinguish equal price bands with different display conditions',()=>{
 const row={dimension_key:'display_price_band',dimension_value:'3_5',label:'¥3.00–4.99'};
 const coupon=dimensionLabel({...row,qualifiers:{price_condition:'coupon_after_display'}}),unspecified=dimensionLabel({...row,qualifiers:{price_condition:'display_unspecified'}});
 assert.notEqual(coupon,unspecified);assert.ok(coupon.includes('券后展示'));assert.ok(unspecified.includes('未注明券条件'));
 assert.equal(dimensionLabel({dimension_key:'sales_bucket',dimension_value:'yipin_eq10',label:'yipin_eq10'}),'销量 =10 件');
});

export const acceptance={suite:"portfolio",passed:checks.length,total:checks.length,checks:checks};
