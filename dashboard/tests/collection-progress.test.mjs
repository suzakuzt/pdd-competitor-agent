import test from 'node:test';
import assert from 'node:assert/strict';
import {collectionProgress,collectionImageSummary,collectionReleasedImages,collectionFailureDetails} from '../src/content/dashboard/collection-progress.js';
const batch=progress=>({kind:'sku_batch',status:'running',progress:{stage:'sku',sku_total:10,sku_completed:2,sku_partial:1,sku_failed:1,...progress}});

test('failure details allowlist reason labels, task identifiers and timestamp fields',()=>{
 const details=collectionFailureDetails({id:'private/path?token=secret',status:'manual_review',reason:'capture_lock_held',message:'Traceback secret',ended_at:'invalid'});
 assert.equal(details.jobId,null);assert.equal(details.endedAt,null);assert.equal(details.reason,'capture_lock_held');assert.equal(details.status,'待检查');assert.match(details.summary,/尚未释放/);assert.doesNotMatch(JSON.stringify(details),/Traceback|secret|private\/path/);
 const unknown=collectionFailureDetails({status:'unexpected-secret',reason:'unreviewed-secret',message:'unreviewed-secret'});assert.equal(unknown.reason,null);assert.equal(unknown.status,'待核实');assert.doesNotMatch(JSON.stringify(unknown),/unreviewed-secret|unexpected-secret/);
});

test('permission diagnosis requires the explicit error type and matching worker reason',()=>{
 assert.match(collectionFailureDetails({reason:'local_worker_failed_requires_review',error_type:'PermissionError'}).cause,/文件访问权限/);
 assert.doesNotMatch(collectionFailureDetails({reason:'local_worker_failed_requires_review',error_type:'OtherError'}).cause,/文件访问权限/);
 assert.doesNotMatch(collectionFailureDetails({reason:'capture_lock_held',error_type:'PermissionError'}).cause,/文件访问权限/);
 const file=collectionFailureDetails({reason:'local_file_access_failed',error_type:'PermissionError'});assert.equal(file.reason,'local_file_access_failed');assert.match(file.summary,/本地文件访问失败/);assert.match(file.nextStep,/访问权限、是否被占用/);
});

test('SKU percentage measures all processed products while keeping outcomes separate',()=>{
 const value=collectionProgress(batch({variants:2,total:6,phase:'逐项读取 SKU 价格和图片'}));
 assert.equal(value.determinate,true);assert.equal(value.percent,40);assert.deepEqual(value.counts,{total:10,complete:2,partial:1,failed:1,processed:4});assert.deepEqual(value.segments,{complete:20,partial:10,failed:10});assert.equal(value.current.percent,33.3);assert.equal(value.current.total,6);assert.match(value.ariaText,/不是采集成功率/);
});

test('one hundred percent means traversal ended even if every product failed',()=>{
 const value=collectionProgress(batch({sku_total:10,sku_completed:0,sku_partial:0,sku_failed:10}));assert.equal(value.percent,100);assert.equal(value.segments.complete,0);assert.equal(value.segments.failed,100);assert.equal(value.counts.complete,0);assert.match(value.traversalNote,/仍有未完整项/);
 const done=collectionProgress(batch({sku_total:10,sku_completed:10,sku_partial:0,sku_failed:0}));assert.match(done.traversalNote,/等待保存校验/);
});

test('unknown shop denominator never reuses cards or arbitrary totals as percentage',()=>{
 const value=collectionProgress({kind:'shop',status:'running',progress:{cards:506,total:521,phase:'正在读取全店商品'}});assert.equal(value.determinate,false);assert.equal(value.percent,null);assert.equal(value.current,null);assert.equal(value.counts,null);assert.match(value.ariaText,/当前阶段进度尚无法计算/);
});

test('connection, backup, validation, publication and cancellation have indeterminate progress',()=>{
 for(const progress of [{phase:'正在连接你已打开的 Chrome'},{phase:'正在配对备份、恢复副本和验收入库'},{stage:'sku_validation',phase:'SKU 已读取，正在备份并校验保存规格'},{phase:'正在更新数据舱'}]){
  const value=collectionProgress(batch({...progress,variants:6,total:6}));assert.equal(value.determinate,false);assert.equal(value.percent,null);assert.equal(value.current,null);
 }
 for(const extra of [{status:'queued'},{status:'validating'},{cancel_requested:true},{host_status_unavailable:true}])assert.equal(collectionProgress({...batch({}),...extra}).percent,null);
});

test('missing optional counters start at zero, but malformed or overflowing counts never fabricate progress',()=>{
 const initial=collectionProgress({kind:'sku_batch',status:'running',progress:{stage:'sku',sku_total:250,sku_completed:0}});assert.equal(initial.percent,0);assert.equal(initial.counts.failed,0);
 for(const progress of [{sku_total:0},{sku_total:-1},{sku_total:'10'},{sku_completed:NaN},{sku_failed:null},{sku_failed:-1},{sku_completed:10}])assert.equal(collectionProgress(batch(progress)).percent,null);
 const almost=collectionProgress(batch({sku_total:10001,sku_completed:10000,sku_partial:0,sku_failed:0}));assert.equal(almost.percent,99.9);
});

test('current SKU ratio is independent of batch ratio and invalid current totals disappear',()=>{
 for(const progress of [{variants:3,total:0},{variants:7,total:6},{variants:null,total:6},{variants:2,total:'6'}])assert.equal(collectionProgress(batch(progress)).current,null);
 const single=collectionProgress({kind:'sku',status:'running',progress:{variants:1,total:4,phase:'逐项读取 SKU 价格和图片'}});assert.equal(single.percent,null);assert.equal(single.current.percent,25);
 for(const status of ['complete','partial','failed','cancelled'])assert.equal(collectionProgress({...batch({}),status}),null);
});

test('image filling reports confirmed coverage, not attempts or stale SKU counts',()=>{
 const value=collectionProgress({kind:'shop',status:'running',progress:{stage:'images',phase:'正在核对并补齐商品图片',image_total:100,image_saved:80,image_missing:20,image_attempted:100,sku_total:10,sku_completed:10}});
 assert.equal(value.percent,80);assert.equal(value.ariaLabel,'商品图片补齐进度');assert.equal(value.counts,null);assert.equal(value.current,null);assert.equal(value.segments.complete,80);assert.match(value.ariaText,/图片已齐 80 \/ 100 · 待补 20/);assert.doesNotMatch(value.ariaText,/100%|预计|分钟/);
 const all=collectionProgress({kind:'shop',status:'running',progress:{stage:'images',image_total:100,image_saved:100,image_missing:0}});assert.equal(all.percent,null);assert.match(all.traversalNote,/等待保存校验/);
});

test('image validation preserves coverage counts without presenting save completion',()=>{
 const job={kind:'shop',status:'running',progress:{stage:'image_validation',image_total:10,image_saved:10,image_missing:0}};
 const value=collectionProgress(job);assert.equal(value.percent,null);assert.equal(value.determinate,false);assert.equal(value.imageCounts.saved,10);assert.match(value.ariaText,/正在校验保存/);
 for(const extra of [{status:'queued'},{cancel_requested:true},{host_status_unavailable:true},{host_attempt_open:true}])assert.equal(collectionProgress({...job,progress:{...job.progress,stage:'images'},...extra}).percent,null);
});

test('invalid image counts and contradictory summaries never claim complete',()=>{
 assert.equal(collectionImageSummary(undefined),null);
 for(const patch of [{total:0},{total:'10'},{saved:11},{missing:-1},{missing:undefined},{saved:9,missing:0}]){
  const summary={total:10,saved:10,missing:0,status:'complete',...patch};assert.equal(collectionImageSummary(summary).status,'unknown');
  const progress=collectionProgress({kind:'shop',status:'running',progress:{stage:'images',image_total:summary.total,image_saved:summary.saved,image_missing:summary.missing}});assert.equal(progress.percent,null);
 }
 assert.equal(collectionImageSummary({total:10,saved:9,missing:1,status:'complete'}).status,'partial');
 assert.equal(collectionImageSummary({total:10,saved:10,missing:0,status:'partial'}).status,'unknown');
 assert.equal(collectionImageSummary({total:10,saved:10,missing:0,status:'complete'}).status,'complete');
});

test('shop SKU traversal never shows 100 percent with missing or unverified main images',()=>{
 for(const extra of [{},{image_summary:{total:10,saved:9,missing:1,status:'partial'}}]){
  const value=collectionProgress({...batch({sku_total:10,sku_completed:10,sku_partial:0,sku_failed:0}),kind:'shop',...extra});
  assert.equal(value.percent,null);assert.match(value.traversalNote,/整轮采集未完成/);
 }
});

test('append-only image recovery requires same-run independent verification, not the stale receipt count',()=>{
 const job={shop_id:'shop-a',status:'complete',receipt:{ok:true,status:'finished',shop_id:'shop-a',run_id:'run-a',snapshot_status:'complete',new_run:{status:'complete',cards:554,image_refs:416}},image_recovery:{run_id:'run-a'},image_summary:{total:554,saved:554,missing:0,status:'complete'}};
 assert.equal(collectionReleasedImages(job).status,'unknown');
 const verification={verified:true,basis:'current_run_image_refs',run_id:'run-a',shop_id:'shop-a',total:554,saved:554,missing:0,status:'complete'};
 assert.equal(collectionReleasedImages({...job,image_verification:verification}).status,'complete');
 for(const change of [{verified:false},{run_id:'wrong'},{shop_id:'wrong'},{total:555},{saved:553}])assert.equal(collectionReleasedImages({...job,image_verification:{...verification,...change}}).status,'unknown');
 const partial=collectionReleasedImages({...job,image_verification:{...verification,saved:553,missing:1,status:'partial'}});assert.equal(partial.status,'partial');assert.equal(partial.missing,1);
 assert.equal(job.receipt.new_run.image_refs,416);
});
test('backup, commit and dashboard stages retain image coverage without percentages or stale SKU progress',()=>{
 for(const [stage,label] of [['release_preparation','数据备份与入库预演进度'],['release_commit','商品数据保存进度'],['dashboard_build','数据舱更新进度']]){
  const value=collectionProgress({kind:'shop',status:'running',progress:{stage,image_total:555,image_saved:555,image_missing:0,sku_total:316,sku_completed:316}});
  assert.equal(value.determinate,false);assert.equal(value.percent,null);assert.equal(value.segments,null);
  assert.equal(value.counts,null);assert.equal(value.current,null);assert.equal(value.imageCounts.saved,555);
  assert.equal(value.ariaLabel,label);assert.doesNotMatch(value.ariaText,/100%|预计|分钟/);
 }
 for(const [phase,label] of [['正在配对备份、恢复副本和验收入库','数据备份与入库预演进度'],['正在更新数据舱','数据舱更新进度']]){
  const value=collectionProgress({kind:'shop',status:'running',progress:{stage:'image_validation',phase,image_total:10,image_saved:10,image_missing:0}});
  assert.equal(value.ariaLabel,label);assert.equal(value.percent,null);
 }
});
