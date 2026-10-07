// Portable synthetic model assertions; historical production counts are intentionally outside this suite.
import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
import {historyRows,selectComparison,filterChanges,deltaLabel,comparisonObservations,priceChange,makeHistoryExport,collectionReasonLabel,timeBasisLabel} from '../../dashboard/src/content/dashboard/history-model.js';

const evidence=[];
function check(name,fn){fn();evidence.push({name,passed:true});}
const summaries=[{comparison_id:'previous',target_run_id:'new',baseline_run_id:'old',mode:'previous'},{comparison_id:'yesterday',target_run_id:'new',baseline_run_id:null,mode:'yesterday_slot',baseline_status:'missing_baseline'}];
check('missing yesterday is retained without previous fallback',()=>{assert.equal(selectComparison(summaries,'new','yesterday_slot').baseline_run_id,null);assert.equal(selectComparison(summaries,'absent','previous'),null);});
const items=[
 {comparison_item_id:'unknown',status:'unknown',event:'unknown',title:'相同标题',goods_id:null,display_delta:null,old_observation_id:null,new_observation_id:1,reason:'missing_goods_id'},
 {comparison_item_id:'zero',status:'comparable',event:'unchanged',title:'相同标题',goods_id:'21',display_delta:0,old_observation_id:2,new_observation_id:3,price_raw_changed:true},
 {comparison_item_id:'increase',status:'comparable',event:'crossed_gt10',title:'跨过',goods_id:'22',display_delta:2,crossed_gt10:true,old_observation_id:4,new_observation_id:5,price_raw_changed:false},
 {comparison_item_id:'negative',status:'anomaly',event:'negative_display_delta',title:'回落',goods_id:'23',display_delta:-3,old_observation_id:6,new_observation_id:7},
 {comparison_item_id:'candidate',status:'candidate',event:'first_observed_candidate',title:'首见',goods_id:'24',display_delta:null,first_observed_candidate:true,old_observation_id:null,new_observation_id:8}
];
check('unknown is incomparable and exact zero stays zero',()=>{assert.equal(deltaLabel(items[0]),'不可比');assert.equal(deltaLabel(items[1]),'0');assert.equal(deltaLabel(items[3]),'-3 · 异常');assert.equal(deltaLabel({...items[1],status:'unknown'}),'不可比');});
check('positive filter contains crossing but excludes negative, unknown and candidate',()=>{assert.deepEqual(filterChanges(items,{category:'increased'}).map(row=>row.comparison_item_id),['increase']);assert.deepEqual(filterChanges(items,{category:'crossed'}).map(row=>row.comparison_item_id),['increase']);assert.equal(filterChanges(items,{category:'candidate'}).length,1);assert.equal(filterChanges(items,{category:'unknown'}).length,1);});
check('same title rows remain independent and Chinese reasons searchable',()=>{assert.equal(filterChanges(items,{search:'相同标题'}).length,2);assert.equal(filterChanges(items,{search:'商品 ID 缺失'}).length,1);});
const observations=Array.from({length:8},(_,index)=>({observation_id:index+1,title:`原卡${index+1}`,run_id:index%2?'old':'new',price_raw:index===1?'¥3':'¥4',asset_sha256:'same-image'}));
check('referenced observations preserve cards and dedupe only repeated observation references',()=>{assert.equal(comparisonObservations(items,observations).length,8);assert.equal(comparisonObservations([items[1],items[1]],observations).length,2);});
check('raw price comparison keeps unknown and unpaired distinct',()=>{const byId=new Map(observations.map(row=>[row.observation_id,row]));assert.equal(priceChange(items[0],byId).status,'unpaired');assert.equal(priceChange(items[1],byId).status,'different_raw');assert.equal(priceChange(items[2],byId).status,'same_raw');assert.equal(priceChange({...items[1],price_raw_changed:null},byId).status,'unknown');assert.equal(filterChanges(items,{category:'price'}).length,1);});
check('export all filtered items with original metadata and no image assets',()=>{const filtered=filterChanges(items,{search:'相同标题'}),output=makeHistoryExport(summaries[0],filtered,{search:'相同标题',category:'all'},observations);assert.equal(output.item_count,2);assert.equal(output.observations.length,3);assert.equal(output.items[0].display_delta,null);assert.equal(output.items[1].price_raw_comparison.status,'different_raw');assert.equal('image_assets' in output,false);assert.equal(JSON.stringify(output).includes('data:image'),false);});
check('history newest first without merging equal-day rounds',()=>{const rows=historyRows([{run_id:'old',observed_to:'2026-10-04T03:00:00Z'},{run_id:'new',observed_to:'2026-10-04T05:00:00Z'}]);assert.deepEqual(rows.map(row=>row.run_id),['new','old']);});
check('collection reason codes render Chinese while unfamiliar codes stay in details',()=>{assert.equal(collectionReasonLabel('scheduler_connection_not_verified_for_this_slot'),'本时段的定时执行连接尚未核实');assert.equal(collectionReasonLabel('no_start_record_within_window'),'允许开始时段内没有执行记录');assert.equal(collectionReasonLabel('future_internal_code'),'已记录执行说明，展开查看原文');assert.equal(collectionReasonLabel('历史手动采集'),'历史手动采集');});
check('recorded and window time bases remain distinct with Chinese labels',()=>{assert.equal(timeBasisLabel('recorded_read_time'),'实际读取时刻');assert.equal(timeBasisLabel('observation_window'),'观察窗口范围');assert.equal(timeBasisLabel(null),'未知');});

export const acceptance={suite:"history",passed:evidence.length,total:evidence.length,checks:evidence};
