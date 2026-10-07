export const ARRIVAL_TYPES={all:'全部新发现线索',first_observed_id_candidate:'ID 首次观察候选',new_card_clue:'新卡片线索 · 身份待核验'};
export const ARRIVAL_REASON_LABELS={prior_complete_identity_coverage_absence:'此前完整且身份覆盖齐全的轮次未记录该 ID',no_prior_complete_identity_coverage:'没有可靠的完整身份基线，较早时间未知',identity_unverified:'商品身份尚未核实',possible_old_card_identity_enrichment:'可能是旧卡补充了商品 ID',invalid_recorded_time_used_window:'记录时刻无效，使用原始观察窗口',possible_title_change:'可能改名：原图已在历史出现',possible_image_change:'可能换图：标题已在历史出现',historical_unreliable_id_seen:'该 ID 历史曾冲突或重复，待核验',observation_windows_overlap:'观察窗口重叠，首次先后待核验'};
export function arrivalReason(reason){return ARRIVAL_REASON_LABELS[reason]||(reason?'有待核验依据，详见来源':'未提供额外依据');}
export function scopedArrivals(items,summary){
 if(!summary||summary.state==='not_configured'||!summary.tracking_id)return [];
 const baseline=new Set(summary.baseline_run_ids||[]);
 const end=item=>{const value=Date.parse(item.first_observation_window?.to);return Number.isFinite(value)?value:-Infinity;};
 return items.filter(item=>item.tracking_id===summary.tracking_id&&!baseline.has(item.run_id)).sort((left,right)=>{
  const leftEnd=end(left),rightEnd=end(right);
  return leftEnd!==rightEnd?(leftEnd<rightEnd?1:-1):String(left.arrival_item_id??'').localeCompare(String(right.arrival_item_id??''));
 });
}
export function filterArrivals(items,filters={}){
 const query=String(filters.search||'').trim().toLocaleLowerCase();
 return items.filter(item=>(!filters.kind||filters.kind==='all'||item.discovery_kind===filters.kind)&&(!query||[item.title,item.goods_id,item.view_order,`#${item.view_order}`,item.observation_id,item.discovery_label].some(value=>String(value??'').toLocaleLowerCase().includes(query))));
}
export function arrivalQueueState(summary,totalCount,matchingCount){
 if(!summary)return 'not_loaded';
 if(summary.state==='not_configured'||!summary.tracking_id)return 'not_configured';
 if(summary.state==='no_post_baseline_run'||summary.post_baseline_run_count===0)return 'waiting';
 if(totalCount===0)return 'no_candidates';
 if(matchingCount===0)return 'no_matches';
 return 'available';
}
export function arrivalObservationRows(items,observations){
 const ids=new Set(items.flatMap(item=>[item.observation_id,...(item.first_observation_id_candidates||[]),...(item.latest_observation_ids||[]),...(item.historical_references||[]).map(reference=>reference.observation_id),...(item.subsequent_references||[]).map(reference=>reference.observation_id)]).map(String));
 return observations.filter(row=>ids.has(String(row.observation_id)));
}
export function arrivalScope(summary,filters){
 return [{field:'tracking_id',label:'固定监控起点',value:summary.tracking_id},filters.kind!=='all'&&{field:'discovery_kind',label:'新发现类别',value:ARRIVAL_TYPES[filters.kind]||'待核验类别'},filters.search&&{field:'title',label:'标题 / ID / 原位置 / 标签',value:filters.search}].filter(Boolean);
}
export function arrivalTimeEvidence(item){
 const has=value=>typeof value==='string'&&Number.isFinite(Date.parse(value));
 const overlapping=item.first_time_order_status==='overlapping_runs_unknown';
 const point=!overlapping&&item.first_seen_time_basis==='recorded_read_time'&&has(item.first_observed_at)?item.first_observed_at:null;
 const window=item.first_observation_window||{};
 const until=has(item.possible_until)?item.possible_until:point||(has(window.to)?window.to:null);
 const bounded=!overlapping&&has(item.possible_since)&&until&&Date.parse(item.possible_since)<=Date.parse(until);
 return {kind:bounded?'bounded_reference':'upper_only',
  since:bounded?item.possible_since:null,until,
  first_observed_at:point,window_from:has(window.from)?window.from:null,window_to:has(window.to)?window.to:null,
  precision:item.first_observed_at_precision||'unknown',basis:item.first_seen_time_basis||'unknown',order_status:item.first_time_order_status||'unknown',latest_order_status:item.latest_time_order_status||'unknown',listing_time_confirmed:false};
}
export function latestArrivalRows(item,byId){return [...new Set((item.latest_observation_ids||[]).map(String))].map(id=>byId.get(id)).filter(Boolean).sort((a,b)=>a.view_order-b.view_order);}
export function firstArrivalRows(item,byId){return [...new Set((item.first_observation_id_candidates||[]).map(String))].map(id=>byId.get(id)).filter(Boolean);}
export function latestArrivalMode(item,rows){
 if(!rows.length)return 'missing';
 if(item.latest_time_order_status==='overlapping_runs_unknown')return 'overlapping';
 if(rows.length===1&&String(rows[0].observation_id)===String(item.observation_id))return 'anchor_only';
 if(rows.length>1)return 'multiple';
 return item.latest_reference_basis==='same_unique_goods_id'?'single_id':'single_clue';
}
