import * as verifiedImages from '../src/content/dashboard/verified-image.js';
import * as skuModel from '../src/content/dashboard/sku-model.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as occasion from '../src/content/dashboard/occasion-model.js';
import * as names from '../src/content/dashboard/product-display-name.js';
import * as times from '../src/content/dashboard/data-time.js';
import * as comparison from '../src/content/dashboard/warehouse-focus-model.js';

const project=path.resolve(import.meta.dirname,'../..'),plugin=path.join(project,'runtime/data-analytics/1.0.11');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(plugin,'scripts/data-app-runtime.mjs'))),compiler=await loadPrebuiltCompiler();
const guard=await import(pathToFileURL(path.join(plugin,'templates/data-app/base/src/source-provenance.js')));
const source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/OccasionProducts.jsx'),'utf8');
const query=rows=>({rows,source:{label:'SYNTHETIC ONLY',tables:['synthetic']},methods:[]});
const base={shop_id:'A',run_id:'a',date:'2026-10-06',observed_at:'2026-10-06T03:21:33Z',sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',price_raw:'券后¥4'};
const row=(id,title,value,extra={})=>({...base,observation_id:id,track_id:`card-${id}`,title,sales_raw:value==null?'未显示':`已拼${value}件`,sales_value:value,yipin_value:value,...extra});
const records=[row(1,'甲 10月20日生日立牌',21,{category:'yipin_gt10',asset_sha256:'a'.repeat(64),image_content_status:'verified_local',yesterday_status:'comparable',yesterday_delta:3,yesterday_match_basis:'provisional_title_image',yesterday_baseline_observation_id:80}),row(2,'乙生日立牌',10),row(3,'丙周年纪念立牌',0),row(4,'丁生日周边',null),row(5,'戊生日应援',200,{sales_label:'已抢',sales_raw:'已抢200件',sales_value:200}),row(6,'乙音乐节应援摆件',0),row(7,'其他普通商品',50),row(8,'别店生日立牌',90,{shop_id:'B'}),row(9,'旧日生日立牌',90,{date:'2026-10-05'})];
const baseline=row(80,'甲 10月20日生日立牌',18,{date:'2026-10-05'});
const observations=[...records,baseline];
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children??'');
test('occasion local thumbnails require complete verified asset rows and matching SHA paths',()=>{
 const sha='a'.repeat(64),data_url=`/__pdd_image/${sha}`;
 for(const value of [{sha256:sha,data_url,integrity_status:'verified_local'},
  {sha256:sha,data_url,integrity_status:'unknown'},
  {sha256:sha,data_url:`/__pdd_image/${'b'.repeat(64)}`,integrity_status:'verified_local'},data_url]){
  const nodes=harness({assets:new Map([[sha,value]])}).render();
  const thumb=nodes.find(node=>node.type==='ZoomableImage'&&node.props.className==='occasion-thumbnail');
  if(value.integrity_status==='verified_local'&&value.data_url===data_url)assert.equal(thumb.props.src,data_url);
  else assert.equal(thumb,undefined);
 }
});
function harness(overrides={}){
 let cursor=0;const states=[],opened=[],sourceCalls=[];
 const props={module:'birthday',rows:records,sourceRows:records,queryId:'warehouse_focus_items',observations,assets:new Map([['a'.repeat(64),{sha256:'a'.repeat(64),data_url:'data:image/png;base64,c3ludGhldGlj',integrity_status:'verified_local'}]]),shopId:'A',date:'2026-10-06',onOpenProduct:row=>opened.push(row),onOpenSource:(...args)=>sourceCalls.push(args),...overrides};
 const react={useMemo:fn=>fn(),useState(initial){const index=cursor++;if(!(index in states))states[index]=typeof initial==='function'?initial():initial;return [states[index],value=>states[index]=typeof value==='function'?value(states[index]):value];}};
 const jsx=(type,props)=>({type,props}),shared=new Proxy({},{get:(_,key)=>key});
 const context={exports:{},module:{exports:{}},require:id=>id.endsWith('verified-image.js')?verifiedImages:id.endsWith('sku-model.js')?skuModel:id.endsWith('CollectionControls.jsx')?{SkuDialog:'SkuDialog'}:id.endsWith('ZoomableImage.jsx')?{ZoomableImage:'ZoomableImage'}:id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='../../data-app-public.jsx'?shared:id.endsWith('occasion-model.js')?occasion:id.endsWith('product-display-name.js')?names:id.endsWith('data-time.js')?times:id.endsWith('warehouse-focus-model.js')?comparison:{},Map,Set,Number,Math,Object,Array};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 const collect=(node,nodes)=>{if(Array.isArray(node))return node.forEach(item=>collect(item,nodes));if(!node||typeof node!=='object')return;nodes.push(node);for(const key of ['children','filters','headerControls'])collect(node.props?.[key],nodes);};
 return {props,opened,sourceCalls,render(){cursor=0;const nodes=[];collect(context.exports.OccasionProducts(props),nodes);return nodes;}};
}
const cards=nodes=>nodes.find(node=>node.props?.id?.endsWith('-cards'));
const button=(nodes,label)=>nodes.find(node=>node.type==='button'&&text(node)===label);
const sortButton=(nodes,label)=>nodes.find(node=>node.type==='button'&&node.props.className==='warehouse-sort-button'&&text(node).startsWith(label));
const ids=nodes=>Array.from(cards(nodes).props.displayRows,item=>item.observation_id);
const cells=nodes=>nodes.filter(node=>node.type==='td');

test('component and isolated table styles compile with canonical compiler',()=>{
 compiler.parseJavaScript(compiler.transform(source,{commonjs:false}));
 compiler.parseCss(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/occasion-products.css'),'utf8'));
});

test('occasion SKU action uses exact card and excludes zero and unknown sales',()=>{
 const skuRows=[...records,row(10,'一件生日周边',1),row(11,'模糊生日周边',20,{sales_precision:'lower_bound',sales_raw:'已拼20+件'})]
  .map(item=>({...item,category:comparison.focusCategory(item)}));
 const opened=[],h=harness({rows:skuRows,sourceRows:skuRows,onOpenSku:item=>opened.push(item)}),nodes=h.render(),buttons=nodes.filter(node=>node.props?.className==='warehouse-sku-button');
 for(const entry of buttons)entry.props.onClick();assert.deepEqual(opened.map(item=>item.observation_id),[1,5,2,10]);assert.ok(opened.every(item=>item.shop_id==='A'));
 const tr=nodes.filter(node=>node.type==='tr').filter(node=>/丙周年|丁生日|模糊生日/.test(text(node)));assert.equal(tr.length,3);assert.ok(tr.every(node=>!text(node).includes('查看 SKU')));
});

test('default birthday module includes zero and unknown without leaking shop or day and retains source lineage',()=>{
 const nodes=harness().render(),table=cards(nodes),summary=nodes.find(node=>node.props?.id==='occasion-birthday-summary');
 assert.deepEqual(Array.from(table.props.displayRows,row=>row.observation_id),[1,5,2,3,4]);
 assert.deepEqual(Array.from(table.props.sourceRows,row=>row.observation_id),[1,2,3,4,5]);
 assert.ok(table.props.sourceRows.every(row=>!Object.hasOwn(row,'occasion')));
 assert.deepEqual(Array.from(table.props.sourceRowsByQuery.observations,row=>row.observation_id),[1,2,3,4,5,80]);
 assert.match(text(summary),/全部相关商品/);assert.match(text(summary),/销量未知/);
 for(const node of [table,summary])guard.reviewedNarrativeQueries(node.props,{warehouse_focus_items:query(records),observations:query(observations)});
 assert.match(text(table),/10月20日（未标年份） · 待核实/);assert.match(text(table),/日期未标明/);
 assert.match(text(table),/2026-10-06 11:21/);assert.match(text(table),/同图线索/);
 const thumb=nodes.find(node=>node.type==='ZoomableImage'&&node.props.className==='occasion-thumbnail');assert.equal(thumb.props.src,'data:image/png;base64,c3ludGhldGlj');
 assert.doesNotMatch(text(table),/倒计时|天后/);
});

test('sales ranges keep zero separate from unknown and include exactly ten in low group',()=>{
 const h=harness();let nodes=h.render();
 const select=label=>{h.render().find(node=>node.type==='button'&&text(node).endsWith(label)).props.onClick();return cards(h.render()).props.displayRows;};
 assert.deepEqual(Array.from(select('销量 1–10'),row=>row.yipin_value),[10]);
 assert.deepEqual(Array.from(select('销量 0'),row=>row.yipin_value),[0]);
 assert.deepEqual(Array.from(select('销量未知'),row=>row.observation_id),[4]);
 assert.equal(h.sourceCalls.at(-1)[0],'close');
});

test('search and title-clue filters narrow raw source evidence together',()=>{
 const h=harness();let nodes=h.render();nodes.find(node=>node.type==='input').props.onChange({target:{value:'甲'}});nodes=h.render();
 assert.deepEqual(Array.from(cards(nodes).props.sourceRows,row=>row.observation_id),[1]);
 nodes.find(node=>node.type==='input').props.onChange({target:{value:''}});nodes=h.render();
 nodes.find(node=>node.type==='Dropdown'&&node.props.label==='标题线索').props.onChange('周年 / 纪念日');nodes=h.render();
 assert.deepEqual(Array.from(cards(nodes).props.sourceRows,row=>row.observation_id),[3]);
 assert.equal(cards(nodes).props.scopeFilters.find(filter=>filter.field==='tag').value,'周年 / 纪念日');
});

test('activity module includes unplaced-order music event and passes exact clicked card to trend route',()=>{
 const h=harness({module:'activity'}),nodes=h.render();
 assert.deepEqual(Array.from(cards(nodes).props.displayRows,row=>row.observation_id),[6,5]);
 nodes.find(node=>node.props?.className==='warehouse-chart-button').props.onClick();
 assert.equal(h.opened[0].observation_id,6);assert.equal(h.opened[0].yipin_value,0);
 assert.equal(nodes.some(node=>node.type==='Dialog'),false);
});

test('pagination displays twenty cards and filter changes reset current page',()=>{
 const many=Array.from({length:45},(_,index)=>row(index+100,`角色生日 ${index}`,index)),h=harness({rows:many,sourceRows:many,observations:many});
 let nodes=h.render();assert.equal(nodes.filter(node=>node.props?.className==='occasion-product').length,20);
 button(nodes,'下一页').props.onClick();nodes=h.render();assert.match(text(nodes.find(node=>node.props?.className==='occasion-pagination')),/2 \/ 3/);
 nodes.find(node=>node.type==='input').props.onChange({target:{value:'生日'}});nodes=h.render();assert.match(text(nodes.find(node=>node.props?.className==='occasion-pagination')),/1 \/ 3/);
 assert.equal(cards(nodes).props.sourceRows.length,45);
});

test('controlled view preserves query and page when recreated after viewing product data',()=>{
 let saved={search:'角色',sales:'all',tag:'all',page:1};const many=Array.from({length:45},(_,index)=>row(index+100,`角色生日 ${index}`,index));
 let h=harness({rows:many,sourceRows:many,viewState:saved,onViewStateChange:next=>saved=next});let nodes=h.render();
 assert.equal(nodes.find(node=>node.type==='input').props.value,'角色');assert.match(text(nodes.find(node=>node.props?.className==='occasion-pagination')),/2 \/ 3/);
 nodes.find(node=>node.props?.className==='warehouse-chart-button').props.onClick();
 h=harness({rows:many,sourceRows:many,viewState:saved,onViewStateChange:next=>saved=next});nodes=h.render();assert.match(text(nodes.find(node=>node.props?.className==='occasion-pagination')),/2 \/ 3/);
 nodes.find(node=>node.type==='input').props.onChange({target:{value:'生日'}});assert.equal(saved.page,0);assert.equal(saved.search,'生日');
});

test('empty filtered results are explicit and no missing sales or invalid comparison becomes zero',()=>{
 const bad=row(20,'未知生日周边',null,{yesterday_status:'sales_not_comparable',yesterday_delta:99,yesterday_growth_rate:2});
 const h=harness({rows:[bad],sourceRows:[bad],observations:[bad]});let nodes=h.render();
 const values=cells(nodes);assert.equal(values.length,7);
 assert.match(text(values[2]),/^—/);assert.equal(text(values[3]),'—');assert.equal(text(values[4]),'—');assert.equal(text(values[5]),'—');
 nodes.find(node=>node.type==='input').props.onChange({target:{value:'不存在'}});nodes=h.render();assert.equal(cards(nodes).props.sourceRows.length,0);assert.match(text(cards(nodes)),/没有符合当前筛选/);
});

test('date search, date type and sort filter the same reviewed cards and preserve source scope',()=>{
 const dated=[row(101,'甲0929生日',10),row(102,'甲1005生日',1),row(103,'甲2026-09-29生日',2),row(104,'甲生日',200),row(105,'别店0929生日',500,{shop_id:'B'})];
 const h=harness({rows:dated,sourceRows:dated,observations:dated});let nodes=h.render();
 assert.deepEqual(Array.from(cards(nodes).props.displayRows,row=>row.observation_id),[103,102,101,104]);
 nodes.find(node=>node.type==='input'&&node.props['aria-label']==='搜索生日日期').props.onChange({target:{value:'09-29'}});nodes=h.render();
 assert.deepEqual(Array.from(cards(nodes).props.displayRows,row=>row.observation_id),[103,101]);
 assert.deepEqual(Array.from(cards(nodes).props.sourceRows,row=>row.observation_id),[101,103]);
 assert.equal(cards(nodes).props.scopeFilters.find(filter=>filter.field==='date_search').value,'09-29');
 nodes.find(node=>node.type==='input'&&node.props['aria-label']==='搜索生日日期').props.onChange({target:{value:''}});nodes=h.render();
 nodes.find(node=>node.type==='Dropdown'&&node.props.label==='排序').props.onChange('date_asc');nodes=h.render();
 assert.deepEqual(Array.from(cards(nodes).props.displayRows,row=>row.observation_id),[103,101,102,104]);
 nodes.find(node=>node.type==='Dropdown'&&node.props.label==='日期').props.onChange('undated');nodes=h.render();
 assert.deepEqual(Array.from(cards(nodes).props.sourceRows,row=>row.observation_id),[104]);
 guard.reviewedNarrativeQueries(cards(nodes).props,{warehouse_focus_items:query(dated),observations:query(dated)});
});

test('controlled birthday date filters survive detail return and date/sort changes reset pages',()=>{
 const many=Array.from({length:45},(_,index)=>row(index+100,`角色0929生日 ${index}`,index));
 let saved={dateSearch:'09-29',dateScope:'dated',sort:'date_asc',page:1};
 let h=harness({rows:many,sourceRows:many,viewState:saved,onViewStateChange:next=>saved=next}),nodes=h.render();
 assert.match(text(nodes.find(node=>node.props?.className==='occasion-pagination')),/2 \/ 3/);
 nodes.find(node=>node.props?.className==='warehouse-chart-button').props.onClick();
 h=harness({rows:many,sourceRows:many,viewState:saved,onViewStateChange:next=>saved=next});nodes=h.render();
 assert.equal(nodes.find(node=>node.type==='input'&&node.props['aria-label']==='搜索生日日期').props.value,'09-29');
 assert.equal(nodes.find(node=>node.type==='Dropdown'&&node.props.label==='排序').props.value,'date_asc');
 nodes.find(node=>node.type==='Dropdown'&&node.props.label==='排序').props.onChange('date_desc');assert.equal(saved.page,0);assert.equal(saved.dateSearch,'09-29');
});

test('activity date controls retain latest full-year dates first and display yearless/lunar clues honestly',()=>{
 const events=[row(201,'甲2026-10-05演唱会',1),row(202,'乙2026-09-29演唱会',2),row(203,'丙10月6日演唱会',3),row(204,'丁农历10月7日音乐节',4)];
 const h=harness({module:'activity',rows:events,sourceRows:events,observations:events}),nodes=h.render();
 assert.deepEqual(Array.from(cards(nodes).props.displayRows,row=>row.observation_id),[201,202,203,204]);
 assert.ok(nodes.find(node=>node.type==='input'&&node.props['aria-label']==='搜索活动日期'));
 assert.match(text(cards(nodes)),/10月6日（未标年份）/);assert.match(text(cards(nodes)),/10月7日（农历，未换算）/);
 assert.match(text(nodes.find(node=>node.props?.className==='occasion-date-note')),/完整年月日优先/);
});

test('both occasion tables expose the same seven product fields with original clues and safe SKU action',()=>{
 const one=row(301,'甲10月20日生日演唱会',30,{category:'yipin_gt10',yesterday_status:'comparable',
  yesterday_baseline_value:20,yesterday_baseline_date:'2026-10-05',yesterday_delta:10,yesterday_growth_rate:.5,
  yesterday_match_basis:'provisional_title_image',asset_sha256:'a'.repeat(64),image_content_status:'verified_local'});
 for(const module of ['birthday','activity']){
  const opened=[],h=harness({module,rows:[one],sourceRows:[one],observations:[one],onOpenSku:item=>opened.push(item)}),nodes=h.render();
  const headers=nodes.filter(node=>node.type==='th').map(text),values=cells(nodes);
  assert.equal(headers.length,7);assert.deepEqual(headers.slice(0,2),['图片 / 商品','展示价']);
  assert.match(headers[2],/^当前销量.*2026-10-06/);assert.match(headers[3],/^昨日销量.*2026-10-05/);
  assert.match(headers[4],/^增加/);assert.match(headers[5],/^涨幅/);assert.equal(headers[6],'数据');
  assert.equal(values.length,7);assert.equal(text(values[1]),one.price_raw);assert.equal(text(values[2]),'30');
  assert.equal(text(values[3]),'20');assert.equal(text(values[4]),'+10');assert.equal(text(values[5]),'+50.0%');
  assert.match(text(values[0]),/原卡 #301/);assert.match(text(values[0]),/甲10月20日生日演唱会/);
  assert.match(text(values[0]),/2026-10-06 11:21/);assert.match(text(values[0]),/同图线索/);
  if(module==='birthday')assert.match(text(values[0]),/10月20日（未标年份）/);
  assert.equal(nodes.filter(node=>node.type==='ZoomableImage').length,1);
  button(nodes,'查看走势').props.onClick();assert.equal(h.opened[0].observation_id,301);
  button(nodes,'查看 SKU').props.onClick();assert.equal(opened[0].observation_id,301);assert.equal(opened[0].shop_id,'A');
 }
});

test('comparison headers sort all filtered cards before pagination and keep unavailable values last in either direction',()=>{
 const many=Array.from({length:43},(_,index)=>row(400+index,`角色生日 ${index}`,500-index,{category:'yipin_gt10',
  yesterday_status:index<40?'comparable':index===40?'zero_baseline':index===41?'unmatched':'negative_anomaly',
  yesterday_delta:index===40?12:index===41?999:index===42?-9:index,
  yesterday_growth_rate:index===40?null:index===41?9:index===42?-.9:index/100}));
 const original=JSON.stringify(many),h=harness({rows:many,sourceRows:many,observations:many});let nodes=h.render();
 button(nodes,'下一页').props.onClick();nodes=h.render();assert.match(text(nodes.find(node=>node.props.className==='occasion-pagination')),/2 \/ 3/);
 sortButton(nodes,'增加').props.onClick();nodes=h.render();
 assert.deepEqual(ids(nodes).slice(0,3),[439,438,437]);assert.deepEqual(ids(nodes).slice(-2),[441,442]);
 assert.ok(ids(nodes).indexOf(412)<ids(nodes).indexOf(440),'Equal increases retain existing date/clue order');
 assert.match(text(nodes.find(node=>node.props.className==='occasion-pagination')),/1 \/ 3/);
 assert.match(text(nodes.find(node=>node.props.className==='occasion-product')),/原卡 #439/);
 assert.equal(nodes.find(node=>node.type==='th'&&text(node).startsWith('增加')).props['aria-sort'],'descending');
 sortButton(nodes,'增加').props.onClick();nodes=h.render();assert.deepEqual(ids(nodes).slice(0,3),[400,401,402]);
 assert.deepEqual(ids(nodes).slice(-2),[441,442]);assert.equal(nodes.find(node=>node.type==='th'&&text(node).startsWith('增加')).props['aria-sort'],'ascending');
 sortButton(nodes,'涨幅').props.onClick();nodes=h.render();assert.deepEqual(ids(nodes).slice(0,3),[439,438,437]);
 assert.deepEqual(ids(nodes).slice(-3),[440,441,442]);
 sortButton(nodes,'涨幅').props.onClick();nodes=h.render();assert.deepEqual(ids(nodes).slice(0,3),[400,401,402]);
 assert.deepEqual(ids(nodes).slice(-3),[440,441,442]);assert.equal(JSON.stringify(many),original);
 assert.equal(cards(nodes).props.sourceRows.length,43);assert.equal(nodes.filter(node=>node.props.className==='occasion-product').length,20);
});

test('date sorting restores the original calendar groups after a metric sort and resets the page',()=>{
 const dated=[row(501,'甲0929生日',10,{yesterday_status:'comparable',yesterday_delta:50}),
  row(502,'甲1005生日',1,{yesterday_status:'comparable',yesterday_delta:1}),
  row(503,'甲2026-09-29生日',2,{yesterday_status:'comparable',yesterday_delta:2}),
  row(504,'甲农历10月7日生日',20,{yesterday_status:'comparable',yesterday_delta:70})];
 let saved={sort:'date_desc',metricSort:null,page:1,dateScope:'all',dateSearch:'',search:'甲'};
 const h=harness({rows:dated,sourceRows:dated,observations:dated,viewState:saved,
  onViewStateChange:next=>{saved=next;h.props.viewState=next;}});let nodes=h.render();
 assert.deepEqual(ids(nodes),[503,502,501,504]);sortButton(nodes,'增加').props.onClick();nodes=h.render();
 assert.deepEqual(ids(nodes),[504,501,503,502]);assert.equal(saved.page,0);assert.equal(saved.metricSort.field,'delta');
 nodes.find(node=>node.type==='Dropdown'&&node.props.label==='排序').props.onChange('date_asc');nodes=h.render();
 assert.equal(saved.metricSort,null);assert.equal(saved.page,0);assert.equal(saved.search,'甲');assert.deepEqual(ids(nodes),[503,501,502,504]);
 assert.ok(nodes.filter(node=>node.type==='th'&&['增加','涨幅'].some(label=>text(node).startsWith(label))).every(node=>node.props['aria-sort']==='none'));
 guard.reviewedNarrativeQueries(cards(nodes).props,{warehouse_focus_items:query(dated),observations:query(dated)});
});

test('changing to the day before yesterday updates headers values sorting and baseline evidence without leaking another shop',()=>{
 const current=[row(601,'甲0929生日',40,{category:'yipin_gt10',yesterday_status:'comparable',yesterday_baseline_value:30,
  yesterday_baseline_date:'2026-10-05',yesterday_baseline_observation_id:701,yesterday_delta:10,yesterday_growth_rate:1/3,
  day_before_yesterday_status:'comparable',day_before_yesterday_baseline_value:20,day_before_yesterday_baseline_date:'2026-10-04',
  day_before_yesterday_baseline_observation_id:801,day_before_yesterday_delta:20,day_before_yesterday_growth_rate:1}),
  row(602,'乙0929生日',25,{category:'yipin_gt10',yesterday_status:'comparable',yesterday_baseline_value:5,
   yesterday_baseline_date:'2026-10-05',yesterday_baseline_observation_id:702,yesterday_delta:20,yesterday_growth_rate:4,
   day_before_yesterday_status:'zero_baseline',day_before_yesterday_baseline_value:0,day_before_yesterday_baseline_date:'2026-10-04',
   day_before_yesterday_baseline_observation_id:802,day_before_yesterday_delta:25,day_before_yesterday_growth_rate:null})];
 const sourceObservations=[...current,row(701,'甲0929生日',30,{date:'2026-10-05'}),row(702,'乙0929生日',5,{date:'2026-10-05'}),
  row(801,'甲0929生日',20,{date:'2026-10-04'}),row(802,'乙0929生日',0,{date:'2026-10-04'}),row(801,'别店0929生日',999,{shop_id:'B',date:'2026-10-04'})];
 let saved={sort:'date_desc',metricSort:{field:'growth_rate',direction:'desc'},dateSearch:'09-29',sales:'all',page:1};const changes=[];
 const h=harness({rows:current,sourceRows:current,observations:sourceObservations,comparison:'yesterday',viewState:saved,
  onViewStateChange:next=>{saved=next;h.props.viewState=next;},onComparisonChange:value=>{changes.push(value);h.props.comparison=value;}});
 let nodes=h.render();assert.deepEqual(ids(nodes),[602,601]);
 assert.deepEqual(Array.from(cards(nodes).props.sourceRowsByQuery.observations,item=>item.observation_id),[601,602,701,702]);
 const dropdown=nodes.find(node=>node.type==='Dropdown'&&node.props.label==='对比');assert.ok(dropdown);
 assert.deepEqual(Array.from(dropdown.props.choices),['yesterday','day_before_yesterday']);dropdown.props.onChange('day_before_yesterday');nodes=h.render();
 assert.deepEqual(changes,['day_before_yesterday']);assert.equal(saved.page,0);assert.equal(saved.dateSearch,'09-29');
 assert.deepEqual({...saved.metricSort},{field:'growth_rate',direction:'desc'});assert.deepEqual(ids(nodes),[601,602]);
 assert.match(text(nodes.find(node=>node.type==='th'&&text(node).startsWith('前天销量'))),/2026-10-04/);
 assert.deepEqual(Array.from(cards(nodes).props.sourceRowsByQuery.observations,item=>item.observation_id),[601,602,801,802]);
 const values=cells(nodes);assert.equal(text(values[3]),'20');assert.equal(text(values[4]),'+20');assert.equal(text(values[5]),'+100.0%');
 assert.equal(text(values[10]),'0');assert.equal(text(values[11]),'+25');assert.equal(text(values[12]),'从零起量');
 const comparisonScope=cards(nodes).props.scopeFilters.find(filter=>filter.field==='comparison');assert.ok(comparisonScope);assert.match(comparisonScope.value,/2026-10-04/);
 assert.match(text(cards(nodes)),/未标年份/);assert.ok(cards(nodes).props.sourceRowsByQuery.observations.every(item=>item.shop_id==='A'));
 guard.reviewedNarrativeQueries(cards(nodes).props,{warehouse_focus_items:query(current),observations:query(sourceObservations)});
});

test('metric ordering and calendar filters survive returning from an exact product view',()=>{
 const many=Array.from({length:45},(_,index)=>row(900+index,`角色0929生日 ${index}`,index+1,{category:'yipin_gt10',
  yesterday_status:'comparable',yesterday_delta:index,day_before_yesterday_status:'comparable',day_before_yesterday_delta:45-index}));
 let saved={search:'角色',dateSearch:'0929',dateScope:'dated',sort:'date_desc',metricSort:{field:'delta',direction:'asc'},page:1};
 let h=harness({rows:many,sourceRows:many,observations:many,viewState:saved,comparison:'day_before_yesterday',onViewStateChange:next=>saved=next});
 let nodes=h.render();const before=ids(nodes);nodes.find(node=>node.props.className==='warehouse-chart-button').props.onClick();
 assert.equal(h.opened[0].observation_id,before[20]);
 h=harness({rows:many,sourceRows:many,observations:many,viewState:saved,comparison:'day_before_yesterday',onViewStateChange:next=>saved=next});nodes=h.render();
 assert.deepEqual(ids(nodes),before);assert.match(text(nodes.find(node=>node.props.className==='occasion-pagination')),/2 \/ 3/);
 assert.equal(nodes.find(node=>node.type==='input'&&node.props['aria-label']==='搜索生日日期').props.value,'0929');
 assert.equal(nodes.find(node=>node.type==='th'&&text(node).startsWith('增加')).props['aria-sort'],'ascending');
});
