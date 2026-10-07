export const HISTORY_MODES={yesterday_slot:'昨天同档位',previous:'上一轮'};
export const CHANGE_CATEGORIES={all:'全部变化与待核验',crossed:'新跨过 10 件',increased:'精确正增量',anomaly:'负增量 / 异常',candidate:'首次观察候选',unknown:'待核验 / 不可比',unchanged:'精确未变化',price:'展示价原文变化',title:'标题原文变化',image:'主图网址变化'};
export const EVENT_LABELS={increased:'精确正增量',unchanged:'精确未变化',crossed_gt10:'新跨过 10 件',negative_display_delta:'展示数回落异常',first_observed_candidate:'首次观察候选',unmatched:'未匹配观察',unknown:'不可比'};
export const COLLECTION_REASON_LABELS={start_window_elapsed_no_historical_collection_fabricated:'已超过开始窗口，未补造历史采集',no_start_record_within_window:'允许开始时段内没有执行记录',scheduler_connection_not_verified_for_this_slot:'本时段的定时执行连接尚未核实',browser_tool_timeout_after_one_connection_recovery:'浏览器在一次恢复后仍超时，保留已采部分',historical_manual_evidence:'已登记历史手动采集证据，不计作到点成功'};
export function collectionReasonLabel(reason,fallback='未记录额外原因'){
 if(!reason)return fallback;
 if(COLLECTION_REASON_LABELS[reason])return COLLECTION_REASON_LABELS[reason];
 if(String(reason).startsWith('explicit_recovery_abandonment:'))return '已人工确认放弃旧执行并恢复，原因见原文';
 return /[\u4e00-\u9fff]/.test(String(reason))&&!/^[a-z0-9_]+\s*:/i.test(String(reason))?String(reason):'已记录执行说明，展开查看原文';
}
export function timeBasisLabel(value){return {recorded_read_time:'实际读取时刻',observation_window:'观察窗口范围'}[value]||(value?'其他时间依据，见原文':'未知');}
export const REASON_LABELS={missing_baseline:'没有可用比较基线',missing_goods_id:'商品 ID 缺失',invalid_goods_id:'商品 ID 无效',duplicate_goods_id:'本轮商品 ID 不唯一',identity_conflict:'商品身份有冲突',observation_time_not_ordered:'前后观察时间顺序不能确认',invalid_observation_time:'观察时间无效',sales_not_exact:'前后销量并非均为精确数字',sales_label_changed:'前后销量标签不同',sales_unit_changed:'前后销量单位不同',negative_display_delta:'前台展示数回落，需复核',no_prior_observation:'此前没有该商品的可追溯观察',baseline_partial:'基线是部分采集，缺失不能证明首见',baseline_identity_incomplete:'基线商品身份覆盖不足，首见待核验',not_observed_in_target:'本轮未观察到，不能确认下架',seen_in_earlier_run:'更早轮次已有该 ID 记录，不属首见',history_coverage_incomplete:'更早历史或身份覆盖不足，首次观察待核验'};
export function historyRows(rows){return [...rows].sort((a,b)=>new Date(b.observed_to).valueOf()-new Date(a.observed_to).valueOf()||String(a.run_id).localeCompare(String(b.run_id)));}
export function selectComparison(summaries,targetRunId,mode){return summaries.find(row=>row.target_run_id===targetRunId&&row.mode===mode)||null;}
export function changeMatches(row,category){
 if(category==='crossed')return row.crossed_gt10===true||row.event==='crossed_gt10';
 if(category==='increased')return row.status==='comparable'&&Number.isFinite(row.display_delta)&&row.display_delta>0;
 if(category==='anomaly')return row.status==='anomaly';
 if(category==='candidate')return row.first_observed_candidate===true||row.event==='first_observed_candidate';
 if(category==='unknown')return row.status==='unknown';
 if(category==='unchanged')return row.status==='comparable'&&row.display_delta===0;
 if(category==='price')return row.price_raw_changed===true;
 if(category==='title')return row.title_changed===true;
 if(category==='image')return row.image_url_changed===true;
 return true;
}
export function reasonLabel(reason){return reason?REASON_LABELS[reason]||'判定说明详见来源':'无额外原因说明';}
export function filterChanges(rows,{search='',category='all'}={}){
 const query=String(search).trim().toLocaleLowerCase();
 return rows.filter(row=>changeMatches(row,category)&&(!query||[row.title,row.goods_id,row.comparison_item_id,row.reason,reasonLabel(row.reason),EVENT_LABELS[row.event],row.old_observation_id,row.new_observation_id].some(value=>String(value??'').toLocaleLowerCase().includes(query))));
}
export function deltaLabel(row){
 if(!['comparable','anomaly'].includes(row.status)||!Number.isFinite(row.display_delta))return '不可比';
 const value=`${row.display_delta>0?'+':''}${row.display_delta}`;
 return row.status==='anomaly'?`${value} · 异常`:value;
}
export function comparisonObservations(items,observations){
 const ids=new Set(items.flatMap(item=>[item.old_observation_id,item.new_observation_id,...(item.identity_clue_observation_ids||[]),...(item.earlier_observation_ids||[])]).filter(id=>id!==null&&id!==undefined));
 return observations.filter(row=>ids.has(row.observation_id));
}
export function priceChange(item,byId){
 const older=byId.get(item.old_observation_id),newer=byId.get(item.new_observation_id);
 return {old_raw:item.old_price_raw??older?.price_raw??null,new_raw:item.new_price_raw??newer?.price_raw??null,status:!older||!newer?'unpaired':item.price_raw_changed===true?'different_raw':item.price_raw_changed===false?'same_raw':'unknown'};
}
export function changeScope(summary,filters){
 return [{field:'comparison_id',label:'比较记录',value:summary.comparison_id},{field:'target_run_id',label:'目标轮次',value:summary.target_run_id},{field:'baseline_run_id',label:'基线轮次',value:summary.baseline_run_id||'无可用基线'},filters.search&&{field:'title',label:'标题 / 商品 ID / 记录 / 原因',value:filters.search},filters.category!=='all'&&{field:{price:'price_raw_changed',title:'title_changed',image:'image_url_changed',crossed:'crossed_gt10',increased:'display_delta',anomaly:'status',candidate:'first_observed_candidate',unknown:'status',unchanged:'display_delta'}[filters.category]||'event',label:'变化类别',value:CHANGE_CATEGORIES[filters.category]}].filter(Boolean);
}
export function makeHistoryExport(summary,items,filters,observations){
 const byId=new Map(observations.map(row=>[row.observation_id,row]));
 return {export_type:'pdd_history_comparison',comparison:{...summary},filters:{...filters},item_count:items.length,limitations:['来源为前台独立观察，显示增量不等于真实订单。','无身份、无基线或时间口径不足时不可比；未知不填零。','首次观察候选不等于实际上架或新品；部分轮次缺失不证明下架。','价格仅对照原文，不确认同规格到手价。'],items:items.map(item=>({...item,price_raw_comparison:priceChange(item,byId)})),observations:comparisonObservations(items,observations)};
}
