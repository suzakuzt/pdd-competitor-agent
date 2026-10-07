import test from 'node:test';
import assert from 'node:assert/strict';
import {displayHistoryRange,displayHistoryStatus} from '../src/content/dashboard/warehouse-display-history-model.js';
const selected={shop_id:'A',observation_id:50,run_id:'current'};
const row=(date,value,extra={})=>({shop_id:'A',anchor_observation_id:50,anchor_run_id:'current',date,cumulative_yipin:value,observation_id:Number(date.slice(-2)),point_status:'matched',match_basis:'provisional_title_image',...extra});
test('history scopes one current original card, current run and shop through the requested date',()=>{
 const rows=[row('2026-09-01',1),row('2026-10-05',9),row('2026-10-06',10),row('2026-10-05',100,{shop_id:'B'}),row('2026-10-04',200,{anchor_observation_id:51}),row('2026-10-03',300,{anchor_run_id:'another'})];
 const all=displayHistoryRange(rows,selected,'2026-10-05');assert.equal(all.source.length,2);assert.equal(all.startDate,'2026-09-01');assert.equal(all.plot.length,35);assert.equal(all.hasClues,true);
 const recent=displayHistoryRange(rows,selected,'2026-10-05',30);assert.equal(recent.source.length,1);assert.equal(recent.plot.length,30);
});
test('missing dates and uncertain recorded matches remain null, never zero or carried forward',()=>{
 const rows=[row('2026-10-01',1),row('2026-10-03',null,{point_status:'ambiguous_identity',observation_id:null}),row('2026-10-05',8)];
 const result=displayHistoryRange(rows,selected,'2026-10-05');assert.deepEqual(result.plot.map(p=>p.cumulative_yipin),[1,null,null,null,8]);assert.equal(result.source.length,3);assert.deepEqual(result.observationIds,[1,5]);
});
test('break-before inserts a null display separator without fabricating an observation or altering the evidence',()=>{
 const first=row('2026-10-04',5),second=row('2026-10-05',8,{break_before:true,baseline_observation_id:4});const result=displayHistoryRange([first,second],selected,'2026-10-05');
 assert.equal(result.source.length,2);assert.equal(result.plot.length,3);assert.equal(result.plot[1].comparison_break,true);assert.equal(result.plot[1].cumulative_yipin,null);assert.equal('observation_id' in result.plot[1],false);assert.equal(second.cumulative_yipin,8);
});
test('zero and negative cumulative changes stay observed values while invalid status remains explicit',()=>{
 const result=displayHistoryRange([row('2026-10-04',10),row('2026-10-05',0,{daily_delta:null,delta_status:'negative_anomaly'})],selected,'2026-10-05');assert.deepEqual(result.plot.map(p=>p.cumulative_yipin),[10,0]);
 assert.equal(displayHistoryStatus(row('2026-10-05',null,{point_status:'time_unverified'})),'时间待核验');assert.equal(displayHistoryStatus({point_status:'incomplete_day'}),'当日仅部分采集');
});
