import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as model from '../src/content/dashboard/sku-model.js';
import * as progressModel from '../src/content/dashboard/collection-progress.js';
import * as names from '../src/content/dashboard/product-display-name.js';
import * as time from '../src/content/dashboard/data-time.js';
const project=path.resolve(import.meta.dirname,'../..');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(project,'runtime/data-analytics/1.0.11/scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler(),source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/CollectionControls.jsx'),'utf8');
const row={shop_id:'synthetic',observation_id:12,title:'合成商品',category:'yipin_gt10',yipin_value:11,observed_at:'2026-10-06T04:00:00Z',price_raw:'券后¥1'};
const variant={specs:[{name:'款式',value:'合成甲'},{name:'尺寸',value:'15cm'}],selection_verified:true,current_price_yuan:'14.5',current_price_raw:'券后¥14.5',current_price_label:'券后价',original_price_yuan:'19.5',original_price_raw:'券前¥19.5',original_price_label:'券前价',available:true,image_data_url:'data:image/png;base64,c3ludGhldGlj'};
const capture={shop_id:'synthetic',observation_id:12,capture_id:'synthetic-only',goods_id:'123',goods_url:'https://mobile.yangkeduo.com/goods.html?goods_id=123',observed_at:'2026-10-06T05:34:56Z',status:'complete',all_combinations_visited:true,option_catalog_verified:true,expected_combinations:1,variants:[variant]};
const text=n=>typeof n==='string'||typeof n==='number'?String(n):Array.isArray(n)?n.map(text).join(''):text(n?.props?.children??'');
function harness(patch={},options={}){
 const data={browser_mode:'local_chrome',jobs:[],sku:null,...patch};let cursor=0;const states=[],requests=[];
 const url='/__pdd_collection_status?shop_id=synthetic&observation_id=12';
 const react={useState(initial){const i=cursor++;if(!(i in states))states[i]=i===0?(options.loading?null:{url:options.url||url,value:data}):i===1?options.error||'':initial;return [states[i],v=>states[i]=typeof v==='function'?v(states[i]):v];},useRef(initial){const i=cursor++;return states[i]??(states[i]={current:initial});},useEffect(){}};
 const jsx=(type,props,key)=>({type,props,key});
 const context={exports:{},module:{exports:{}},require:id=>id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='../../data-app-public.jsx'?{Dialog:'Dialog'}:id.endsWith('ZoomableImage.jsx')?{ZoomableImage:'ZoomableImage'}:id.endsWith('collection-progress.js')?progressModel:id.endsWith('sku-model.js')?model:id.endsWith('product-display-name.js')?names:id.endsWith('data-time.js')?time:{},fetch:async(url,requestOptions)=>{requests.push({url,options:requestOptions});return {ok:true,json:async()=>requestOptions?.method==='POST'?{job:{id:'synthetic-job',kind:'sku',shop_id:'synthetic',observation_id:12,status:'queued'}}:data};},encodeURIComponent,Date,Number,Math,AbortController};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 function collect(node,nodes){if(Array.isArray(node))return node.forEach(n=>collect(n,nodes));if(!node||typeof node!=='object')return;if(typeof node.type==='function')return collect(node.type(node.props),nodes);nodes.push(node);collect(node.props?.children,nodes);}
 return {requests,api:context.exports,render(){cursor=0;const nodes=[];collect(context.exports.SkuPanel({row:options.row||row,shopId:'synthetic'}),nodes);return nodes;}};
}
test('SKU uses an accessible expanded shared dialog and only mounts reads when opened',()=>{
 const h=harness();assert.equal(h.api.SkuDialog({row:null,shopId:'synthetic'}).props.children,null);
 const dialog=h.api.SkuDialog({row,shopId:'synthetic'});assert.equal(dialog.type,'Dialog');assert.equal(dialog.props.open,true);assert.equal(dialog.props.expanded,true);
 compiler.parseCss(fs.readFileSync(path.join(project,'dashboard/src/content/dashboard/sku-panel.css'),'utf8'));
});
test('loading and API errors do not show success, table or start an automatic capture',()=>{
 const h=harness({}, {loading:true}),nodes=h.render();assert.match(text(nodes),/正在读取 SKU 数据/);assert.equal(nodes.some(n=>n.type==='table'),false);assert.equal(nodes.find(n=>n.type==='button').props.disabled,true);assert.equal(h.requests.length,0);
 const failed=harness({}, {loading:true,error:'本机服务未响应'}).render();assert.match(text(failed),/本机服务未响应/);assert.equal(failed.find(n=>n.props?.role==='alert').type,'p');assert.doesNotMatch(text(failed),/完整采集|尚未采集/);
});
test('empty capture keeps table empty and only explicit action sends a scoped SKU request',async()=>{
 const h=harness(),nodes=h.render();assert.match(text(nodes),/尚未采集此商品 SKU/);assert.match(text(nodes),/点击“采集 SKU”后才会读取本商品规格图片和价格/);assert.doesNotMatch(text(nodes),/全店采集完成后|将依次读取|自动采集/);assert.equal(nodes.some(n=>n.type==='table'),false);assert.equal(h.requests.length,0);
 await nodes.find(n=>n.type==='button').props.onClick();assert.equal(h.requests.filter(r=>r.options?.method==='POST').length,1);assert.deepEqual(JSON.parse(h.requests[0].options.body),{shop_id:'synthetic',kind:'sku',observation_id:12});
});
test('each SKU shows its verified original/current amounts, coupon labels, image and saved minute',()=>{
 const nodes=harness({sku:capture}).render();assert.match(text(nodes),/1 个规格 · 完整采集/);assert.match(text(nodes),/2026-10-06 13:34/);assert.match(text(nodes),/¥19.50/);assert.match(text(nodes),/¥14.50/);assert.match(text(nodes),/券前价/);assert.match(text(nodes),/券后价/);assert.match(text(nodes),/款式：合成甲/);assert.match(text(nodes),/尺寸：15cm/);assert.equal(nodes.find(n=>n.type==='ZoomableImage'&&n.props.className==='sku-image').props.src,variant.image_data_url);assert.equal(nodes.find(n=>n.type==='table').props['aria-label'],'商品 SKU 图片与价格');assert.doesNotMatch(text(nodes),/¥1\D/);
});
test('a completed catalogue with no saved images is partial and reports the exact missing image count',()=>{
 const value={...capture,expected_combinations:3,variants:Array.from({length:3},(_,index)=>({...variant,sku_id:String(index),image_data_url:null}))},before=JSON.stringify(value);
 const nodes=harness({sku:value}).render();assert.match(text(nodes),/3 个规格 · 部分采集/);assert.match(text(nodes),/待补图片 3／当前价未显示 0/);assert.doesNotMatch(text(nodes),/完整采集/);
 assert.equal(nodes.filter(n=>n.type==='ZoomableImage').length,0);assert.ok(nodes.some(n=>n.type==='table'));assert.equal(text(nodes.find(n=>n.type==='button')),'重新采集 SKU');assert.equal(JSON.stringify(value),before);
 assert.deepEqual(model.skuCoverage(value),{catalogComplete:true,missingImages:3,missingCurrentPrices:0,complete:false});
});
test('missing webpage original price does not invalidate complete saved current prices and images',()=>{
 const value={...capture,variants:[{...variant,original_price_yuan:null,original_price_raw:null,original_price_label:null}]},nodes=harness({sku:value}).render();
 assert.match(text(nodes),/1 个规格 · 完整采集/);assert.match(text(nodes),/¥14.50/);assert.match(text(nodes),/未显示/);assert.doesNotMatch(text(nodes),/部分采集|待补图片|当前价未显示/);
 assert.equal(nodes.find(n=>n.type==='ZoomableImage').props.src,variant.image_data_url);assert.equal(model.skuCoverage(value).complete,true);
});
test('missing or unverified current prices count as incomplete but zero prices and unavailable variants do not add false gaps',()=>{
 const value={...capture,expected_combinations:4,variants:[{...variant,current_price_yuan:null},{...variant,selection_verified:false},{...variant,current_price_yuan:0},{specs:variant.specs,available:false,selection_verified:false}]};
 const nodes=harness({sku:value}).render();assert.match(text(nodes),/4 个规格 · 部分采集/);assert.match(text(nodes),/待补图片 0／当前价未显示 2/);assert.match(text(nodes),/¥0.00/);assert.match(text(nodes),/不可购买/);assert.doesNotMatch(text(nodes),/完整采集/);
 assert.deepEqual(model.skuCoverage(value),{catalogComplete:true,missingImages:0,missingCurrentPrices:2,complete:false});
});
test('an entirely unavailable but fully visited catalogue does not invent required prices or images',()=>{
 const value={...capture,variants:[{specs:variant.specs,available:false,selection_verified:false}]},nodes=harness({sku:value}).render();
 assert.equal(model.skuCoverage(value).complete,true);assert.match(text(nodes),/1 个规格 · 完整采集/);assert.match(text(nodes),/不可购买/);assert.doesNotMatch(text(nodes),/待补图片|当前价未显示/);
});
test('complete image and price values cannot hide partial, unverified or count-mismatched catalogues',()=>{
 for(const patch of [{status:'partial'},{all_combinations_visited:false},{option_catalog_verified:false},{expected_combinations:2},{expected_combinations:undefined},{expected_combinations:0,variants:[]}]){
  const nodes=harness({sku:{...capture,...patch}}).render();assert.match(text(nodes),/部分采集/);assert.match(text(nodes),/目录完整性待核验/);assert.doesNotMatch(text(nodes),/完整采集/);
 }
 const legacy={...variant,current_price_yuan:null,price_yuan:'14.50'};assert.equal(model.skuCoverage({...capture,variants:[legacy]}).complete,true);
 assert.equal(model.skuCoverage({...capture,variants:[{...variant,current_price_yuan:'9'.repeat(400)}]}).missingCurrentPrices,1);
});
test('unknown prices/images and partial coverage stay unknown; no product or other-SKU fallback',()=>{
 const nodes=harness({sku:{...capture,status:'partial',variants:[{specs:variant.specs,selection_verified:true,price_raw:'¥3–¥8',available:null}, {...variant,sku_id:'4',selection_verified:false}]}}).render();
 assert.match(text(nodes),/部分采集/);assert.match(text(nodes),/部分规格尚未读取/);assert.match(text(nodes),/未显示/);assert.match(text(nodes),/未核实/);assert.match(text(nodes),/暂无图片/);assert.doesNotMatch(text(nodes),/完整采集/);
 assert.equal(model.skuPrice({...variant,original_price_yuan:null},'original'),'未显示');assert.equal(model.skuPrice({...variant,current_price_yuan:0}),'¥0.00');assert.equal(model.skuPrice({...variant,current_price_yuan:'Infinity'}),'未显示');
});
test('wrong-product result is ignored and stale capture is explicitly labelled',()=>{
 assert.equal(model.skuView({sku:{...capture,observation_id:99}},row,'synthetic').capture,null);assert.equal(model.skuView({sku:{...capture,shop_id:'other'}},row,'synthetic').capture,null);
 assert.match(text(harness({sku:{...capture,observed_at:'2026-10-05T04:00:00Z'}}).render()),/早于当前商品清单/);
 const h=harness({sku:capture},{url:'/__pdd_collection_status?shop_id=other&observation_id=12'});assert.match(text(h.render()),/正在读取 SKU 数据/);assert.equal(h.render().some(n=>n.type==='table'),false);
});
test('zero and unknown do not offer capture; a running pipeline disables duplicate manual capture',()=>{
 for(const other of [{category:'yipin_zero',yipin_value:0},{category:'unknown',yipin_value:null}]){const nodes=harness({}, {row:{...row,...other}}).render();assert.equal(nodes.some(n=>n.type==='button'),false);}
 const nodes=harness({sku_pipeline:{status:'running',progress:{sku_total:250,sku_completed:1}}}).render();assert.equal(nodes.find(n=>n.type==='button').props.disabled,true);assert.match(text(nodes),/已有 SKU 任务正在运行/);
});
test('manual-review result preserves older capture with failure notice instead of declaring new completion',()=>{
 const nodes=harness({sku:capture,jobs:[{kind:'sku',observation_id:12,status:'manual_review',reason:'access_restricted'}]}).render();assert.match(text(nodes),/完成人工验证/);assert.match(text(nodes),/下方保留上次已保存结果/);assert.equal(nodes.some(n=>n.type==='table'),true);
});

test('ended batch failure stays bound to the selected card as history alongside the manual capture prompt',()=>{
 const pipeline={kind:'shop',shop_id:'synthetic',status:'complete',ended_at:'2026-10-06T06:00:00Z',sku_summary:{status:'partial',items:[{observation_id:99,status:'manual_review',message:'OTHER CARD ONLY'},{observation_id:12,status:'manual_review',reason:'sku_card_ambiguous',message:'本轮有完全相同标题和主图的多张原卡，SKU 单独留待核对。'}]}};
 const h=harness({sku_pipeline:pipeline}),nodes=h.render();assert.match(text(nodes),/本轮有完全相同标题和主图/);assert.doesNotMatch(text(nodes),/OTHER CARD ONLY/);assert.match(text(nodes),/尚未采集此商品 SKU/);
 assert.match(text(nodes.find(n=>n.type==='details')),/上次 SKU 采集结果/);assert.equal(nodes.some(n=>n.props?.role==='alert'),false);assert.equal(nodes.find(n=>n.type==='button').props.disabled,false);assert.equal(h.requests.length,0);
 const noMessage={...pipeline,sku_summary:{items:[{observation_id:12,status:'failed',reason:'sku_entry_unavailable'}]}};
 assert.match(text(harness({sku_pipeline:noMessage}).render()),/未找到此商品可读取的规格入口/);
 const wrongShop={...pipeline,shop_id:'other'};assert.match(text(harness({sku_pipeline:wrongShop}).render()),/尚未采集此商品 SKU/);
});

test('new successful capture suppresses older manual and batch failures but keeps a later failure',()=>{
 const priorJob={kind:'sku',observation_id:12,status:'manual_review',reason:'access_restricted',ended_at:'2026-10-06T04:50:00Z'};
 const priorPipeline={status:'complete',ended_at:'2026-10-06T05:00:00Z',sku_summary:{items:[{observation_id:12,status:'failed',reason:'sku_entry_unavailable'}]}};
 const nodes=harness({sku:capture,jobs:[priorJob],sku_pipeline:priorPipeline}).render();assert.doesNotMatch(text(nodes),/完成人工验证|未找到此商品|上次已保存结果/);assert.equal(nodes.some(n=>n.type==='table'),true);
 const later=harness({sku:capture,jobs:[{...priorJob,ended_at:'2026-10-06T06:00:00Z'}],sku_pipeline:priorPipeline}).render();assert.match(text(later),/完成人工验证/);assert.match(text(later),/上次已保存结果/);
});

test('SKU validation does not claim that browser collection is still running',()=>{
 const nodes=harness({sku_pipeline:{status:'running',progress:{stage:'sku_validation',sku_total:250,sku_completed:20}}}).render();assert.match(text(nodes),/SKU 已读取，正在备份并校验保存/);assert.doesNotMatch(text(nodes),/正在依次采集|尚未采集此商品/);assert.equal(text(nodes.find(n=>n.type==='button')),'SKU 校验中…');
});

test('main-image repair leaves SKU for an explicit later click',()=>{
 const nodes=harness({sku_pipeline:{kind:'shop',status:'running',progress:{stage:'images',image_total:500,image_saved:499,image_missing:1}}}).render();
 assert.match(text(nodes),/全店商品和主图正在采集；结束后可点击“采集 SKU”读取本商品/);assert.doesNotMatch(text(nodes),/正在依次采集|SKU 已读取|验收后才会开始/);
});

test('explicit recapture keeps saved variants and zoomable images visible during the new task',async()=>{
 const h=harness({sku:capture}),nodes=h.render(),button=nodes.find(n=>n.type==='button');assert.equal(text(button),'重新采集 SKU');assert.match(text(nodes),/采集期间保留已保存结果/);assert.equal(h.requests.length,0);
 await button.props.onClick();assert.deepEqual(JSON.parse(h.requests.find(r=>r.options?.method==='POST').options.body),{shop_id:'synthetic',kind:'sku',observation_id:12});
 const active=harness({sku:capture,jobs:[{kind:'sku',shop_id:'synthetic',observation_id:12,status:'running',progress:{phase:'定位原卡'}}]}).render();assert.equal(active.find(n=>n.type==='button').props.disabled,true);assert.ok(active.some(n=>n.type==='table'));assert.equal(active.find(n=>n.type==='ZoomableImage').props.src,variant.image_data_url);assert.match(text(active),/¥14.50/);
});

test('waiting or unclosed shop tasks remain blocking, while ended batch tasks allow a scoped retry',()=>{
 for(const patch of [{status:'host_claiming'},{status:'awaiting_browser'},{status:'manual_review',host_attempt_open:true}]){
  const nodes=harness({sku_pipeline:{kind:'shop',shop_id:'synthetic',...patch}}).render();assert.equal(nodes.find(n=>n.type==='button').props.disabled,true);
 }
 const nodes=harness({latest_sku_batch_job:{kind:'sku_batch',shop_id:'synthetic',status:'needs_browser',ended_at:'2026-10-06T06:00:00Z',sku_summary:{items:[{observation_id:12,status:'needs_browser',reason:'connection_required'}]}}}).render();assert.equal(nodes.find(n=>n.type==='button').props.disabled,false);assert.match(text(nodes),/上次 SKU 采集结果/);assert.equal(nodes.some(n=>n.props?.role==='alert'),false);
});

test('prices keep only amount and label visible, raw source stays in tooltip and identity note stays in source',()=>{
 const nodes=harness({sku:{...capture,identity_basis:'current_unique_card_candidate'}}).render();const price=nodes.find(n=>n.type==='td'&&n.props.title===variant.current_price_raw);assert.equal(text(price),'¥14.50券后价');assert.equal(price.props.title,'券后¥14.5');
 const source=nodes.find(n=>n.type==='details'&&n.props.className==='sku-source');assert.match(text(source),/商品对应关系待确认/);assert.equal(nodes.some(n=>n.props?.className==='sku-alert'&&/商品对应/.test(text(n))),false);
});

test('single SKU dialog shows live location, specification and verification progress without a shop import claim',()=>{
 const job={id:'synthetic-single',kind:'sku',shop_id:'synthetic',observation_id:12,status:'running',started_at:'2026-10-06T04:00:00Z'};
 for(const phase of ['在本店列表定位商品，核对标题和主图','正在翻页定位商品 · 第 3 次','直接打开已核实商品链接']){
  const nodes=harness({jobs:[{...job,progress:{phase}}]}).render();assert.match(text(nodes),new RegExp(`单品 SKU · ${phase}`));assert.match(text(nodes),/已用/);assert.equal(nodes.find(n=>n.props?.role==='progressbar').props['aria-valuenow'],undefined);assert.doesNotMatch(text(nodes),/全店数据已入库|采集本店|正在读取各规格/);
 }
 const reading=harness({jobs:[{...job,progress:{phase:'逐项读取 SKU 价格和图片',variants:2,total:4}}]}).render(),bars=reading.filter(n=>n.props?.role==='progressbar');
 assert.equal(bars.length,2);assert.equal(bars[1].props['aria-label'],'当前商品规格读取进度');assert.equal(bars[1].props['aria-valuenow'],50);assert.match(text(reading),/2 \/ 4 个规格 · 50%/);assert.doesNotMatch(text(reading),/全店数据已入库|完整采集/);
 const saving=harness({jobs:[{...job,progress:{phase:'SKU 已读取，正在备份和校验保存',variants:4,total:4}}]}).render();
 assert.match(text(saving),/单品 SKU 已读取，正在核验并保存/);assert.equal(text(saving.find(n=>n.type==='button')),'SKU 校验中…');assert.equal(saving.filter(n=>n.props?.role==='progressbar').length,1);assert.equal(saving.find(n=>n.props?.role==='progressbar').props['aria-valuenow'],undefined);assert.doesNotMatch(text(saving),/100%|全店数据已入库|SKU 采集中|备份并校验/);
 const currentSaving=harness({jobs:[{...job,progress:{phase:'单品 SKU 已读取，正在核验并保存',variants:4,total:4}}]}).render();
 assert.match(text(currentSaving),/单品 SKU 已读取，正在核验并保存/);assert.equal(currentSaving.filter(n=>n.props?.role==='progressbar').length,1);assert.equal(currentSaving.find(n=>n.props?.role==='progressbar').props['aria-valuenow'],undefined);assert.doesNotMatch(text(currentSaving),/100%|SKU 采集中/);
});
