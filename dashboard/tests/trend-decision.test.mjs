import * as displayNames from '../src/content/dashboard/product-display-name.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as model from '../src/content/dashboard/trend-decision-model.js';
import {warehouseSource} from '../src/content/dashboard/warehouse-model.js';
const project=path.resolve(import.meta.dirname,'../..'),plugin=path.join(project,'runtime/data-analytics/1.0.11');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(plugin,'scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler(),guard=await import(pathToFileURL(path.join(plugin,'templates/data-app/base/src/source-provenance.js')));
const source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/TrendDecisionPanel.jsx'),'utf8');
const query=rows=>({rows,source:{label:'SYNTHETIC ONLY',tables:['synthetic']},methods:[]});
const date='2026-10-05',asset={sha256:'a'.repeat(64),data_url:'data:image/png;base64,AAAA',integrity_status:'verified_local'};
const products=Array.from({length:8},(_,i)=>({shop_id:'A',run_id:'a',date,observation_id:i+1,track_id:`card-${i+1}`,title:`合成商品 ${i+1}`,category:i<4?'yipin_gt10':'yipin_1to10',yipin_value:i<4?20+i:i-3,asset_sha256:asset.sha256,image_content_status:'verified_local'}));
const signals=products.map((p,i)=>({...p,anchor_run_id:p.run_id,anchor_observation_id:p.observation_id,included_in_watch:true,signal_code:'single_day_growth',label:'单日增长线索',level:'watch',history_days:2,consecutive_days:2,latest_delta:i+1,rate24:null,basis:'provisional_title_image',forecast_eligible:false,reasons:['合成每日对照']}));
const queries={trend_signal_summary:query([{shop_id:'A',date,reference_run_id:'a',full_snapshot_days:2}]),trend_signals:query(signals),warehouse_focus_items:query(products),image_assets:query([asset]),warehouse_display_history:query(products.flatMap(p=>[{shop_id:'A',anchor_run_id:'a',anchor_observation_id:p.observation_id,date,observation_id:p.observation_id},{shop_id:'B',anchor_run_id:'b',anchor_observation_id:p.observation_id,date,observation_id:p.observation_id}])),trend_decision_records:query([])};
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children||'');
function harness(overrides={},props={}){
 const scopedQueries={...queries,...overrides};let cursor=0;const states=[],opened=[];
 const react={useEffect(){},useMemo:fn=>fn(),useState(initial){const i=cursor++;if(!(i in states))states[i]=initial;return [states[i],value=>states[i]=value];}};
 const jsx=(type,props)=>({type,props}),shared=new Proxy({useDataApp:()=>({queries:scopedQueries})},{get:(obj,key)=>obj[key]??key});
 const context={exports:{},module:{exports:{}},require:id=>id.endsWith('product-display-name.js')?displayNames:id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='../../data-app-public.jsx'?shared:id.endsWith('trend-decision-model.js')?model:id.endsWith('warehouse-model.js')?{warehouseSource}:{}};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 const collect=(node,nodes)=>{if(Array.isArray(node))return node.forEach(n=>collect(n,nodes));if(!node||typeof node!=='object')return;nodes.push(node);collect(node.props?.children,nodes);};
 return {opened,queries:scopedQueries,render(){cursor=0;const nodes=[];collect(context.exports.TrendDecisionPanel({shopId:'A',onOpenProduct:row=>opened.push(row),...props}),nodes);return nodes;}};
}
test('two-day signals remain single-day observations, with at most five image-backed positive cards',()=>{
 const view=model.trendPanelView(queries,'A');assert.equal(view.cards.length,5);assert.deepEqual(view.cards.map(c=>c.product.observation_id),[8,7,6,5,4]);assert.equal(view.cards.every(c=>c.group==='observe'),true);assert.match(view.readiness,/2 天 · 数据积累中/);assert.match(view.conclusion,/暂无持续信号/);assert.equal(view.cards[0].product.category,'yipin_1to10');assert.equal(view.cards[0].product,products[7]);
});
test('sustained signals sort before observations but do not infer probability or burst success',()=>{
 const sustained={...signals[0],signal_code:'sustained_growth',label:'连续增长线索',history_days:4,consecutive_days:4,level:'watch',latest_delta:20};
 const view=model.trendPanelView({...queries,trend_signals:query([sustained,...signals.slice(1)])},'A');assert.equal(view.cards[0].product.observation_id,1);assert.equal(view.cards[0].group,'watch');assert.match(view.conclusion,/重点变化/);assert.equal(model.trendSignalGroup({...sustained,consecutive_days:3}),'observe');assert.equal(model.trendSignalGroup({...sustained,signal_code:'single_day_growth'}),'observe');
});
test('zero, unknown, other labels, unverified images and ambiguous product joins never enter watch cards',()=>{
 const altered=products.map((p,i)=>i===0?{...p,category:'yipin_zero',yipin_value:0}:i===1?{...p,category:'unknown',yipin_value:null}:i===2?{...p,category:'other_label'}:i===3?{...p,image_content_status:'url_only'}:p);
 const view=model.trendPanelView({...queries,warehouse_focus_items:query([...altered,{...altered[7]}])},'A');assert.deepEqual(view.cards.map(c=>c.product.observation_id),[7,6,5]);assert.equal(model.trendCardImage(products[0],new Map([[asset.sha256,{...asset,data_url:'https://example.com/image.jpg'}]])),null);
});
test('shop, capture date and reference run prevent stale cross-shop trend and image joins',()=>{
 const wrong={...signals[7],shop_id:'B',latest_delta:999};const old={...signals[6],run_id:'old',anchor_run_id:'old',latest_delta:998};
 const scoped={...queries,trend_signals:query([wrong,old,signals[0]])};const view=model.trendPanelView(scoped,'A');assert.equal(view.cards.length,1);assert.equal(view.cards[0].product.observation_id,1);assert.equal(view.sourceRowsByQuery.warehouse_display_history.length,1);assert.equal(view.sourceRowsByQuery.warehouse_display_history[0].shop_id,'A');
 const historical=model.trendPanelView(scoped,'A','2026-10-04');assert.equal(historical.cards.length,0);assert.equal(historical.summary,null);assert.match(historical.readiness,/尚未生成/);
});
test('verification label appears only for a formal same-shop same-run original-card record',()=>{
 const recorded={shop_id:'A',run_id:'a',date,observation_id:8,record_state:'recorded',evaluation_state:'waiting',due_date:'2026-10-12'};
 const view=model.trendPanelView({...queries,trend_decision_records:query([recorded,{...recorded,run_id:'old',observation_id:7},{...recorded,shop_id:'B',observation_id:6}])},'A');assert.equal(view.cards.filter(c=>c.record).length,1);assert.match(model.trendValidationLabel(view.cards[0].record),/已记录 · 2026-10-12 回看/);assert.equal(model.trendValidationLabel({...recorded,record_state:'draft'}),'');assert.equal(model.trendValidationLabel({...recorded,evaluation_state:'insufficient_data'}),'7天验证资料不足');assert.doesNotMatch(model.trendValidationLabel(recorded),/命中|成功/);
});
test('raw increments never become 24-hour growth when timestamp precision gives no normalized value',()=>{
 assert.equal(model.trendGrowthEvidence(signals[0],products[0]),'销量 20 件 · 本次 +1 件');assert.doesNotMatch(model.trendGrowthEvidence(signals[0],products[0]),/日增|24h/);assert.match(model.trendGrowthEvidence({...signals[0],rate24:1.52},products[0]),/折算24h \+1.5 件/);
});
test('panel compiles and every displayed fact retains exact reviewed query rows',()=>{
 compiler.parseCss(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/trend-decision.css'),'utf8'));
 const h=harness(),nodes=h.render(),panel=nodes.find(n=>n.props?.id==='trend-decision-panel');assert.ok(panel);guard.reviewedNarrativeQueries(panel.props,h.queries);assert.equal(panel.props.sourceRows[0],queries.trend_signal_summary.rows[0]);assert.equal(panel.props.displayRows.length,5);assert.equal(nodes.filter(n=>n.type==='img').length,5);assert.equal(nodes.some(n=>n.type==='Dialog'||n.type==='ZoomableImage'),false);assert.doesNotMatch(text(nodes),/爆款|命中率|成功率|本次推荐已记录/);
 panel.props.onOpen('source',panel.props);const sidebar=h.render().find(n=>n.type==='SourceSidebar');assert.equal(sidebar.props.getSource('warehouse_focus_items').rows.length,5);assert.equal(sidebar.props.getSource('warehouse_display_history').rows.every(row=>row.shop_id==='A'),true);
});
test('data-chart buttons pass the exact focused original card to the parent callback',()=>{
 const h=harness();h.render().find(n=>n.props?.className==='pdd-trend-chart-button').props.onClick();assert.equal(h.opened.length,1);assert.equal(h.opened[0],products[7]);assert.equal(harness({}, {onOpenProduct:undefined}).render().find(n=>n.props?.className==='pdd-trend-chart-button').props.disabled,true);
});
test('missing trend queries show an honest placeholder with no source or collection action',()=>{
 const nodes=harness({trend_signals:undefined}).render();assert.match(text(nodes),/本轮趋势数据尚未生成/);assert.equal(nodes.some(n=>n.type==='DataComponent'||n.type==='button'),false);assert.doesNotMatch(source,/AgentSearch|加入7天验证/);
});
test('backend selected review set and rank govern displayed recommendations and never backfill an unselected card',()=>{
 const ranked=signals.map((s,i)=>({...s,rank:8-i,selected_for_review:[0,2,3,6,7].includes(i),latest_delta:i===5?999:s.latest_delta}));
 const scoped={...queries,trend_signals:query(ranked)};assert.deepEqual(model.trendPanelView(scoped,'A').cards.map(c=>c.product.observation_id),[8,7,4,3,1]);
 const missingImage=products.map(p=>p.observation_id===8?{...p,image_content_status:'url_only'}:p);assert.deepEqual(model.trendPanelView({...scoped,warehouse_focus_items:query(missingImage)},'A').cards.map(c=>c.product.observation_id),[7,4,3,1]);
 assert.equal(model.trendPanelView({...queries,trend_signals:query(ranked.map(s=>({...s,selected_for_review:false})))},'A').cards.length,0);
});
test('legacy fallback uses backend level and growth ordering and never recommends zero growth',()=>{
 const rows=[{...signals[0],observation_id:1,level:'priority',latest_delta:1},{...signals[1],observation_id:2,latest_delta:20,rate24:5},{...signals[2],observation_id:3,latest_delta:10,rate24:5},{...signals[3],observation_id:4,latest_delta:0}];
 assert.deepEqual(model.trendPanelView({...queries,trend_signals:query(rows)},'A').cards.map(c=>c.product.observation_id),[1,2,3]);
});

function pollingHarness(){
 let cursor=0,effectDeps,cleanup,currentProps={shopId:'A'},reloads=0,timerId=0;const states=[],effects=[],timers=new Map(),requests=[];
 const react={useMemo:fn=>fn(),useState(initial){const i=cursor++;if(!(i in states))states[i]=initial;return [states[i],v=>states[i]=v];},useEffect(fn,deps){if(!effectDeps||deps.some((v,i)=>v!==effectDeps[i])){cleanup?.();effectDeps=[...deps];effects.push(()=>{cleanup=fn();});}}};
 const jsx=(type,props)=>({type,props}),shared=new Proxy({useDataApp:()=>({queries})},{get:(obj,key)=>obj[key]??key});
 const context={exports:{},module:{exports:{}},require:id=>id.endsWith('product-display-name.js')?displayNames:id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='../../data-app-public.jsx'?shared:id.endsWith('trend-decision-model.js')?model:id.endsWith('warehouse-model.js')?{warehouseSource}:{},AbortController,encodeURIComponent,fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject})),setInterval:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},clearInterval:id=>timers.delete(id),window:{location:{reload(){reloads++;}}}};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 const collect=(node,nodes)=>{if(Array.isArray(node))return node.forEach(n=>collect(n,nodes));if(!node||typeof node!=='object')return;nodes.push(node);collect(node.props?.children,nodes);};
 return {requests,timers,get reloads(){return reloads;},render(props={}){currentProps={...currentProps,...props};cursor=0;const nodes=[];collect(context.exports.TrendDecisionPanel(currentProps),nodes);for(const fn of effects.splice(0))fn();return nodes;},tick(){for(const timer of timers.values())timer.fn();},unmount(){cleanup?.();},async respond(index,status){requests[index].resolve({ok:true,json:async()=>status});await new Promise(resolve=>setImmediate(resolve));},async fail(index){requests[index].reject(new Error('offline'));await new Promise(resolve=>setImmediate(resolve));}};
}
test('local status polling runs once on mount and every 20s, new run prompts only manual reload',async()=>{
 const h=pollingHarness();h.render();assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/__pdd_trend_status?shop_id=A');assert.equal(h.requests[0].options.method,undefined);assert.equal([...h.timers.values()][0].ms,20000);
 await h.respond(0,{shop_id:'A',reference_run_id:'a',selected_count:5});assert.equal(h.render().some(n=>n.props?.className==='pdd-trend-new'),false);h.tick();assert.equal(h.requests.length,2);await h.respond(1,{shop_id:'A',reference_run_id:'a-new',selected_count:5});let nodes=h.render();assert.match(text(nodes.find(n=>n.props?.className==='pdd-trend-new')),/新一轮趋势分析已生成加载最新提醒/);assert.equal(h.reloads,0);nodes.find(n=>n.type==='button'&&text(n)==='加载最新提醒').props.onClick();assert.equal(h.reloads,1);assert.equal(nodes.find(n=>n.props?.id==='trend-decision-panel').props.displayRows.length,5);
});
test('poll errors preserve displayed cards and busy polling does not overlap requests',async()=>{
 const h=pollingHarness();h.render();h.tick();assert.equal(h.requests.length,1);await h.fail(0);assert.equal(h.render().find(n=>n.props?.id==='trend-decision-panel').props.displayRows.length,5);h.tick();assert.equal(h.requests.length,2);await h.respond(1,{shop_id:'other',reference_run_id:'new'});assert.equal(h.render().some(n=>n.props?.className==='pdd-trend-new'),false);assert.equal(h.reloads,0);
});
test('shop changes abort and ignore old responses, encode shop IDs and clean up timers',async()=>{
 const h=pollingHarness();h.render();h.render({shopId:'B /新'});assert.equal(h.requests[0].options.signal.aborted,true);assert.equal(h.timers.size,1);assert.equal(h.requests[1].url,`/__pdd_trend_status?shop_id=${encodeURIComponent('B /新')}`);await h.respond(0,{shop_id:'A',reference_run_id:'new-a'});assert.equal(h.render().some(n=>n.props?.className==='pdd-trend-new'),false);h.unmount();assert.equal(h.requests[1].options.signal.aborted,true);assert.equal(h.timers.size,0);await h.respond(1,{shop_id:'B /新',reference_run_id:'new-b'});assert.equal(h.render().some(n=>n.props?.className==='pdd-trend-new'),false);
});
