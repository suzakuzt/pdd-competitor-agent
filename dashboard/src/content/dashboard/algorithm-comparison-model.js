export const ALGORITHM_LABELS={rule:'现有规则',cumulative:'累计销量',rate24:'最近增长',blend_025:'近期均速权重25%',blend_050:'近期均速权重50%',blend_075:'近期均速权重75%'};
export const EVIDENCE_CHANNELS={confirmed:'已确认商品',provisional:'同图展示线索'};
const QUERY_IDS=['trend_backtest_summary','trend_backtest_metrics','trend_backtest_windows','trend_backtest_selection'];
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
export function backtestPercent(value){return finite(value)&&value>=0&&value<=1?`${Number((value*100).toFixed(1))}%`:'—';}
export function backtestMetricDisplay(row,ready=true){
 const known=count(row.known),mature=count(row.mature_selected),hit=count(row.hit),waiting=count(row.waiting),unknown=count(row.unknown);
 const canEvaluate=ready&&known>0&&mature>=known&&hit!==null&&hit<=known;
 return {waiting:ready?(waiting??'—'):'待积累',known:ready&&mature>0&&known!==null?`${known} / ${mature}`:'—',coverage:ready&&mature>0?backtestPercent(row.coverage):'—',hit:canEvaluate?`${hit} / ${known}`:'—',knownRate:canEvaluate?backtestPercent(row.known_hit_rate):'—',range:canEvaluate&&finite(row.lower_bound)&&finite(row.upper_bound)&&row.lower_bound>=0&&row.upper_bound<=1&&row.lower_bound<=row.upper_bound?`${backtestPercent(row.lower_bound)}–${backtestPercent(row.upper_bound)}`:'—',unknown:unknown??'—'};
}
export function backtestReadiness(summary){
 return ({no_frozen_candidates:'等待留存候选',insufficient_pool:'数据积累中',waiting_outcomes:'等待7天验证',insufficient_history:'验证样本积累中',shadow_evaluation:'历史对照已更新'})[summary?.readiness]||'数据积累中';
}
export function backtestView(queries,shopId,asOfDate='',requestedChannel=''){
 const rows=id=>queries[id]?.rows||[];
 const shopSummaries=rows('trend_backtest_summary').filter(row=>row.shop_id===shopId).sort((a,b)=>String(b.as_of_date).localeCompare(String(a.as_of_date))||String(b.evaluated_at).localeCompare(String(a.evaluated_at)));
 const head=shopSummaries[0]||null,cutoff=head?.as_of_date||'';
 const currentCatalogue=[...rows('warehouse_focus_summary'),...rows('trend_signal_summary')].filter(row=>row.shop_id===shopId).map(row=>row.date).filter(Boolean).sort().at(-1)||cutoff;
 const historical=Boolean(asOfDate&&currentCatalogue&&asOfDate<currentCatalogue);
 const exactScope=row=>Boolean(head)&&row.shop_id===shopId&&row.as_of_date===cutoff&&row.evaluated_at===head.evaluated_at&&row.backtest_version===head.backtest_version&&row.source_protocol_version===head.source_protocol_version;
 const summaries=historical?[]:shopSummaries.filter(exactScope);
 const hasData=channel=>summaries.some(row=>row.evidence_channel===channel&&['channel_watch_count','eligible_pool_cards','waiting_selected','unknown_selected','known_selected'].some(key=>count(row[key])>0));
 const defaultChannel=hasData('confirmed')?'confirmed':hasData('provisional')?'provisional':'confirmed';
 const channel=requestedChannel in EVIDENCE_CHANNELS?requestedChannel:defaultChannel;
 const summary=summaries.find(row=>row.evidence_channel===channel)||null;
 const scoped=id=>historical?[]:rows(id).filter(row=>exactScope(row)&&row.evidence_channel===channel);
 const metrics=scoped('trend_backtest_metrics').filter(row=>row.strategy in ALGORITHM_LABELS).sort((a,b)=>Object.keys(ALGORITHM_LABELS).indexOf(a.strategy)-Object.keys(ALGORITHM_LABELS).indexOf(b.strategy));
 const windows=scoped('trend_backtest_windows').filter(row=>row.strategy in ALGORITHM_LABELS&&row.date<=cutoff).sort((a,b)=>String(b.date).localeCompare(String(a.date))||Object.keys(ALGORITHM_LABELS).indexOf(a.strategy)-Object.keys(ALGORITHM_LABELS).indexOf(b.strategy));
 const selection=scoped('trend_backtest_selection').filter(row=>row.date<=cutoff).sort((a,b)=>String(b.date).localeCompare(String(a.date)));
 const sourceRowsByQuery={trend_backtest_summary:summary?[summary]:[],trend_backtest_metrics:metrics,trend_backtest_windows:windows,trend_backtest_selection:selection};
 const queryIds=QUERY_IDS.filter(id=>queries[id]);
 const ready=Boolean(summary&&count(summary.eligible_pool_cards)>0&&!['no_frozen_candidates','insufficient_pool'].includes(summary.readiness));
 const groups=[];for(const row of windows){let group=groups.find(item=>item.cohort_id===row.cohort_id&&item.date===row.date);if(!group){group={cohort_id:row.cohort_id,date:row.date,due_date:row.due_date,rows:[]};groups.push(group);}group.rows.push(row);}
 return {channel,summary,summaries,cutoff,batchKey:[cutoff,head?.evaluated_at,head?.backtest_version,head?.source_protocol_version].join('|'),historical,ready,metrics,windows,selection,groups,queryIds,sourceRowsByQuery,readiness:backtestReadiness(summary),frozenCount:count(summary?.frozen_watch_count),basic:metrics.filter(row=>!row.strategy.startsWith('blend_')),blended:metrics.filter(row=>row.strategy.startsWith('blend_'))};
}
