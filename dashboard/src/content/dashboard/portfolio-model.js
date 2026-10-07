export const DIMENSION_LABELS={sales_bucket:'销量结构',sales_band:'销量观察档',sales_label_unit:'原标签与单位',display_price_band:'原展示金额',category_clue:'标题品类线索',image_coverage:'主图覆盖',identity_coverage:'身份覆盖',candidate_structure:'同标题结构'};
export const TARGET_STATUS={observed:'已有真实观察',pending_capture:'已登记 · 尚未采集',needs_identity:'待核实身份 · 尚未采集'};
export const TARGET_IDENTITY={stable_url:'有店铺网址线索',name_only:'只有名称，身份待核实',unresolved_share_link:'分享链接待解析核实',observed_source:'已有真实观察来源'};
const SALES_DIMENSION_LABELS={other_label:'其他销量标签（各口径独立）',unknown:'销量未知或未解析',yipin_1to9:'销量 1–9 件',yipin_eq10:'销量 =10 件',yipin_gt10:'销量 >10 件',yipin_zero:'销量 0 件',yipin_11_30:'销量 11–30 件',yipin_31_100:'销量 31–100 件',yipin_gt100:'销量 >100 件'};
export function dimensionLabel(row){
 const value=row.dimension_value||row.label;
 if(row.dimension_key==='sales_bucket'||row.dimension_key==='sales_band')return SALES_DIMENSION_LABELS[value]||row.label;
 if(row.dimension_key==='image_coverage')return {cached:'已有本地核验图',failed_or_blocked:'历史失败或限制',unavailable:'主图待补',pending:'主图待补'}[value]||row.label;
 if(row.dimension_key==='identity_coverage')return {unique:'商品 ID 本轮唯一',unknown:'商品 ID 待补或无效',conflict:'商品身份冲突'}[value]||row.label;
 if(row.dimension_key==='candidate_structure')return {multiple_eligible_candidate_group:'同标题至少 2 卡各自达标 · 全部组员',single_eligible_candidate_group:'同标题仅 1 卡达标 · 全部组员',not_in_candidate_group:'不在已建立的候选组'}[value]||row.label;
 if(row.dimension_key==='display_price_band'){const condition={coupon_after_display:'券后展示',display_unspecified:'未注明券条件'}[row.qualifiers?.price_condition]||'金额条件未知';return `${row.label} · ${condition}`;}
 return row.label;
}
export function availableShops(queries){
 if(queries.competitor_shops)return queries.competitor_shops.rows||[];
 return [...new Map((queries.runs?.rows||[]).filter(row=>row.shop_id).map(row=>[row.shop_id,{shop_id:row.shop_id,shop_name:row.shop_name||row.shop_id}])).values()];
}
export function scopeShopData(queries,shopId){
 const runs=(queries.runs?.rows||[]).filter(row=>row.shop_id===shopId),runIds=new Set(runs.map(row=>row.run_id));
 const observations=(queries.observations?.rows||[]).filter(row=>runIds.has(row.run_id));
 const histories=(queries.run_history?.rows||[]).filter(row=>runIds.has(row.run_id));
 const summaries=(queries.comparison_summaries?.rows||[]).filter(row=>runIds.has(row.target_run_id)&&(!row.baseline_run_id||runIds.has(row.baseline_run_id)));
 const comparisonIds=new Set(summaries.map(row=>row.comparison_id));
 return {runs,runIds,observations,histories,summaries,items:(queries.comparison_items?.rows||[]).filter(row=>comparisonIds.has(row.comparison_id)),
  arrivalSummary:(queries.new_arrival_summary?.rows||[]).find(row=>row.shop_id===shopId),arrivalItems:(queries.new_arrival_items?.rows||[]).filter(row=>runIds.has(row.run_id)),
  attempts:(queries.collection_attempts?.rows||[]).filter(row=>row.shop_id?row.shop_id===shopId:runIds.has(row.run_id))};
}
export function shopScope(shopId){return shopId?[{field:'shop_id',label:'店铺范围',value:shopId}]:[];}
export function drilldownProducts(products,drilldown){
 if(!drilldown)return [];
 const ids=new Set((drilldown.observation_ids||[]).map(String));
 return products.filter(row=>row.shop_id===drilldown.shop_id&&row.run_id===drilldown.run_id&&ids.has(String(row.observation_id)));
}
export function applyDrilldown(rows,drilldown,shopId,runId){
 if(!drilldown||drilldown.shop_id!==shopId||drilldown.run_id!==runId)return rows;
 const ids=new Set((drilldown.observation_ids||[]).map(String));
 return rows.filter(row=>ids.has(String(row.observation_id)));
}
export function makeDrilldown(row,label,sourceQuery){
 return {request_id:`${row.shop_id}:${row.run_id||row.reference_run_id}:${row.dimension_id||row.strategy_id||'all'}`,shop_id:row.shop_id,run_id:row.run_id||row.reference_run_id,observation_ids:[...(row.observation_ids||row.reference_observation_ids||[])],label,source_query:sourceQuery,source_id:row.dimension_id||row.strategy_id||row.shop_id};
}
export function makeShopRequest(name,url){
 const cleanName=String(name||'').trim(),cleanUrl=String(url||'').trim();
 if(!cleanName&&!cleanUrl)return '';
 return ['请按标准模型分析拼多多店铺。',cleanName?`名称：${cleanName}`:null,cleanUrl?`链接：${cleanUrl}`:null,'先核实店铺身份，保存完整或部分的实际采集范围与所有原始卡片，输出多维度分析和有来源的策略线索。','当前这只是请求草稿，尚未登记、尚未采集；请按现有本地采集流程处理。'].filter(Boolean).join('\n');
}
export function conciseText(value){
 const text=typeof value==='string'?value:value&&typeof value==='object'?value.text||value.statement||value.label||value.description||JSON.stringify(value):String(value??'');
 return text.replaceAll('no_confirmed_comparable_pairs','没有合格可比配对').replaceAll('no_post_baseline_run','起点后尚无记录，等待下一轮采集').replaceAll('not_configured','起点尚未配置');
}
