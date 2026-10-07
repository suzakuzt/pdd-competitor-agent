import test from 'node:test';
import assert from 'node:assert/strict';
import {INITIAL_FILTERS,EMPTY_FILTERS,eligible,filterCards,compareSales,bandMatches} from '../src/content/dashboard/pdd-model.js';
import {applyWatchBand,buildPeerEvidence,strategyMatches} from '../src/content/dashboard/strategy-model.js';
import {focusCategory,focusView} from '../src/content/dashboard/warehouse-focus-model.js';
import {exactSingleCardSales} from '../src/content/dashboard/product-tables-model.js';

const card=(id,label,value,extra={})=>({observation_id:id,view_order:id,shop_id:'A',run_id:'a',date:'2026-10-06',title:`商品${id}`,sales_label:label,sales_unit:'件',sales_value:value,sales_precision:'exact_display',sales_raw:`${label}${value}件`,...extra});

test('default picking and watch bands include both exact sales labels and retain raw evidence',()=>{
 const rows=[card(1,'已拼',12),card(2,'已抢',30),card(3,'已售',900),card(4,'已抢',10),card(5,'已抢',99,{sales_precision:'non_exact_or_unparsed',sales_raw:'已抢99+件'})],before=JSON.stringify(rows);
 assert.deepEqual(filterCards(rows,INITIAL_FILTERS).map(row=>row.observation_id),[2,1]);
 assert.deepEqual(filterCards(rows,applyWatchBand(INITIAL_FILTERS,'early')).map(row=>row.observation_id),[2,1]);
 assert.equal(eligible(rows[1]),true);assert.equal(eligible(rows[3]),false);assert.equal(eligible(rows[4]),false);
 assert.equal(bandMatches(rows[3],'boundary'),true);
 assert.equal(exactSingleCardSales(rows[1]),30);assert.equal(exactSingleCardSales(rows[4]),null);
 assert.equal(JSON.stringify(rows),before);
});

test('combined sales ordering uses count while explicit original-label filtering stays available',()=>{
 const rows=[card(1,'已拼',12),card(2,'已抢',30),card(3,'已拼',30),card(4,'已售',900),card(5,'已抢',10,{sales_unit:'单'})];
 assert.deepEqual([...rows].sort(compareSales).map(row=>row.observation_id),[2,3,1,5,4]);
 const selected=filterCards(rows,{...EMPTY_FILTERS,labelUnit:'["已抢","件"]'});
 assert.deepEqual(selected.map(row=>row.observation_id),[2]);assert.equal(selected[0].sales_label,'已抢');
});

test('legacy other-label derived category cannot exclude exact 已抢 from current warehouse',()=>{
 const rows=[card(1,'已抢',21,{category:'other_label',yipin_value:null}),card(2,'已抢',10),card(3,'已抢',0),card(4,'已抢',null,{sales_precision:'missing',sales_raw:''}),card(5,'已售',90)];
 assert.deepEqual(rows.map(focusCategory),['yipin_gt10','yipin_1to10','yipin_zero','unknown','other_label']);
 const queries={warehouse_days:{rows:[{shop_id:'A',date:'2026-10-06',run_id:'a',is_complete:true}]},warehouse_records:{rows}};
 const view=focusView(queries,'A');assert.equal(view.counts.yipin_gt10,1);assert.equal(view.rows[0].yipin_value,21);assert.equal(view.rows[0].sales_label,'已抢');
 assert.equal(rows[0].category,'other_label');assert.equal(rows[0].yipin_value,null);
});

test('same-title review counts both labels without summing their values',()=>{
 const rows=[card(1,'已拼',11),card(2,'已抢',12),card(3,'已抢',10)];
 const peers=buildPeerEvidence([{run_id:'a',group_id:'g',member_observation_ids:[1,2,3]}],rows);
 assert.deepEqual(peers.get(1).eligible_observation_ids,[1,2]);
 assert.equal(strategyMatches(rows[1],{peerConfirmed:true},peers),true);
 assert.equal(strategyMatches(rows[2],{peerConfirmed:true},peers),false);
});
