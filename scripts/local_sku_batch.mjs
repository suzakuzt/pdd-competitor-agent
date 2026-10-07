import {captureSku} from './local_sku_capture.mjs';
import {guard,ReviewError} from './local_browser.mjs';

const globalStatuses=new Set(['needs_login','needs_browser','needs_url','cancelled']);
const globalReasons=new Set(['access_restricted','source_changed','connection_required','browser_operation_failed','browser_dialog','sku_identity_changed']);
const localReasons=new Set(['sku_card_ambiguous','sku_card_obscured','sku_card_not_found','sku_entry_unavailable','sku_web_unavailable','sku_layout_unrecognized','sku_catalog_incomplete','sku_catalog_changed','sku_option_missing','sku_option_obscured','sku_selection_unverified','sku_combination_limit']);
// A missing product field is local. Unknown failures stay explicit rather than
// silently trying the entire shop against an unrecognized broken connection.
const mustStop=(status,reason)=>globalStatuses.has(status)||globalReasons.has(reason)||!localReasons.has(reason);

// One connection for the entire list. Checkpoint each product before moving on.
export async function captureSkuBatch(browser,request,{cancelled,progress,checkpoint,saveItem,capture=captureSku}={}){
 const rows=request.observations;
 if(!Array.isArray(rows)||rows.length>10000||new Set(rows.map(r=>r.observation_id)).size!==rows.length||rows.some(r=>!Number.isSafeInteger(r.observation_id)||r.observation_id<1||!['已拼','已抢'].includes(r.sales_label)||r.sales_precision!=='exact_display'||r.sales_unit!=='件'||!Number.isInteger(r.sales_value)||r.sales_value<=0))throw new ReviewError('SKU 队列必须是本轮有精确销量的原卡。');
 const batch={status:'running',total:rows.length,completed:0,partial:0,failed:0,remaining:rows.length,items:[]};
 let reuseShop=request.reuse_shop===true;
 const update=async extra=>{batch.remaining=rows.length-batch.items.length;await checkpoint(batch);await progress({phase:request.sku_only?'正在补采商品 SKU':'全店数据已入库，正在读取 SKU',stage:'sku',sku_total:rows.length,sku_completed:batch.completed,sku_partial:batch.partial,sku_failed:batch.failed,sku_remaining:batch.remaining,...extra});};
 await update();
 for(const row of rows){
  if(await cancelled()){batch.status='cancelled';batch.reason='operator_cancelled';break;}
  await update({sku_observation_id:row.observation_id,sku_title:row.title});
  try{
   const sku=await capture(browser,{...request,observation:row,reuse_shop:reuseShop},cancelled,p=>progress({...p,stage:'sku',sku_total:rows.length,sku_completed:batch.completed,sku_partial:batch.partial,sku_failed:batch.failed,sku_remaining:rows.length-batch.items.length,sku_observation_id:row.observation_id}));
   const urls=new Set(sku.variants.map(v=>v.image_url).filter(Boolean)),allImages=await browser.flush();
   const images=Object.fromEntries(Object.entries(allImages).filter(([url])=>urls.has(url)));
   const file=await saveItem({observation_id:row.observation_id,sku,images});
   browser.discardImages?.();
   batch.items.push({observation_id:row.observation_id,status:sku.status,capture_file:file,...(sku.status==='complete'?{}:{reason:sku.stop_reason||'sku_read_failed',message:sku.stop_message||'本商品部分规格未读完，已保存成功读取的规格。'})});
   batch[sku.status==='complete'?'completed':'partial']++;await update();
   if(sku.status!=='complete'&&mustStop(sku.stop_status,sku.stop_reason)){batch.status=sku.stop_status||'manual_review';batch.reason=sku.stop_reason||'sku_read_failed';batch.message=sku.stop_message||'本商品部分规格未读完，已保存结果保留。';break;}
  }catch(error){
   batch.items.push({observation_id:row.observation_id,status:error.status||'manual_review',reason:error.reason||'sku_read_failed',message:error instanceof ReviewError?error.message:'此商品 SKU 未完成，原记录保留。'});batch.failed++;await update();
   const fatal=mustStop(error.status,error.reason);
   if(fatal){batch.status=error.status||'manual_review';batch.reason=error.reason||'sku_read_failed';batch.message=error instanceof ReviewError?error.message:'采集程序未完成，已保存结果保留。';break;}
  }
  if(await cancelled()){batch.status='cancelled';batch.reason='operator_cancelled';break;}
  try{
   const page=await guard(browser);
   if(/\/goods1?\.html/.test(new URL(page.url).pathname))await browser.back();
   reuseShop=true;
  }catch(error){batch.status=error.status||'manual_review';batch.reason=error.reason||'sku_return_failed';batch.message=error instanceof ReviewError?error.message:'未能返回本店，已保存结果保留。';break;}
 }
 if(batch.status==='running')batch.status=batch.completed===rows.length?'complete':'partial';
 await update();return batch;
}
