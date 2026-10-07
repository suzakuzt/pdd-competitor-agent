import {verifiedCardImage} from './verified-image.js';
import React,{useMemo,useState} from 'react';
import {DataComponent,Dropdown,Section} from '../../data-app-public.jsx';
import {classifyOccasion,occasionDates,occasionRows,occasionSummary} from './occasion-model.js';
import {productDisplayName} from './product-display-name.js';
import {dataUpdateTime} from './data-time.js';
import {comparisonDisplayValue,comparisonStatus,comparisonDate,COMPARISON_LABELS,MATCH_LABELS,formatGrowth,nextFocusSort,focusSortLabel,sortComparisonRows} from './warehouse-focus-model.js';
import './occasion-products.css';
import {skuEligible} from './sku-model.js';
import {ZoomableImage} from './ZoomableImage.jsx';

const TITLES={birthday:'生日 / 纪念日',activity:'活动 / 应援'};
const SALES={all:'全部相关商品',gt10:'销量 >10',low:'销量 1–10',zero:'销量 0',unknown:'销量未知'};
const SORTS={date_desc:'日期倒序',date_asc:'日期正序',clue_sales:'线索与销量'};
const DATE_SCOPES={all:'全部日期',dated:'有日期',undated:'未标日期',lunar:'农历线索'};
const DEFAULT_VIEW={search:'',sales:'all',tag:'all',dateSearch:'',dateScope:'all',sort:'date_desc',metricSort:null,page:0};

export function OccasionProducts({module,rows=[],sourceRows=[],queryId,observations=[],assets=new Map(),shopId,date,comparison='yesterday',onComparisonChange,onOpenProduct,onOpenSku,onOpenSource,viewState,onViewStateChange}){
 const [localState,setLocalState]=useState(DEFAULT_VIEW),view={...DEFAULT_VIEW,...(viewState||localState)};
 const {search,sales,tag,dateSearch,dateScope,sort,metricSort,page}=view;
 const baselineDate=comparisonDate(date,comparison),sortLabel=metricSort?focusSortLabel(metricSort):SORTS[sort];
 const update=patch=>{const next={...view,...patch};if(onViewStateChange)onViewStateChange(next);else setLocalState(next);onOpenSource?.('close');};
 const scopedRows=useMemo(()=>rows.filter(row=>row.shop_id===shopId&&row.date===date),[rows,shopId,date]);
 const all=useMemo(()=>occasionRows(scopedRows,module,{search:'',sales:'all',tag:'all'}),[scopedRows,module]);
 const summary=useMemo(()=>occasionSummary(scopedRows,module),[scopedRows,module]);
 const cards=useMemo(()=>sortComparisonRows(occasionRows(scopedRows,module,{search,sales,tag,dateSearch,dateScope,sort}),comparison,metricSort),[scopedRows,module,search,sales,tag,dateSearch,dateScope,sort,comparison,metricSort]);
 const original=filtered=>{const ids=new Set(filtered.map(row=>row.observation_id));return sourceRows.filter(row=>row.shop_id===shopId&&row.date===date&&ids.has(row.observation_id));};
 const evidenceRows=original(cards),allEvidence=original(all);
 const ids=new Set(cards.flatMap(row=>[row.observation_id,row[`${comparison}_baseline_observation_id`]]).filter(id=>id!=null));
 const evidenceObservations=observations.filter(row=>row.shop_id===shopId&&ids.has(row.observation_id));
 const scope=[{field:'shop_id',label:'店铺',value:shopId},{field:'date',label:'清单日期',value:date},{field:'occasion',label:'专题',value:TITLES[module]}];
 const filters=[...scope,{field:'comparison',label:'对比日期',value:`${COMPARISON_LABELS[comparison]} ${baselineDate}`},{field:'search',label:'标题搜索',value:search||'全部'},{field:'sales',label:'销量范围（已拼 / 已抢）',value:SALES[sales]},{field:'tag',label:'标题线索',value:tag==='all'?'全部':tag},{field:'date_search',label:'原文日期搜索',value:dateSearch||'全部'},{field:'date_scope',label:'日期类型',value:DATE_SCOPES[dateScope]},{field:'date_sort',label:'日期排序',value:SORTS[sort]},{field:'sort',label:'排序',value:sortLabel}];
 const pages=Math.max(1,Math.ceil(cards.length/20)),currentPage=Math.min(Math.max(0,page),pages-1),shown=cards.slice(currentPage*20,currentPage*20+20);
 const tagChoices=['all',...summary.tags.map(item=>item.label)],tagLabels=Object.fromEntries([['all','全部线索'],...summary.tags.map(item=>[item.label,`${item.label} · ${item.count}`])]);
 return <div className="occasion-products">
  <DataComponent id={`occasion-${module}-summary`} queryId={queryId} sourceRows={allEvidence} displayRows={all} scopeFilters={scope} onOpen={onOpenSource} kind="metric" title={TITLES[module]} description="按商品原标题关键词筛选；包含尚未出单商品。分类可能重叠，不相加。原标题日期只是待核实线索，不代表实际上架或活动日期。" variant="plain">
   <div className="occasion-counts" data-reviewed-rows="true">{Object.entries(SALES).map(([id,label])=><button type="button" key={id} className={sales===id?'occasion-count-active':''} aria-pressed={sales===id} onClick={()=>update({sales:id,page:0})}><strong>{id==='all'?summary.total:summary[id]}</strong><span>{label}</span></button>)}</div>
  </DataComponent>
  <Section id={`occasion-${module}-section`} title={`${TITLES[module]}商品`} spacing="content">
   <div className="occasion-filters">
    <label className="occasion-search"><input type="search" aria-label={`搜索${TITLES[module]}商品`} placeholder="搜索角色 / 人名 / 活动 / 商品" value={search} onChange={event=>update({search:event.target.value,page:0})}/></label>
    <label className="occasion-search occasion-date-search"><input type="search" aria-label={module==='birthday'?'搜索生日日期':'搜索活动日期'} placeholder="日期：0929 / 9月29日 / 2026-09-29" value={dateSearch} onChange={event=>update({dateSearch:event.target.value,page:0})}/></label>
    <Dropdown label="排序" value={sort} choices={Object.keys(SORTS)} choiceLabels={SORTS} onChange={value=>update({sort:value,metricSort:null,page:0})}/>
    {onComparisonChange&&<Dropdown label="对比" showLabel value={comparison} choices={Object.keys(COMPARISON_LABELS)} choiceLabels={COMPARISON_LABELS} onChange={value=>{onComparisonChange(value);update({page:0});}}/>}
    <Dropdown label="日期" allLabel="全部日期" value={dateScope} choices={Object.keys(DATE_SCOPES)} choiceLabels={DATE_SCOPES} onChange={value=>update({dateScope:value,page:0})}/>
    <Dropdown label="标题线索" allLabel="全部线索" value={tag} choices={tagChoices} choiceLabels={tagLabels} onChange={value=>update({tag:value,page:0})}/>
   </div>
   <p className="occasion-date-note">按原标题日期：完整年月日优先，未标年份按月日另组排序；农历未换算、日期未明排后。日期均为待核实线索。</p>
   <DataComponent id={`occasion-${module}-cards`} queryId={queryId} queryIds={[queryId,'observations']} sourceRows={evidenceRows} sourceRowsByQuery={{[queryId]:evidenceRows,observations:evidenceObservations}} displayRows={cards} scopeFilters={filters} onOpen={onOpenSource} kind="table" title={`${cards.length} 张商品原卡`} description="范围为当前店铺当前完整日的上新清单。已拼和已抢的明确件数均计入销量；模糊和缺失销量保留未知，同标题同图的跨日差值只作展示线索。" variant="plain">
    <div className="occasion-table-wrap"><table className="occasion-table" data-reviewed-rows="true"><thead><tr><th>图片 / 商品</th><th>展示价</th><th className="occasion-number">当前销量<small>{date}</small></th><th className="occasion-number">{comparison==='yesterday'?'昨日':'前天'}销量<small>{baselineDate}</small></th>{[['delta','增加'],['growth_rate','涨幅']].map(([field,label])=><th key={field} className="occasion-number" aria-sort={metricSort?.field===field?(metricSort.direction==='asc'?'ascending':'descending'):'none'}><button type="button" className="warehouse-sort-button" onClick={()=>update({metricSort:nextFocusSort(metricSort,field),page:0})} aria-label={`${label}：点击按${metricSort?.field===field&&metricSort.direction==='desc'?'升序':'降序'}排序`} title={`按${label}${metricSort?.field===field&&metricSort.direction==='desc'?'升序':'降序'}排列，暂无可比数据排在最后`}>{label}<span aria-hidden="true">{metricSort?.field===field?(metricSort.direction==='asc'?'↑':'↓'):'↕'}</span></button></th>)}<th className="occasion-action">数据</th></tr></thead><tbody>{shown.map(row=>{
     const classified=row.occasion||classifyOccasion(row),change=comparisonDisplayValue(row,comparison),delta=change.delta;
     const dates=occasionDates(classified,module),dateText=dates.length?`原文日期：${dates.map(item=>`${item.text}${item.calendar==='lunar'?'（农历，未换算）':item.year?'':'（未标年份）'}`).join('、')} · 待核实`:`${classified.hasLunar?'农历线索 · ':''}日期未标明`;
     const image=verifiedCardImage(row,assets);
     return <tr key={row.observation_id}>
      <td><div className="occasion-product">{image?<ZoomableImage className="occasion-thumbnail" src={image} alt={row.title||'商品主图'}/>:<span className="occasion-missing-image">待补图</span>}<div className="occasion-product-copy"><span className="occasion-title" title={row.title||'标题未记录'}>{productDisplayName(row.title)}</span><small>原卡 #{row.observation_id} · <span title={MATCH_LABELS[change.basis]||comparisonStatus(change.status)}>{change.basis==='confirmed_goods_id'?'已确认':change.basis==='provisional_title_image'?'同图线索':comparisonStatus(change.status)}</span></small><small className="occasion-updated">更新于 <time dateTime={row.observed_at}>{dataUpdateTime(row.observed_at)}</time></small><div className="occasion-tags">{(classified.tags[module]||[]).map(label=><span key={label}>{label}</span>)}</div><small className="occasion-date-clue" title="仅原标题中与本专题相关的日期文字；不推定年份，不代表已核实的生日或活动日">{dateText}</small><details><summary>原始名称与对比依据</summary><p>{row.title||'标题未记录'}</p><p>{row.sales_raw||'销量未显示'} · {row.price_raw||'价格未显示'}</p><p>对照 {change.date||'无'} · {comparisonStatus(change.status)}</p>{change.basis==='provisional_title_image'&&<p>两日同店唯一同标题同原图的展示线索，原卡分别保留。</p>}</details></div></div></td>
      <td className="warehouse-price"><span className="warehouse-price-raw" title={row.price_raw||'价格未显示'}>{row.price_raw||'—'}</span></td>
      <td className="occasion-number"><strong>{row.yipin_value??'—'}</strong>{row.yipin_value==null&&<small>{row.sales_raw||'销量未显示'}</small>}</td>
      <td className="occasion-number" title={comparisonStatus(change.status)}>{change.value??'—'}</td>
      <td className={`occasion-number${delta>0?' warehouse-increase':''}`} title={comparisonStatus(change.status)}>{delta==null?'—':`${delta>0?'+':''}${delta}`}</td>
      <td className="occasion-number" title={comparisonStatus(change.status)}>{formatGrowth(change.rate,change.status)}</td>
      <td className="occasion-action"><div className="warehouse-data-actions"><button type="button" className="warehouse-chart-button" aria-label={`查看数据图表：${row.title||'商品'}`} onClick={()=>onOpenProduct?.(row)}>查看走势</button>{onOpenSku&&skuEligible(row)&&<button type="button" className="warehouse-sku-button" aria-label={`查看 SKU：${row.title||'商品'}`} onClick={()=>onOpenSku(row)}>查看 SKU</button>}</div></td>
     </tr>;
    })}</tbody></table></div>
    {!cards.length&&<p className="occasion-empty">{summary.total?'没有符合当前筛选的商品。':'本次上新清单没有匹配的原标题线索。'}</p>}
    <div className="occasion-pagination"><span>{cards.length} 张原卡 · 按{sortLabel}{metricSort&&' · 无可比数据置后'} · 零销量 / 未知均保留</span><div><button type="button" disabled={currentPage===0} onClick={()=>update({page:currentPage-1})}>上一页</button><span>{currentPage+1} / {pages}</span><button type="button" disabled={currentPage+1>=pages} onClick={()=>update({page:currentPage+1})}>下一页</button></div></div>
   </DataComponent>
  </Section>
 </div>;
}
