import {catalogueDay,warehouseScope,warehouseCards} from './warehouse-model.js';

export const FOCUS_CATEGORIES={yipin_gt10:'销量 >10',yipin_1to10:'销量 1–10',yipin_zero:'销量 0',other_label:'其他销量口径',unknown:'销量未显示 / 模糊'};
export const COMPARISON_LABELS={yesterday:'较昨日',day_before_yesterday:'较前天'};
export const MATCH_LABELS={confirmed_goods_id:'已确认同商品',provisional_title_image:'同标题同图线索'};
export function focusCategory(row){
 if(!row.sales_label&&row.category in FOCUS_CATEGORIES)return row.category;
 if(row.sales_label&&!['已拼','已抢'].includes(row.sales_label))return 'other_label';
 if(!['已拼','已抢'].includes(row.sales_label)||row.sales_unit!=='件'||row.sales_precision!=='exact_display'||!Number.isInteger(row.sales_value)||row.sales_value<0||typeof row.sales_raw!=='string'||!row.sales_raw.trim())return 'unknown';
 return row.sales_value>10?'yipin_gt10':row.sales_value>0?'yipin_1to10':'yipin_zero';
}
export function focusView(queries,shopId,asOf=''){
 const warehouse=warehouseScope(queries,shopId),day=catalogueDay(warehouse.days,asOf),date=day?.date||'';
 const summary=(queries.warehouse_focus_summary?.rows||[]).find(row=>row.shop_id===shopId&&row.run_id===day?.run_id);
 const queryId=summary&&queries.warehouse_focus_items?'warehouse_focus_items':'warehouse_records';
 const source=queryId==='warehouse_focus_items'?(queries.warehouse_focus_items.rows||[]).filter(row=>row.shop_id===shopId&&row.run_id===day?.run_id):warehouseCards(warehouse.records,date);
 const recorded=new Map(warehouse.records.map(row=>[row.observation_id,row]));
 const rows=source.map(row=>({...recorded.get(row.observation_id),...row,category:focusCategory(row),yipin_value:row.yipin_value??(['yipin_gt10','yipin_1to10','yipin_zero'].includes(focusCategory(row))?row.sales_value:null),track_id:row.track_id||recorded.get(row.observation_id)?.track_id}));
 const counts=Object.fromEntries(Object.keys(FOCUS_CATEGORIES).map(category=>[category,rows.filter(row=>row.category===category).length]));
 return {warehouse,day,date,summary,queryId,source,rows,counts};
}
export function comparisonValue(row,prefix){
 return {value:row[`${prefix}_value`]??row[`${prefix}_baseline_value`]??null,date:row[`${prefix}_baseline_date`]||'',delta:row[`${prefix}_delta`]??null,rate:row[`${prefix}_growth_rate`]??null,basis:row[`${prefix}_match_basis`]||null,status:row[`${prefix}_status`]||'not_prepared',rapid:row[`${prefix}_rapid_growth`]===true};
}
export function comparisonDisplayValue(row,prefix){
 const value=comparisonValue(row,prefix);
 return {...value,value:Number.isFinite(value.value)?value.value:null,
  delta:['comparable','zero_baseline'].includes(value.status)&&Number.isFinite(value.delta)?value.delta:null,
  rate:value.status==='comparable'&&Number.isFinite(value.rate)?value.rate:null};
}
export function focusSortLabel(sort,lane='yipin_gt10'){
 const label={delta:'增加',growth_rate:'涨幅'}[sort?.field];
 return label?`${label}${sort.direction==='asc'?'升序':'降序'}`:`${['rapid','rising'].includes(lane)?'增量':'销量'}降序`;
}
export function nextFocusSort(sort,field){
 return {field,direction:sort?.field===field&&sort.direction==='desc'?'asc':'desc'};
}
function comparisonSortValue(row,prefix,field){
 const value=comparisonValue(row,prefix);
 if(field==='growth_rate')return value.status==='comparable'&&Number.isFinite(value.rate)?value.rate:null;
 return ['comparable','zero_baseline'].includes(value.status)&&Number.isFinite(value.delta)?value.delta:null;
}
export function sortComparisonRows(rows,prefix,sort){
 if(!['delta','growth_rate'].includes(sort?.field))return [...rows];
 const direction=sort.direction==='asc'?1:-1;
 // Preserve each module's existing order for ties; unknowns stay last both ways.
 return [...rows].sort((a,b)=>{
  const av=comparisonSortValue(a,prefix,sort.field),bv=comparisonSortValue(b,prefix,sort.field);
  return av===null?(bv===null?0:1):bv===null?-1:direction*(av-bv);
 });
}
export function focusRows(rows,lane='yipin_gt10',search='',prefix='yesterday',sort=null){
 const term=search.trim().toLowerCase();
 const filtered=rows.filter(row=>(lane==='all'?true:lane==='rapid'||lane==='rising'?row.category==='yipin_gt10'&&(lane==='rapid'?comparisonValue(row,prefix).rapid:comparisonValue(row,prefix).delta>0):row.category===lane)&&(!term||`${row.title||''} ${row.goods_id||''} ${row.observation_id}`.toLowerCase().includes(term))).sort((a,b)=>['rapid','rising'].includes(lane)?(comparisonValue(b,prefix).delta??-1)-(comparisonValue(a,prefix).delta??-1)||(b.yipin_value??-1)-(a.yipin_value??-1):(b.yipin_value??-1)-(a.yipin_value??-1)||(a.view_order??0)-(b.view_order??0));
 return sortComparisonRows(filtered,prefix,sort);
}
export function focusComparisonCounts(rows,prefix){
 const available=rows.map(row=>comparisonValue(row,prefix));
 return {increased:available.filter(row=>Number.isFinite(row.delta)&&row.delta>0).length,confirmed:available.filter(row=>row.rapid&&row.basis==='confirmed_goods_id').length,provisional:available.filter(row=>row.rapid&&row.basis==='provisional_title_image').length,comparable:available.filter(row=>Number.isFinite(row.delta)).length,confirmedComparable:available.filter(row=>Number.isFinite(row.delta)&&row.basis==='confirmed_goods_id').length,provisionalComparable:available.filter(row=>Number.isFinite(row.delta)&&row.basis==='provisional_title_image').length};
}
export function comparisonDate(date,prefix){
 if(!/^\d{4}-\d{2}-\d{2}$/.test(date||''))return '';
 return new Date(Date.parse(`${date}T00:00:00Z`)-(prefix==='day_before_yesterday'?2:1)*86400000).toISOString().slice(0,10);
}
export function displayClueSeries(row){
 if(!row||!Number.isFinite(row.yipin_value)||!/^\d{4}-\d{2}-\d{2}$/.test(row.date||''))return {source:[],plot:[],observationIds:[]};
 const earlier=Object.keys(COMPARISON_LABELS).flatMap(prefix=>{
  const c=comparisonValue(row,prefix),observationId=row[`${prefix}_baseline_observation_id`];
  return c.basis==='provisional_title_image'&&['comparable','zero_baseline'].includes(c.status)&&Number.isFinite(c.value)&&c.date===comparisonDate(row.date,prefix)&&observationId!=null?[{date:c.date,display_yipin:c.value,observation_id:observationId,run_id:row[`${prefix}_baseline_run_id`],basis:c.basis}]:[];
 });
 if(!earlier.length)return {source:[],plot:[],observationIds:[]};
 const source=[...earlier,{date:row.date,display_yipin:row.yipin_value,observation_id:row.observation_id,run_id:row.run_id,basis:'current_card'}].sort((a,b)=>a.date.localeCompare(b.date));
 const byDate=new Map(source.map(p=>[p.date,p])),start=Date.parse(`${source[0].date}T00:00:00Z`),end=Date.parse(`${row.date}T00:00:00Z`);
 const plot=Array.from({length:Math.round((end-start)/86400000)+1},(_,i)=>{const date=new Date(start+i*86400000).toISOString().slice(0,10);return byDate.get(date)||{date,display_yipin:null,missing_day:true};});
 return {source,plot,observationIds:source.map(p=>p.observation_id)};
}
export function comparisonStatus(status){
 return ({comparable:'可比较',zero_baseline:'从零起量',not_prepared:'该日未生成对比',missing_baseline:'无该日采集',partial_baseline:'基期未采完整',target_incomplete:'当前未采完整',unmatched:'无匹配记录',ambiguous_identity:'匹配有歧义',identity_conflict:'身份有冲突',identity_unverified:'暂无匹配记录',sales_not_comparable:'销量口径不可比',negative_anomaly:'负差值待复核',time_unverified:'时间待核验'})[status]||'暂无可比记录';
}
export function formatGrowth(rate,status){return Number.isFinite(rate)?`${rate>0?'+':''}${(rate*100).toFixed(1)}%`:status==='zero_baseline'?'从零起量':'—';}
