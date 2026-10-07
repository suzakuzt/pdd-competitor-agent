import test from 'node:test';
import assert from 'node:assert/strict';
import {skuSummaryText,latestCollectionJob} from '../src/content/dashboard/sku-model.js';

test('SKU stage exception without counters preserves its failure and explanation',()=>{
 const result=skuSummaryText({status:'manual_review',reason:'sku_stage_failed_requires_review',message:'全店数据已入库；后续 SKU 采集未完成，已保存规格保留。'});
 assert.match(result,/SKU 未完成/);assert.doesNotMatch(result,/全店已完成/);
 assert.match(result,/后续 SKU 采集未完成，已保存规格保留/);
 assert.doesNotMatch(result,/完整 0|\/ 0/);
});

test('reason-only exceptions and login states get readable explanations without invented counters',()=>{
 assert.equal(skuSummaryText({status:'failed',reason:'sku_backup_failed'}),'SKU 未完成：保存前的备份校验失败，已保存结果保留。');
 assert.equal(skuSummaryText({status:'manual_review',reason:'sku_stage_failed_requires_review'}),'SKU 未完成：规格采集发生异常，已保存结果保留。');
 assert.match(skuSummaryText({status:'needs_login'}),/SKU 未完成：请在原 Chrome 登录拼多多后重试/);
 assert.match(skuSummaryText({status:'needs_browser',reason:'connection_required'}),/允许本机采集连接/);
 assert.match(skuSummaryText({status:'manual_review',reason:'access_restricted'}),/完成人工验证/);
});

test('unstarted, cancelled, interrupted and unknown failures remain explicit',()=>{
 for(const status of ['pending','not_started'])assert.match(skuSummaryText({status}),/SKU 尚未开始：规格采集尚未开始/);
 assert.match(skuSummaryText({status:'cancelled'}),/SKU 未完成：本次规格采集已取消/);
 assert.match(skuSummaryText({status:'interrupted'}),/SKU 未完成：规格采集已中断/);
 assert.match(skuSummaryText({status:'failed',reason:'unknown_future_reason',message:'  '}),/SKU 未完成：原因待确认/);
 assert.match(skuSummaryText({status:'partial',message:{}}),/SKU 未完成/);
});

test('valid counters retain their existing complete and partial summaries',()=>{
 assert.equal(skuSummaryText({status:'complete',total:250,completed:250,partial:0,failed:0,remaining:0}),'SKU 完整 250 / 250 个商品');
 assert.equal(skuSummaryText({status:'partial',total:250,completed:240,partial:2,failed:3,remaining:5}),'SKU 完整 240 / 250 个商品 · 部分 2 · 未成功 3 · 待采 5');
 assert.equal(skuSummaryText({status:'complete',total:0,completed:0}),'SKU 完整 0 / 0 个商品');
});

test('absent or nonterminal SKU summaries do not invent a failure',()=>{
 for(const value of [undefined,null,{}, {status:'running'},{status:'complete'}])assert.equal(skuSummaryText(value),'');
});

test('real-shaped connection stop keeps all counters and appends its actionable reason',()=>{
 const result=skuSummaryText({status:'needs_browser',reason:'connection_required',total:250,completed:0,partial:0,failed:1,remaining:249,message:'SKU 完成 0/250 个商品，部分 0，未成功 1，待处理 249。'});
 assert.equal(result,'SKU 完整 0 / 250 个商品 · 未成功 1 · 待采 249 · 停止原因：请在 Chrome 允许本机采集连接后重试。');
 assert.equal((result.match(/250/g)||[]).length,1);
});

test('global login, challenge, cancellation and browser failures expose reasons with counters',()=>{
 const base={total:250,completed:10,failed:1,remaining:239};
 assert.match(skuSummaryText({...base,status:'needs_login'}),/停止原因：请在原 Chrome 登录拼多多后重试/);
 assert.match(skuSummaryText({...base,status:'manual_review',reason:'access_restricted'}),/停止原因：请在 Chrome 完成人工验证后重试/);
 assert.match(skuSummaryText({...base,status:'cancelled'}),/停止原因：本次规格采集已取消/);
 assert.match(skuSummaryText({...base,status:'failed',reason:'browser_operation_failed'}),/停止原因：浏览器连接或页面操作中断/);
 assert.match(skuSummaryText({...base,status:'manual_review',reason:'new_unknown_reason',message:'SKU 完成 10/250 个商品，未成功 1。'}),/停止原因：规格采集已停止，原因待核对/);
});

test('ordinary complete and local partial results do not acquire a global stop warning',()=>{
 assert.equal(skuSummaryText({status:'complete',total:3,completed:3,message:'已完成'}),'SKU 完整 3 / 3 个商品');
 assert.equal(skuSummaryText({status:'partial',reason:'sku_layout_unrecognized',total:3,completed:2,partial:1,remaining:0,message:'一个商品部分规格未读完'}),'SKU 完整 2 / 3 个商品 · 部分 1');
});

test('safe actual stop message overrides connection permission mapping both with and without totals',()=>{
 const base={status:'needs_browser',reason:'connection_required',stop_message:'  Chrome 页面操作已超时，本轮已停止。  ',message:'通用连接异常'};
 const without=skuSummaryText(base),withCounts=skuSummaryText({...base,total:250,completed:0,failed:1,remaining:249});
 assert.equal(without,'SKU 未完成：Chrome 页面操作已超时，本轮已停止。');
 assert.equal(withCounts,'SKU 完整 0 / 250 个商品 · 未成功 1 · 待采 249 · 停止原因：Chrome 页面操作已超时，本轮已停止。');
 for(const result of [without,withCounts])assert.doesNotMatch(result,/允许本机采集连接|通用连接异常/);
});

test('blank or non-string stop messages preserve existing reason fallback',()=>{
 for(const stop_message of [undefined,null,'   ',{},42]){
  const base={status:'needs_browser',reason:'connection_required',stop_message};
  assert.match(skuSummaryText(base),/允许本机采集连接/);
  assert.match(skuSummaryText({...base,total:1,failed:1}),/允许本机采集连接/);
 }
});

test('independent SKU summary distinguishes current retry count and already complete scope',()=>{
 assert.equal(skuSummaryText({status:'complete',total:248,completed:248,already_complete:2,scope_total:250},{standalone:true}),'本轮 SKU 完整 248 / 248 个商品 · 已有完整 2 · 全部有销量 250');
 const stopped=skuSummaryText({status:'failed',reason:'connection_required',stop_message:'Chrome 页面操作已超时。'},{standalone:true});assert.equal(stopped,'SKU 未完成：Chrome 页面操作已超时。');assert.doesNotMatch(stopped,/全店已完成/);
});

test('active collection wins over newer terminal and otherwise timestamps choose the newer batch or shop',()=>{
 const shop={id:'shop',kind:'shop',status:'complete',created_at:'2026-10-06T02:00:00Z'},batch={id:'batch',kind:'sku_batch',status:'complete',created_at:'2026-10-06T03:00:00Z'};
 assert.equal(latestCollectionJob([shop,batch]).id,'batch');assert.equal(latestCollectionJob([{...shop,status:'running'},batch]).id,'shop');assert.equal(latestCollectionJob([{...shop,created_at:'2026-10-06T04:00:00Z'},batch]).id,'shop');assert.equal(latestCollectionJob([null,undefined]),null);
});
