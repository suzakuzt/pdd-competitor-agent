export const INTAKE_LABELS={needs_identity:'待核验',pending_capture:'待采集',awaiting_snapshot:'已有数据 · 待载入',saved_unpublished:'已有记录 · 待发布',partial:'部分数据',observed:'已有数据',unknown:'目标未载入'};
export function publishedTargetJob(target){
 const candidates=[target?.latest_collection,target?.onboarding?.job];
 return candidates.find(job=>job&&job.shop_id===target.shop_id&&['complete','partial'].includes(job.status)&&job.dashboard_built===true)||null;
}
export function targetDataHref(target,currentHref){
 if(target?.identity_conflict||!/^shop_[0-9a-f]{24}$/.test(target?.shop_id||'')||!publishedTargetJob(target))return null;
 return selectionHref(currentHref,{shopId:target.shop_id});
}
export function selectionHref(currentHref,{shopId='',targetId=''}){
 const url=new URL(currentHref);url.searchParams.set('pdd_view','warehouse');url.searchParams.delete('f.run_id');
 if(targetId){url.searchParams.set('pdd_target',targetId);url.searchParams.delete('pdd_shop');}
 else if(shopId){url.searchParams.set('pdd_shop',shopId);url.searchParams.delete('pdd_target');}
 return url.pathname+url.search+url.hash;
}
export function mergeShopDirectory(queries,directory){
 if(!directory)return queries;
 const targets=new Map((queries.competitor_targets?.rows||[]).map(row=>[row.target_id,row]));
 for(const row of directory.targets)targets.set(row.target_id,{...targets.get(row.target_id),...row,directory_live:true});
 return {...queries,competitor_targets:{...queries.competitor_targets,rows:[...targets.values()]}};
}
export function readyShopTarget(queries,shopId){
 return (queries.competitor_targets?.rows||[]).filter(target=>target.shop_id===shopId&&!target.identity_conflict&&target.onboarding?.status==='ready'&&target.onboarding.shop_id===shopId&&(target.onboarding.target_id===target.target_id||target.onboarding.resolved_target_id===target.target_id)).sort((a,b)=>String(b.onboarding.created_at||'').localeCompare(String(a.onboarding.created_at||'')))[0]||null;
}
const epoch=run=>Number.isFinite(run.observed_to_epoch)?run.observed_to_epoch:Date.parse(run.observed_to)||0;
export function targetCoverage(target,queries){
 const runs=(queries.runs?.rows||[]).filter(run=>target?.shop_id&&run.shop_id===target.shop_id).slice().sort((a,b)=>epoch(b)-epoch(a)||String(a.run_id).localeCompare(String(b.run_id)));
 const reference=runs.find(run=>run.status==='complete'&&run.end_boundary_observed===true)||null,latest=runs[0]||null;
 const conflict=Boolean(target?.identity_conflict),needsIdentity=conflict||target?.status==='needs_identity'||!target?.shop_id;
 const state=needsIdentity?'needs_identity':runs.length?(reference?'observed':'partial'):publishedTargetJob(target)?'awaiting_snapshot':Number.isSafeInteger(target?.run_count)&&target.run_count>0?'saved_unpublished':'pending_capture';
 const latestComplete=Boolean(latest?.status==='complete'&&latest?.end_boundary_observed===true);
 const coverageLabel=!latest?'尚未采集':!reference?(runs.length===1?'首轮部分 · 补扫待处理':'仅有部分轮 · 补扫待处理'):!latestComplete?'最新部分 · 历史完整参考保留':'最新完整观察';
 return {state,label:INTAKE_LABELS[state],runs,reference,latest,hasData:runs.length>0,canAnalyze:runs.length>0&&!needsIdentity,latestComplete,coverageLabel,historyCount:runs.length};
}
export function shopSelectionOptions(queries,shops=[]){
 const entries=shops.map(shop=>[shop.shop_id,shop.shop_name||shop.shop_id]),shopIds=new Set(shops.map(shop=>shop.shop_id)),seen=new Set(entries.map(([value])=>value));
 const targets=queries.competitor_targets?.rows||[],represented=new Set();
 const ordered=targets.slice().sort((a,b)=>Number(Boolean(b.onboarding))-Number(Boolean(a.onboarding)));
 for(const target of ordered){
  if(typeof target.target_id!=='string'||!target.target_id)continue;
  const coverage=targetCoverage(target,queries);
  if(coverage.canAnalyze&&shopIds.has(target.shop_id))continue;
  if(target.shop_id&&!target.identity_conflict){if(represented.has(target.shop_id))continue;represented.add(target.shop_id);}
  const value=`target:${target.target_id}`;
  if(seen.has(value))continue;
  const status=target.identity_conflict?'身份冲突':['queued','running'].includes(target.onboarding?.status)?target.onboarding.collection_job_id?'采集中':'识别中':coverage.label;
  entries.push([value,`${target.display_name||target.observed_shop_name||target.target_id} · ${status}`]);seen.add(value);
 }
 return {choices:entries.map(([value])=>value),choiceLabels:Object.fromEntries(entries)};
}
export function selectionOptionValue(selection,options,queries){
 if(selection.canAnalyze)return selection.shopId;
 const direct=`target:${selection.targetId}`;if(options.choices.includes(direct))return direct;
 const shopId=selection.target?.shop_id;if(shopId&&!selection.target.identity_conflict){
  if(options.choices.includes(shopId))return shopId;
  return options.choices.find(value=>value.startsWith('target:')&&(queries.competitor_targets?.rows||[]).some(row=>row.target_id===value.slice(7)&&row.shop_id===shopId&&!row.identity_conflict))||direct;
 }
 return direct;
}
export function resolveIntakeSelection(queries,selection,shops){
 const targets=queries.competitor_targets?.rows||[],requestedTarget=selection.targetId||'',requestedShop=selection.shopId||'';
 const unknown={shopId:'',shop:null,target:null,targetId:requestedTarget,state:'unknown',canAnalyze:false};
 if(requestedTarget){
  const target=targets.find(row=>row.target_id===requestedTarget);if(!target)return unknown;
  const coverage=targetCoverage(target,queries),shop=shops.find(row=>row.shop_id===target.shop_id)||null;
  return {...coverage,target,targetId:target.target_id,shop,shopId:coverage.canAnalyze&&shop?shop.shop_id:'',canAnalyze:Boolean(coverage.canAnalyze&&shop)};
 }
 const shop=requestedShop?shops.find(row=>row.shop_id===requestedShop):shops[0];
 if(!shop){const target=requestedShop?targets.find(row=>row.shop_id===requestedShop&&!row.identity_conflict):null;return target?{...targetCoverage(target,queries),target,targetId:target.target_id,shop:null,shopId:'',canAnalyze:false}:unknown;}
 const target=targets.find(row=>row.shop_id===shop.shop_id&&!row.identity_conflict)||null;
 const coverage=targetCoverage(target||{shop_id:shop.shop_id},queries);
 return {...coverage,target,targetId:target?.target_id||'',shop,shopId:coverage.canAnalyze?shop.shop_id:'',canAnalyze:coverage.canAnalyze};
}
export function safeIntakeLink(raw){
 if(typeof raw!=='string'||/[\u0000-\u0020\u007f]/u.test(raw))return null;
 try{const url=new URL(raw);return ['https:','http:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}
}
export function intakePayload(name,url){
 const cleanName=String(name||'').trim(),cleanUrl=String(url||'').trim();
 if(!cleanName&&!cleanUrl)throw new Error('请填写店铺链接，或先用店铺名称登记待核验目标。');
 if(cleanName.length>200||cleanUrl.length>4096)throw new Error('名称或链接过长，请使用店铺名称和原始分享链接。');
 return {name:cleanName||null,url:cleanUrl||null};
}
export function intakeCompletion(data){
 const receipt=data?.receipt;
 if(data?.status!=='succeeded'||!receipt||receipt.dashboard_built!==true||receipt.website_collection_performed!==false||!['registered','unchanged'].includes(receipt.registration_status)||typeof receipt.target_id!=='string'||!receipt.target_id||receipt.target_id.length>200||!(receipt.shop_id===null||typeof receipt.shop_id==='string')||!['needs_identity','pending_capture','observed'].includes(receipt.target_status))throw new Error('登记或页面更新收据不完整，尚未确认成功；请先核对目标列表。');
 return receipt;
}
export function intakeReloadHref(currentHref,targetId){
 const url=new URL(currentHref);url.searchParams.set('pdd_target',targetId);url.searchParams.delete('pdd_shop');return url.pathname+url.search+url.hash;
}
export function intakeCollectionRequest(target){
 return ['请继续处理已登记的拼多多店铺分析目标。',`目标 ID：${target.target_id}`,`名称：${target.display_name||target.observed_shop_name||'待核实'}`,target.source_url?`原始链接：${target.source_url}`:null,target.shop_id?`店铺身份线索：${target.shop_id}`:null,'请先核实身份，再按既有授权流程采集；实际完整或部分范围、独立原卡、观察时间和失败原因均保留。','本次只是复制请求，没有启动浏览器或采集；不合并同标题卡片，不猜销量、上架或利润。'].filter(Boolean).join('\n');
}
export async function readIntakeJson(fetcher,url,options={}){
 const response=await fetcher(url,{credentials:'same-origin',cache:'no-store',...options,headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});
 let data;try{data=await response.json();}catch{throw new Error('本机服务没有返回有效 JSON，尚未确认登记结果。');}
 if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('本机服务返回格式不完整，尚未确认登记结果。');
 if(!response.ok){const error=new Error(data.message||data.error||`登记请求未完成（HTTP ${response.status}）`);error.receipt=data.receipt;error.errorCode=data.error_code;throw error;}
 return {data,httpStatus:response.status};
}
export const TRACKING_LABELS={unconfigured:'未设置跟踪',enabled:'已启用跟踪',paused:'已暂停跟踪'};
export function targetTracking(target){
 const loaded=Object.hasOwn(TRACKING_LABELS,target?.tracking_status)&&Number.isSafeInteger(target?.tracking_revision)&&target.tracking_revision>=0;
 const canConfigure=loaded&&target.can_configure_tracking===true&&typeof target.shop_id==='string'&&Boolean(target.shop_id)&&!target.identity_conflict&&target.status!=='needs_identity';
 return {loaded,status:loaded?target.tracking_status:'unavailable',revision:loaded?target.tracking_revision:null,label:loaded?TRACKING_LABELS[target.tracking_status]:'跟踪设置尚未载入',canConfigure,
  note:!loaded?'请刷新最新面板后核对设置。':!canConfigure?'先核实店铺身份，再设置跟踪。':target.tracking_status==='paused'?'暂停后保留全部历史和固定起点。':target.tracking_status==='enabled'?'已保存跟踪意向；采集时间在本店更新区域单独设置。':'尚未选择是否跟踪；现有历史照常保留。'};
}
export function trackingPayload(target,status){
 const setting=targetTracking(target);
 if(!setting.canConfigure)throw new Error('当前店铺尚不具备保存跟踪设置的条件，请先核实身份并刷新。');
 if(!['enabled','paused'].includes(status))throw new Error('跟踪设置只能为启用或暂停。');
 if(typeof target.target_id!=='string'||!target.target_id||target.target_id.length>200)throw new Error('当前目标编号无效。');
 return {target_id:target.target_id,status,expected_revision:setting.revision};
}
export function trackingCompletion(data,target,status){
 const receipt=data?.receipt,previous=targetTracking(target);
 if(data?.status!=='succeeded'||!receipt||receipt.operation!=='tracking'||!['saved','unchanged'].includes(receipt.setting_status)||receipt.dashboard_built!==true||receipt.website_collection_performed!==false||receipt.baseline_changed!==false||receipt.history_deleted!==false||receipt.tracking_connection_status!=='connection_pending'||receipt.target_id!==target.target_id||receipt.shop_id!==target.shop_id||receipt.tracking_status!==status||!Number.isSafeInteger(receipt.tracking_revision)||receipt.tracking_revision<previous.revision)throw new Error('跟踪设置或页面更新收据不完整，尚未确认成功；请核对后刷新。');
 return receipt;
}
export function trackingReloadHref(currentHref,targetId){
 const url=new URL(currentHref);url.searchParams.set('pdd_target',targetId);url.searchParams.delete('pdd_shop');url.searchParams.set('pdd_manage','1');return url.pathname+url.search+url.hash;
}
