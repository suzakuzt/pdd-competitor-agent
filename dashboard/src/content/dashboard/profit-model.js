export const OPPORTUNITY_LABELS={all:'全部线索',new_candidate:'首见优先',comparable_growth:'可比展示增长',eligible_reference:'历史单卡销量 >10',watch:'待观察'};
export const TRIAL_STATUS={draft:'草稿',testing:'试品中',paused:'已暂停',closed:'已结束'};
export const PLAN_FIELDS=[['price','我的计划售价','元 / 每笔订单'],['goods_cost','货品成本','元 / 每笔订单'],['packaging_cost','包装成本','元 / 每笔订单'],['shipping_cost','寄出运费','元 / 每笔订单'],['platform_fee_rate_pct','平台费率','百分数，如 0.6 表示 0.6%'],['platform_fee_fixed','平台固定费','元 / 每笔订单'],['refund_allowance','退款损耗预留','元 / 每笔订单'],['ad_cost_per_order','投放成本','元 / 每笔订单'],['other_cost_per_order','其他成本','元 / 每笔订单'],['tax_cost_per_order','税费预留','元 / 每笔订单'],['fixed_test_cost','本次固定试品成本','元 / 本次试品'],['max_loss_budget','最大可接受亏损','元 / 本次试品'],['target_orders','计划订单数','笔','count'],['review_after_orders','复核订单数','笔','count']];
export const ACTUAL_FIELDS=[['paid_orders','累计付款订单数','笔','count'],['settled_orders','累计结算订单数','笔','count'],['net_receipts','净回款（已扣退款，未扣其他任何费用）','元 / 累计'],['goods_cost','实际货品成本','元 / 累计'],['packaging_cost','实际包装成本','元 / 累计'],['shipping_cost','实际寄出运费','元 / 累计'],['platform_fees','实际平台费用','元 / 累计'],['ad_spend','实际投放支出','元 / 累计'],['refund_extra_cost','退款额外损耗','元 / 累计，不重复录入已扣退款'],['tax_cost','实际税费','元 / 累计'],['other_cost','实际其他成本','元 / 累计'],['fixed_cost','实际固定成本','元 / 本次试品']];
export const CHECK_FIELDS=[['supply_confirmed','供应和交付已核验'],['rights_confirmed','商品 / 素材使用权已核验'],['spec_confirmed','自家规格与定价已核验']];
const OPTIONAL_TEXT=['name','channel','own_product_ref','notes'];
const textOrNull=value=>value===null||value===undefined||String(value).trim()===''?null:String(value).trim();
function inputValue(value,count=false){const text=textOrNull(value);if(text===null)return null;if(!count)return text;if(!/^\d+$/.test(text)||!Number.isSafeInteger(Number(text)))throw new Error('订单数须为非负整数；未填写请留空。');return Number(text);}
function observationId(value){if(value===null||value===undefined)return null;if(typeof value!=='number'||!Number.isSafeInteger(value)||value<=0)throw new Error('原卡锚点须为真实记录的正整数 ID，不能猜测或改写。');return value;}
export function blankTrial(opportunity,id){return {trial_id:id,revision:0,name:opportunity.title||'',shop_id:opportunity.shop_id,observation_id:observationId(opportunity.observation_id),status:'draft',channel:null,own_product_ref:null,plan:Object.fromEntries(PLAN_FIELDS.map(([key])=>[key,null])),actual:{...Object.fromEntries(ACTUAL_FIELDS.map(([key])=>[key,null])),period_start:null,period_end:null,settlement_complete:null,evidence_reference:null},checks:Object.fromEntries(CHECK_FIELDS.map(([key])=>[key,null])),notes:null};}
export function trialPayload(row){
 const result={trial_id:row.trial_id,revision:row.revision??0,shop_id:row.shop_id??null,observation_id:observationId(row.observation_id),status:row.status||'draft'};
 for(const field of OPTIONAL_TEXT)result[field]=textOrNull(row[field]);
 result.plan=Object.fromEntries(PLAN_FIELDS.map(([key,,,kind])=>[key,inputValue(row.plan?.[key],kind==='count')]));
 result.actual=Object.fromEntries(ACTUAL_FIELDS.map(([key,,,kind])=>[key,inputValue(row.actual?.[key],kind==='count')]));
 for(const field of ['period_start','period_end','evidence_reference'])result.actual[field]=textOrNull(row.actual?.[field]);
 result.actual.settlement_complete=typeof row.actual?.settlement_complete==='boolean'?row.actual.settlement_complete:null;
 result.checks=Object.fromEntries(CHECK_FIELDS.map(([key])=>[key,typeof row.checks?.[key]==='boolean'?row.checks[key]:null]));
 return result;
}
export function filterOpportunities(rows,shopId,filters){
 const search=String(filters.search||'').trim().toLocaleLowerCase();
 const exactEligible=row=>row.priority_key==='eligible_reference'&&row.sales_precision==='exact_display'&&['已拼','已抢'].includes(row.sales_label)&&row.sales_unit==='件'&&Number.isSafeInteger(row.sales_value)&&row.sales_value>10;
 return rows.filter(row=>row.shop_id===shopId).filter(row=>filters.priority==='all'||row.priority_key===filters.priority).filter(row=>!search||[row.title,row.goods_id,row.view_order,...(row.reasons||[]),...(row.artist_names||[])].filter(value=>value!==null&&value!==undefined).join(' ').toLocaleLowerCase().includes(search)).sort((a,b)=>{
  const priority=a.priority_order-b.priority_order;if(priority)return priority;
  if(a.priority_key==='eligible_reference'&&b.priority_key==='eligible_reference'){
   const validA=exactEligible(a),validB=exactEligible(b);if(validA!==validB)return validA?-1:1;
   if(validA&&a.sales_value!==b.sales_value)return b.sales_value-a.sales_value;
  }
  return String(a.run_id).localeCompare(String(b.run_id))||a.view_order-b.view_order;
 });
}
export function arrivalEvidenceNote(summary){
 if(!Number.isSafeInteger(summary?.arrival_evidence_count)||summary.arrival_evidence_count<0)return null;
 const count=summary.coverage_expansion_evidence_count;
 const expansion=Number.isSafeInteger(count)&&count>=0?`（其中可能补采旧存量 ${count}）`:'';
 return `首见线索 ${summary.arrival_evidence_count}${expansion} · 不等于新品，不与四档相加`;
}
export function opportunityDrilldown(row){return {shop_id:row.shop_id,run_id:row.run_id,observation_ids:[row.observation_id],label:`选品线索 · ${row.title}`,source_query:'profit_opportunities',source_id:row.opportunity_id,request_id:`profit:${row.opportunity_id}`};}
export function profitEvidence(queries,rows,shopId){const runIds=new Set((queries.runs?.rows||[]).filter(row=>row.shop_id===shopId).map(row=>row.run_id)),ids=new Set(rows.flatMap(row=>row.evidence_observation_ids||[row.observation_id]).map(String)),comparisonIds=new Set(rows.flatMap(row=>row.comparison_item_ids||[])),arrivalIds=new Set(rows.flatMap(row=>row.new_arrival_item_ids||[]));return {observations:(queries.observations?.rows||[]).filter(row=>runIds.has(row.run_id)&&ids.has(String(row.observation_id))),comparison_items:(queries.comparison_items?.rows||[]).filter(row=>runIds.has(row.target_run_id)&&comparisonIds.has(row.comparison_item_id)),new_arrival_items:(queries.new_arrival_items?.rows||[]).filter(row=>row.shop_id===shopId&&arrivalIds.has(row.arrival_item_id))};}
export function profitScope(shopId,filters=null){return [{field:'shop_id',label:'当前店铺',value:shopId},...(filters?[{field:'priority_key',label:'机会类型',value:OPPORTUNITY_LABELS[filters.priority]},...(filters.search.trim()?[{field:'title',label:'标题 / 人物 / 位置 / 商品 ID 任一匹配',value:filters.search.trim()}]:[]),{field:'observation_id',label:'轮次范围',value:'参考与最新轮的独立原卡；不同轮次不相加销量'}]:[{field:'trial_id',label:'试品范围',value:'当前店铺的独立试品累计期间；不限定拼多多观察轮次，不跨试品合计利润'}])];}
export function profitReviewedSource(queries,component,queryId=component?.queryId){if(!component||!(component.queryIds||[component.queryId]).includes(queryId)||!queries[queryId])return null;let query=queries[queryId];if(['profit_trials','profit_trial_summary'].includes(queryId)&&query.source?.files){const trialIds=new Set((component.sourceRowsByQuery?.profit_trials||[]).map(row=>row.trial_id).filter(id=>typeof id==='string'&&id));const files=Object.fromEntries(Object.entries(query.source.files).filter(([filename])=>trialIds.has(filename.replaceAll('\\','/').split('/').at(-2))));query={...query,source:{...query.source,files}};}return {query,rows:component.sourceRowsByQuery?.[queryId]??(queryId===component.queryId?component.sourceRows:undefined)??[],filters:component.scopeFilters||[]};}
async function apiBody(response){let body;try{body=await response.json();}catch{throw new Error('本机试品服务未连接或返回非 JSON；未执行成功。');}if(!response.ok){const message=body.message||body.error||`请求失败（HTTP ${response.status}）`;throw new Error(typeof message==='string'?message:JSON.stringify(message));}return body;}
export async function previewProfit(fetcher,trial){const body=await apiBody(await fetcher('/__pdd_profit_preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trial:trialPayload(trial)})}));if(body.status!=='ok'||!body.analysis||typeof body.analysis!=='object')throw new Error('后台未返回有效计算结果；未使用本地猜测。');return body.analysis;}
export async function saveProfit(fetcher,trial){const payload=trialPayload(trial),body=await apiBody(await fetcher('/__pdd_profit_save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trial:payload,expected_revision:payload.revision})}));if(!['running','succeeded'].includes(body.status))throw new Error(body.message||'后台未确认保存任务。');return body;}
export async function readProfitStatus(fetcher){const body=await apiBody(await fetcher('/__pdd_profit_status',{cache:'no-store'}));if(!['idle','running','succeeded','failed'].includes(body.status))throw new Error('保存状态无法识别，未确认成功。');return body;}
export function profitSaveCompletion(state,awaiting){if(!awaiting)return {action:'none'};if(state.status==='succeeded')return state.receipt?.dashboard_built===true?{action:'reload'}:{action:'error',message:'记录已返回，但面板尚未确认构建成功；保留当前内容，请核对后台状态。'};if(state.status==='failed'||state.status==='idle')return {action:'error',message:state.message||'保存未确认完成，保留草稿。'};return {action:'wait'};}
export function profitReloadUrl(href,shopId){const url=new URL(href);url.searchParams.set('pdd_view','profit');if(shopId)url.searchParams.set('pdd_shop',shopId);return url.toString();}
export function amountLabel(value){return value===null||value===undefined||value===''?'未知':String(value);}
export function trialExport(row){return JSON.stringify({export_type:'reviewed_profit_trial',scope:'当前已保存试品；用户自填数据，不是平台核实结果',trial:row},null,2);}
