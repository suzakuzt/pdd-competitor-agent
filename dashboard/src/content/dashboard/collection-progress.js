// Report actual traversal counts only; shop size and save/build duration are unknown.
const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
const optionalCount=value=>value===undefined?0:count(value);
const percent=(value,total)=>Math.floor(value/total*1000)/10;
// Render reviewed explanations, never raw exceptions, filesystem paths or tokens.
export function collectionFailureDetails(job,reasonOverride){
 const reason=reasonOverride||job?.recovery_reason||job?.reason||'';
 const problems={
  capture_lock_held:['上次采集尚未释放，暂时不能启动。','采集独占锁仍被占用，本次请求没有取得采集权限。','先确认原任务是否已停止并处理未释放状态，再点开始采集；连续重试不会解除占用。'],
  connection_busy:['采集连接正被占用，暂时不能启动。','已有任务正在使用当前 Chrome 采集连接。','等待原任务结束；如果原任务已停止，先检查连接占用状态。'],
  connection_required:['Chrome 采集连接尚未就绪。','本机采集程序尚未连接到你的 Chrome。','在 Chrome 允许本机采集连接后，再点开始采集。'],
  login_required:['需要在原 Chrome 登录拼多多。','页面未确认有效登录状态。','在原 Chrome 完成登录后再采集；登录状态由 Chrome 保存。'],
  access_restricted:['需要先在 Chrome 完成人工验证。','网页要求验证，程序已停止自动操作。','在 Chrome 完成页面提示的验证后，再点开始采集。'],
  dashboard_build_failed:['数据已入库，数据舱更新失败。','本次数据发布未完成，当前页面可能仍显示之前的版本。','修复数据舱更新步骤后刷新页面，无需重复采集已入库数据。'],
  zero_products:['本次未读取到商品。','本店页面没有返回可确认的商品卡片。','确认原 Chrome 中店铺商品能正常显示，再更新本店分享链接。'],
  entry_unavailable:['本店分享链接不可用。','采集程序未能从当前入口打开本店商品列表。','粘贴本店最新可访问的分享链接，再点更新链接并采集。'],
  identity_mismatch:['打开的页面与本店不符。','当前页面未通过本店身份核对。','确认分享链接属于当前店铺后，更新链接并采集。'],
  local_file_access_failed:['采集因本地文件访问失败停止。','本机采集程序未能完成项目文件的读取或保存。','先检查项目目录和文件的访问权限、是否被占用；修复后再启动采集。'],
  local_worker_failed_requires_review:job?.error_type==='PermissionError'
   ?['采集因本地文件访问权限问题停止。','本机采集程序遇到文件访问权限错误。','先检查项目目录和文件的访问权限、是否被占用；修复后再启动采集。']
   :['本机采集程序异常停止，需要检查后再启动。','本机程序未正常完成本次采集，尚未确认具体原因。','根据下方任务编号检查采集记录，处理异常后再启动；不要连续重试。'],
 };
 const problem=Object.prototype.hasOwnProperty.call(problems,reason)?problems[reason]:null;
 const status=({failed:'失败',partial:'部分完成',manual_review:'待检查',interrupted:'已中断',cancelled:'已停止',needs_browser:'等待 Chrome 连接',needs_login:'等待登录',needs_url:'等待更新链接',complete:'已完成',running:'进行中'})[job?.status]||'待核实';
 const blocked=job?.host_attempt_open===true||job?.host_status_unavailable===true;
 const fallback=blocked?['采集状态待核实，暂时不能启动。','之前的任务尚未确认安全结束，已阻止重复启动。','先确认原任务是否仍在运行并处理异常，再开始新的采集。']:['采集未完成，需要检查本次任务。','本次采集已停止，具体原因尚未确认。','根据下方任务编号检查采集记录，处理异常后再启动。'];
 const [summary,cause,nextStep]=problem||fallback;
 return {summary,cause,nextStep,status,reason:problem?reason:null,jobId:typeof job?.id==='string'&&/^[a-zA-Z0-9_-]{1,80}$/.test(job.id)?job.id:null,
  endedAt:typeof job?.ended_at==='string'&&Number.isFinite(Date.parse(job.ended_at))?job.ended_at:null};
}
export function collectionImageSummary(summary){
 if(summary===undefined||summary===null)return null;
 const total=count(summary.total),saved=count(summary.saved),missing=count(summary.missing);
 const valid=total!==null&&total>0&&saved!==null&&missing!==null&&saved+missing===total;
 if(!valid)return {status:'unknown',total:null,saved:null,missing:null,text:'图片完整性待核验'};
 const status=summary.status==='complete'&&missing===0?'complete':missing>0?'partial':'unknown';
 return {status,total,saved,missing,text:`图片已齐 ${saved} / ${total} · 待补 ${missing}${status==='unknown'?' · 待核验':''}`};
}
export function collectionReleasedImages(job,shopId=job?.shop_id){
 const receipt=job?.receipt,actual=receipt?.new_run,total=count(actual?.cards),verified=job?.image_verification;
 const unknown=()=>collectionImageSummary({});
 if(receipt?.ok!==true||receipt.status!=='finished'||!receipt.run_id||!shopId||receipt.shop_id!==shopId||receipt.snapshot_status!=='complete'||actual?.status!=='complete'||total===null||total<=0)return unknown();
 if(job.image_recovery){
  if(verified?.verified!==true||verified.basis!=='current_run_image_refs'||verified.shop_id!==shopId||verified.run_id!==receipt.run_id||verified.total!==total)return unknown();
  return collectionImageSummary(verified);
 }
 const saved=count(actual?.image_refs);
 if(saved===null||saved>total)return unknown();
 const expected={total,saved,missing:total-saved,status:saved===total?'complete':'partial'},given=job?.image_summary;
 if(given!==undefined&&given!==null&&Object.entries(expected).some(([key,value])=>given[key]!==value))return unknown();
 return collectionImageSummary(expected);
}
const publicationStages={
 image_validation:{label:'正在核对图片缓存和本轮主图',buttonLabel:'图片校验中…',ariaLabel:'商品图片校验进度'},
 release_preparation:{label:'正在备份并预演入库',buttonLabel:'备份验收中…',ariaLabel:'数据备份与入库预演进度'},
 release_commit:{label:'正在保存商品数据',buttonLabel:'正在保存…',ariaLabel:'商品数据保存进度'},
 dashboard_build:{label:'正在更新数据舱',buttonLabel:'更新数据舱…',ariaLabel:'数据舱更新进度'},
};
export function collectionPublicationStage(job){
 const p=job?.progress||{};
 if(['sku','sku_batch'].includes(job?.kind)||String(p.stage||'').startsWith('sku'))return null;
 let key=p.stage;
 // Historical workers kept image_validation throughout backup and publication.
 // The explicit legacy phase remains authoritative for those old records.
 if(!['release_preparation','release_commit','dashboard_build'].includes(key)){
  if(/更新数据舱/.test(p.phase||''))key='dashboard_build';
  else if(/配对备份|恢复副本|验收入库|备份并预演入库|备份并校验数据/.test(p.phase||''))key='release_preparation';
 }
 return Object.prototype.hasOwnProperty.call(publicationStages,key)?{key,...publicationStages[key]}:null;
}
export function collectionProgress(job){
 if(!job||!['queued','running','validating','host_claiming','awaiting_browser'].includes(job.status)&&!job.host_attempt_open&&!job.host_status_unavailable)return null;
 const p=job.progress||{},total=count(p.sku_total),complete=optionalCount(p.sku_completed),partial=optionalCount(p.sku_partial),failed=optionalCount(p.sku_failed);
 const publication=collectionPublicationStage(job);
 if(publication){
  const imageCounts=p.image_total===undefined?null:collectionImageSummary({total:p.image_total,saved:p.image_saved,missing:p.image_missing,status:p.image_missing===0?'complete':'partial'});
  const explanation=publication.key==='image_validation'?'正在校验保存图片依据':'当前阶段';
  return {determinate:false,percent:null,counts:null,current:null,imageCounts,segments:null,
   ariaLabel:publication.ariaLabel,ariaText:`${publication.label}。${imageCounts?imageCounts.text+'。':''}${explanation}进度尚无法计算。`,
   stageLabel:publication.label,currentStage:null,traversalNote:''};
 }
 if(['images','image_validation'].includes(p.stage)){
  const imageCounts=collectionImageSummary({total:p.image_total,saved:p.image_saved,missing:p.image_missing,status:p.image_missing===0?'complete':'partial'});
  const determinate=p.stage==='images'&&job.status==='running'&&!job.cancel_requested&&!job.host_attempt_open&&!job.host_status_unavailable&&imageCounts.total!==null&&imageCounts.missing>0;
  return {determinate,percent:determinate?percent(imageCounts.saved,imageCounts.total):null,counts:null,current:null,imageCounts,
   segments:determinate?{complete:imageCounts.saved/imageCounts.total*100}:null,
   ariaLabel:'商品图片补齐进度',ariaText:`${imageCounts.text}。${p.stage==='image_validation'?'正在校验保存，进度尚无法计算。':'按已确认图片覆盖计算，不代表整轮采集完成。'}`,
   stageLabel:p.phase||'',currentStage:null,traversalNote:imageCounts.missing===0?'图片已读取，等待保存校验':''};
 }
 const validCounts=total!==null&&complete!==null&&partial!==null&&failed!==null&&complete+partial+failed<=total;
 const counts=validCounts?{total,complete,partial,failed,processed:complete+partial+failed}:null;
 const waiting=job.status!=='running'||job.cancel_requested===true||job.host_attempt_open===true||job.host_status_unavailable===true||p.stage==='sku_validation'||/连接|校验|验收|备份|发布|配对|更新数据舱/.test(p.phase||'');
 const imagesUnverified=job.kind==='shop'&&collectionReleasedImages(job).status!=='complete';
 const determinate=!waiting&&counts!==null&&total>0&&!(imagesUnverified&&counts.processed===total);
 const variants=count(p.variants),variantTotal=count(p.total);
 const current=!waiting&&!(imagesUnverified&&(job.image_summary?.missing>0||p.image_missing>0))&&variants!==null&&variantTotal!==null&&variantTotal>0&&variants<=variantTotal?{read:variants,total:variantTotal,percent:percent(variants,variantTotal)}:null;
 return {determinate,percent:determinate?percent(counts.processed,total):null,counts,current,
  segments:determinate?{complete:complete/total*100,partial:partial/total*100,failed:failed/total*100}:null,
  ariaLabel:total!==null||job.kind==='sku'||job.kind==='sku_batch'?'SKU 商品处理进度':'全店采集进度',
  ariaText:determinate?`${counts.processed} / ${total} 个商品已处理；完整 ${complete}，部分 ${partial}，失败 ${failed}。这是处理进度，不是采集成功率。`:`${p.phase||'等待采集'}；当前阶段进度尚无法计算。`,
  stageLabel:p.phase||'',
  currentStage:current?(p.phase||'读取当前商品规格'):null,
  traversalNote:imagesUnverified&&counts?.processed===total?'商品主图仍待补齐或验收，整轮采集未完成':determinate&&counts.processed===total?(partial||failed?'商品已遍历，仍有未完整项':'商品已遍历，等待保存校验'):''};
}
