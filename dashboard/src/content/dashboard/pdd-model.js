import {STRATEGY_FILTERS,strategyMatches} from './strategy-model.js';
export {parseDisplayPrice,strategyIssues,buildPeerEvidence,matchReasons,selectionEvidence,applyWatchBand,WATCH_LABELS,observationBand,verificationGaps} from './strategy-model.js';
export const EMPTY_FILTERS = {search:'',labelUnit:'all',band:'all',image:'all',identity:'all',sort:'sales',...STRATEGY_FILTERS};
export const INITIAL_FILTERS = {...EMPTY_FILTERS,band:'eligible',labelUnit:'all',salesMin:'11',watchBand:'all',sort:'sales'};
export const BAND_LABELS = {all:'全部销量状态',eligible:'销量 >10 件',yipin:'已有销量（精确正数）',boundary:'销量 =10 件',low:'销量 1–9 件',positive:'各标签精确正数',missing:'销量未知 / 未显示',fuzzy:'模糊或未解析',zero:'精确零值'};
export const ID_LABELS = {unique_goods_id:'商品 ID 本轮唯一',unknown_no_goods_id:'商品 ID 待补',unknown_invalid_goods_id:'商品 ID 无效，待核验',conflict_goods_url:'ID 与链接冲突',conflict_duplicate_goods_id:'本轮商品 ID 重复'};
export const IMAGE_LABELS = {cached:'已有本地图',pending:'图片待补',blocked:'失败 / 限制待复核'};
export const PRECISION_LABELS = {card_read:'逐卡读取',batch_read:'批次读取',run_window:'整轮窗口',legacy_unspecified:'旧来源未说明精度',unknown:'未知'};
const SALES_LABELS = new Set(['已拼','已抢','总售','已售','售出']);
export const exact = row => row.sales_precision==='exact_display' && Number.isFinite(row.sales_value);
export const positive = row => exact(row)&&SALES_LABELS.has(row.sales_label)&&row.sales_value>0;
export const eligible = row => exact(row)&&Number.isInteger(row.sales_value)&&['已拼','已抢'].includes(row.sales_label)&&row.sales_unit==='件'&&row.sales_value>10;
export const labelUnitKey = row => JSON.stringify([row.sales_label,row.sales_unit]);
export const imageState = row => row.image_content_status==='verified_local'?'cached':row.previous_attempt_blocked||/blocked|failed|invalid/i.test(row.image_status||'')||row.image_content_status==='unavailable_or_invalid'?'blocked':'pending';
export function bandMatches(row,band){
 if(band==='eligible')return eligible(row);
 if(band==='yipin')return positive(row)&&['已拼','已抢'].includes(row.sales_label);
 if(band==='boundary'||band==='low')return exact(row)&&['已拼','已抢'].includes(row.sales_label)&&row.sales_unit==='件'&&(band==='boundary'?row.sales_value===10:row.sales_value>0&&row.sales_value<10);
 if(band==='positive')return positive(row);
 if(band==='missing')return row.sales_precision==='missing';
 if(band==='fuzzy')return row.sales_precision==='non_exact_or_unparsed';
 if(band==='zero')return exact(row)&&row.sales_value===0;
 return true;
}
export function canSortSales(value){if(!value||value==='all')return false;try{const [label,unit]=JSON.parse(value);return SALES_LABELS.has(label)&&Boolean(unit);}catch{return false;}}
// 已拼 and 已抢 share one sales measure; other labels and units stay separate.
export const SALES_SORT_DESCRIPTION='已拼与已抢按同口径销量从高到低；其他标签和单位单独排列，未知销量置后。';
const SALES_LABEL_ORDER=['已拼','已抢','总售','已售','售出'];
function salesComparable(row){return exact(row)&&SALES_LABELS.has(row.sales_label)&&Boolean(row.sales_unit);}
export function compareSales(a,b){
 const aKnown=salesComparable(a),bKnown=salesComparable(b);
 if(aKnown!==bKnown)return aKnown?-1:1;
 if(aKnown){
  const labelRank=label=>['已拼','已抢'].includes(label)?0:SALES_LABEL_ORDER.indexOf(label);
  const labelDifference=labelRank(a.sales_label)-labelRank(b.sales_label);
  if(labelDifference)return labelDifference;
  if(a.sales_unit!==b.sales_unit){if(a.sales_unit==='件')return -1;if(b.sales_unit==='件')return 1;return a.sales_unit.localeCompare(b.sales_unit,'zh-CN');}
  if(a.sales_value!==b.sales_value)return b.sales_value-a.sales_value;
 }
 return a.view_order-b.view_order||a.observation_id-b.observation_id;
}
export function filterCards(rows,filters,peerEvidence=new Map()){
 const query=(filters.search||'').trim().toLocaleLowerCase();
 return rows.filter(row=>(!query||[row.title,row.view_order,`#${row.view_order}`,row.goods_id].some(v=>String(v??'').toLocaleLowerCase().includes(query)))
 &&(!filters.labelUnit||filters.labelUnit==='all'||labelUnitKey(row)===filters.labelUnit)&&bandMatches(row,filters.band)
 &&(!filters.image||filters.image==='all'||imageState(row)===filters.image)
 &&(!filters.identity||filters.identity==='all'||(filters.identity==='unique'?row.identity_status==='unique_goods_id':filters.identity==='conflict'?row.identity_status?.startsWith('conflict_'):row.identity_status?.startsWith('unknown_')))
 &&strategyMatches(row,filters,peerEvidence))
 .sort((a,b)=>{if(filters.sort==='evidence'){const rank={cached:0,pending:1,blocked:2},imageDifference=rank[imageState(a)]-rank[imageState(b)];if(imageDifference)return imageDifference;const identityDifference=Number(b.identity_status==='unique_goods_id')-Number(a.identity_status==='unique_goods_id');if(identityDifference)return identityDifference;}if(filters.sort==='sales')return compareSales(a,b);return a.view_order-b.view_order||a.observation_id-b.observation_id;});
}
export function relatedCandidates(groups,allRunRows,matchingRows){const selected=new Set(matchingRows.map(r=>r.observation_id)),byId=new Map(allRunRows.map(r=>[r.observation_id,r]));return groups.filter(g=>g.member_observation_ids.some(id=>selected.has(id))).map(group=>({group,matchedCount:group.member_observation_ids.filter(id=>selected.has(id)).length,rows:group.member_observation_ids.map(id=>byId.get(id)).filter(Boolean).sort((a,b)=>a.view_order-b.view_order)}));}
export function chooseDefaultRun(runs){const sorted=[...runs].sort((a,b)=>b.observed_to_epoch-a.observed_to_epoch);return sorted.find(r=>r.status==='complete'&&r.end_boundary_observed)||sorted[0];}
export function beijing(value,short=false){if(!value||!Number.isFinite(new Date(value).valueOf()))return '时间未知';return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:short?undefined:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:short?undefined:'2-digit',hour12:false}).format(new Date(value));}
export function safeLink(value){try{const url=new URL(value);return ['https:','http:'].includes(url.protocol)?value:null;}catch{return null;}}
export function filterScope(f){
 const labelUnit=f.labelUnit!=='all'?JSON.parse(f.labelUnit):null;
 const boundLabel=value=>value!==null&&value!==undefined&&String(value).trim()!==''?value:'不限';
 return [f.search&&{field:'title',label:'标题 / 位置 / ID 任一匹配',value:f.search},
  labelUnit&&{field:'sales_label',label:'销量标签',value:labelUnit[0]||'未知'},
  labelUnit&&{field:'sales_unit',label:'销量单位',value:labelUnit[1]||'未知'},
  f.band!=='all'&&{field:'sales_value',label:'销量分档（原标签与精度约束）',value:BAND_LABELS[f.band]},
  f.image!=='all'&&{field:'image_content_status',label:'图片状态',value:IMAGE_LABELS[f.image]},
  f.identity!=='all'&&{field:'identity_status',label:'身份状态',value:{unique:'ID 本轮唯一',unknown:'ID 待补 / 无效',conflict:'身份冲突'}[f.identity]},
  (f.salesMin!==''&&f.salesMin!=null||f.salesMax!==''&&f.salesMax!=null)&&{field:'sales_value',label:'单卡精确销量件区间',value:`${boundLabel(f.salesMin)}–${boundLabel(f.salesMax)} 件`},
  (f.priceMin!==''&&f.priceMin!=null||f.priceMax!==''&&f.priceMax!=null)&&{field:'price_raw',label:'单一原展示金额（非成本）',value:`¥${boundLabel(f.priceMin)}–¥${boundLabel(f.priceMax)}；金额未知排除`},
  f.theme&&{field:'title',label:'主题词（任一包含）',value:f.theme},
  f.peerConfirmed&&{field:'observation_id',label:'同标题对照规则',value:'本轮同组至少2张各自精确销量（已拼/已抢）>10件，销量不相加'},
  f.sort==='sales'&&{field:'sales_value',label:'排序',value:SALES_SORT_DESCRIPTION},
  f.sort==='evidence'&&{field:'image_content_status',label:'核验资料排序',value:'本地图→身份可查→原位置；不是盈利或增长评分'}].filter(Boolean);
}
