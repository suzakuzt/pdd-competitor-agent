import React,{useEffect,useMemo,useState} from 'react';
import {DataComponent,SourceSidebar,useDataApp} from '../../data-app-public.jsx';
import {warehouseSource} from './warehouse-model.js';
import {trendGrowthEvidence,trendPanelView,trendValidationLabel} from './trend-decision-model.js';
import './trend-decision.css';
import {productDisplayName} from './product-display-name.js';
import {AlgorithmComparisonPanel} from './AlgorithmComparisonPanel.jsx';

export function TrendDecisionPanel({shopId,asOfDate='',onOpenProduct}){
 const {queries}=useDataApp(),[source,setSource]=useState(null),[latest,setLatest]=useState(null);
 const view=useMemo(()=>trendPanelView(queries,shopId,asOfDate),[queries,shopId,asOfDate]);
 useEffect(()=>{
  let cancelled=false,busy=false;const controller=new AbortController();setLatest(null);
  const read=async()=>{if(busy)return;busy=true;try{const response=await fetch(`/__pdd_trend_status?shop_id=${encodeURIComponent(shopId)}`,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});if(!response.ok)return;const status=await response.json();if(!cancelled&&status?.shop_id===shopId&&typeof status.reference_run_id==='string'&&status.reference_run_id)setLatest(status);}catch{}finally{busy=false;}};
  read();const timer=setInterval(read,20000);return ()=>{cancelled=true;controller.abort();clearInterval(timer);};
 },[shopId]);
 const hasNew=latest?.shop_id===shopId&&latest.reference_run_id&&latest.reference_run_id!==view.summary?.reference_run_id;
 if(!queries.trend_signals||!queries.trend_signal_summary)return <section className="pdd-trend-decision"><div className="pdd-trend-heading"><h3>增长提醒</h3><small>本轮趋势数据尚未生成</small></div></section>;
 const scopeFilters=[{field:'shop_id',label:'店铺',value:shopId},{field:'date',label:'采集日期',value:view.date},{field:'selection',label:'展示范围',value:'正销量、有核验图片的前5条信号'}];
 return <section className="pdd-trend-decision">
  {hasNew&&<div className="pdd-trend-new"><span>新一轮趋势分析已生成</span><button type="button" onClick={()=>window.location.reload()}>加载最新提醒</button></div>}
  <DataComponent id="trend-decision-panel" queryId="trend_signal_summary" queryIds={view.queryIds} sourceRows={view.sourceRowsByQuery.trend_signal_summary} sourceRowsByQuery={view.sourceRowsByQuery} displayRows={view.cards.map(card=>card.signal)} scopeFilters={scopeFilters} kind="table" title="增长提醒" variant="plain" onOpen={(action,component)=>{if(action==='source')setSource(component);}}>
   <div data-reviewed-rows="true">
    <div className="pdd-trend-heading"><strong>{view.conclusion}</strong><small>采集 {view.date||'日期待定'} · {view.readiness}</small></div>
    {['watch','observe'].map(group=>{const cards=view.cards.filter(card=>card.group===group);return cards.length>0&&<div className={`pdd-trend-group pdd-trend-${group}`} key={group}>
     <h4>{group==='watch'?'重点提醒':'单日观察'} <span>{cards.length}</span></h4>
     <div className="pdd-trend-cards">{cards.map(({signal,product,image,record})=><article className="pdd-trend-card" key={product.observation_id}>
      <img src={image} alt={product.title||'商品主图'} loading="lazy" title={product.image_content_kind==='store_search_variant'?'店内搜索同文件图片':'已保存商品主图'}/>
      <div className="pdd-trend-card-body"><div className="pdd-trend-signal"><strong>{signal.label||'展示变化线索'}</strong><small>{signal.basis==='provisional_title_image'?'同标题同图线索':signal.basis==='confirmed_goods_id'?'已确认商品':'展示线索'}</small></div>
       <div className="pdd-trend-product-title" title={product.title}>{productDisplayName(product.title)}</div>
       <p className="pdd-trend-evidence" title={(signal.reasons||[]).join('；')}>{trendGrowthEvidence(signal,product)}</p>
       {record&&<small className="pdd-trend-validation">{trendValidationLabel(record)}</small>}
      </div>
      <button type="button" className="pdd-trend-chart-button" disabled={typeof onOpenProduct!=='function'||!product.track_id} onClick={()=>onOpenProduct?.(product)}>查看走势</button>
     </article>)}</div>
    </div>;})}
   </div>
  </DataComponent>
  <AlgorithmComparisonPanel key={`algorithm:${shopId}:${asOfDate}`} shopId={shopId} asOfDate={asOfDate}/>
  {source&&<SourceSidebar key={source.id} component={source} queries={queries} getSource={id=>warehouseSource(queries,source,id)} onClose={()=>setSource(null)}/>}
 </section>;
}
