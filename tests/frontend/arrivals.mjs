// Portable synthetic model assertions; historical production counts are intentionally outside this suite.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {scopedArrivals,filterArrivals,arrivalQueueState,arrivalObservationRows,arrivalScope,arrivalTimeEvidence,latestArrivalRows,latestArrivalMode,firstArrivalRows} from '../../dashboard/src/content/dashboard/new-arrivals-model.js';

// Fixtures are isolated model inputs, never written into a reviewed snapshot.
const checks=[];
function check(name,fn){fn();checks.push({name,status:'passed'});}
const summary={tracking_id:'origin',state:'candidates_available',baseline_run_ids:['old-a','old-b'],post_baseline_run_count:2};
const first={arrival_item_id:'a',tracking_id:'origin',run_id:'new-a',observation_id:'o1',title:'同标题卡片',view_order:11,goods_id:'123',discovery_kind:'first_observed_id_candidate',discovery_label:'ID 首次观察候选',first_observed_at:'2026-10-05T00:00:10Z',first_observed_at_precision:'second',first_seen_time_basis:'recorded_read_time',first_observation_window:{from:'2026-10-05T00:00:00Z',to:'2026-10-05T00:01:00Z'},possible_since:null,possible_until:'2026-10-05T00:00:10Z',latest_observation_ids:['o3'],latest_reference_basis:'same_unique_goods_id',subsequent_references:[{observation_id:'o3'}]};
const second={...first,arrival_item_id:'b',observation_id:'o2',view_order:12,goods_id:null,discovery_kind:'new_card_clue',latest_observation_ids:['o4','o5'],latest_reference_basis:'exact_title_original_image_url_clue_only',historical_references:[{observation_id:'old'}],subsequent_references:[{observation_id:'o4'},{observation_id:'o5'}]};
const obs=[{observation_id:'old',title:'旧卡',view_order:1,sales_value:99},{observation_id:'o1',title:first.title,view_order:11,sales_value:1,sales_raw:'已拼1件'},{observation_id:'o2',title:first.title,view_order:12,sales_value:null},{observation_id:'o3',title:first.title,view_order:1,sales_value:12,sales_raw:'已拼12件'},{observation_id:'o4',title:first.title,view_order:7,sales_value:3},{observation_id:'o5',title:first.title,view_order:8,sales_value:4},{observation_id:'unrelated',view_order:9,sales_value:500}];
const byId=new Map(obs.map(row=>[row.observation_id,row]));
check('empty lifecycle states do not claim no new products',()=>{
 assert.equal(arrivalQueueState(undefined,0,0),'not_loaded');
 assert.equal(arrivalQueueState({state:'not_configured'},0,0),'not_configured');
 assert.equal(arrivalQueueState({...summary,state:'no_post_baseline_run',post_baseline_run_count:0},0,0),'waiting');
 assert.equal(arrivalQueueState(summary,0,0),'no_candidates');
 assert.equal(arrivalQueueState(summary,2,0),'no_matches');
});
check('fixed baseline and foreign tracking exclude old inventory, equal titles remain separate',()=>{
 const rows=scopedArrivals([first,second,{...first,run_id:'old-a'},{...first,tracking_id:'other'}],summary);
 assert.deepEqual(rows.map(row=>row.arrival_item_id),['a','b']);
 assert.deepEqual(scopedArrivals([first],{state:'not_configured'}),[]);
});
check('queue sorts recent first-window ends first with deterministic ID fallback and never mutates input',()=>{
 const rows=[
  {...first,arrival_item_id:'z',first_observation_window:{from:'2026-10-05T00:00:00Z',to:'2026-10-05T00:01:00Z'}},
  {...first,arrival_item_id:'b',first_observation_window:{from:'2026-10-06T00:00:00Z',to:'2026-10-06T00:01:00Z'}},
  {...first,arrival_item_id:'unknown',first_observation_window:{to:'invalid'}},
  {...first,arrival_item_id:'a',first_observation_window:{from:'2026-10-05T23:59:00Z',to:'2026-10-06T00:01:00Z'},first_time_order_status:'overlapping_runs_unknown'}
 ];
 const before=JSON.stringify(rows),ordered=modelSort(rows);
 assert.deepEqual(ordered.map(row=>row.arrival_item_id),['a','b','z','unknown']);
 assert.equal(JSON.stringify(rows),before);assert.notEqual(ordered,rows);
 assert.equal(ordered[0],rows[3]);assert.equal(ordered[0].first_observed_at,first.first_observed_at);
 assert.equal(ordered[0].first_time_order_status,'overlapping_runs_unknown');
 function modelSort(input){return scopedArrivals(input,summary);}
});
check('type and literal search match source scope and do not sum cards',()=>{
 const filters={kind:'new_card_clue',search:'#12'};
 assert.deepEqual(filterArrivals([first,second],filters),[second]);
 const scope=arrivalScope(summary,filters);
 assert.equal(scope[0].value,'origin');assert.equal(scope[1].field,'discovery_kind');assert.equal(scope[2].value,'#12');
 assert.equal(filterArrivals([first,second],{kind:'all',search:first.title}).length,2);
 assert.equal(filterArrivals([first,second],{kind:'all',search:'123'})[0],first);
});
check('first anchor stays at one while latest stable ID raw card is twelve',()=>{
 const rows=latestArrivalRows(first,byId);
 assert.equal(rows.length,1);assert.equal(rows[0].sales_value,12);
 assert.equal(byId.get(first.observation_id).sales_value,1);
 assert.equal(latestArrivalMode(first,rows),'single_id');
 assert.equal(first.first_observed_at,'2026-10-05T00:00:10Z');
 assert.equal(scopedArrivals([first],summary).length,1);
 assert.ok(!Object.hasOwn(first,'sales_delta'));
});
check('weak latest multi-card references keep both original cards and never pick or aggregate',()=>{
 const rows=latestArrivalRows(second,byId);
 assert.deepEqual(rows.map(row=>[row.observation_id,row.sales_value]),[['o4',3],['o5',4]]);
 assert.equal(latestArrivalMode(second,rows),'multiple');
 assert.equal(latestArrivalMode({...first,latest_reference_basis:'exact_title_original_image_url_clue_only'},[obs[3]]),'single_clue');
 assert.equal(latestArrivalMode(first,[obs[1]]),'anchor_only');
 assert.equal(latestArrivalMode(first,[]),'missing');
});
check('source rows include actual filtered anchor plus latest/historical/subsequent references only',()=>{
 assert.deepEqual(arrivalObservationRows([second],obs).map(row=>row.observation_id),['old','o2','o4','o5']);
 assert.deepEqual(arrivalObservationRows([first],obs).map(row=>row.observation_id),['o1','o3']);
});
check('unknown lower time remains unknown, time precision and first observed anchor retained',()=>{
 const time=arrivalTimeEvidence(first);
 assert.equal(time.kind,'upper_only');assert.equal(time.since,null);assert.equal(time.first_observed_at,first.first_observed_at);
 assert.equal(time.precision,'second');assert.equal(time.listing_time_confirmed,false);
 const window=arrivalTimeEvidence({...first,first_seen_time_basis:'observation_window'});
 assert.equal(window.first_observed_at,null);assert.equal(window.window_to,first.first_observation_window.to);
});
check('bounded interval remains only reference; reversed or invalid evidence never forms a period',()=>{
 const good=arrivalTimeEvidence({...first,possible_since:'2026-10-04T00:00:00Z'});
 assert.equal(good.kind,'bounded_reference');assert.equal(good.listing_time_confirmed,false);
 const bad=arrivalTimeEvidence({...first,possible_since:'2026-10-06T00:00:00Z'});
 assert.equal(bad.kind,'upper_only');assert.equal(bad.since,null);
 assert.equal(arrivalTimeEvidence({possible_until:'not a date'}).until,null);
});
check('overlapping windows never assert unique first or latest and retain all possible first source cards',()=>{
 const item={...first,first_time_order_status:'overlapping_runs_unknown',latest_time_order_status:'overlapping_runs_unknown',first_observation_id_candidates:['o1','o2'],possible_since:'2026-10-04T00:00:00Z'};
 const time=arrivalTimeEvidence(item);
 assert.equal(time.order_status,'overlapping_runs_unknown');assert.equal(time.latest_order_status,'overlapping_runs_unknown');
 assert.equal(time.first_observed_at,null);assert.equal(time.since,null);assert.equal(time.kind,'upper_only');
 assert.deepEqual(firstArrivalRows(item,byId).map(row=>row.observation_id),['o1','o2']);
 assert.deepEqual(arrivalObservationRows([item],obs).map(row=>row.observation_id),['o1','o2','o3']);
 assert.equal(latestArrivalMode(item,latestArrivalRows(item,byId)),'overlapping');
});
check('numeric observation IDs and serialized references join without mutating original ID types',()=>{
 const numericRows=[{observation_id:1,view_order:1},{observation_id:2,view_order:2}];
 const numericById=new Map(numericRows.map(row=>[String(row.observation_id),row]));
 const item={observation_id:'1',first_observation_id_candidates:[1,'2'],latest_observation_ids:[2,'2']};
 assert.deepEqual(arrivalObservationRows([item],numericRows).map(row=>row.observation_id),[1,2]);
 assert.deepEqual(firstArrivalRows(item,numericById),numericRows);
 assert.deepEqual(latestArrivalRows(item,numericById),[numericRows[1]]);
 assert.equal(typeof latestArrivalRows(item,numericById)[0].observation_id,'number');
});

export const acceptance={suite:"arrivals",passed:checks.length,total:checks.length,checks:checks};
