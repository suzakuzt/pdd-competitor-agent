// Transparent review rules, never a profitability or demand-growth score.
export const STRATEGY_FILTERS = {salesMin:'',salesMax:'',priceMin:'',priceMax:'',theme:'',peerConfirmed:false,watchBand:'custom'};
export const WATCH_LABELS = {all:'全部达标 · 11件起',early:'11–30件 · 人工观察档',middle:'31–100件 · 人工观察档',higher:'>100件 · 人工观察档',custom:'自定义范围'};
const exactYipin = row => row.sales_precision==='exact_display' && ['已拼','已抢'].includes(row.sales_label) && row.sales_unit==='件' && Number.isInteger(row.sales_value);
const passesMain = row => exactYipin(row) && row.sales_value>10;
const filled = value => value!==null && value!==undefined && String(value).trim()!=='';
function bound(value, integer=false){
 if(!filled(value))return null;
 const text=String(value).trim();
 if(!(integer?/^\d+$/:/^\d+(?:\.\d{1,2})?$/).test(text))return NaN;
 const result=Number(text);return Number.isSafeInteger(integer?result:Math.round(result*100))?result:NaN;
}
export function parseDisplayPrice(raw){
 if(raw===null||raw===undefined||String(raw).trim()==='')return {status:'missing',fen:null,yuan:null,condition:null};
 const match=String(raw).trim().match(/^(券后\s*)?[¥￥]\s*(\d+(?:\.\d{1,2})?)$/);
 if(!match)return {status:'unparsed',fen:null,yuan:null,condition:null};
 const [whole,fraction='']=match[2].split('.');
 const fen=Number(whole)*100+Number(fraction.padEnd(2,'0'));
 if(!Number.isSafeInteger(fen))return {status:'unparsed',fen:null,yuan:null,condition:null};
 return {status:'single_display_amount',fen,yuan:fen/100,condition:match[1]?'coupon_after_display':'display_unspecified'};
}
export function strategyIssues(filters={}){
 const errors=[];
 const smin=bound(filters.salesMin,true),smax=bound(filters.salesMax,true),pmin=bound(filters.priceMin),pmax=bound(filters.priceMax);
 if(Number.isNaN(smin)||Number.isNaN(smax))errors.push('销量范围须为非负整数，留空表示不限制。');
 else if(smin!==null&&smax!==null&&smin>smax)errors.push('销量下限不能大于上限。');
 if(Number.isNaN(pmin)||Number.isNaN(pmax))errors.push('展示价须为非负金额，最多两位小数；留空表示不限制。');
 else if(pmin!==null&&pmax!==null&&pmin>pmax)errors.push('展示价下限不能大于上限。');
 return errors;
}
export function buildPeerEvidence(groups,runRows){
 const byId=new Map(runRows.map(row=>[row.observation_id,row])),evidence=new Map();
 for(const group of groups){
  const members=[...new Set(group.member_observation_ids||[])].map(id=>byId.get(id)).filter(row=>row&&row.run_id===group.run_id);
  const qualified=members.filter(passesMain);
  const item={group_id:group.group_id,group_code:group.group_code,run_id:group.run_id,member_count:members.length,eligible_count:qualified.length,eligible_observation_ids:qualified.map(row=>row.observation_id)};
  for(const member of members){const previous=evidence.get(member.observation_id);if(!previous||previous.eligible_count<item.eligible_count)evidence.set(member.observation_id,item);}
 }
 return evidence;
}
export function strategyMatches(row,filters={},peerEvidence=new Map()){
 if(strategyIssues(filters).length)return false;
 const smin=bound(filters.salesMin,true),smax=bound(filters.salesMax,true);
 if(smin!==null||smax!==null){if(!exactYipin(row)||(smin!==null&&row.sales_value<smin)||(smax!==null&&row.sales_value>smax))return false;}
 const pmin=bound(filters.priceMin),pmax=bound(filters.priceMax);
 if(pmin!==null||pmax!==null){const price=parseDisplayPrice(row.price_raw);if(price.fen===null||(pmin!==null&&price.fen<Math.round(pmin*100))||(pmax!==null&&price.fen>Math.round(pmax*100)))return false;}
 const terms=String(filters.theme||'').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
 if(terms.length&&!terms.some(term=>String(row.title||'').toLocaleLowerCase().includes(term)))return false;
 if(filters.peerConfirmed&&(!passesMain(row)||(peerEvidence.get(row.observation_id)?.eligible_count||0)<2))return false;
 return true;
}
export function observationBand(row){if(!passesMain(row))return null;return row.sales_value<=30?'11–30件':row.sales_value<=100?'31–100件':'>100件';}
export function applyWatchBand(filters,watchBand){
 const ranges={all:['11',''],early:['11','30'],middle:['31','100'],higher:['101','']};
 return watchBand==='custom'?{...filters,watchBand}:{...filters,watchBand,band:'eligible',labelUnit:'all',salesMin:ranges[watchBand]?.[0]||'11',salesMax:ranges[watchBand]?.[1]||''};
}
export function matchReasons(row,filters={},peerEvidence=new Map()){
 const reasons=[];
 if(passesMain(row))reasons.push(`单卡精确销量 ${row.sales_value} 件，超过 10 件`);
 else reasons.push(row.sales_raw?`原展示：${row.sales_raw}`:'销量原文缺失，保留未知');
 if(filled(filters.salesMin)||filled(filters.salesMax))reasons.push(`销量范围 ${filled(filters.salesMin)?filters.salesMin:'不限'}–${filled(filters.salesMax)?filters.salesMax:'不限'} 件`);
 reasons.push(`原展示价：${row.price_raw||'未显示'}`);
 if(filled(filters.priceMin)||filled(filters.priceMax))reasons.push(`单一原展示金额在 ¥${filled(filters.priceMin)?filters.priceMin:'不限'}–¥${filled(filters.priceMax)?filters.priceMax:'不限'} 范围；非采购成本`);
 const terms=String(filters.theme||'').trim().split(/\s+/).filter(Boolean).filter(term=>String(row.title||'').toLocaleLowerCase().includes(term.toLocaleLowerCase()));
 if(terms.length)reasons.push(`标题包含：${terms.join('、')}`);
 const peer=peerEvidence.get(row.observation_id);
 if(peer)reasons.push(`同标题 ${peer.group_code} 中 ${peer.eligible_count} 张卡各自销量 >10 件；不相加`);
 return reasons;
}
export function verificationGaps(row){
 const gaps=[];
 if(row.identity_status!=='unique_goods_id')gaps.push(row.identity_status?.startsWith('conflict_')?'商品身份有冲突':'商品 ID / 可追溯链接待补');
 else if(!row.goods_url)gaps.push('商品链接待补');
 if(row.image_content_status!=='verified_local')gaps.push(row.previous_attempt_blocked||/blocked|failed|invalid/i.test(row.image_status||'')||row.image_content_status==='unavailable_or_invalid'?'图片失败 / 限制待复核':'主图待补');
 gaps.push('同款同规格、实际到手价待核验','采购成本、起订量、运费与可承担试款预算待填写');
 return gaps;
}
export function selectionEvidence(row,filters={},peerEvidence=new Map()){
 const price=parseDisplayPrice(row.price_raw),peer=peerEvidence.get(row.observation_id);
 return {rule_version:1,main_threshold_eligible:passesMain(row),observation_band:observationBand(row),display_price_fen:price.fen,display_price_parse_status:price.status,display_price_condition:price.condition,same_title_eligible_count:peer?.eligible_count??null,matched_reasons:matchReasons(row,filters,peerEvidence),verification_gaps:verificationGaps(row),profitability_status:'unknown',cross_run_growth_status:'unknown'};
}
