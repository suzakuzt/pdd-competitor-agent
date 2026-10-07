import test from 'node:test';
import assert from 'node:assert/strict';
import {tableShopScope,buildTableView,tableSourceBinding,exportProductTable} from '../src/content/dashboard/product-tables-model.js';
function fixture(){
 const cards=[{observation_id:1,run_id:'a-ref',view_order:1,title:'同设计',normalized_title:'同设计',image_url:'https://example.com/a.jpg',sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:12},
 {observation_id:2,run_id:'a-ref',view_order:2,title:'同设计',normalized_title:'同设计',image_url:'https://example.com/a.jpg',sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:3},
 {observation_id:3,run_id:'a-ref',view_order:3,title:'另一商品',normalized_title:'另一商品',sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:0},
 {observation_id:4,run_id:'a-ref',view_order:4,title:'已抢商品',sales_label:'已抢',sales_unit:'件',sales_precision:'exact_display',sales_value:900},
 {observation_id:5,run_id:'a-latest',view_order:1,title:'最新部分',sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:100},
 {observation_id:6,run_id:'b-ref',title:'别店',sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:1000}];
 return {product_table_shops:{rows:[{shop_id:'a',reference_run_id:'a-ref',latest_run_id:'a-latest',baseline_state:'unconfigured'}]},runs:{rows:[{shop_id:'a',run_id:'a-ref'},{shop_id:'a',run_id:'a-latest'},{shop_id:'b',run_id:'b-ref'}]},observations:{rows:cards},product_table_items:{rows:[]},new_arrival_items:{rows:[]}};
}
test('whole new list uses complete reference, includes low/zero/unknown sales, independent of baseline',()=>{
 const view=buildTableView(tableShopScope(fixture(),'a'),'catalog');
 assert.equal(view.originals.length,4);assert.equal(view.items.length,4);assert.equal(view.groups.length,3);
 assert.deepEqual(view.groups.map(x=>x.highestSales),[900,12,0]);assert.ok(view.items.every(x=>x.first_seen_at===null));
 assert.deepEqual(view.sourceRuns.map(x=>x.run_id),['a-ref']);
});
test('folding preserves all original cards and never adds sales',()=>{
 const view=buildTableView(tableShopScope(fixture(),'a'),'catalog');const first=view.groups.find(group=>group.originalCount===2);
 assert.equal(first.highestSales,12);assert.equal(first.originalCount,2);assert.deepEqual(first.originals.map(x=>x.sales_value),[12,3]);
});
test('catalog source binds actual observations instead of invented reviewed items',()=>{
 const queries=fixture(),view=buildTableView(tableShopScope(queries,'a'),'catalog'),binding=tableSourceBinding(view);
 assert.equal(binding.queryId,'observations');assert.equal(binding.sourceRows.length,4);
 assert.ok(binding.sourceRows.every(row=>queries.observations.rows.includes(row)));assert.equal('product_table_items' in binding.sourceRowsByQuery,false);
});
test('all-page exports retain zero and unknown labels with their original values',()=>{
 const view=buildTableView(tableShopScope(fixture(),'a'),'catalog'),output=exportProductTable(view,view.shop);
 assert.equal(output.source_observations.length,4);assert.equal(output.source_observations.find(x=>x.observation_id===4).sales_label,'已抢');
 assert.equal(output.source_observations.find(x=>x.observation_id===3).sales_value,0);
});
test('foreign run references cannot expose other shop cards',()=>{
 const queries=fixture();queries.product_table_shops.rows[0].reference_run_id='b-ref';
 assert.equal(buildTableView(tableShopScope(queries,'a'),'catalog').originals.length,0);
});
test('new-list search filters group members but retains matching group evidence',()=>{
 const view=buildTableView(tableShopScope(fixture(),'a'),'catalog','同设计');
 assert.equal(view.groups.length,1);assert.equal(view.originals.length,2);assert.equal(view.unfilteredItemCount,4);
});
