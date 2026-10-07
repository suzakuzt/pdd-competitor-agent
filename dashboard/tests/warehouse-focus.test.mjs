import test from 'node:test';
import assert from 'node:assert/strict';
import {focusCategory,focusView,focusRows,focusSortLabel,nextFocusSort,focusComparisonCounts,comparisonValue,comparisonDate,displayClueSeries,formatGrowth} from '../src/content/dashboard/warehouse-focus-model.js';
const exact=value=>({sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:value,sales_raw:`已拼${value}件`});
test('five sales classes keep exact threshold, true zero, other labels and unknown separate',()=>{
 assert.deepEqual([11,10,1,0].map(value=>focusCategory(exact(value))),['yipin_gt10','yipin_1to10','yipin_1to10','yipin_zero']);
 assert.equal(focusCategory({...exact(30),sales_label:'已抢'}),'yipin_gt10');
 for(const patch of [{sales_value:null},{sales_precision:'approximate'},{sales_unit:null},{sales_value:-1}])assert.equal(focusCategory({...exact(30),...patch}),'unknown');
});
test('main view uses complete shop catalogue and never filters by first discovery or 30-day age',()=>{
 const rows=[{...exact(21),observation_id:1,shop_id:'A',date:'2026-10-04',track_id:'a',monitor_first_date:'2020-01-01',monitor_origin:'reference_stock'},{...exact(0),observation_id:2,shop_id:'A',date:'2026-10-05',track_id:'b'}];
 const queries={warehouse_days:{rows:[{shop_id:'A',date:'2026-10-04',run_id:'full',is_complete:true},{shop_id:'A',date:'2026-10-05',run_id:'partial',is_complete:false}]},warehouse_records:{rows}};
 const view=focusView(queries,'A');assert.equal(view.date,'2026-10-04');assert.equal(view.rows.length,1);assert.equal(focusRows(view.rows)[0].observation_id,1);
 assert.equal(focusView(queries,'B').rows.length,0);assert.equal(rows[0].category,undefined);
});
test('focus query stays within selected run and historical selection cannot leak latest comparisons',()=>{
 const queries={warehouse_days:{rows:[{shop_id:'A',date:'2026-10-04',run_id:'old',is_complete:true},{shop_id:'A',date:'2026-10-05',run_id:'new',is_complete:true}]},warehouse_records:{rows:[{...exact(12),shop_id:'A',date:'2026-10-04',run_id:'old',observation_id:1}]},warehouse_focus_summary:{rows:[{shop_id:'A',run_id:'new'}]},warehouse_focus_items:{rows:[{...exact(20),shop_id:'A',run_id:'new',observation_id:2},{...exact(30),shop_id:'B',run_id:'new',observation_id:3}]}};
 assert.equal(focusView(queries,'A').rows.length,1);assert.equal(focusView(queries,'A').queryId,'warehouse_focus_items');
 assert.equal(focusView(queries,'A','2026-10-04').queryId,'warehouse_records');assert.equal(focusView(queries,'A','2026-10-04').rows[0].observation_id,1);
});
test('rapid view preserves confirmed and provisional evidence and uses selected comparison',()=>{
 const rows=[{title:'old product',category:'yipin_gt10',yipin_value:100,observation_id:1,yesterday_match_basis:'confirmed_goods_id',yesterday_delta:10,yesterday_rapid_growth:true,day_before_yesterday_delta:5,day_before_yesterday_rapid_growth:false},{title:'weak clue',category:'yipin_gt10',yipin_value:50,observation_id:2,yesterday_match_basis:'provisional_title_image',yesterday_delta:20,yesterday_rapid_growth:true}];
 assert.deepEqual(focusRows(rows,'rapid').map(r=>r.observation_id),[2,1]);
 assert.equal(focusRows(rows,'rapid','','day_before_yesterday').length,0);
 assert.deepEqual(focusComparisonCounts(rows,'yesterday'),{increased:2,confirmed:1,provisional:1,comparable:2,confirmedComparable:1,provisionalComparable:1});
 assert.equal(focusRows(rows,'yipin_gt10','old')[0].observation_id,1);
});
test('comparison dates use the shown complete catalogue day, including year boundary',()=>{
 assert.equal(comparisonDate('2026-10-04','yesterday'),'2026-10-03');
 assert.equal(comparisonDate('2026-01-01','day_before_yesterday'),'2025-12-30');
});
test('independent clue series uses matched original cards, preserves missing days and never creates product tracks',()=>{
 const row={date:'2026-10-05',run_id:'now',observation_id:30,track_id:'original30',yipin_value:35,day_before_yesterday_baseline_date:'2026-10-03',day_before_yesterday_baseline_observation_id:10,day_before_yesterday_baseline_value:20,day_before_yesterday_match_basis:'provisional_title_image',day_before_yesterday_status:'comparable'};
 const series=displayClueSeries(row);assert.deepEqual(series.observationIds,[10,30]);assert.equal(series.plot.length,3);assert.equal(series.plot[1].display_yipin,null);assert.equal(row.track_id,'original30');
 assert.equal(displayClueSeries({...row,day_before_yesterday_status:'negative_anomaly'}).source.length,0);
 assert.equal(displayClueSeries({...row,day_before_yesterday_match_basis:'confirmed_goods_id'}).source.length,0);
 assert.equal(displayClueSeries({...row,day_before_yesterday_baseline_date:'2026-09-01'}).source.length,0);
});
test('zero baseline clue retains observed zero and no percentage is manufactured',()=>{
 const series=displayClueSeries({date:'2026-10-05',observation_id:2,yipin_value:15,yesterday_baseline_date:'2026-10-04',yesterday_baseline_observation_id:1,yesterday_baseline_value:0,yesterday_match_basis:'provisional_title_image',yesterday_status:'zero_baseline'});
 assert.equal(series.source[0].display_yipin,0);assert.equal(series.source[1].display_yipin,15);assert.equal(formatGrowth(null,'zero_baseline'),'从零起量');
});
test('missing comparison is unknown and zero baseline never shows infinite growth',()=>{
 assert.equal(comparisonValue({},'yesterday').delta,null);assert.equal(formatGrowth(null,'zero_baseline'),'从零起量');
 assert.equal(formatGrowth(.35,'comparable'),'+35.0%');assert.equal(formatGrowth(null,'missing_baseline'),'—');
 assert.equal(comparisonValue({yesterday_baseline_value:0},'yesterday').value,0);
});

test('rapid picking table excludes exact ten even when zero-base growth flag is true',()=>{
 const rows=[{category:'yipin_1to10',yipin_value:10,yesterday_rapid_growth:true,yesterday_delta:10},{category:'yipin_gt10',yipin_value:15,yesterday_rapid_growth:true,yesterday_delta:15}];
 assert.deepEqual(focusRows(rows,'rapid').map(r=>r.yipin_value),[15]);
});

test('positive display change count and picking lane include small rises but exclude zero and low sellers',()=>{
 const rows=[{category:'yipin_gt10',yipin_value:21,yesterday_delta:1},{category:'yipin_gt10',yipin_value:25,yesterday_delta:0},{category:'yipin_gt10',yipin_value:30,yesterday_delta:null},{category:'yipin_1to10',yipin_value:5,yesterday_delta:5}];
 assert.equal(focusComparisonCounts(rows.filter(r=>r.category==='yipin_gt10'),'yesterday').increased,1);assert.deepEqual(focusRows(rows,'rising').map(r=>r.yipin_value),[21]);
});

test('explicit increase sorting is numeric, stable and never mutates source cards',()=>{
 const rows=[2,100,12,0,12].map((delta,i)=>({observation_id:i+1,title:'测试商品',category:'yipin_gt10',yipin_value:100-i,yesterday_delta:delta,yesterday_status:'comparable'}));
 const ids=sort=>focusRows(rows,'yipin_gt10','','yesterday',sort).map(r=>r.observation_id);
 assert.deepEqual(ids(null),[1,2,3,4,5]);
 assert.deepEqual(ids({field:'delta',direction:'desc'}),[2,3,5,1,4]);
 assert.deepEqual(ids({field:'delta',direction:'asc'}),[4,1,3,5,2]);
 assert.deepEqual(rows.map(r=>r.observation_id),[1,2,3,4,5]);
});

test('growth sorting uses unrounded numeric ratios and keeps unavailable rates last both ways',()=>{
 const rates=[.1001,.1002,0,null,Infinity,'0.9'];
 const rows=rates.map((rate,i)=>({observation_id:i+1,category:'yipin_gt10',yipin_value:100-i,yesterday_growth_rate:rate,yesterday_status:'comparable'}));
 rows.push({observation_id:7,category:'yipin_gt10',yipin_value:10,yesterday_growth_rate:2,yesterday_delta:10,yesterday_status:'zero_baseline'});
 rows.push({observation_id:8,category:'yipin_gt10',yipin_value:9,yesterday_growth_rate:3,yesterday_status:'missing_baseline'});
 assert.deepEqual(focusRows(rows,'yipin_gt10','','yesterday',{field:'growth_rate',direction:'desc'}).map(r=>r.observation_id),[2,1,3,4,5,6,7,8]);
 assert.deepEqual(focusRows(rows,'yipin_gt10','','yesterday',{field:'growth_rate',direction:'asc'}).map(r=>r.observation_id),[3,1,2,4,5,6,7,8]);
});

test('zero-base increases are sortable while missing and invalid comparisons stay last',()=>{
 const rows=[{observation_id:1,yesterday_delta:null,yesterday_status:'missing_baseline'},{observation_id:2,yesterday_delta:0,yesterday_status:'comparable'},{observation_id:3,yesterday_delta:12,yesterday_status:'zero_baseline'},{observation_id:4,yesterday_delta:50,yesterday_status:'unmatched'}].map(row=>({...row,category:'yipin_gt10',yipin_value:100}));
 assert.deepEqual(focusRows(rows,'all','','yesterday',{field:'delta',direction:'desc'}).map(r=>r.observation_id),[3,2,1,4]);
 assert.deepEqual(focusRows(rows,'all','','yesterday',{field:'delta',direction:'asc'}).map(r=>r.observation_id),[2,3,1,4]);
});

test('selected date, sales lane and search filter apply before global comparison sorting',()=>{
 const rows=Array.from({length:40},(_,i)=>({observation_id:i,title:i===0?'其他':'查询',category:i===39?'yipin_1to10':'yipin_gt10',yipin_value:100-i,yesterday_delta:40-i,yesterday_status:'comparable',day_before_yesterday_delta:i,day_before_yesterday_status:'comparable'}));
 const sorted=focusRows(rows,'yipin_gt10','查询','day_before_yesterday',{field:'delta',direction:'desc'});
 assert.equal(sorted.length,38);assert.deepEqual(sorted.slice(0,3).map(r=>r.observation_id),[38,37,36]);assert.equal(sorted.slice(25)[0].observation_id,13);
 assert.equal(focusRows(rows,'yipin_gt10','查询','yesterday',{field:'delta',direction:'desc'})[0].observation_id,1);
});

test('column switch starts descending and each active header toggles direction',()=>{
 const first=nextFocusSort(null,'delta'),second=nextFocusSort(first,'delta'),other=nextFocusSort(second,'growth_rate');
 assert.deepEqual(first,{field:'delta',direction:'desc'});assert.deepEqual(second,{field:'delta',direction:'asc'});assert.deepEqual(other,{field:'growth_rate',direction:'desc'});
 assert.equal(focusSortLabel(second),'增加升序');assert.equal(focusSortLabel(other),'涨幅降序');assert.equal(focusSortLabel(null,'rising'),'增量降序');assert.equal(focusSortLabel(null),'销量降序');
});
