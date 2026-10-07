import test from 'node:test';
import assert from 'node:assert/strict';
import {warehouseScope,warehouseCards,trendRange,warehouseSource,catalogueDay,monitoringCards,monitoringCohort,lifecycleSummary} from '../src/content/dashboard/warehouse-model.js';
test('daily warehouse has explicit shop and date scope',()=>{
 const queries={warehouse_days:{rows:[{shop_id:'A',date:'2026-10-04'},{shop_id:'B',date:'2026-10-05'}]},warehouse_records:{rows:[{shop_id:'A',date:'2026-10-04',title:'toy',observation_id:1},{shop_id:'B',date:'2026-10-05',title:'toy'}]}};
 const data=warehouseScope(queries,'A');assert.equal(data.days.length,1);assert.equal(data.records.length,1);
 assert.equal(warehouseCards(data.records,'2026-10-05').length,0);assert.equal(warehouseCards(data.records,'2026-10-04','toy').length,1);
 assert.equal(warehouseScope(queries,'unknown').records.length,0);
});
test('30 days moves an observed candidate to reference and retains its identity and history',()=>{
 const record={track_id:'verified',date:'2026-10-04',monitor_origin:'first_observed_candidate',monitor_first_date:'2026-10-04'};
 assert.equal(monitoringCohort(record,'2026-11-02').cohort,'new_observation');
 assert.equal(monitoringCohort(record,'2026-11-03').cohort,'old_reference');
 assert.equal(monitoringCohort(record,'2026-11-03').retired_candidate,true);
 assert.equal(monitoringCohort({...record,monitor_first_date:null},'2026-11-03').cohort,'pending');
 assert.equal(monitoringCards([record],'2026-11-03','old_reference')[0].track_id,'verified');
});
test('partial latest day keeps the earlier complete store catalogue while history retains current partial',()=>{
 const days=[{date:'2026-10-05',is_complete:false,card_count:20},{date:'2026-10-04',is_complete:true,card_count:489}];
 assert.equal(catalogueDay(days,'2026-10-05').card_count,489);
 assert.equal(catalogueDay(days,'2026-10-03'),undefined);
 assert.equal(catalogueDay([days[0]],'2026-10-05').is_complete,false);
});
test('focus includes earlier watched items, isolates weak cards, and selects canonical same-day data',()=>{
 const first={track_id:'id1',date:'2026-10-04',observation_id:1,monitor_origin:'first_observed_candidate',monitor_first_date:'2026-10-04'};
 const later={...first,date:'2026-10-05',observation_id:2};
 const archived={...later,observation_id:3,archived_anchor:true};
 const weak={...first,track_id:'weak',observation_id:4};
 const rows=monitoringCards([first,later,weak,archived],'2026-10-05','new_observation');
 assert.equal(rows.length,2);assert.equal(rows.find(r=>r.track_id==='id1').observation_id,2);
 assert.equal(monitoringCards([first,later],'2026-10-04')[0].observation_id,1);
});
test('all-time timeline retains an old launch period and does not label sparse data as a lifecycle stage',()=>{
 const points=[{track_id:'a',date:'2025-01-02',daily_delta:10,delta_status:'comparable'},{track_id:'a',date:'2026-10-05',daily_delta:null,delta_status:'negative_anomaly'}];
 assert.equal(trendRange(points,'a','2026-10-05','all').source.length,2);
 const result=lifecycleSummary(points);assert.equal(result.peak_delta,10);assert.equal(result.comparable_days,1);assert.equal(result.status,'数据积累中');
});
test('missing dates are null and range never includes future or another track',()=>{
 const points=[{track_id:'a',date:'2026-10-03',cumulative_yipin:10},{track_id:'a',date:'2026-10-05',cumulative_yipin:20},{track_id:'a',date:'2026-10-06',cumulative_yipin:25},{track_id:'b',date:'2026-10-05',cumulative_yipin:50}];
 const range=trendRange(points,'a','2026-10-05',30);
 assert.equal(range.source.length,2);assert.equal(range.plot.length,30);
 assert.equal(range.plot.find(p=>p.date==='2026-10-04').cumulative_yipin,null);
 assert.equal(range.plot.at(-1).cumulative_yipin,20);
 assert.equal(trendRange(points,'a','2026-10-05',99999).plot.length,30);
});
test('source inspection uses only current component rows and filters',()=>{
 const queries={warehouse_points:{rows:[{shop_id:'A'},{shop_id:'B'}]},observations:{rows:[1,2]}};
 const component={queryId:'warehouse_points',queryIds:['warehouse_points','observations'],sourceRows:[{shop_id:'A'}],sourceRowsByQuery:{observations:[1]},scopeFilters:[{field:'shop_id',value:'A'}]};
 assert.deepEqual(warehouseSource(queries,component).rows,[{shop_id:'A'}]);
 assert.deepEqual(warehouseSource(queries,component,'observations').rows,[1]);
 assert.equal(warehouseSource(queries,component,'unknown'),null);
});
