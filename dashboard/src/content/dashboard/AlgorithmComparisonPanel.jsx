import React,{useMemo,useState} from 'react';
import {DataComponent,SourceSidebar,Tabs,useDataApp} from '../../data-app-public.jsx';
import {warehouseSource} from './warehouse-model.js';
import {ALGORITHM_LABELS,EVIDENCE_CHANNELS,backtestMetricDisplay,backtestView} from './algorithm-comparison-model.js';
import './algorithm-comparison.css';

function AlgorithmTable({rows,ready}){
 return <div className="algorithm-table-wrap"><table className="algorithm-table" aria-label="算法验证对比"><thead><tr><th>算法</th><th>待验证</th><th title="可评价数 / 已到期样本数；覆盖率不代表命中率">可评价 / 到期<small>覆盖</small></th><th title="仅在可评价样本中计算，未知样本不视为失败">已知命中<small>命中 / 可评价</small></th><th title="把已到期未知样本分别视为未命中或命中所得范围；不是成功概率">整体范围</th></tr></thead><tbody>{rows.map(row=>{const value=backtestMetricDisplay(row,ready);return <tr key={row.strategy}><th scope="row">{ALGORITHM_LABELS[row.strategy]}</th><td>{value.waiting}</td><td>{value.known}<small>{value.coverage==='—'?'—':`覆盖 ${value.coverage}`}</small></td><td>{value.hit}<small>{value.knownRate}</small></td><td>{value.range}<small>{ready&&Number.isInteger(row.unknown)&&row.unknown>0?`${row.unknown} 项待核验`:''}</small></td></tr>;})}</tbody></table></div>;
}

export function AlgorithmComparisonPanel({shopId,asOfDate=''}){
 const {queries}=useDataApp(),[choice,setChoice]=useState(null),[source,setSource]=useState(null),[shownRounds,setShownRounds]=useState(5);
 const scopeKey=`${shopId}:${asOfDate}`,requested=choice?.scope===scopeKey?choice.channel:'';
 const view=useMemo(()=>backtestView(queries,shopId,asOfDate,requested),[queries,shopId,asOfDate,requested]);
 const switchChannel=channel=>{setChoice({scope:scopeKey,channel});setSource(null);setShownRounds(5);};
 if(['trend_backtest_summary','trend_backtest_metrics','trend_backtest_windows','trend_backtest_selection'].some(id=>!queries[id]))return <section className="algorithm-comparison"><div className="algorithm-heading"><strong>算法对比</strong><small>数据积累中</small></div></section>;
 if(view.historical)return <section className="algorithm-comparison"><div className="algorithm-heading"><strong>算法对比</strong><small>此历史日期未生成算法对比</small></div></section>;
 const filters=[{field:'shop_id',label:'店铺',value:shopId},{field:'as_of_date',label:'评估截至',value:view.cutoff},{field:'evidence_channel',label:'证据通道',value:EVIDENCE_CHANNELS[view.channel]}];
 const sourceScope=`${scopeKey}:${view.channel}:${view.batchKey}`;
 const openSource=(action,component)=>{if(action==='source')setSource({scope:sourceScope,component});};
 const evidence={queryId:'trend_backtest_metrics',queryIds:view.queryIds,sourceRows:view.metrics,sourceRowsByQuery:view.sourceRowsByQuery,displayRows:view.metrics,scopeFilters:filters,onOpen:openSource};
 const activeSource=source?.scope===sourceScope?source.component:null;
 const latestSelection=view.selection[0];
 return <section className="algorithm-comparison">
  <DataComponent {...evidence} id="algorithm-comparison-table" title="算法对比" kind="table" variant="plain" headerControls={<Tabs id="algorithm-evidence-channel" label="算法证据通道" value={view.channel} variant="pills" items={Object.entries(EVIDENCE_CHANNELS).map(([id,label])=>({id,label}))} onChange={switchChannel}/>}>
   <div data-reviewed-rows="true">
    <div className="algorithm-heading"><strong>{view.readiness}</strong><small>{view.frozenCount===null?'尚无留存候选':`本店留存 ${view.frozenCount} 项候选`} · 评估截至 {view.cutoff||'待定'}</small></div>
    {view.summary?.readiness==='insufficient_pool'&&<p className="algorithm-channel-note">缺少3段可比较的历史数据。</p>}
    {view.channel==='provisional'&&<p className="algorithm-channel-note">同图展示线索，未确认商品身份。</p>}
    {view.basic.length?<AlgorithmTable rows={view.basic} ready={view.ready}/>:<p className="algorithm-empty">此通道尚无可对比样本。</p>}
    {view.blended.length>0&&<details className="algorithm-details"><summary>加权策略 <small>{latestSelection?.chosen_parameter==null?'未启用':'仅供试验对照'}</small></summary><AlgorithmTable rows={view.blended} ready={view.ready}/></details>}
   </div>
  </DataComponent>
  <details className="algorithm-details algorithm-evidence"><summary>每轮样本与规则</summary>
   <DataComponent {...evidence} id="algorithm-comparison-rounds" queryId="trend_backtest_windows" sourceRows={view.windows} displayRows={view.windows} title="逐轮验证依据" kind="table" variant="plain">
    <div data-reviewed-rows="true">
     <p className="algorithm-rule">六种策略使用同一候选池。仅用推荐时已知数据，按7天后结果对照。</p>
     <p className="algorithm-rule">候选须有有效区间增长和至少3段均速基准；缺少基准时不补算。</p>
     {view.summary&&<p className="algorithm-rule">缺区间基准 {view.summary.excluded_missing_rate??'—'} · 缺近期均速 {view.summary.excluded_missing_recent??'—'} · 身份或图片待核验 {view.summary.excluded_identity_or_image??'—'}<small>排除原因按本店留存关注池计；两通道不相加。</small></p>}
     {latestSelection&&<p className="algorithm-rule">试验参数：{latestSelection.chosen_parameter==null?'尚未启用':`近期均速权重 ${latestSelection.chosen_parameter*100}%`} · 训练窗口 {latestSelection.training_windows??'—'}<small>训练截至 {latestSelection.training_cutoff||'未记录'}；候选参数不代表已验证最佳算法。</small></p>}
     {view.groups.slice(0,shownRounds).map(group=><details className="algorithm-round" key={`${group.cohort_id}:${group.date}`}><summary>{group.date}<small>验证日 {group.due_date||'待定'}</small></summary><div className="algorithm-table-wrap"><table className="algorithm-table algorithm-round-table" aria-label={`${group.date} 样本记录`}><thead><tr><th>算法</th><th>共同池</th><th>入选记录</th><th>可评价</th><th>已知命中</th></tr></thead><tbody>{group.rows.map(row=>{const value=backtestMetricDisplay(row);return <tr key={row.strategy}><th scope="row">{ALGORITHM_LABELS[row.strategy]}</th><td>{row.pool_count??'—'}</td><td><details><summary>{Array.isArray(row.selected_ids)?row.selected_ids.length:'—'} 项</summary><small>{(row.selected_ids||[]).map(id=>`#${id}`).join('、')||'尚未入选'}</small></details></td><td>{value.known}</td><td>{value.hit}<small>{value.knownRate}</small></td></tr>;})}</tbody></table></div></details>)}
     {!view.groups.length&&<p className="algorithm-empty">尚无满足共同基准的对比轮次。</p>}
     {view.groups.length>shownRounds&&<button type="button" className="algorithm-more" onClick={()=>setShownRounds(value=>value+5)}>更多轮次 · 还有 {view.groups.length-shownRounds}</button>}
    </div>
   </DataComponent>
  </details>
  {activeSource&&<SourceSidebar key={activeSource.id} component={activeSource} queries={queries} getSource={id=>warehouseSource(queries,activeSource,id)} onClose={()=>setSource(null)}/>}
 </section>;
}
