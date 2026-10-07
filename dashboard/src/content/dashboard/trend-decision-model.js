import {verifiedCardImage} from './verified-image.js';
const LEVEL_ORDER={priority:0,watch:1,observe:2};
const SINGLE_DAY_CODES=new Set(['single_day_growth','single_day_observation','insufficient_history']);
const rowKey=row=>`${row.shop_id}:${row.run_id||row.anchor_run_id||''}:${row.observation_id??row.anchor_observation_id}`;
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const positiveCard=row=>['yipin_gt10','yipin_1to10'].includes(row.category)&&Number.isInteger(row.yipin_value)&&row.yipin_value>0;

export function trendCardImage(row,assets){
 return verifiedCardImage(row,assets);
}
export function trendSignalGroup(signal){
 if(SINGLE_DAY_CODES.has(signal.signal_code)||!finite(signal.consecutive_days)||signal.consecutive_days<4)return 'observe';
 return ['priority','watch'].includes(signal.level)?'watch':'observe';
}
export function trendGrowthEvidence(signal,product){
 const parts=[`销量 ${product.yipin_value} 件`];
 if(finite(signal.latest_delta))parts.push(`本次 ${signal.latest_delta>0?'+':''}${Number(signal.latest_delta.toFixed(1))} 件`);
 if(finite(signal.rate24))parts.push(`折算24h ${signal.rate24>0?'+':''}${Number(signal.rate24.toFixed(1))} 件`);
 return parts.join(' · ');
}
export function trendValidationLabel(record){
 if(record?.record_state!=='recorded')return '';
 if(record.evaluation_state==='waiting')return `已记录${record.due_date?` · ${record.due_date} 回看`: ' · 7天验证中'}`;
 if(record.evaluation_state==='insufficient_data')return record.outcome_label||'7天验证资料不足';
 if(record.evaluation_state==='evaluated')return record.outcome_label||(record.outcome===true?'7天后继续起量':record.outcome===false?'7天后未持续起量':'7天验证结果待核验');
 return '本次推荐已记录';
}
export function trendPanelView(queries,shopId,asOfDate=''){
 const summaries=(queries.trend_signal_summary?.rows||[]).filter(row=>row.shop_id===shopId&&(!asOfDate||row.date===asOfDate)).sort((a,b)=>String(b.date).localeCompare(String(a.date)));
 const summary=summaries[0]||null,date=asOfDate||summary?.date||'';
 const signalRows=(queries.trend_signals?.rows||[]).filter(row=>row.shop_id===shopId&&row.date===date&&(!summary?.reference_run_id||(row.anchor_run_id||row.run_id)===summary.reference_run_id));
 const products=(queries.warehouse_focus_items?.rows||[]).filter(row=>row.shop_id===shopId&&row.date===date);
 const byKey=new Map();for(const row of products){const key=rowKey(row);byKey.set(key,byKey.has(key)?null:row);}
 const assets=new Map((queries.image_assets?.rows||[]).map(row=>[row.sha256,row]));
 const decisions=(queries.trend_decision_records?.rows||[]).filter(row=>row.shop_id===shopId&&row.date===date&&row.record_state==='recorded');
 const rankedContract=signalRows.some(row=>typeof row.selected_for_review==='boolean');
 const candidates=signalRows.filter(row=>row.included_in_watch===true&&row.level in LEVEL_ORDER&&(rankedContract?row.selected_for_review===true:finite(row.latest_delta)&&row.latest_delta>0)).flatMap(signal=>{
  const product=byKey.get(rowKey(signal)),image=trendCardImage(product,assets);
  if(!product||!positiveCard(product)||!image)return [];
  return [{signal,product,image,group:trendSignalGroup(signal),record:decisions.find(row=>rowKey(row)===rowKey(product))||null}];
 }).sort((a,b)=>rankedContract?(a.signal.rank??Infinity)-(b.signal.rank??Infinity):(LEVEL_ORDER[a.signal.level]-LEVEL_ORDER[b.signal.level])||((finite(b.signal.rate24)?b.signal.rate24:b.signal.latest_delta)-(finite(a.signal.rate24)?a.signal.rate24:a.signal.latest_delta))||b.signal.latest_delta-a.signal.latest_delta||a.product.observation_id-b.product.observation_id);
 const seen=new Set(),cards=candidates.filter(card=>{const key=rowKey(card.product);if(seen.has(key))return false;seen.add(key);return true;}).slice(0,5);
 const history=(queries.warehouse_display_history?.rows||[]).filter(row=>row.shop_id===shopId&&cards.some(card=>row.anchor_observation_id===card.product.observation_id&&row.anchor_run_id===card.product.run_id));
 const sourceRowsByQuery={trend_signal_summary:summary?[summary]:[],trend_signals:signalRows,warehouse_focus_items:cards.map(card=>card.product),image_assets:cards.map(card=>assets.get(card.product.asset_sha256)).filter((asset,i,rows)=>rows.indexOf(asset)===i),warehouse_display_history:history,trend_decision_records:cards.flatMap(card=>card.record?[card.record]:[])};
 const queryIds=Object.keys(sourceRowsByQuery).filter(id=>queries[id]);
 const hasAlerts=cards.some(card=>card.group==='watch'),days=summary?.full_snapshot_days;
 const readiness=!summary?'本轮趋势数据尚未生成':finite(days)&&days<4?`已记录 ${days} 天 · 数据积累中`:`已记录 ${finite(days)?days:'—'} 天`;
 const conclusion=hasAlerts?'有重点变化，优先查看提醒':cards.length?'暂无持续信号，可看单日观察':signalRows.some(row=>row.included_in_watch&&['priority','watch','observe'].includes(row.level))?'暂无可展示的有图关注卡':'暂无持续信号，等待下一次采集';
 return {date,summary,cards,readiness,conclusion,queryIds,sourceRowsByQuery};
}
