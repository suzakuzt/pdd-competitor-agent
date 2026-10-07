import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as model from '../src/content/dashboard/algorithm-comparison-model.js';
import {warehouseSource} from '../src/content/dashboard/warehouse-model.js';

const project=path.resolve(import.meta.dirname,'../..'),plugin=path.join(project,'runtime/data-analytics/1.0.11');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(plugin,'scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler(),guard=await import(pathToFileURL(path.join(plugin,'templates/data-app/base/src/source-provenance.js')));
const source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/AlgorithmComparisonPanel.jsx'),'utf8');
const query=rows=>({rows,source:{label:'SYNTHETIC ONLY',tables:['synthetic']},methods:[]});
const meta={shop_id:'A',as_of_date:'2026-10-15',evaluated_at:'2026-10-15T08:00:00+08:00',backtest_version:'test-v1',source_protocol_version:'test-p1'};
const strategies=Object.keys(model.ALGORITHM_LABELS);
const channels=['confirmed','provisional'];
function fixture({cold=false}={}){
 const metric={mature_selected:cold?0:5,known:cold?0:3,hit:cold?0:2,unknown:cold?0:2,waiting:cold?0:4,known_hit_rate:cold?null:2/3,coverage:cold?null:3/5,lower_bound:cold?null:2/5,upper_bound:cold?null:4/5};
 return {
  warehouse_focus_summary:query([{shop_id:'A',date:'2026-10-14'}]),
  trend_backtest_summary:query(channels.map(channel=>({...meta,evidence_channel:channel,frozen_watch_count:191,channel_watch_count:channel==='provisional'?184:0,eligible_pool_cards:cold||channel==='confirmed'?0:9,waiting_selected:cold||channel==='confirmed'?0:4,known_selected:cold||channel==='confirmed'?0:3,unknown_selected:cold||channel==='confirmed'?0:2,readiness:cold||channel==='confirmed'?'insufficient_pool':'shadow_evaluation',excluded_missing_rate:50,excluded_missing_recent:0,excluded_identity_or_image:7}))),
  trend_backtest_metrics:query(channels.flatMap(evidence_channel=>strategies.map(strategy=>({...meta,evidence_channel,strategy,...metric})))),
  trend_backtest_windows:query(channels.flatMap(evidence_channel=>strategies.map(strategy=>({...meta,evidence_channel,strategy,date:'2026-10-05',cohort_id:'cohort-a',decision_at:'2026-10-05T09:00:00+08:00',due_date:'2026-10-12',pool_count:cold?0:9,pool_ids:cold?[]:[1,2,3,4,5,6,7,8,9],selected_ids:cold?[]:[1,2,3,4,5],...metric})))),
  trend_backtest_selection:query(channels.map(evidence_channel=>({...meta,evidence_channel,date:'2026-10-05',cohort_id:'cohort-a',training_cutoff:'2026-10-05T09:00:00+08:00',training_windows:0,eligible_prior_windows:0,chosen_parameter:null,candidate_parameter:0.75,readiness:'insufficient_history',shadow_only:true,forecast_validated:false})))
 };
}
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children??'');
function harness(initialQueries=fixture(),initialProps={}){
 let queries=initialQueries,props={shopId:'A',asOfDate:'2026-10-14',...initialProps},cursor=0;const states=[];
 const react={useMemo:fn=>fn(),useState(initial){const i=cursor++;if(!(i in states))states[i]=initial;return [states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];}};
 const jsx=(type,props)=>({type,props}),shared=new Proxy({useDataApp:()=>({queries})},{get:(obj,key)=>obj[key]??key});
 const context={exports:{},module:{exports:{}},require:id=>id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='../../data-app-public.jsx'?shared:id.endsWith('algorithm-comparison-model.js')?model:id.endsWith('warehouse-model.js')?{warehouseSource}:{}};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 const collect=(node,nodes)=>{if(Array.isArray(node))return node.forEach(n=>collect(n,nodes));if(!node||typeof node!=='object')return;if(typeof node.type==='function')return collect(node.type(node.props),nodes);nodes.push(node);collect(node.props?.children,nodes);};
 return {get queries(){return queries;},render(nextProps={},nextQueries=queries){queries=nextQueries;props={...props,...nextProps};cursor=0;const nodes=[];collect(context.exports.AlgorithmComparisonPanel(props),nodes);return nodes;}};
}

test('cold start selects the data-bearing channel without summing duplicated shop totals',()=>{
 const queries=fixture({cold:true}),before=JSON.stringify(queries),view=model.backtestView(queries,'A','2026-10-14');
 assert.equal(view.channel,'provisional');assert.equal(view.frozenCount,191);assert.equal(view.ready,false);assert.equal(view.readiness,'数据积累中');
 assert.equal(view.basic.length,3);assert.equal(view.blended.length,3);assert.equal(view.metrics.every(row=>model.backtestMetricDisplay(row,view.ready).waiting==='待积累'),true);
 assert.equal(JSON.stringify(queries),before);assert.equal(model.backtestView(queries,'A','','confirmed').channel,'confirmed');
});

test('unknown outcomes remain distinct from a measured zero hit rate',()=>{
 const unknown=model.backtestMetricDisplay({mature_selected:5,known:0,hit:0,unknown:5,waiting:0,known_hit_rate:null,coverage:0,lower_bound:0,upper_bound:1});
 assert.equal(unknown.known,'0 / 5');assert.equal(unknown.coverage,'0%');assert.equal(unknown.hit,'—');assert.equal(unknown.knownRate,'—');assert.equal(unknown.range,'—');
 const measured=model.backtestMetricDisplay({mature_selected:5,known:3,hit:0,unknown:2,waiting:0,known_hit_rate:0,coverage:.6,lower_bound:0,upper_bound:.4});
 assert.equal(measured.hit,'0 / 3');assert.equal(measured.knownRate,'0%');assert.equal(measured.range,'0%–40%');
 assert.equal(model.backtestMetricDisplay({known:3,mature_selected:2,hit:1,known_hit_rate:.3,lower_bound:.1,upper_bound:.8}).knownRate,'—');
 assert.equal(model.backtestPercent(NaN),'—');assert.equal(model.backtestPercent(null),'—');
});

test('metrics bind one shop, evidence channel and exact evaluation batch',()=>{
 const queries=fixture();const original=queries.trend_backtest_metrics.rows.find(row=>row.evidence_channel==='provisional');
 for(const patch of [{shop_id:'B'},{as_of_date:'2026-10-14'},{evaluated_at:'2026-10-15T07:00:00+08:00'},{backtest_version:'old-v0'},{source_protocol_version:'old-p0'}])queries.trend_backtest_metrics.rows.push({...original,...patch,hit:999});
 queries.trend_backtest_windows.rows.push({...queries.trend_backtest_windows.rows[6],date:'2026-10-16'});
 const view=model.backtestView(queries,'A','2026-10-14');assert.equal(view.metrics.length,6);assert.equal(view.metrics[0],original);assert.equal(view.windows.length,6);
 for(const rows of Object.values(view.sourceRowsByQuery))assert.equal(rows.every(row=>row.shop_id==='A'&&row.evidence_channel==='provisional'&&row.as_of_date===meta.as_of_date&&row.evaluated_at===meta.evaluated_at),true);
 assert.equal(view.selection[0].training_cutoff,'2026-10-05T09:00:00+08:00');assert.notEqual(view.selection[0].training_cutoff,meta.evaluated_at);
});

test('latest catalogue may show explicitly dated evaluation but historical captures cannot reuse it',()=>{
 const queries=fixture(),latest=model.backtestView(queries,'A','2026-10-14'),historical=model.backtestView(queries,'A','2026-10-05');
 assert.equal(latest.historical,false);assert.equal(latest.cutoff,'2026-10-15');assert.equal(latest.metrics.length,6);
 assert.equal(historical.historical,true);assert.equal(historical.metrics.length,0);assert.equal(historical.windows.length,0);assert.equal(historical.summary,null);
 const nodes=harness(queries,{asOfDate:'2026-10-05'}).render();assert.match(text(nodes),/此历史日期未生成算法对比/);assert.equal(nodes.some(n=>n.type==='DataComponent'),false);
});

test('compiled UI presents three base methods and folded trials without enabling a candidate parameter',()=>{
 compiler.parseCss(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/algorithm-comparison.css'),'utf8'));
 const h=harness(fixture({cold:true})),nodes=h.render(),tables=nodes.filter(n=>n.type==='table');
 assert.equal(tables.length,3);assert.equal(nodes.filter(n=>n.type==='details'&&n.props?.className==='algorithm-details')[0].props.open,undefined);
 assert.match(text(nodes),/本店留存 191 项候选/);assert.match(text(nodes),/缺少3段可比较的历史数据/);assert.match(text(nodes),/同图展示线索，未确认商品身份/);assert.match(text(nodes),/加权策略 未启用/);assert.doesNotMatch(text(nodes),/最佳算法已|胜出|训练样本/);assert.equal(nodes.filter(n=>n.type==='td').every(n=>!text(n).includes('0%')),true);
 assert.match(text(nodes),/训练截至 2026-10-05T09:00:00\+08:00/);assert.doesNotMatch(text(nodes),/试验参数：近期均速权重 75%/);
});

test('source actions retain exact reviewed rows and follow a manual channel switch',()=>{
 const h=harness(),nodes=h.render(),panel=nodes.find(n=>n.props?.id==='algorithm-comparison-table'),rounds=nodes.find(n=>n.props?.id==='algorithm-comparison-rounds');
 guard.reviewedNarrativeQueries(panel.props,h.queries);guard.reviewedNarrativeQueries(rounds.props,h.queries);
 assert.equal(panel.props.sourceRows[0],h.queries.trend_backtest_metrics.rows[6]);assert.equal(panel.props.sourceRowsByQuery.trend_backtest_summary[0],h.queries.trend_backtest_summary.rows[1]);
 panel.props.onOpen('source',panel.props);const opened=h.render().find(n=>n.type==='SourceSidebar');assert.equal(opened.props.getSource('trend_backtest_windows').rows.every(row=>row.evidence_channel==='provisional'),true);
 panel.props.headerControls.props.onChange('confirmed');const changed=h.render();assert.equal(changed.some(n=>n.type==='SourceSidebar'),false);assert.equal(changed.find(n=>n.props?.id==='algorithm-comparison-table').props.sourceRows.every(row=>row.evidence_channel==='confirmed'),true);
});

test('switching shop or same-date evaluation revision hides stale source rows',()=>{
 const h=harness(),panel=h.render().find(n=>n.props?.id==='algorithm-comparison-table');panel.props.onOpen('source',panel.props);assert.equal(h.render().some(n=>n.type==='SourceSidebar'),true);
 const changed=fixture();for(const value of Object.values(changed))for(const row of value.rows)if(row.evaluated_at)row.evaluated_at='2026-10-15T09:00:00+08:00';
 assert.equal(h.render({},changed).some(n=>n.type==='SourceSidebar'),false);assert.equal(h.render({shopId:'B'}).find(n=>n.props?.id==='algorithm-comparison-table').props.sourceRows.length,0);
});

test('missing queries leave a truthful placeholder with no model, collection or source request',()=>{
 const queries=fixture();delete queries.trend_backtest_selection;const nodes=harness(queries).render();assert.match(text(nodes),/数据积累中/);assert.equal(nodes.some(n=>n.type==='DataComponent'||n.type==='button'),false);
 assert.doesNotMatch(source,/fetch\(|setInterval\(|__pdd_collect|__pdd_agent/);
});
