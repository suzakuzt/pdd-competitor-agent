import React,{useMemo,useState} from 'react';
import {DataComponent,Dropdown,SourceSidebar,Tabs,useDataApp} from '../../data-app-public.jsx';
import {warehouseSource} from './warehouse-model.js';
import {productDisplayName} from './product-display-name.js';
import {FORECAST_CHANNELS,FORECAST_SORTS,forecastNumber,forecastReason,productForecastView} from './product-forecast-model.js';
import {ZoomableImage} from './ZoomableImage.jsx';
import './product-forecast.css';

export function ProductForecastPanel({shopId,asOfDate='',onOpenProduct}){
 const {queries}=useDataApp(),[channel,setChannel]=useState(''),[sort,setSort]=useState('increment'),[limit,setLimit]=useState(6),[source,setSource]=useState(null);
 const view=useMemo(()=>productForecastView(queries,shopId,asOfDate,channel,sort),[queries,shopId,asOfDate,channel,sort]);
 if(!queries.trend_forecasts||!queries.trend_forecast_summary)return <section className="product-forecast"><h3>短期销量估算</h3><p>本轮估算资料尚未生成。</p></section>;
 const filters=[{field:'shop_id',label:'店铺',value:shopId},{field:'date',label:'采集日期',value:view.date},{field:'evidence_channel',label:'对应依据',value:FORECAST_CHANNELS[view.channel]},{field:'sort',label:'排序',value:FORECAST_SORTS[sort]},{field:'selection',label:'范围',value:view.estimatedCount?'有图、正销量且连续区间足够的商品':'有图、正销量，等待历史积累的商品'}];
 const changeChannel=value=>{setChannel(value);setSource(null);setLimit(6);};
 return <section className="product-forecast">
  <DataComponent id="short-term-product-forecast" queryId="trend_forecasts" queryIds={view.queryIds} sourceRows={view.sourceRowsByQuery.trend_forecasts} sourceRowsByQuery={view.sourceRowsByQuery} displayRows={view.cards.map(card=>card.forecast)} scopeFilters={filters} title="短期销量估算 · 跟踪参考" kind="table" variant="plain" headerControls={<Tabs id="forecast-evidence-channel" label="销量估算依据" value={view.channel} variant="pills" items={Object.entries(FORECAST_CHANNELS).map(([id,label])=>({id,label}))} onChange={changeChannel}/>} onOpen={(action,component)=>{if(action==='source')setSource(component);}}>
   <div data-reviewed-rows="true">
    <div className="product-forecast-heading"><strong>{view.estimatedCount?`可初步估算 ${view.estimatedCount} 个商品 · 等待积累 ${view.waitingCount} 个`:`数据积累中 · 至少需要${view.summary?.minimum_intervals||2}个连续可比区间`}</strong><Dropdown label="优先查看" showLabel value={sort} choices={Object.keys(FORECAST_SORTS)} choiceLabels={FORECAST_SORTS} onChange={value=>{setSort(value);setSource(null);setLimit(6);}}/></div>
    <p className="product-forecast-note">近期数据权重更高，按实际采集间隔折算速度；未来增量以这次读取时刻为起点。同图线索仍待核实商品身份。</p>
    <div className="product-forecast-table-wrap"><table className="product-forecast-table" aria-label="商品短期销量估算"><thead><tr><th>图片 / 商品</th><th>当前销量</th><th>未来1天增量</th><th>未来7天增量</th><th>7天情景范围</th><th>依据 / 操作</th></tr></thead><tbody>{view.cards.slice(0,limit).map(({forecast,product,image})=><tr key={product.observation_id}><td><div className="product-forecast-product"><ZoomableImage src={image} alt={product.title} className="product-forecast-image"/><div><span title={product.title}>{productDisplayName(product.title)}</span><small>原卡 #{product.observation_id}</small></div></div></td><td>{product.yipin_value}</td><td>{forecast.forecast_status==='estimated'?`+${forecastNumber(forecast.forecast_increment_1d)}`:'—'}</td><td>{forecast.forecast_status==='estimated'?`+${forecastNumber(forecast.forecast_increment_7d)}`:'—'}</td><td>{forecast.forecast_status==='estimated'?`${forecastNumber(forecast.scenario_low_7d)}–${forecastNumber(forecast.scenario_high_7d)} 件`:'待积累'}</td><td><small>{forecastReason(forecast)}</small><button type="button" disabled={typeof onOpenProduct!=='function'||!product.track_id} onClick={()=>onOpenProduct?.(product)}>查看走势</button><details><summary>估算依据</summary><p>{FORECAST_CHANNELS[forecast.evidence_channel||'unmatched']} · {forecast.normalized_interval_count??0} 个有效区间</p><p>{forecast.evidence_dates?.join(' 至 ')||'暂无连续历史'} · 每日速度 {forecastNumber(forecast.ewma_rate24)} 件</p><p>当前累计 {product.yipin_value} 件；估算采用近期加权速度，缺日和异常数值不补算。</p></details></td></tr>)}</tbody></table></div>
    {!view.cards.length&&<p className="product-forecast-empty">{view.summary?'当前范围没有符合估算条件的商品，保留全店商品供继续跟踪。':'该历史日期尚未生成销量估算。'}</p>}
    {view.cards.length>limit&&<button type="button" className="product-forecast-more" onClick={()=>setLimit(value=>value+6)}>查看更多 · 还有 {view.cards.length-limit} 个</button>}
    <p className="product-forecast-note">简版估算尚未验证准确率；情景范围来自近期最低、最高速度，不是置信区间。可先按预估增量排序核对规格和成本，是否跟品仍需结合经营条件。</p>
   </div>
  </DataComponent>
  {source&&<SourceSidebar key={source.id} component={source} queries={queries} getSource={id=>warehouseSource(queries,source,id)} onClose={()=>setSource(null)}/>}
 </section>;
}
