import * as verifiedImages from '../src/content/dashboard/verified-image.js';
import * as skuModel from '../src/content/dashboard/sku-model.js';
import * as occasionModel from '../src/content/dashboard/occasion-model.js';
import * as dataTime from '../src/content/dashboard/data-time.js';
import * as displayNames from '../src/content/dashboard/product-display-name.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as model from '../src/content/dashboard/warehouse-model.js';
import * as focusModel from '../src/content/dashboard/warehouse-focus-model.js';
import * as trackingModel from '../src/content/dashboard/warehouse-tracking-model.js';
import * as displayModel from '../src/content/dashboard/warehouse-display-history-model.js';
const project=path.resolve(import.meta.dirname,'../..');
const plugin=path.join(project,'runtime/data-analytics/1.0.11');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(plugin,'scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler();
const guard=await import(pathToFileURL(path.join(plugin,'templates/data-app/base/src/source-provenance.js')));
const source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/WarehouseContent.jsx'),'utf8');
const query=rows=>({rows,source:{label:'SYNTHETIC ONLY',tables:['synthetic']},methods:[]});
const dates=['2026-10-04','2026-10-05','2026-10-06'];
const records=dates.map((date,i)=>({shop_id:'A',track_id:'same_id',goods_id:'123',identity_basis:'verified_goods_id',date,observed_at:`${date}T00:59:08Z`,observation_id:i+1,title:'合成跟踪商品',run_id:`a${i}`,monitor_origin:'reference_stock',monitor_first_date:'2020-01-01',price_raw:'券后¥4',sales_raw:`已拼${25+i*10}件`,sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:25+i*10}));
const extra=[{value:24,category:'yipin_gt10'},{value:10,category:'yipin_1to10'},{value:0,category:'yipin_zero'},{value:null,category:'other_label'},{value:null,category:'unknown'}].map((v,i)=>({...records[1],observation_id:i+10,track_id:`weak${i}`,goods_id:null,identity_basis:'independent_card',title:`合成分类商品${i}`,sales_value:v.value,sales_label:v.category==='other_label'?'已售':'已拼',sales_raw:v.value===null?'未显示':`已拼${v.value}件`,category:v.category}));
const points=records.map((row,i)=>({...row,is_complete:i<2,cumulative_yipin:row.sales_value,daily_delta:i?10:null,delta_status:i?'comparable':'no_previous_day',display_price_yuan:4,price_condition:'coupon_after_display'}));
const focused=[records[1],...extra].map((row,i)=>({...row,category:row.category||'yipin_gt10',yipin_value:row.sales_label==='已拼'?row.sales_value:null,yesterday_baseline_date:dates[0],yesterday_baseline_observation_id:i?100+i:1,yesterday_baseline_value:i?12:25,yesterday_match_basis:i?'provisional_title_image':'confirmed_goods_id',yesterday_delta:i===0?10:i===1?12:null,yesterday_growth_rate:i===0?.4:i===1?1:null,yesterday_status:i<2?'comparable':'sales_not_comparable',yesterday_rapid_growth:i<2,day_before_yesterday_status:'missing_baseline'}));
const queries={warehouse_days:query(dates.map((date,i)=>({shop_id:'A',date,run_id:`a${i}`,is_complete:i<2,card_count:i===1?6:1,version_count:1,observed_to:`${date}T01:00:00Z`,all_run_ids:[`a${i}`]}))),warehouse_records:query([...records,...extra]),warehouse_watch_records:query([...records,...extra]),warehouse_points:query(points),warehouse_tracks:query([]),warehouse_focus_summary:query([{shop_id:'A',date:dates[1],run_id:'a1'}]),warehouse_focus_items:query(focused),observations:query([...records,...extra]),image_assets:query([])};
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children||'');
test('warehouse local thumbnails require asset verification and the original SHA binding',()=>{
 const sha='a'.repeat(64),data_url=`/__pdd_image/${sha}`;
 const row={...focused[0],asset_sha256:sha,image_content_status:'verified_local'};
 for(const integrity_status of ['verified_local','unknown',undefined])for(const source of [data_url,`/__pdd_image/${'b'.repeat(64)}`,'https://example.invalid/image.png']){
  const nodes=harness({warehouse_focus_items:query([row]),image_assets:query([{sha256:sha,data_url:source,integrity_status}])}).render();
  const thumb=nodes.find(node=>node.type==='ZoomableImage'&&node.props.className==='warehouse-thumbnail');
  if(integrity_status==='verified_local'&&source===data_url)assert.equal(thumb.props.src,data_url);
  else assert.equal(thumb,undefined);
 }
});
function harness(overrides={},shopId='A'){
 const scopedQueries={...queries,...overrides};let cursor=0;const states=[];
 const react={useMemo:fn=>fn(),useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return [states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];}};
 const shared=new Proxy({useDataApp:()=>({queries:scopedQueries})},{get:(obj,key)=>obj[key]??key});
 const jsx=(type,props)=>({type,props});
 const context={exports:{},module:{exports:{}},require:id=>id.endsWith('verified-image.js')?verifiedImages:id.endsWith('sku-model.js')?skuModel:id.endsWith('CollectionControls.jsx')?{SkuDialog:'SkuDialog'}:id.endsWith('occasion-model.js')?occasionModel:id.endsWith('OccasionProducts.jsx')?{OccasionProducts:'OccasionProducts'}:id.endsWith('data-time.js')?dataTime:id.endsWith('product-display-name.js')?displayNames:id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='../../data-app-public.jsx'?shared:id.endsWith('warehouse-model.js')?model:id.endsWith('warehouse-focus-model.js')?focusModel:id.endsWith('warehouse-tracking-model.js')?trackingModel:id.endsWith('warehouse-display-history-model.js')?displayModel:id.endsWith('pdd-model.js')?{beijing:value=>value||'未知'}:id.endsWith('ZoomableImage.jsx')?{ZoomableImage:'ZoomableImage'}:{},Map,Set,Date,Math,JSON,Number,String,Boolean,Object,Array};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 const collect=(node,nodes)=>{if(Array.isArray(node))return node.forEach(n=>collect(n,nodes));if(!node||typeof node!=='object')return;nodes.push(node);for(const field of ['children','filters','headerControls'])collect(node.props?.[field],nodes);};
 return {render(){cursor=0;const nodes=[];collect(context.exports.WarehouseContent({shopId}),nodes);return nodes;}};
}
test('UI and CSS compile with canonical compiler',()=>{
 compiler.parseJavaScript(compiler.transform(source,{commonjs:false}));compiler.parseJavaScript(compiler.transform(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/DashboardContent.jsx'),'utf8'),{commonjs:false}));compiler.parseCss(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/warehouse.css'),'utf8'));
});

test('SKU button opens only the exact positive-sales card and closing preserves the sorted table',()=>{
 const h=harness();let nodes=h.render();const buttons=nodes.filter(n=>n.props?.className==='warehouse-sku-button');assert.equal(buttons.length,3);
 nodes.find(n=>n.props?.['aria-label']==='涨幅：点击按降序排序').props.onClick();nodes=h.render();
 const before=Array.from(nodes.find(n=>n.props?.id==='warehouse-cards').props.displayRows,r=>r.observation_id);
 nodes.find(n=>n.props?.className==='warehouse-sku-button').props.onClick();nodes=h.render();const modal=nodes.find(n=>n.type==='SkuDialog');assert.equal(modal.props.shopId,'A');assert.equal(modal.props.row.observation_id,before[0]);assert.ok(nodes.some(n=>n.props?.id==='warehouse-cards'));
 modal.props.onClose();nodes=h.render();assert.equal(nodes.find(n=>n.type==='SkuDialog').props.row,null);assert.deepEqual(Array.from(nodes.find(n=>n.props?.id==='warehouse-cards').props.displayRows,r=>r.observation_id),before);
 nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.onChange('yipin_1to10');nodes=h.render();assert.equal(nodes.filter(n=>n.props?.className==='warehouse-sku-button').length,1);
 nodes.find(n=>n.props?.['aria-pressed']===false&&text(n).includes('销量 0'))?.props.onClick();
});
test('default table includes every sales category from the complete catalogue with source-safe evidence',()=>{
 const nodes=harness().render(),cards=nodes.find(n=>n.props?.id==='warehouse-cards');
 assert.equal(cards.props.queryId,'warehouse_focus_items');assert.equal(cards.props.sourceRows.length,6);assert.equal(cards.props.sourceRows[0].date,'2026-10-05');
 assert.deepEqual(Array.from(cards.props.displayRows,r=>r.yipin_value),[35,24,10,0,null,null]);
 assert.equal(nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.value,'all');
 const current=nodes.find(n=>n.props?.id==='warehouse-current');assert.match(text(current),/部分记录/);assert.equal(current.props.sourceRows.length,2);
 assert.equal(nodes.filter(n=>n.type==='EvidenceChart').length,0);assert.equal(nodes.some(n=>n.props?.id==='warehouse-cohort-tabs'),false);
 for(const node of nodes.filter(n=>['DataComponent','EvidenceChart'].includes(n.type)))guard.reviewedNarrativeQueries(node.props,queries);
});
test('low and unknown sales are reachable without treating missing sales as zero',()=>{
 const h=harness();let nodes=h.render();nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.onChange('unknown');nodes=h.render();
 assert.equal(nodes.find(n=>n.props?.id==='warehouse-cards').props.sourceRows.length,1);assert.equal(nodes.find(n=>n.props?.id==='warehouse-cards').props.displayRows[0].yipin_value,null);
 nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.onChange('yipin_zero');nodes=h.render();assert.equal(nodes.find(n=>n.props?.id==='warehouse-cards').props.displayRows[0].yipin_value,0);
 nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.onChange('all');assert.equal(h.render().find(n=>n.props?.id==='warehouse-cards').props.sourceRows.length,6);
});
test('rapid list changes with comparison date and keeps evidence distinction',()=>{
 const h=harness();let nodes=h.render();nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.onChange('rapid');nodes=h.render();
 const cards=nodes.find(n=>n.props?.id==='warehouse-cards');assert.deepEqual(Array.from(cards.props.sourceRows,r=>r.observation_id).sort((a,b)=>a-b),[2,10]);
 assert.match(text(cards),/已确认/);assert.match(text(cards),/同图线索/);
 nodes.find(n=>n.props?.label==='销量对比').props.onChange('day_before_yesterday');nodes=h.render();assert.equal(nodes.find(n=>n.props?.id==='warehouse-cards').props.sourceRows.length,0);
 assert.match(text(nodes.find(n=>n.props?.id==='warehouse-cards')),/暂无商品|没有达到|尚无可对照/);
});
test('clicking product opens intact lifecycle sales, increments and separate price sources',()=>{
 const h=harness();let nodes=h.render();nodes.find(n=>n.type==='button'&&n.props?.className==='warehouse-chart-button').props.onClick();nodes=h.render();
 const charts=nodes.filter(n=>n.type==='EvidenceChart');assert.deepEqual(charts.map(n=>n.props.id),['warehouse-sales-chart','warehouse-delta-chart','warehouse-price-chart']);
 for(const c of charts){assert.equal(c.props.spec.stackable,false);assert.equal(c.props.rows.length,3);assert.equal(c.props.sourceRows.length,3);guard.reviewedNarrativeQueries(c.props,queries);}
 assert.equal(charts[2].props.sourceRows.every(r=>r.price_condition==='coupon_after_display'),true);assert.equal(nodes.find(n=>n.props?.label==='时间范围').props.value,'all');
});
test('clicking provisional card shows separate clue graph and original-card table without identity merging',()=>{
 const h=harness();let nodes=h.render();nodes.filter(n=>n.type==='button'&&n.props?.className==='warehouse-chart-button')[1].props.onClick();nodes=h.render();
 const chart=nodes.find(n=>n.props?.id==='warehouse-clue-chart');assert.equal(chart.type,'EvidenceChart');assert.deepEqual(Array.from(chart.props.rows,p=>p.display_yipin),[12,24]);assert.equal(chart.props.queryId,'warehouse_focus_items');assert.equal(chart.props.spec.stackable,false);
 assert.deepEqual(Array.from(nodes.find(n=>n.props?.id==='warehouse-clue-days').props.displayRows,p=>p.observation_id),[101,10]);
 assert.match(text(nodes.find(n=>n.props?.id==='warehouse-clue-days')),/身份待核实/);guard.reviewedNarrativeQueries(chart.props,queries);
 assert.equal(nodes.filter(n=>n.type==='details'&&n.props?.className==='warehouse-details'&&n.props.open===false).length,1);
});
test('stale catalogue comparison labels show exact dates and price keeps complete original tooltip',()=>{
 const nodes=harness().render(),control=nodes.find(n=>n.props?.label==='销量对比');assert.equal(control.props.choiceLabels.yesterday,'较昨日 · 2026-10-04');
 const overview=nodes.find(n=>n.props?.id==='warehouse-growth-overview');assert.match(overview.props.title,/2026-10-05/);assert.equal(overview.props.sourceRows.length,6);
 assert.equal(nodes.find(n=>n.props?.className==='warehouse-price-raw').props.title,'券后¥4');
});
test('search resets prior source and date switch cannot leak latest comparison rows',()=>{
 const h=harness();let nodes=h.render(),cards=nodes.find(n=>n.props?.id==='warehouse-cards');cards.props.onOpen('source',cards.props);assert.equal(h.render().filter(n=>n.type==='SourceSidebar').length,1);
 h.render().find(n=>n.type==='input').props.onChange({target:{value:'not found'}});nodes=h.render();assert.equal(nodes.filter(n=>n.type==='SourceSidebar').length,0);assert.equal(nodes.find(n=>n.props?.id==='warehouse-cards').props.sourceRows.length,0);
 nodes.find(n=>n.type==='input').props.onChange({target:{value:''}});h.render().find(n=>n.props?.label==='监测截至日期').props.onChange('2026-10-04');nodes=h.render();cards=nodes.find(n=>n.props?.id==='warehouse-cards');assert.equal(cards.props.queryId,'warehouse_records');assert.equal(cards.props.sourceRows.length,1);assert.equal(cards.props.displayRows[0].yesterday_delta,undefined);
 assert.equal(nodes.filter(n=>n.type==='EvidenceChart').length,0);
});

test('first view exposes all products, zero and unknown categories without a closed gate',()=>{
 const nodes=harness().render(),tabs=nodes.find(n=>n.props?.id==='warehouse-focus-tabs');
 for(const id of ['all','yipin_gt10','yipin_1to10','yipin_zero','unknown','other_label','daily_new'])assert.ok(tabs.props.items.some(item=>item.id===id),`${id} must have a direct tab`);
 assert.equal(tabs.props.value,'all');
 assert.equal(nodes.some(n=>n.type==='details'&&n.props?.className==='warehouse-details warehouse-other-categories'&&!n.props.open),false);
 assert.equal(nodes.find(n=>n.props?.className==='warehouse-priority-counts').props.children.length,4);
});

test('top metrics retain growth clues and their date-comparison actions',()=>{
 const h=harness();let nodes=h.render(),node=nodes.find(n=>n.props?.className==='warehouse-priority-counts');assert.match(text(node),/展示销量上涨/);assert.match(text(node),/快速增长线索/);
 assert.deepEqual(Array.from(node.props.children,c=>text(c.props.children[0])),['2','1','2','2']);
 node.props.children[2].props.onClick();nodes=h.render();assert.equal(nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.value,'rising');
 assert.equal(nodes.find(n=>n.props?.id==='warehouse-cards').props.displayRows.length,2);
});

test('right-side chart button enters a product screen instead of a modal and returns to the list',()=>{
 const h=harness();let nodes=h.render();assert.equal(nodes.some(n=>n.type==='Dialog'),false);
 nodes.find(n=>n.type==='button'&&n.props?.className==='warehouse-chart-button').props.onClick();nodes=h.render();assert.equal(nodes.some(n=>n.props?.id==='warehouse-cards'),false);assert.equal(nodes.some(n=>n.props?.className==='pdd-warehouse warehouse-product-detail'),true);assert.match(text(nodes.find(n=>n.props?.className==='warehouse-detail-heading')),/合成跟踪商品/);
 nodes.find(n=>n.props?.className==='warehouse-back-button').props.onClick();assert.equal(h.render().some(n=>n.props?.id==='warehouse-cards'),true);
});

test('one-to-ten main tab includes ten, excludes zero and opens the same data screen',()=>{
 const h=harness();let nodes=h.render();const tab=nodes.find(n=>n.props?.id==='warehouse-focus-tabs');assert.match(tab.props.items.find(i=>i.id==='yipin_1to10').label,/1–10件观察 · 1/);tab.props.onChange('yipin_1to10');nodes=h.render();const table=nodes.find(n=>n.props?.id==='warehouse-cards');assert.deepEqual(Array.from(table.props.displayRows,r=>r.yipin_value),[10]);assert.match(nodes.find(n=>n.props?.id==='warehouse-cards-section').props.title,/1–10/);guard.reviewedNarrativeQueries(table.props,queries);
 nodes.find(n=>n.type==='button'&&n.props?.className==='warehouse-chart-button').props.onClick();assert.equal(h.render().some(n=>n.props?.className==='pdd-warehouse warehouse-product-detail'),true);
});

test('one-to-ten product uses all-period display history with scoped evidence and explicit gaps',()=>{
 const historyRows=[{date:'2026-09-01',cumulative_yipin:1,observation_id:70,match_basis:'provisional_title_image',point_status:'matched'},{date:'2026-10-03',cumulative_yipin:null,observation_id:null,point_status:'ambiguous_identity'},{date:'2026-10-04',cumulative_yipin:8,observation_id:71,match_basis:'provisional_title_image',point_status:'matched'},{date:'2026-10-05',cumulative_yipin:10,observation_id:11,match_basis:'current_card',point_status:'anchor'}].map(p=>({shop_id:'A',anchor_observation_id:11,anchor_run_id:'a1',...p}));
 const historyQuery=query(historyRows),h=harness({warehouse_display_history:historyQuery});let nodes=h.render();nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.onChange('yipin_1to10');nodes=h.render();nodes.find(n=>n.type==='button'&&n.props?.className==='warehouse-chart-button').props.onClick();nodes=h.render();
 const chart=nodes.find(n=>n.props?.id==='warehouse-display-history-chart');assert.equal(chart.props.queryId,'warehouse_display_history');assert.equal(chart.props.sourceRows.length,4);assert.equal(chart.props.rows.length,36);assert.equal(chart.props.rows.find(p=>p.date==='2026-10-03').cumulative_yipin,null);assert.match(chart.props.description,/同标题同图/);guard.reviewedNarrativeQueries(chart.props,{...queries,warehouse_display_history:historyQuery});
 const increase=nodes.find(n=>n.props?.id==='warehouse-display-daily-increase');assert.equal(increase.props.spec.type,'bar');assert.equal(increase.props.spec.y,'daily_delta');assert.equal(increase.props.spec.showValues,true);assert.equal(increase.props.rows.find(p=>p.date==='2026-10-03').daily_delta,undefined);assert.equal(increase.props.rows.find(p=>p.date==='2026-10-02').daily_delta,null);assert.equal(increase.props.rows.some(p=>p.comparison_break),false);assert.equal(increase.props.sourceRows.length,4);guard.reviewedNarrativeQueries(increase.props,{...queries,warehouse_display_history:historyQuery});
 nodes.find(n=>n.props?.label==='时间范围').props.onChange('30');nodes=h.render();const limited=nodes.find(n=>n.props?.id==='warehouse-display-history-chart');assert.equal(limited.props.sourceRows.length,3);assert.equal(limited.props.rows.length,30);
});

test('return from charts preserves positive-sales group, search text and the selected table page',()=>{
 const many=Array.from({length:40},(_,i)=>({...extra[1],observation_id:200+i,track_id:`small-${i}`,title:`观察商品 ${i}`,yipin_value:10,category:'yipin_1to10',view_order:i}));
 const h=harness({warehouse_focus_items:query(many),warehouse_records:query(many),warehouse_watch_records:query(many)});let nodes=h.render();nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.onChange('yipin_1to10');nodes=h.render();nodes.find(n=>n.type==='input').props.onChange({target:{value:'观察'}});nodes=h.render();nodes.find(n=>n.type==='button'&&text(n)==='下一页').props.onClick();nodes=h.render();const firstTitle=nodes.find(n=>n.props?.className==='warehouse-title').props.children;
 nodes.find(n=>n.props?.className==='warehouse-chart-button').props.onClick();nodes=h.render();assert.match(text(nodes.find(n=>n.props?.className==='warehouse-detail-heading')),new RegExp(firstTitle));nodes.find(n=>n.props?.className==='warehouse-back-button').props.onClick();nodes=h.render();assert.equal(nodes.find(n=>n.props?.id==='warehouse-focus-tabs').props.value,'yipin_1to10');assert.equal(nodes.find(n=>n.type==='input').props.value,'观察');assert.equal(nodes.find(n=>n.props?.className==='warehouse-title').props.children,firstTitle);assert.match(text(nodes.find(n=>n.props?.className==='pdd-pagination')),/2 \/ 2/);
});
test('product thumbnails open the verified image while the rightmost data button separately navigates',()=>{
 const imgrow={...focused[0],asset_sha256:'a'.repeat(64),image_content_status:'verified_local'},h=harness({warehouse_focus_items:query([imgrow]),image_assets:query([{sha256:'a'.repeat(64),data_url:'data:image/png;base64,c3ludGhldGlj',integrity_status:'verified_local'}])}),nodes=h.render();
 const thumb=nodes.find(n=>n.props?.className==='warehouse-thumbnail');assert.equal(thumb.type,'ZoomableImage');assert.equal(thumb.props.src,'data:image/png;base64,c3ludGhldGlj');assert.equal(thumb.props.onClick,undefined);const title=nodes.find(n=>n.props?.className==='warehouse-title');assert.equal(title.type,'span');assert.equal(title.props.onClick,undefined);assert.equal(nodes.some(n=>n.type==='Dialog'),false);assert.equal(nodes.find(n=>n.props?.className==='warehouse-chart-button').props.children,'查看走势');assert.equal(nodes.find(n=>n.type==='th'&&n.props?.className==='warehouse-action-cell').props.children,'数据');
});


test('data timestamps use Beijing minutes and invalid source times stay unknown',()=>{
 assert.equal(dataTime.dataUpdateTime('2026-10-05T16:01:59Z'),'2026-10-06 00:01');
 assert.equal(dataTime.dataUpdateTime('2026-10-06T11:21:11+08:00'),'2026-10-06 11:21');
 assert.equal(dataTime.dataUpdateTime(null),'时间未知');assert.equal(dataTime.dataUpdateTime('bad'),'时间未知');
});

test('visible update time follows displayed complete catalogue and each product observation',()=>{
 const h=harness();let nodes=h.render();
 const update=nodes.find(n=>n.props?.id==='warehouse-data-updated');
 assert.match(text(update),/2026-10-05 09:00/);assert.doesNotMatch(text(update),/2026-10-06/);
 guard.reviewedNarrativeQueries(update.props,queries);
 assert.match(text(nodes.find(n=>n.props?.className==='warehouse-product-updated')),/2026-10-05 08:59/);
 nodes.find(n=>n.props?.className==='warehouse-chart-button').props.onClick();nodes=h.render();
 assert.match(text(nodes.find(n=>n.props?.className==='warehouse-detail-heading')),/数据更新：2026-10-05 08:59/);
 nodes.find(n=>n.props?.className==='warehouse-back-button').props.onClick();nodes=h.render();
 nodes.find(n=>n.props?.label==='监测截至日期').props.onChange('2026-10-04');nodes=h.render();
 assert.match(text(nodes.find(n=>n.props?.id==='warehouse-data-updated')),/2026-10-04 09:00/);
 assert.match(text(nodes.find(n=>n.props?.className==='warehouse-product-updated')),/2026-10-04 08:59/);
});


test('birthday and activity modules are direct tabs, include zero and unknown cards and return from trends to that module',()=>{
 const birthday={...extra[2],title:'合成角色生日立牌',observed_at:'2026-10-05T01:00:00Z'},activity={...extra[4],title:'合成艺人巡演应援摆件'};
 const themed=[birthday,activity],h=harness({warehouse_focus_items:query(themed),warehouse_records:query([...records,...themed]),warehouse_watch_records:query([...records,...themed])});
 let nodes=h.render(),tabs=nodes.find(n=>n.props?.id==='warehouse-occasion-tabs');
 assert.deepEqual(Array.from(tabs.props.items,i=>i.id),['all','birthday','activity']);
 assert.match(tabs.props.items[1].label,/1$/);assert.match(tabs.props.items[2].label,/1$/);
 tabs.props.onChange('birthday');nodes=h.render();let module=nodes.find(n=>n.type==='OccasionProducts');
 assert.equal(module.props.module,'birthday');assert.equal(module.props.date,'2026-10-05');assert.equal(module.props.rows.find(r=>r.observation_id===birthday.observation_id).yipin_value,0);
 assert.equal(nodes.some(n=>n.props?.id==='warehouse-cards'),false);
 module.props.onViewStateChange({search:'生日',sales:'zero',tag:'all',page:0});module.props.onOpenProduct(birthday);nodes=h.render();assert.match(text(nodes.find(n=>n.props?.className==='warehouse-detail-heading')),/合成角色生日立牌/);
 nodes.find(n=>n.props?.className==='warehouse-back-button').props.onClick();nodes=h.render();assert.equal(nodes.find(n=>n.type==='OccasionProducts').props.module,'birthday');assert.equal(nodes.find(n=>n.type==='OccasionProducts').props.viewState.search,'生日');
 nodes.find(n=>n.props?.id==='warehouse-occasion-tabs').props.onChange('activity');nodes=h.render();module=nodes.find(n=>n.type==='OccasionProducts');assert.equal(module.props.module,'activity');assert.equal(module.props.rows.find(r=>r.observation_id===activity.observation_id).yipin_value,null);
 nodes.find(n=>n.props?.id==='warehouse-occasion-tabs').props.onChange('all');nodes=h.render();assert.equal(nodes.some(n=>n.props?.id==='warehouse-cards'),true);
});

test('module date switch receives only its effective dated catalogue and source scope',()=>{
 const h=harness();let nodes=h.render();nodes.find(n=>n.props?.id==='warehouse-occasion-tabs').props.onChange('birthday');nodes=h.render();
 nodes.find(n=>n.props?.label==='监测截至日期').props.onChange('2026-10-04');nodes=h.render();const module=nodes.find(n=>n.type==='OccasionProducts');
 assert.equal(module.props.date,'2026-10-04');assert.equal(module.props.queryId,'warehouse_records');assert.ok(module.props.rows.every(r=>r.date==='2026-10-04'&&r.shop_id==='A'));
 assert.ok(module.props.sourceRows.every(r=>r.date==='2026-10-04'&&r.shop_id==='A'));
});

test('increase and growth headers toggle numeric ordering, accessible state and source scope',()=>{
 const h=harness();let nodes=h.render();
 const header=(all,label)=>all.find(n=>n.type==='button'&&n.props?.className==='warehouse-sort-button'&&text(n).startsWith(label));
 assert.deepEqual(nodes.filter(n=>n.type==='th'&&n.props?.['aria-sort']).map(n=>n.props['aria-sort']),['none','none']);
 header(nodes,'增加').props.onClick();nodes=h.render();
 let cards=nodes.find(n=>n.props?.id==='warehouse-cards');assert.deepEqual(Array.from(cards.props.displayRows,r=>r.observation_id),[10,2,11,12,13,14]);
 assert.equal(nodes.find(n=>n.type==='th'&&text(n).startsWith('增加')).props['aria-sort'],'descending');
 assert.equal(cards.props.scopeFilters.find(f=>f.field==='sort').value,'增加降序；无可比数据置后');assert.match(text(nodes.find(n=>n.props?.className==='pdd-pagination')),/按增加降序/);
 cards.props.onOpen('source',cards.props);header(h.render(),'增加').props.onClick();nodes=h.render();cards=nodes.find(n=>n.props?.id==='warehouse-cards');
 assert.deepEqual(Array.from(cards.props.displayRows,r=>r.observation_id),[2,10,11,12,13,14]);assert.equal(nodes.some(n=>n.type==='SourceSidebar'),false);
 assert.equal(nodes.find(n=>n.type==='th'&&text(n).startsWith('增加')).props['aria-sort'],'ascending');
 header(nodes,'涨幅').props.onClick();nodes=h.render();
 assert.equal(nodes.find(n=>n.type==='th'&&text(n).startsWith('涨幅')).props['aria-sort'],'descending');assert.equal(nodes.find(n=>n.type==='th'&&text(n).startsWith('增加')).props['aria-sort'],'none');
 guard.reviewedNarrativeQueries(nodes.find(n=>n.props?.id==='warehouse-cards').props,queries);
});

test('sort covers all filtered cards before pagination and survives chart return',()=>{
 const many=Array.from({length:40},(_,i)=>({...focused[0],observation_id:200+i,track_id:`sort-${i}`,title:`排序商品 ${i}`,yipin_value:100-i,yesterday_delta:i,yesterday_growth_rate:i/100,view_order:i}));
 const h=harness({warehouse_focus_items:query(many),warehouse_records:query(many),warehouse_watch_records:query(many)});let nodes=h.render();
 nodes.find(n=>n.type==='input').props.onChange({target:{value:'排序'}});nodes=h.render();
 nodes.find(n=>n.type==='button'&&text(n)==='下一页').props.onClick();nodes=h.render();assert.match(text(nodes.find(n=>n.props?.className==='pdd-pagination')),/2 \/ 2/);
 nodes.find(n=>n.props?.className==='warehouse-sort-button'&&text(n).startsWith('增加')).props.onClick();nodes=h.render();
 assert.match(text(nodes.find(n=>n.props?.className==='pdd-pagination')),/1 \/ 2/);assert.equal(nodes.find(n=>n.props?.className==='warehouse-title').props.children,'排序商品 39');
 assert.equal(nodes.filter(n=>n.props?.className==='warehouse-chart-button').length,25);
 nodes.find(n=>n.type==='button'&&text(n)==='下一页').props.onClick();nodes=h.render();const firstTitle=nodes.find(n=>n.props?.className==='warehouse-title').props.children;assert.equal(firstTitle,'排序商品 14');
 nodes.find(n=>n.props?.className==='warehouse-chart-button').props.onClick();nodes=h.render();nodes.find(n=>n.props?.className==='warehouse-back-button').props.onClick();nodes=h.render();
 assert.equal(nodes.find(n=>n.type==='input').props.value,'排序');assert.equal(nodes.find(n=>n.props?.className==='warehouse-title').props.children,firstTitle);
 assert.match(text(nodes.find(n=>n.props?.className==='pdd-pagination')),/按增加降序.*2 \/ 2/);assert.equal(nodes.find(n=>n.type==='th'&&text(n).startsWith('增加')).props['aria-sort'],'descending');
});

test('changing comparison uses that days numeric values and preserves selected sort',()=>{
 const rows=focused.slice(0,2).map((row,i)=>({...row,day_before_yesterday_status:'comparable',day_before_yesterday_delta:i?2:20,day_before_yesterday_growth_rate:i?.02:.2}));
 const h=harness({warehouse_focus_items:query(rows)});let nodes=h.render();nodes.find(n=>n.props?.className==='warehouse-sort-button'&&text(n).startsWith('增加')).props.onClick();nodes=h.render();
 assert.deepEqual(Array.from(nodes.find(n=>n.props?.id==='warehouse-cards').props.displayRows,r=>r.observation_id),[10,2]);
 nodes.find(n=>n.props?.label==='销量对比').props.onChange('day_before_yesterday');nodes=h.render();
 assert.deepEqual(Array.from(nodes.find(n=>n.props?.id==='warehouse-cards').props.displayRows,r=>r.observation_id),[2,10]);
 assert.equal(nodes.find(n=>n.type==='th'&&text(n).startsWith('增加')).props['aria-sort'],'descending');
});

test('occasion modules and all arrivals share comparison selection and the same SKU dialog',()=>{
 const h=harness();let nodes=h.render();
 nodes.find(n=>n.props?.label==='销量对比').props.onChange('day_before_yesterday');nodes=h.render();
 nodes.find(n=>n.props?.id==='warehouse-occasion-tabs').props.onChange('birthday');nodes=h.render();
 let module=nodes.find(n=>n.type==='OccasionProducts');assert.equal(module.props.comparison,'day_before_yesterday');
 const card=module.props.rows.find(row=>skuModel.skuEligible(row));assert.ok(card);
 module.props.onOpenSku(card);nodes=h.render();
 const dialog=nodes.find(n=>n.type==='SkuDialog');assert.equal(dialog.props.row.observation_id,card.observation_id);assert.equal(dialog.props.shopId,'A');
 dialog.props.onClose();nodes=h.render();module=nodes.find(n=>n.type==='OccasionProducts');module.props.onComparisonChange('yesterday');nodes=h.render();
 assert.equal(nodes.find(n=>n.type==='OccasionProducts').props.comparison,'yesterday');
 nodes.find(n=>n.props?.id==='warehouse-occasion-tabs').props.onChange('activity');nodes=h.render();assert.equal(nodes.find(n=>n.type==='OccasionProducts').props.comparison,'yesterday');
 nodes.find(n=>n.props?.id==='warehouse-occasion-tabs').props.onChange('all');nodes=h.render();assert.equal(nodes.find(n=>n.props?.label==='销量对比').props.value,'yesterday');
});

function completeCatalogue130(){
 const sha='d'.repeat(64),rows=Array.from({length:130},(_,i)=>{
  const value=i<19?30:i<48?5:i<80?0:null;
  return {...records[1],observation_id:1000+i,track_id:`independent-${i}`,goods_id:null,identity_basis:'independent_card',view_order:i+1,
   title:`合成${i%2?'徽章':'立牌'} ${i}`,sales_value:value,yipin_value:value,sales_raw:value===null?'':`已拼${value}件`,sales_label:value===null?null:'已拼',
   category:value===null?'unknown':value===0?'yipin_zero':value>10?'yipin_gt10':'yipin_1to10',asset_sha256:sha,image_content_status:'verified_local',
   yesterday_status:value===null?'sales_not_comparable':'comparable',yesterday_delta:value===null?null:i,yesterday_growth_rate:value===null?null:i/100,
   discovery_status:'existing',first_observed_date:'2026-10-04'};
 });
 return {rows,overrides:{warehouse_focus_items:query(rows),warehouse_records:query(rows),warehouse_watch_records:query(rows),observations:query(rows),
  warehouse_points:query(rows.map(row=>({...row,cumulative_yipin:row.sales_value,daily_delta:null,delta_status:'no_previous_day'}))),
  image_assets:query([{sha256:sha,data_url:`/__pdd_image/${sha}`,integrity_status:'verified_local'}])}};
}
const tableRows=nodes=>nodes.filter(node=>node.type==='tr'&&/原卡 #\d+/.test(text(node)));
const pageCardIds=nodes=>tableRows(nodes).map(node=>Number(text(node).match(/原卡 #(\d+)/)[1]));

test('all 130 independent cards are reachable once across six real table pages including zero and missing sales',()=>{
 const {rows,overrides}=completeCatalogue130(),h=harness(overrides),visited=[];let nodes=h.render();
 for(let page=0;page<6;page++){
  const cards=nodes.find(node=>node.props?.id==='warehouse-cards');
  assert.equal(cards.props.displayRows.length,130);assert.equal(cards.props.sourceRows.length,130);
  assert.match(text(nodes.find(node=>node.props?.className==='pdd-pagination')),new RegExp(`${page+1} / 6`));
  const ids=pageCardIds(nodes);assert.equal(ids.length,page===5?5:25);visited.push(...ids);
  assert.equal(nodes.filter(node=>node.props?.className==='warehouse-chart-button').length,ids.length);
  assert.equal(nodes.filter(node=>node.type==='ZoomableImage'&&node.props?.className==='warehouse-thumbnail').length,ids.length);
  assert.equal(nodes.filter(node=>node.props?.className==='warehouse-sku-button').length,rows.filter(row=>ids.includes(row.observation_id)&&row.sales_value>0).length);
  const next=nodes.find(node=>node.type==='button'&&text(node)==='下一页');assert.equal(next.props.disabled,page===5);
  if(!next.props.disabled){next.props.onClick();nodes=h.render();}
 }
 assert.deepEqual(visited,rows.map(row=>row.observation_id));assert.equal(new Set(visited).size,130);
 const cards=nodes.find(node=>node.props?.id==='warehouse-cards');guard.reviewedNarrativeQueries(cards.props,{...queries,...overrides});
});

test('zero and unshown-sales cards retain verified images and per-card timelines without SKU or invented sales',()=>{
 const {overrides}=completeCatalogue130(),h=harness(overrides);
 for(const [lane,expectedCount,expectedValue] of [['yipin_zero',32,0],['unknown',50,null]]){
  let nodes=h.render();nodes.find(node=>node.props?.id==='warehouse-focus-tabs').props.onChange(lane);nodes=h.render();
  const cards=nodes.find(node=>node.props?.id==='warehouse-cards');assert.equal(cards.props.displayRows.length,expectedCount);
  assert.ok(cards.props.displayRows.every(row=>row.yipin_value===expectedValue));
  assert.equal(nodes.filter(node=>node.props?.className==='warehouse-sku-button').length,0);
  assert.equal(nodes.filter(node=>node.type==='ZoomableImage'&&node.props?.className==='warehouse-thumbnail').length,25);
  const observationId=pageCardIds(nodes)[0];nodes.find(node=>node.props?.className==='warehouse-chart-button').props.onClick();nodes=h.render();
  assert.match(text(nodes.find(node=>node.props?.className==='warehouse-detail-heading')),new RegExp(`#${observationId}`));
  assert.equal(nodes.filter(node=>node.props?.className==='warehouse-sku-button').length,0);
  const timeline=nodes.find(node=>node.props?.id==='warehouse-product-days');assert.equal(timeline.props.sourceRows.length,1);
  assert.equal(timeline.props.sourceRows[0].observation_id,observationId);assert.equal(timeline.props.sourceRows[0].cumulative_yipin,expectedValue);
  assert.equal(nodes.find(node=>node.props?.id==='warehouse-sales-chart').props.sourceRows[0].cumulative_yipin,expectedValue);
  nodes.find(node=>node.props?.className==='warehouse-back-button').props.onClick();nodes=h.render();
  assert.equal(nodes.find(node=>node.props?.id==='warehouse-focus-tabs').props.value,lane);assert.equal(pageCardIds(nodes)[0],observationId);
 }
});

test('category, search and comparison changes reset page and evidence while preserving the selected sort',()=>{
 const {overrides}=completeCatalogue130(),h=harness(overrides);let nodes=h.render();
 const next=()=>{nodes.find(node=>node.type==='button'&&text(node)==='下一页').props.onClick();nodes=h.render();};
 const openSource=()=>{const cards=nodes.find(node=>node.props?.id==='warehouse-cards');cards.props.onOpen('source',cards.props);nodes=h.render();assert.ok(nodes.some(node=>node.type==='SourceSidebar'));};
 next();openSource();nodes.find(node=>node.props?.className==='warehouse-sort-button'&&text(node).startsWith('增加')).props.onClick();nodes=h.render();
 assert.equal(pageCardIds(nodes)[0],1079);assert.match(text(nodes.find(node=>node.props?.className==='pdd-pagination')),/1 \/ 6/);assert.equal(nodes.some(node=>node.type==='SourceSidebar'),false);
 next();openSource();nodes.find(node=>node.props?.label==='商品品类').props.onChange('standee');nodes=h.render();
 let cards=nodes.find(node=>node.props?.id==='warehouse-cards');assert.equal(cards.props.displayRows.length,65);assert.ok(cards.props.displayRows.every(row=>row.title.includes('立牌')));
 assert.match(text(nodes.find(node=>node.props?.className==='pdd-pagination')),/1 \/ 3/);assert.equal(nodes.some(node=>node.type==='SourceSidebar'),false);
 assert.equal(nodes.find(node=>node.type==='th'&&text(node).startsWith('增加')).props['aria-sort'],'descending');
 assert.ok(cards.props.scopeFilters.some(filter=>filter.field==='product_category'&&String(filter.value).includes('立牌')));
 next();openSource();nodes.find(node=>node.type==='input'&&node.props?.['aria-label']==='搜索数仓商品').props.onChange({target:{value:' 128'}});nodes=h.render();
 cards=nodes.find(node=>node.props?.id==='warehouse-cards');assert.deepEqual(Array.from(cards.props.displayRows,row=>row.observation_id),[1128]);assert.equal(cards.props.displayRows[0].yipin_value,null);
 assert.deepEqual(Array.from(cards.props.sourceRows,row=>row.observation_id),[1128]);assert.equal(nodes.some(node=>node.type==='SourceSidebar'),false);assert.match(text(nodes.find(node=>node.props?.className==='pdd-pagination')),/1 \/ 1/);
 openSource();nodes.find(node=>node.props?.label==='销量对比').props.onChange('day_before_yesterday');nodes=h.render();
 assert.equal(nodes.some(node=>node.type==='SourceSidebar'),false);assert.equal(nodes.find(node=>node.props?.label==='商品品类').props.value,'standee');
 assert.equal(nodes.find(node=>node.type==='th'&&text(node).startsWith('增加')).props['aria-sort'],'descending');
 cards=nodes.find(node=>node.props?.id==='warehouse-cards');assert.deepEqual(Array.from(cards.props.displayRows,row=>row.observation_id),[1128]);
 guard.reviewedNarrativeQueries(cards.props,{...queries,...overrides});
});

test('shop and date scopes keep foreign cards out and selecting an archive date restores the all-products lane',()=>{
 const bRows=focused.map(row=>({...row,shop_id:'B',run_id:'b1',observation_id:row.observation_id+9000,track_id:`b:${row.track_id}`,title:`别店${row.title}`}));
 const overrides={warehouse_days:query([...queries.warehouse_days.rows,...queries.warehouse_days.rows.map(row=>({...row,shop_id:'B',run_id:row.run_id.replace('a','b')}))]),
  warehouse_focus_summary:query([...queries.warehouse_focus_summary.rows,{shop_id:'B',run_id:'b1'}]),warehouse_focus_items:query([...focused,...bRows]),
  warehouse_records:query([...queries.warehouse_records.rows,...bRows]),warehouse_watch_records:query([...queries.warehouse_watch_records.rows,...bRows]),observations:query([...queries.observations.rows,...bRows])};
 const h=harness(overrides);let nodes=h.render();assert.ok(nodes.find(node=>node.props?.id==='warehouse-cards').props.displayRows.every(row=>row.shop_id==='A'));
 nodes.find(node=>node.props?.id==='warehouse-focus-tabs').props.onChange('unknown');nodes=h.render();
 nodes.find(node=>node.type==='input'&&node.props?.['aria-label']==='搜索数仓商品').props.onChange({target:{value:'合成'}});nodes=h.render();
 nodes.find(node=>node.type==='button'&&node.props?.className==='pdd-text-button'&&text(node)==='2026-10-04').props.onClick();nodes=h.render();
 assert.equal(nodes.find(node=>node.props?.id==='warehouse-focus-tabs').props.value,'all');assert.equal(nodes.find(node=>node.type==='input').props.value,'');
 const cards=nodes.find(node=>node.props?.id==='warehouse-cards');assert.deepEqual(Array.from(cards.props.displayRows,row=>row.observation_id),[1]);assert.ok(cards.props.sourceRows.every(row=>row.shop_id==='A'&&row.date==='2026-10-04'));
 const bNodes=harness(overrides,'B').render(),bCards=bNodes.find(node=>node.props?.id==='warehouse-cards');assert.equal(bCards.props.displayRows.length,6);assert.ok(bCards.props.sourceRows.every(row=>row.shop_id==='B'));
 assert.equal(bNodes.find(node=>node.props?.id==='warehouse-focus-tabs').props.value,'all');assert.equal(bNodes.find(node=>node.type==='input').props.value,'');
 guard.reviewedNarrativeQueries(cards.props,{...queries,...overrides});guard.reviewedNarrativeQueries(bCards.props,{...queries,...overrides});
});

test('daily records tab opens both confirmed-history and candidate first sightings without claiming an actual listing date',()=>{
 const statuses=['first_observed_after_complete','first_observed_candidate','initial_catalogue','identity_unresolved','existing'];
 const rows=focused.slice(0,5).map((row,i)=>({...row,view_order:i+1,title:i<2?'同标题独立立牌':row.title,discovery_status:statuses[i],first_observed_date:row.date,first_observation_id:row.observation_id}));
 const old={...rows[0],observation_id:99,track_id:'old-first',first_observed_date:'2026-10-04'};
 const overrides={warehouse_focus_items:query([...rows,old]),warehouse_records:query([...rows,old]),warehouse_watch_records:query([...rows,old])},h=harness(overrides);let nodes=h.render();
 const tabs=nodes.find(node=>node.props?.id==='warehouse-focus-tabs');assert.match(tabs.props.items.find(item=>item.id==='daily_new').label,/2$/);tabs.props.onChange('daily_new');nodes=h.render();
 const cards=nodes.find(node=>node.props?.id==='warehouse-cards');assert.equal(nodes.find(node=>node.props?.id==='warehouse-focus-tabs').props.value,'daily_new');
 assert.deepEqual(Array.from(cards.props.displayRows,row=>row.observation_id),[2,10]);assert.deepEqual(Array.from(cards.props.sourceRows,row=>row.observation_id),[2,10]);
 assert.equal(nodes.filter(node=>node.props?.className==='warehouse-chart-button').length,2);assert.match(text(nodes),/首次监测/);assert.match(text(nodes),/不代表当日上架|不是上架时间/);
 guard.reviewedNarrativeQueries(cards.props,{...queries,...overrides});
});

test('category tracking control opens visible scoped counts and selecting a category filters every sales group',()=>{
 const {overrides}=completeCatalogue130(),h=harness(overrides);let nodes=h.render();
 assert.equal(nodes.some(node=>node.props?.id==='warehouse-product-types'),false);
 const trigger=nodes.find(node=>node.props?.className==='warehouse-tracking-actions').props.children;
 trigger.props.onClick();nodes=h.render();const categoryPanel=nodes.find(node=>node.props?.id==='warehouse-product-types');assert.ok(categoryPanel);
 assert.equal(categoryPanel.props.sourceRows.length,130);assert.match(text(categoryPanel),/多.*品类|多个品类/);assert.match(text(categoryPanel),/SKU/);
 assert.ok(nodes.some(node=>node.type==='button'&&node.props?.className==='warehouse-category-active'&&text(node).includes('全部品类')));
 const standee=nodes.find(node=>node.type==='button'&&text(node)==='65立牌');assert.ok(standee);standee.props.onClick();nodes=h.render();
 const cards=nodes.find(node=>node.props?.id==='warehouse-cards');assert.equal(cards.props.displayRows.length,65);assert.ok(cards.props.displayRows.some(row=>row.yipin_value===0));assert.ok(cards.props.displayRows.some(row=>row.yipin_value===null));
 assert.equal(nodes.find(node=>node.props?.label==='商品品类').props.value,'standee');assert.equal(nodes.find(node=>node.props?.id==='warehouse-focus-tabs').props.value,'all');
 guard.reviewedNarrativeQueries(categoryPanel.props,{...queries,...overrides});guard.reviewedNarrativeQueries(cards.props,{...queries,...overrides});
});

test('date dropdown clears category, search, sort, source and SKU selections before exposing the chosen catalogue',()=>{
 const {overrides}=completeCatalogue130();overrides.warehouse_records=query([...overrides.warehouse_records.rows,records[0]]);overrides.warehouse_watch_records=overrides.warehouse_records;
 const h=harness(overrides);let nodes=h.render();nodes.find(node=>node.props?.label==='商品品类').props.onChange('standee');nodes=h.render();
 nodes.find(node=>node.props?.className==='warehouse-sort-button').props.onClick();nodes=h.render();
 nodes.find(node=>node.type==='input').props.onChange({target:{value:' 2'}});nodes=h.render();
 nodes.find(node=>node.props?.className==='warehouse-sku-button').props.onClick();nodes=h.render();assert.ok(nodes.find(node=>node.type==='SkuDialog').props.row);
 const before=nodes.find(node=>node.props?.id==='warehouse-cards');before.props.onOpen('source',before.props);nodes=h.render();
 nodes.find(node=>node.props?.label==='监测截至日期').props.onChange('2026-10-04');nodes=h.render();
 assert.equal(nodes.find(node=>node.props?.label==='商品品类').props.value,'all');assert.equal(nodes.find(node=>node.type==='input').props.value,'');
 assert.equal(nodes.find(node=>node.props?.id==='warehouse-focus-tabs').props.value,'all');assert.ok(nodes.filter(node=>node.type==='th'&&node.props?.['aria-sort']).every(node=>node.props['aria-sort']==='none'));
 assert.equal(nodes.some(node=>node.type==='SourceSidebar'),false);assert.equal(nodes.find(node=>node.type==='SkuDialog').props.row,null);
 const cards=nodes.find(node=>node.props?.id==='warehouse-cards');assert.deepEqual(Array.from(cards.props.displayRows,row=>row.observation_id),[1]);assert.ok(cards.props.sourceRows.every(row=>row.date==='2026-10-04'));
 assert.match(text(nodes.find(node=>node.props?.className==='pdd-pagination')),/1 \/ 1/);assert.equal(cards.props.scopeFilters.find(filter=>filter.field==='product_category').value,'全部品类');
});
