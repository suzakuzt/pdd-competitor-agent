import {verifiedCardImage} from './verified-image.js';

export const FORECAST_CHANNELS={provisional:'同图展示线索',confirmed:'已确认商品',unmatched:'等待建立对照'};
export const FORECAST_SORTS={increment:'预估7天增量',sales:'当前销量'};
const key=row=>`${row.shop_id}:${row.run_id||row.anchor_run_id}:${row.observation_id??row.anchor_observation_id}`;
const nonnegative=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
export function forecastNumber(value){return nonnegative(value)?Number(value.toFixed(1)).toLocaleString('zh-CN',{maximumFractionDigits:1}):'—';}
export function forecastReason(row){
 if(row.forecast_status==='estimated')return '按近期速度推算';
 const count=Number.isSafeInteger(row.normalized_interval_count)?row.normalized_interval_count:0;
 const minimum=Number.isSafeInteger(row.minimum_intervals)?row.minimum_intervals:2;
 return count<minimum?`${row.reason_label||'等待每日积累'} · 有效区间 ${count} / ${minimum}`:'历史暂不可推算';
}
export function productForecastView(queries,shopId,asOfDate='',requestedChannel='',sort='increment'){
 const rows=id=>queries[id]?.rows||[];
 const summary=rows('trend_forecast_summary').filter(row=>row.shop_id===shopId&&(!asOfDate||row.date===asOfDate)).sort((a,b)=>String(b.date).localeCompare(String(a.date)))[0]||null;
 const date=asOfDate||summary?.date||'',runId=summary?.reference_run_id;
 const forecasts=summary&&runId?rows('trend_forecasts').filter(row=>row.shop_id===shopId&&row.date===date&&(row.run_id||row.anchor_run_id)===runId):[];
 const products=rows('warehouse_focus_items').filter(row=>row.shop_id===shopId&&row.date===date&&row.run_id===runId);
 const productMap=new Map();for(const row of products){const k=key(row);productMap.set(k,productMap.has(k)?null:row);}
 const assets=new Map(rows('image_assets').map(row=>[row.sha256,row]));
 const forecastKeys=new Map();for(const row of forecasts){const k=key(row);forecastKeys.set(k,(forecastKeys.get(k)||0)+1);}
 const candidates=forecasts.flatMap(forecast=>{
  const product=productMap.get(key(forecast)),image=verifiedCardImage(product,assets);
  if(forecastKeys.get(key(forecast))!==1||!product||!image||!Number.isSafeInteger(product.yipin_value)||product.yipin_value<=0||!['yipin_gt10','yipin_1to10'].includes(product.category)||!['estimated','insufficient_data'].includes(forecast.forecast_status)||!['confirmed','provisional',null].includes(forecast.evidence_channel??null))return [];
  if(forecast.forecast_status==='estimated'&&forecast.evidence_channel==null)return [];
  if(forecast.forecast_status==='estimated'&&(!['forecast_increment_1d','forecast_increment_7d','scenario_low_7d','scenario_high_7d'].every(field=>nonnegative(forecast[field]))||forecast.scenario_low_7d>forecast.forecast_increment_7d||forecast.scenario_high_7d<forecast.forecast_increment_7d||forecast.forecast_validated!==false))return [];
  return [{forecast,product,image,channel:forecast.evidence_channel||'unmatched'}];
 });
 const channel=requestedChannel in FORECAST_CHANNELS?requestedChannel:candidates.some(card=>card.channel==='confirmed'&&card.forecast.forecast_status==='estimated')?'confirmed':candidates.some(card=>card.channel==='provisional')?'provisional':candidates.some(card=>card.channel==='confirmed')?'confirmed':'unmatched';
 const scoped=candidates.filter(card=>card.channel===channel),estimated=scoped.filter(card=>card.forecast.forecast_status==='estimated');
 const cards=(estimated.length?estimated:scoped).sort((a,b)=>sort==='sales'?b.product.yipin_value-a.product.yipin_value||a.product.observation_id-b.product.observation_id:(b.forecast.forecast_increment_7d??-1)-(a.forecast.forecast_increment_7d??-1)||b.product.yipin_value-a.product.yipin_value||a.product.observation_id-b.product.observation_id);
 const ids=new Set(cards.flatMap(card=>card.forecast.evidence_observation_ids||[]));
 for(const card of cards)ids.add(card.product.observation_id);
 const sourceRowsByQuery={trend_forecast_summary:summary?[summary]:[],trend_forecasts:cards.map(card=>card.forecast),warehouse_focus_items:cards.map(card=>card.product),warehouse_display_history:rows('warehouse_display_history').filter(row=>row.shop_id===shopId&&row.anchor_run_id===runId&&row.date<=date&&cards.some(card=>row.anchor_observation_id===card.product.observation_id)),observations:rows('observations').filter(row=>row.shop_id===shopId&&ids.has(row.observation_id)),image_assets:rows('image_assets').filter(asset=>cards.some(card=>card.product.asset_sha256===asset.sha256))};
 return {summary,date,channel,cards,estimatedCount:estimated.length,waitingCount:scoped.filter(card=>card.forecast.forecast_status==='insufficient_data').length,queryIds:Object.keys(sourceRowsByQuery).filter(id=>queries[id]),sourceRowsByQuery};
}
