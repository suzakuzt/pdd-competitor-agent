// SKU values come from a selected specification capture, never the card's headline price.
export function skuEligible(row){return ['yipin_gt10','yipin_1to10'].includes(row?.category)&&Number.isFinite(row?.yipin_value)&&row.yipin_value>0;}
const skuAmount=value=>value!==null&&value!==undefined&&value!==''&&/^\d+(?:\.\d{1,2})?$/.test(String(value))&&Number.isFinite(Number(value));
export function skuPrice(row,kind='current'){
 if(row?.selection_verified!==true)return '未核实';
 const value=kind==='original'?row.original_price_yuan:(row.current_price_yuan??row.price_yuan);
 if(!skuAmount(value))return '未显示';
 return `¥${Number(value).toFixed(2)}`;
}
export function skuCoverage(capture){
 const variants=Array.isArray(capture?.variants)?capture.variants:[],required=variants.filter(row=>row?.available!==false);
 const missingImages=required.filter(row=>typeof row?.image_data_url!=='string'||!/^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/.test(row.image_data_url)).length;
 const missingCurrentPrices=required.filter(row=>row?.selection_verified!==true||!skuAmount(row.current_price_yuan??row.price_yuan)).length;
 const catalogComplete=capture?.status==='complete'&&capture.all_combinations_visited===true&&capture.option_catalog_verified===true&&Number.isSafeInteger(capture.expected_combinations)&&capture.expected_combinations>0&&variants.length===capture.expected_combinations;
 return {catalogComplete,missingImages,missingCurrentPrices,complete:catalogComplete&&missingImages===0&&missingCurrentPrices===0};
}
export function skuSpecs(row){return (row?.specs||[]).map(spec=>`${spec.name}：${spec.value}`).join(' / ')||'规格未记录';}
export function latestCollectionJob(jobs){
 const active=job=>['queued','running','validating','host_claiming','awaiting_browser'].includes(job?.status)||job?.host_attempt_open===true||job?.host_status_unavailable===true;
 const time=job=>[job.created_at,job.started_at,job.ended_at].map(value=>Date.parse(value||'')).find(Number.isFinite)||0;
 return jobs.filter(Boolean).sort((a,b)=>Number(active(b))-Number(active(a))||time(b)-time(a))[0]||null;
}
export function skuView(data,row,shopId){
 const capture=data?.sku?.shop_id===shopId&&String(data.sku.observation_id)===String(row?.observation_id)?data.sku:null;
 const jobs=(data?.jobs||[]).filter(job=>job.kind==='sku'&&(!job.shop_id||job.shop_id===shopId)&&String(job.observation_id)===String(row?.observation_id));
 const active=jobs.find(job=>['queued','running','validating','host_claiming','awaiting_browser'].includes(job.status));
 const latest=jobs[0];
 const observed=Date.parse(row?.observed_at||''),captured=Date.parse(capture?.observed_at||'');
 const pipeline=latestCollectionJob([data?.sku_pipeline,data?.latest_sku_batch_job].filter(job=>job&&(!job.shop_id||job.shop_id===shopId)));
 const batchItem=(pipeline?.sku_summary?.items||[]).find(item=>String(item.observation_id)===String(row?.observation_id));
 const batchOutcome=batchItem?{...batchItem,ended_at:batchItem.ended_at||pipeline.ended_at,started_at:batchItem.started_at||pipeline.started_at}:null;
 const ended=job=>Date.parse(job?.ended_at||job?.started_at||'');
 const failures={login_required:'请在原 Chrome 登录拼多多后重试。',connection_required:'请在 Chrome 允许本机采集连接后重试。',access_restricted:'请在 Chrome 完成人工验证后重试。',identity_mismatch:'未能确认当前商品，已停止读取，未混入其他商品。',sku_card_ambiguous:'本轮有同标题同主图的多张原卡，此商品 SKU 待核对。',sku_card_not_found:'未找到此原卡，本次未给它绑定其他同名商品。',sku_identity_changed:'商品与所选原卡不一致，本次 SKU 采集已停止。',sku_entry_unavailable:'未找到此商品可读取的规格入口。',sku_layout_unrecognized:'规格窗口暂未识别，本商品 SKU 未采集完成。',sku_backup_failed:'SKU 保存前的备份校验失败，原有数据保留。',sku_evidence_or_save_failed:'本商品规格证据未通过校验或保存，已有记录保留。'};
 const failureOutcome=[latest,batchOutcome].filter(job=>job&&['failed','partial','manual_review','interrupted','needs_login','needs_browser','needs_url','cancelled','not_started','pending'].includes(job.status)&&!(capture?.status==='complete'&&Number.isFinite(captured)&&Number.isFinite(ended(job))&&captured>ended(job))).sort((a,b)=>(Number.isFinite(ended(b))?ended(b):0)-(Number.isFinite(ended(a))?ended(a):0))[0];
 const reason=failureOutcome?.reason||failureOutcome?.recovery_reason;
 const failure=failureOutcome?(failureOutcome.message||failures[reason]||(failureOutcome.status==='needs_login'?failures.login_required:failureOutcome.status==='needs_browser'?failures.connection_required:'本商品 SKU 尚未采集完成，可以重试。')):'';
 const pipelineActive=pipeline&&(['queued','running','validating','host_claiming','awaiting_browser'].includes(pipeline.status)||pipeline.host_attempt_open===true||pipeline.host_status_unavailable===true)?pipeline:null;
 const validating=active?.status==='validating'||active?.progress?.stage==='sku_validation'||pipelineActive?.progress?.stage==='sku_validation';
 return {capture,coverage:skuCoverage(capture),active,latest,failure,failureIsHistorical:!!failureOutcome&&failureOutcome===batchOutcome&&!pipelineActive,pipelineActive,validating,stale:!!capture&&Number.isFinite(observed)&&Number.isFinite(captured)&&captured<observed,eligible:skuEligible(row)&&data?.sku_eligibility?.eligible!==false};
}

export function skuSummaryText(summary,{standalone=false}={}){
 if(!summary)return '';
 const reasons={sku_stage_failed_requires_review:'规格采集发生异常，已保存结果保留。',sku_backup_failed:'保存前的备份校验失败，已保存结果保留。',sku_browser_connection_closed:'Chrome 连接已结束。',sku_browser_process_failed:'浏览器采集程序未正常完成。',sku_browser_process_interrupted:'浏览器采集过程已中断。',sku_browser_receipt_missing:'未取得规格采集完成记录。',login_required:'请在原 Chrome 登录拼多多后重试。',connection_required:'请在 Chrome 允许本机采集连接后重试。',access_restricted:'请在 Chrome 完成人工验证后重试。',operator_cancelled:'本次规格采集已取消。',source_changed:'店铺页面发生变化，规格采集已停止。',browser_operation_failed:'浏览器连接或页面操作中断。',browser_dialog:'请先处理 Chrome 中的提示窗口后重试。',sku_identity_changed:'商品或店铺身份不符，规格采集已停止。',sku_return_failed:'未能返回店铺页面，后续规格采集已停止。'};
 const fallback={needs_login:reasons.login_required,needs_browser:reasons.connection_required,needs_url:'请更新本店分享链接后重试。',cancelled:reasons.operator_cancelled,pending:'规格采集尚未开始。',not_started:'规格采集尚未开始。',interrupted:'规格采集已中断，已保存结果保留。'};
 const message=typeof summary.message==='string'?summary.message.trim():'';
 const stopMessage=typeof summary.stop_message==='string'?summary.stop_message.trim():'';
 if(!Number.isInteger(summary.total)){
  const incomplete=['partial','failed','manual_review','interrupted','needs_login','needs_browser','needs_url','cancelled','pending','not_started'];
  if(!incomplete.includes(summary.status))return '';
  const reason=stopMessage||message||reasons[summary.reason]||fallback[summary.status]||'原因待确认，请检查本次采集状态。';
  return `SKU ${['pending','not_started'].includes(summary.status)?'尚未开始':'未完成'}：${reason}`;
 }
 const counts=`${standalone?'本轮 ':''}SKU 完整 ${summary.completed||0} / ${summary.total} 个商品${summary.partial?` · 部分 ${summary.partial}`:''}${summary.failed?` · 未成功 ${summary.failed}`:''}${summary.remaining?` · 待采 ${summary.remaining}`:''}${standalone&&Number.isInteger(summary.already_complete)?` · 已有完整 ${summary.already_complete}`:''}${standalone&&Number.isInteger(summary.scope_total)?` · 全部有销量 ${summary.scope_total}`:''}`;
 const stopped=['failed','manual_review','interrupted','needs_login','needs_browser','needs_url','cancelled'].includes(summary.status);
 if(!stopped)return counts;
 // Batch messages may only repeat counters; use the actual stop reason instead.
 const reason=stopMessage||reasons[summary.reason]||fallback[summary.status]||(message&&!/^SKU\s*完成\s*\d/.test(message)?message:'规格采集已停止，原因待核对。');
 return `${counts} · 停止原因：${reason}`;
}
