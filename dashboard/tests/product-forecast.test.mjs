import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as model from '../src/content/dashboard/product-forecast-model.js';
import * as displayNames from '../src/content/dashboard/product-display-name.js';
import {warehouseSource} from '../src/content/dashboard/warehouse-model.js';

const project=path.resolve(import.meta.dirname,'../..');
const plugin=path.join(project,'runtime/data-analytics/1.0.11');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(plugin,'scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler();
const guard=await import(pathToFileURL(path.join(plugin,'templates/data-app/base/src/source-provenance.js')));
const source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/ProductForecastPanel.jsx'),'utf8');
const compiled=compiler.transform(source,{commonjs:true});
const query=rows=>({rows,source:{label:'SYNTHETIC ONLY',tables:['synthetic_forecast']},methods:[]});
const date='2026-10-07',previousDate='2026-10-06';
const asset={sha256:'a'.repeat(64),data_url:`/__pdd_image/${'a'.repeat(64)}`,integrity_status:'verified_local'};
const products=[22,11,8,20,13,5,40,60].map((sales,i)=>({
 shop_id:'A',run_id:'a-current',date,observation_id:i+1,track_id:`synthetic-track-${i+1}`,
 title:`SYNTHETIC 合成商品 ${i+1}`,category:sales>10?'yipin_gt10':'yipin_1to10',yipin_value:sales,
 image_content_status:'verified_local',asset_sha256:asset.sha256,
}));
const forecasts=products.map((product,i)=>({
 shop_id:product.shop_id,run_id:product.run_id,date,observation_id:product.observation_id,
 forecast_status:'estimated',evidence_channel:'provisional',forecast_validated:false,
 forecast_increment_1d:i+1,forecast_increment_7d:(i+1)*7,scenario_low_7d:(i+1)*3,scenario_high_7d:(i+1)*14,
 normalized_interval_count:3,minimum_intervals:3,ewma_rate24:i+1,
 evidence_dates:['2026-10-04',date],evidence_observation_ids:[101+i,product.observation_id],
}));
const summary={shop_id:'A',date,reference_run_id:'a-current',estimated_count:8};
const history=products.flatMap(product=>[
 {shop_id:'A',anchor_run_id:'a-current',anchor_observation_id:product.observation_id,date:previousDate,observation_id:100+product.observation_id},
 {shop_id:'A',anchor_run_id:'a-current',anchor_observation_id:product.observation_id,date,observation_id:product.observation_id},
]);
const observations=products.flatMap(product=>[
 {shop_id:'A',run_id:'a-previous',observation_id:100+product.observation_id,title:product.title},
 {shop_id:'A',run_id:'a-current',observation_id:product.observation_id,title:product.title},
]);
const queries={trend_forecast_summary:query([summary]),trend_forecasts:query(forecasts),
 warehouse_focus_items:query(products),image_assets:query([asset]),
 warehouse_display_history:query(history),observations:query(observations)};
const view=(overrides={},channel='',sort='increment',asOfDate='')=>model.productForecastView({...queries,...overrides},'A',asOfDate,channel,sort);
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children||'');
const waiting=(row,channel='provisional')=>({...row,forecast_status:'insufficient_data',evidence_channel:channel,
 forecast_increment_1d:null,forecast_increment_7d:null,scenario_low_7d:null,scenario_high_7d:null,
 normalized_interval_count:1,ewma_rate24:null});

function harness(overrides={},initialProps={}){
 const scopedQueries={...queries,...overrides},states=[],opened=[];
 let cursor=0,props={shopId:'A',onOpenProduct:row=>opened.push(row),...initialProps};
 const react={useMemo:fn=>fn(),useState(initial){
  const index=cursor++;if(!(index in states))states[index]=typeof initial==='function'?initial():initial;
  return [states[index],value=>{states[index]=typeof value==='function'?value(states[index]):value;}];
 }};
 const jsx=(type,props)=>({type,props});
 const shared=new Proxy({useDataApp:()=>({queries:scopedQueries})},{get:(obj,key)=>obj[key]??key});
 const context={exports:{},module:{exports:{}},require:id=>{
  if(id==='react')return {__esModule:true,default:react,...react};
  if(id==='react/jsx-runtime')return {jsx,jsxs:jsx};
  if(id==='../../data-app-public.jsx')return shared;
  if(id.endsWith('product-forecast-model.js'))return model;
  if(id.endsWith('product-display-name.js'))return displayNames;
  if(id.endsWith('warehouse-model.js'))return {warehouseSource};
  if(id.endsWith('ZoomableImage.jsx'))return {ZoomableImage:'ZoomableImage'};
  return {};
 }};
 context.module.exports=context.exports;vm.runInNewContext(compiled,context);
 const collect=(node,nodes)=>{
  if(Array.isArray(node))return node.forEach(value=>collect(value,nodes));
  if(!node||typeof node!=='object')return;
  nodes.push(node);collect(node.props?.children,nodes);
 };
 return {queries:scopedQueries,opened,render(nextProps={}){
  props={...props,...nextProps};cursor=0;const nodes=[];
  collect(context.exports.ProductForecastPanel(props),nodes);return nodes;
 }};
}
const panel=nodes=>nodes.find(node=>node.props?.id==='short-term-product-forecast');
const images=nodes=>nodes.filter(node=>node.type==='ZoomableImage');

test('forecast selection uses the selected shop, exact date and its summary reference run',()=>{
 const otherSummary={...summary,shop_id:'B',date:'2026-10-08',reference_run_id:'b-current'};
 const oldSummary={...summary,date:previousDate,reference_run_id:'a-previous'};
 const oldProduct={...products[0],run_id:'a-previous',date:previousDate};
 const oldForecast={...forecasts[0],run_id:'a-previous',date:previousDate};
 const scoped={trend_forecast_summary:query([oldSummary,otherSummary,summary]),
  trend_forecasts:query([forecasts[0],{...forecasts[1],shop_id:'B'},
   {...forecasts[2],run_id:'wrong-run'},{...forecasts[3],date:previousDate},oldForecast]),
  warehouse_focus_items:query([...products,oldProduct,{...products[1],shop_id:'B'}])};
 const current=view(scoped);assert.equal(current.summary,summary);
 assert.deepEqual(current.cards.map(card=>card.product.observation_id),[1]);
 assert.equal(current.cards[0].forecast,forecasts[0]);assert.equal(current.cards[0].product,products[0]);
 const historical=view(scoped,'','increment',previousDate);
 assert.equal(historical.summary,oldSummary);assert.equal(historical.cards[0].product,oldProduct);
 const missing=view(scoped,'','increment','2026-10-01');assert.equal(missing.summary,null);assert.deepEqual(missing.cards,[]);
});

test('duplicate product or forecast keys are rejected without choosing one matching row',()=>{
 const duplicateProducts=view({warehouse_focus_items:query([...products,{...products[0]}])});
 assert.equal(duplicateProducts.cards.length,7);assert.ok(duplicateProducts.cards.every(card=>card.product.observation_id!==1));
 const duplicateForecasts=view({trend_forecasts:query([...forecasts,{...forecasts[1],evidence_channel:'confirmed'}])});
 assert.equal(duplicateForecasts.cards.length,7);assert.ok(duplicateForecasts.cards.every(card=>card.product.observation_id!==2));
 assert.equal(view({warehouse_focus_items:query([...products,{...products[0],shop_id:'B'}])}).cards.length,8);
});

test('only exact positive-sales cards with a verified original-card image are eligible',()=>{
 for(const changes of [{yipin_value:0},{yipin_value:-1},{yipin_value:null},{yipin_value:1.5},
  {yipin_value:'12'},{yipin_value:Number.NaN},{yipin_value:Number.POSITIVE_INFINITY},
  {yipin_value:Number.MAX_SAFE_INTEGER+1},{category:'unknown'},{category:'other_label'},
  {category:'yipin_zero'},{image_content_status:'url_only'},{asset_sha256:'b'.repeat(64)}]){
  const result=view({warehouse_focus_items:query([{...products[0],...changes}]),trend_forecasts:query([forecasts[0]])});
  assert.equal(result.cards.length,0,JSON.stringify(changes));
 }
 const small=view({warehouse_focus_items:query([products[5]]),trend_forecasts:query([forecasts[5]])});
 assert.equal(small.cards[0].product.yipin_value,5);
});

test('verified inline and matching local SHA images work while foreign or mismatched paths fail',()=>{
 for(const image of [asset,{...asset,data_url:'data:image/png;base64,AAAA'}]){
  assert.equal(view({image_assets:query([image])}).cards[0].image,image.data_url);
 }
 for(const changes of [{integrity_status:'unknown'},{data_url:'https://example.invalid/image.png'},
  {data_url:`/__pdd_image/${'b'.repeat(64)}`},{data_url:`/__pdd_image/${asset.sha256}?download=1`},
  {data_url:`//example.invalid/__pdd_image/${asset.sha256}`}]){
  assert.deepEqual(view({image_assets:query([{...asset,...changes}])}).cards,[]);
 }
});

test('confirmed and provisional evidence stay separate and null channels remain waiting for a match',()=>{
 const rows=[forecasts[0],{...forecasts[1],evidence_channel:'confirmed'},waiting(forecasts[2],null)];
 const scoped={trend_forecasts:query(rows)};
 assert.equal(view(scoped).channel,'confirmed');assert.deepEqual(view(scoped).cards.map(card=>card.product.observation_id),[2]);
 assert.deepEqual(view(scoped,'provisional').cards.map(card=>card.product.observation_id),[1]);
 const unmatched=view(scoped,'unmatched');assert.equal(unmatched.cards.length,1);
 assert.equal(unmatched.cards[0].forecast,rows[2]);assert.equal(unmatched.estimatedCount,0);assert.equal(unmatched.waitingCount,1);
 assert.equal(view({trend_forecasts:query([{...forecasts[0],evidence_channel:null}])},'unmatched').cards.length,0);
 assert.equal(view({trend_forecasts:query([{...forecasts[0],evidence_channel:'unknown'}])}).cards.length,0);
});

test('insufficient rows never fill estimated results or gain invented forecast numbers',()=>{
 const insufficient=waiting(forecasts[1]);
 const scoped={trend_forecasts:query([forecasts[0],insufficient])};
 const result=view(scoped);assert.equal(result.estimatedCount,1);assert.equal(result.waitingCount,1);
 assert.deepEqual(result.cards.map(card=>card.product.observation_id),[1]);
 const waitingQueries={trend_forecasts:query([insufficient])};
 const waitingView=view(waitingQueries);assert.equal(waitingView.cards[0].forecast,insufficient);
 assert.equal(waitingView.cards[0].forecast.forecast_increment_7d,null);
 assert.match(model.forecastReason(insufficient),/有效区间 1 \/ 3/);
 const nodes=harness(waitingQueries).render();assert.match(text(nodes),/数据积累中/);assert.match(text(nodes),/待积累/);
 const cells=nodes.filter(node=>node.type==='td');assert.equal(cells.filter(node=>text(node)==='—').length,2);
 assert.equal(model.forecastNumber(null),'—');assert.equal(model.forecastNumber(Number.NaN),'—');
});

test('estimates reject missing, nonfinite, negative or inverted ranges and unvalidated-state conflicts',()=>{
 const fields=['forecast_increment_1d','forecast_increment_7d','scenario_low_7d','scenario_high_7d'];
 for(const field of fields)for(const value of [null,undefined,-1,Number.NaN,Number.POSITIVE_INFINITY,'7']){
  assert.equal(view({trend_forecasts:query([{...forecasts[0],[field]:value}])}).cards.length,0,`${field}: ${value}`);
 }
 for(const changes of [{scenario_low_7d:8},{scenario_high_7d:6},{forecast_validated:true},
  {forecast_validated:null},{forecast_validated:undefined},{forecast_status:'unknown'}]){
  assert.equal(view({trend_forecasts:query([{...forecasts[0],...changes}])}).cards.length,0,JSON.stringify(changes));
 }
 const zero={...forecasts[0],forecast_increment_1d:0,forecast_increment_7d:0,scenario_low_7d:0,scenario_high_7d:0};
 assert.equal(view({trend_forecasts:query([zero])}).cards[0].forecast,zero);
});

test('increment and sales sorting retain independent original cards without mutating reviewed rows',()=>{
 const before=JSON.stringify(queries);
 assert.deepEqual(view().cards.map(card=>card.product.observation_id),[8,7,6,5,4,3,2,1]);
 assert.deepEqual(view({},'provisional','sales').cards.map(card=>card.product.observation_id),[8,7,1,4,5,2,3,6]);
 const repeatedTitle=products.map(product=>({...product,title:'SYNTHETIC 同标题同原图'}));
 assert.equal(view({warehouse_focus_items:query(repeatedTitle)}).cards.length,8);
 assert.equal(JSON.stringify(queries),before);
});

test('source evidence includes only exact reviewed same-shop selected cards and historical references',()=>{
 const excludedHistory=[{...history[0],shop_id:'B'},{...history[0],anchor_run_id:'foreign-run'},
  {...history[0],date:'2026-10-08'},{...history[0],anchor_observation_id:999}];
 const excludedObservations=[{...observations[0],shop_id:'B'},
  {shop_id:'A',run_id:'unrelated',observation_id:999}];
 const foreignAsset={...asset,sha256:'b'.repeat(64),data_url:`/__pdd_image/${'b'.repeat(64)}`};
 const scoped={trend_forecasts:query([forecasts[0]]),
  warehouse_display_history:query([...history,...excludedHistory]),
  observations:query([...observations,...excludedObservations]),image_assets:query([asset,foreignAsset])};
 const result=view(scoped),evidence=result.sourceRowsByQuery;
 assert.deepEqual(evidence.trend_forecast_summary,[summary]);assert.deepEqual(evidence.trend_forecasts,[forecasts[0]]);
 assert.deepEqual(evidence.warehouse_focus_items,[products[0]]);
 assert.deepEqual(evidence.warehouse_display_history,history.slice(0,2));
 assert.deepEqual(evidence.observations,observations.slice(0,2));assert.deepEqual(evidence.image_assets,[asset]);
 for(const [id,rows] of Object.entries(evidence))for(const row of rows){
  assert.ok(({...queries,...scoped})[id].rows.includes(row),`${id} must retain the reviewed object`);
 }
});

test('official compiler and provenance validator accept the complete panel with exact source rows',()=>{
 compiler.parseCss(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/product-forecast.css'),'utf8'));
 const h=harness(),nodes=h.render(),component=panel(nodes);
 assert.ok(component);guard.reviewedNarrativeQueries(component.props,h.queries);
 assert.equal(component.props.displayRows.length,8);assert.equal(images(nodes).length,6);
 assert.ok(component.props.sourceRows.every(row=>forecasts.includes(row)));
 assert.ok(component.props.sourceRowsByQuery.observations.every(row=>observations.includes(row)));
 assert.match(text(nodes),/尚未验证准确率/);assert.match(text(nodes),/不是置信区间/);
 assert.ok(nodes.some(node=>node.props?.['data-reviewed-rows']==='true'));
 assert.equal(nodes.some(node=>node.type==='main'),false);
});

test('more expands by six with a functional setter and changing sort restores the first six',()=>{
 const extraProducts=Array.from({length:6},(_,i)=>({...products[0],observation_id:i+9,track_id:`synthetic-track-${i+9}`,yipin_value:i+70}));
 const extraForecasts=extraProducts.map(product=>({...forecasts[0],observation_id:product.observation_id,
  forecast_increment_7d:product.observation_id*7,scenario_high_7d:product.observation_id*14}));
 const h=harness({warehouse_focus_items:query([...products,...extraProducts]),trend_forecasts:query([...forecasts,...extraForecasts])});
 let nodes=h.render();assert.equal(images(nodes).length,6);
 nodes.find(node=>node.props?.className==='product-forecast-more').props.onClick();
 nodes=h.render();assert.equal(images(nodes).length,12);
 nodes.find(node=>node.props?.className==='product-forecast-more').props.onClick();
 nodes=h.render();assert.equal(images(nodes).length,14);
 assert.equal(nodes.some(node=>node.props?.className==='product-forecast-more'),false);
 nodes.find(node=>node.type==='Dropdown').props.onChange('sales');nodes=h.render();
 assert.equal(images(nodes).length,6);
 assert.deepEqual(Array.from(panel(nodes).props.displayRows,row=>row.observation_id),[14,13,12,11,10,9,8,7,1,4,5,2,3,6]);
});

test('channel switch scopes data, closes its old source panel and resets expanded rows',()=>{
 const rows=[...forecasts,{...forecasts[0],observation_id:9,evidence_channel:'confirmed'}];
 const extra={...products[0],observation_id:9,track_id:'synthetic-track-9'};
 const h=harness({trend_forecasts:query(rows),warehouse_focus_items:query([...products,extra])});
 let nodes=h.render();assert.equal(panel(nodes).props.headerControls.props.value,'confirmed');
 panel(nodes).props.headerControls.props.onChange('provisional');nodes=h.render();
 nodes.find(node=>node.props?.className==='product-forecast-more').props.onClick();nodes=h.render();
 assert.equal(images(nodes).length,8);panel(nodes).props.onOpen('source',panel(nodes).props);
 nodes=h.render();assert.ok(nodes.some(node=>node.type==='SourceSidebar'));
 panel(nodes).props.headerControls.props.onChange('confirmed');nodes=h.render();
 assert.equal(nodes.some(node=>node.type==='SourceSidebar'),false);assert.equal(images(nodes).length,1);
 panel(nodes).props.headerControls.props.onChange('provisional');assert.equal(images(h.render()).length,6);
});

test('trend action sends the exact original card and is disabled without a callback or track',()=>{
 const h=harness();h.render().find(node=>node.type==='button'&&text(node)==='查看走势').props.onClick();
 assert.equal(h.opened.length,1);assert.equal(h.opened[0],products[7]);
 for(const h of [harness({}, {onOpenProduct:undefined}),
  harness({warehouse_focus_items:query(products.map(product=>({...product,track_id:null})))})]){
  assert.ok(h.render().filter(node=>node.type==='button'&&text(node)==='查看走势').every(node=>node.props.disabled));
 }
});

test('source sidebar uses the same reviewed selection and denies unrelated queries',()=>{
 const h=harness({trend_forecasts:query([forecasts[0]])});let nodes=h.render();
 const component=panel(nodes);component.props.onOpen('source',component.props);nodes=h.render();
 const sidebar=nodes.find(node=>node.type==='SourceSidebar');assert.ok(sidebar);
 for(const id of component.props.queryIds){
  const actual=sidebar.props.getSource(id);assert.equal(actual.query,h.queries[id]);
  assert.equal(actual.rows,component.props.sourceRowsByQuery[id]);
 }
 assert.equal(sidebar.props.getSource('unrelated'),null);
 assert.ok(sidebar.props.getSource('observations').rows.every(row=>row.shop_id==='A'));
 sidebar.props.onClose();assert.equal(h.render().some(node=>node.type==='SourceSidebar'),false);
});

test('null evidence channel is explicitly labeled as waiting for a comparison',()=>{
 const nodes=harness({trend_forecasts:query([waiting(forecasts[0],null)])}).render();
 assert.equal(panel(nodes).props.headerControls.props.value,'unmatched');
 assert.match(text(nodes.filter(node=>node.type==='details')),/等待建立对照/);
 assert.equal(images(nodes).length,1);
});

test('missing queries or a missing historical summary never reuse another date forecasts',()=>{
 for(const overrides of [{trend_forecasts:undefined},{trend_forecast_summary:undefined}]){
  const nodes=harness(overrides).render();assert.match(text(nodes),/本轮估算资料尚未生成/);
  assert.equal(nodes.some(node=>node.type==='DataComponent'||node.type==='button'),false);
 }
 const nodes=harness({}, {asOfDate:'2026-10-01'}).render();assert.match(text(nodes),/该历史日期尚未生成销量估算/);
 assert.equal(images(nodes).length,0);assert.deepEqual(Array.from(panel(nodes).props.displayRows),[]);
});
