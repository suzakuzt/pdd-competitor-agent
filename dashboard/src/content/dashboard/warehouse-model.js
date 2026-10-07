export const DELTA_LABELS={comparable:'可比较',no_previous_day:'缺前日记录',missing_previous_day:'前日缺采',identity_unverified:'商品身份待核验',time_unverified:'读取时间待核验',sales_not_comparable:'销量标签或精度不可比',negative_anomaly:'负差值需复核'};
export const PRICE_LABELS={coupon_after_display:'券后展示价',display_unspecified:'未注明券的展示价'};
export const COHORT_LABELS={new_observation:'新品观察 · 首见待确认',old_reference:'老品参考',pending:'分类待核验'};
export const REASON_LABELS={initial_catalogue_or_fixed_baseline:'建档存量，实际上架年龄未知',first_observed_not_listing_date:'首次监测候选，未核实上架日期',possible_coverage_expansion:'补扫扩大覆盖，可能是旧存量',possible_existing_card_change:'疑似旧卡改图、改标题或补身份',first_time_unverified:'首见时间未核实',no_complete_catalogue:'尚无完整建档清单',not_after_initial_catalogue:'建档期间已观察，是否新品未知',identity_or_newness_unverified:'身份或新品依据不足'};
export function catalogueDay(days,asOf){
 const available=days.filter(d=>!asOf||d.date<=asOf).sort((a,b)=>b.date.localeCompare(a.date));
 return available.find(d=>d.is_complete)||available[0];
}
export function monitoringCohort(row,asOf){
 const first=row.monitor_first_date;
 const age=first&&first<=asOf?Math.floor((Date.parse(`${asOf}T00:00:00Z`)-Date.parse(`${first}T00:00:00Z`))/86400000):null;
 const cohort=row.monitor_origin==='reference_stock'?'old_reference':row.monitor_origin==='first_observed_candidate'&&age!==null?(age<30?'new_observation':'old_reference'):'pending';
 return {cohort,monitor_age_days:age,retired_candidate:cohort==='old_reference'&&row.monitor_origin==='first_observed_candidate'};
}
export function monitoringCards(records,asOf,cohort='',search=''){
 const latest=new Map();
 for(const row of [...records].filter(r=>r.date<=asOf).sort((a,b)=>a.date.localeCompare(b.date)||Number(Boolean(b.archived_anchor))-Number(Boolean(a.archived_anchor))||(a.observed_at||'').localeCompare(b.observed_at||'')))latest.set(row.track_id,row);
 const value=search.trim().toLowerCase();
 return [...latest.values()].map(row=>({...row,...monitoringCohort(row,asOf)})).filter(row=>(!cohort||row.cohort===cohort)&&(!value||`${row.title||''} ${row.goods_id||''} ${row.observation_id}`.toLowerCase().includes(value)));
}
export function warehouseScope(queries,shopId){
 const scoped=name=>(queries[name]?.rows||[]).filter(row=>row.shop_id===shopId);
 return {days:scoped('warehouse_days').sort((a,b)=>b.date.localeCompare(a.date)),records:scoped('warehouse_records'),watchRecords:queries.warehouse_watch_records?scoped('warehouse_watch_records'):scoped('warehouse_records'),tracks:scoped('warehouse_tracks'),points:scoped('warehouse_points')};
}
export function warehouseCards(records,date,search=''){
 const value=search.trim().toLowerCase();
 return records.filter(r=>r.date===date&&(!value||`${r.title||''} ${r.goods_id||''} ${r.observation_id}`.toLowerCase().includes(value)));
}
export function trendRange(points,trackId,endDate,length=30){
 if(!/^\d{4}-\d{2}-\d{2}$/.test(endDate||''))return {source:[],plot:[],startDate:''};
 const count=[30,90,365].includes(Number(length))?Number(length):30;
 const trackPoints=points.filter(p=>p.track_id===trackId&&p.date<=endDate).sort((a,b)=>a.date.localeCompare(b.date));
 const end=new Date(`${endDate}T00:00:00Z`),start=length==='all'&&trackPoints.length?new Date(`${trackPoints[0].date}T00:00:00Z`):new Date(end.getTime()-(count-1)*86400000),startDate=start.toISOString().slice(0,10);
 const source=points.filter(p=>p.track_id===trackId&&p.date>=startDate&&p.date<=endDate).sort((a,b)=>a.date.localeCompare(b.date));
 const byDate=new Map(source.map(p=>[p.date,p]));
 const plot=Array.from({length:Math.floor((end-start)/86400000)+1},(_,i)=>{const date=new Date(start.getTime()+i*86400000).toISOString().slice(0,10);return byDate.get(date)||{date,cumulative_yipin:null,daily_delta:null,display_price_yuan:null,price_condition:null,missing_day:true};});
 return {source,plot,startDate};
}
export function lifecycleSummary(points){
 const comparable=points.filter(p=>p.delta_status==='comparable'&&Number.isFinite(p.daily_delta));
 const peak=comparable.reduce((best,p)=>!best||p.daily_delta>best.daily_delta?p:best,null);
 return {recorded_days:points.length,comparable_days:comparable.length,peak_day:peak?.date||null,peak_delta:peak?.daily_delta??null,status:comparable.length>=2?'已有可比变化记录':'数据积累中'};
}
export function warehouseSource(queries,component,queryId=component?.queryId){
 if(!component||!(component.queryIds||[component.queryId]).includes(queryId)||!queries[queryId])return null;
 return {query:queries[queryId],rows:component.sourceRowsByQuery?.[queryId]??(queryId===component.queryId?component.sourceRows:[])??[],filters:component.scopeFilters||[]};
}
