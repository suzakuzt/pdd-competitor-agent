import React,{useEffect,useRef,useState} from 'react';
import {dataUpdateTime} from './data-time.js';
import {Dialog} from '../../data-app-public.jsx';
import {ZoomableImage} from './ZoomableImage.jsx';
import {productDisplayName} from './product-display-name.js';
import {skuPrice,skuSpecs,skuView,skuSummaryText} from './sku-model.js';
import {collectionProgress,collectionReleasedImages,collectionFailureDetails,collectionPublicationStage} from './collection-progress.js';
import './collection-controls.css';
import './sku-panel.css';

export const collectionTime=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未设置';
async function api(path,payload,signal){const response=await fetch(path,payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{signal});const result=await response.json();if(!response.ok)throw new Error(result.message||'本机服务暂不可用');return result;}
function useCollection(shopId,observationId){
 const [loaded,setLoaded]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const url=`/__pdd_collection_status?shop_id=${encodeURIComponent(shopId)}${observationId?`&observation_id=${observationId}`:''}`;
 const sessionRef=useRef({url,active:true,busy:false,revision:0});
 if(sessionRef.current.url!==url)sessionRef.current={url,active:true,busy:false,revision:0};
 const session=sessionRef.current;
 useEffect(()=>{session.active=true;let loading=false;const controller=new AbortController();const load=async()=>{if(loading||session.busy)return;loading=true;const revision=session.revision;try{const value=await api(url,undefined,controller.signal);if(session.active&&revision===session.revision){setLoaded({url,value});setError('');}}catch(e){if(session.active&&revision===session.revision&&e.name!=='AbortError')setError(e.message);}finally{loading=false;}};load();const timer=setInterval(load,2500);return()=>{session.active=false;controller.abort();clearInterval(timer);};},[url]);
 const action=async(path,payload,onJob)=>{
  if(session.busy||!session.active)return null;
  session.busy=true;session.revision++;setBusy(true);setError('');
  try{const result=await api(path,payload);if(!session.active)return result;
   if(result.job){onJob?.(result.job);setLoaded(current=>({url,value:{...(current?.url===url?current.value:{}),[result.job.kind==='sku_batch'?'latest_sku_batch_job':'latest_job']:result.job,recovery_required:result.job.recovery_required===true,recovery_reason:result.job.recovery_reason||null,jobs:[result.job,...(current?.url===url?current.value.jobs||[]:[]).filter(job=>job.id!==result.job.id)]}}));}
   try{const value=await api(url);if(session.active)setLoaded({url,value});}catch{if(session.active)setError('请求已提交，暂时无法刷新进度。');}
   return result;
  }catch(e){if(session.active)setError(e.message);return null;}finally{session.busy=false;if(session.active)setBusy(false);}
 };
 return {data:loaded?.url===url?loaded.value:null,error,busy,action};
}
export const collectionElapsed=value=>{if(!Number.isFinite(value)||value<0)return '待记录';const seconds=Math.round(value);return `${Math.floor(seconds/60)}分${seconds%60}秒`;};
const announcedCollectionJobs=new Set();
const SKU_BATCH_TERMINAL=['complete','partial','failed','cancelled','interrupted','manual_review','needs_login','needs_browser','needs_url'];
const publishedImagePartial=job=>job?.status==='partial'&&job.dashboard_built===true&&job.reason!=='dashboard_build_failed'&&collectionReleasedImages(job).status==='partial';
export function collectionCompletion(job,shopId){
 const receipt=job?.receipt;
 if(!job?.id||(job.shop_id&&job.shop_id!==shopId))return null;
 if(job.kind==='sku_batch'){
  if(!SKU_BATCH_TERMINAL.includes(job.status))return null;
  const summary=job.sku_summary||{status:job.status,reason:job.reason,message:job.message};
  return {id:job.id,shopId,kind:'sku_batch',count:Number.isSafeInteger(summary.total)?summary.total:null,completedAt:dataUpdateTime(job.ended_at),skuSummary:skuSummaryText(summary,{standalone:true}),skuOutcome:summary.status||job.status};
 }
 // The local service validates the same-shop release receipt before complete.
 if(!job?.id||(job.shop_id&&job.shop_id!==shopId)||job.status!=='complete'&&!publishedImagePartial(job)||job.dashboard_built!==true||job.reason==='dashboard_build_failed')return null;
 const count=Number.isSafeInteger(receipt?.new_run?.cards)&&receipt.new_run.cards>0?receipt.new_run.cards:Number.isSafeInteger(job.progress?.cards)&&job.progress.cards>0?job.progress.cards:null;
 const imageSummary=collectionReleasedImages(job,shopId);
 return {id:job.id,shopId,count,completedAt:dataUpdateTime(job.ended_at),imageSummary};
}
export function collectionCompletionTitle(completion,announced=false){
 if(completion?.kind==='sku_batch')return completion.skuOutcome==='complete'?(announced?'SKU 采集完成':'最近一次 SKU 采集完成'):completion.skuOutcome==='partial'?'SKU 部分完成':completion.skuOutcome==='cancelled'?'SKU 采集已停止':'SKU 采集未完成';
 if(completion?.imageSummary?.status!=='complete')return completion?.imageSummary?.missing>0?`采集未完成：仍有 ${completion.imageSummary.missing} 张商品主图待补`:'采集未完成：图片完整性待核验';
 return announced?'本店采集完成，数据已更新':'最近一次采集已完成';
}
// Only tasks observed running here (or submitted here) can announce a transition.
// The page-lifetime delivery set also prevents a remount from announcing twice.
export function collectionCompletionEvent(tracker,view,shopId){
 if(tracker.shopId!==shopId){tracker.shopId=shopId;tracker.pending=new Set();}
 if(view.active){if(view.active.shop_id===shopId&&view.active.id)tracker.pending.add(view.active.id);return null;}
 const job=[view.latest,view.latestBatch].find(item=>item?.id&&tracker.pending.has(item.id));
 if(!job)return null;
 tracker.pending.delete(job.id);
 const completion=collectionCompletion(job,shopId),key=`${shopId}:${job.id}`;
 if(!completion||tracker.delivered.has(key))return null;
 tracker.delivered.add(key);
 return completion;
}
export function collectionView(data,now=Date.now()){
 const jobs=data?.jobs||[],mode=data?.browser_mode,local=mode==='local_chrome';
 const inMode=job=>job&&(!job.browser_mode||job.browser_mode===mode)&&(!job.kind||job.kind==='shop');
 const latestShop=Object.prototype.hasOwnProperty.call(data||{},'latest_job')?(inMode(data.latest_job)?data.latest_job:null):jobs.find(inMode);
 const inBatchMode=job=>job?.kind==='sku_batch'&&(!job.browser_mode||job.browser_mode===mode);
 const latestBatch=Object.prototype.hasOwnProperty.call(data||{},'latest_sku_batch_job')?(inBatchMode(data.latest_sku_batch_job)?data.latest_sku_batch_job:null):jobs.find(inBatchMode);
 const active=[...jobs,latestBatch,latestShop].find(job=>job&&(job.host_status_unavailable===true||job.host_attempt_open===true||['queued','running','host_claiming','awaiting_browser','validating'].includes(job.status)));
 // Finished batch attempts are historical evidence, not the current shop outcome.
 const latest=active?.kind==='sku_batch'?active:latestShop;
 const standaloneSku=active?.kind==='sku_batch',singleSku=active?.kind==='sku',standaloneResult=latest?.kind==='sku_batch';
 const blocked=active?.host_status_unavailable===true||active?.host_attempt_open===true;
 const reason=latest?.recovery_reason||latest?.reason||data?.recovery_reason||'';
 const connection=local&&(['needs_browser','connection_required'].includes(latest?.status)||reason==='connection_required');
 const login=local&&(latest?.status==='needs_login'||reason==='login_required');
 const challenge=reason==='access_restricted';
 const recoverable=local&&!standaloneResult&&!active&&!connection&&!login&&!challenge&&(latest?.status==='needs_url'||['entry_unavailable','zero_products','identity_mismatch'].includes(reason)||data?.recovery_required===true||latest?.recovery_required===true);
 const count=Number.isSafeInteger(active?.progress?.cards)&&active.progress.cards>=0?` · 已读取 ${active.progress.cards} 张`:'';
 const connecting=active?.status==='running'&&active.progress?.phase==='正在连接你已打开的 Chrome';
 const publication=active&&['running','validating'].includes(active.status)?collectionPublicationStage(active):null;
 const publishing=publication?.key==='dashboard_build';
 const collectingImages=active?.status==='running'&&active.progress?.stage==='images';
 const validatingImages=publication?.key==='image_validation';
 const validating=active?.status==='validating'||active?.status==='running'&&/校验|核验|验收|备份|发布|配对/.test(active.progress?.phase||'');
 const skuValidating=active?.status==='running'&&active.progress?.stage==='sku_validation'||singleSku&&validating;
 const collectingSku=active?.status==='running'&&!skuValidating&&(singleSku||Number.isSafeInteger(active.progress?.sku_total));
 const skuProgress=collectingSku&&!singleSku?`SKU 已处理 ${(active.progress.sku_completed||0)+(active.progress.sku_partial||0)+(active.progress.sku_failed||0)} / ${active.progress.sku_total} 个商品`:'';
 const started=Date.parse(active?.started_at||''),elapsed=active&&Number.isFinite(started)?` · 已用 ${collectionElapsed(Math.max(0,(now-started)/1000))}`:'';
 const phase=blocked?'采集异常，需处理':(standaloneSku||singleSku)&&active.cancel_requested?'已请求停止 SKU，正在保存已读取规格':connecting?'等待 Chrome 连接':publication?publication.label+count:collectingImages?'正在核对并补齐商品图片':singleSku?(skuValidating?'单品 SKU 已读取，正在核验并保存':active.status==='queued'?'单品 SKU 已排队，等待采集器启动':active.status==='running'?`单品 SKU · ${active.progress?.phase||'正在读取规格图片和价格'}`:'单品 SKU 等待连接采集器'):skuValidating?`${standaloneSku?'':'全店数据已入库 · '}SKU 正在备份并校验保存`:collectingSku?`${standaloneSku?'补采规格 · ':'全店数据已入库 · '}${skuProgress}`:publishing?`正在更新数据舱${count}`:validating?`正在备份并校验数据${count}`:active?.status==='running'?(standaloneSku?'正在补采 SKU':`采集中${count}`):active?.status==='queued'?(standaloneSku?'SKU 已排队，等待采集器启动':'等待采集器启动'):active?'等待连接采集器':'';
 const progress=phase?phase+elapsed:'';
 const failure=standaloneResult||publishedImagePartial(latest)?'':connection?'请在 Chrome 允许本机采集连接，再点开始采集。拼多多登录由原 Chrome 保留。':login?'请在原 Chrome 登录拼多多，再点开始采集；登录状态由 Chrome 自动保存。':challenge?'请在 Chrome 完成人工验证后重试。':latest?.status==='complete'&&reason==='dashboard_build_failed'?'数据已入库，数据舱更新失败，请运行 REFRESH_DASHBOARD.cmd。':latest?.status==='complete'&&latest.dashboard_built!==true?'采集已入库，尚未确认数据舱更新完成。':recoverable?reason==='zero_products'?'本次未读取到商品，请更新本店分享链接。':reason==='identity_mismatch'?'链接与本店不符，请粘贴本店最新分享链接。':'分享链接不可用，请更新后重试。':['failed','partial','manual_review','interrupted'].includes(latest?.status)?collectionFailureDetails(latest,reason).summary:'';
 const failureDetails=blocked?collectionFailureDetails(active):failure&&latest?.status!=='complete'?collectionFailureDetails(latest,reason|| (connection?'connection_required':login?'login_required':'')):reason==='dashboard_build_failed'?collectionFailureDetails(latest,reason):null;
 return {latest,latestBatch,active,blocked,local,recoverable,connection,connecting,login,challenge,validating,publishing,publication,collectingImages,validatingImages,collectingSku,skuValidating,standaloneSku,singleSku,progress,failure,failureDetails,complete:!active&&(latest?.status==='complete'||publishedImagePartial(latest))};
}
export function CollectionSkuHistory({view,shopId,excludeId}){
 const batch=view.latestBatch?.id!==excludeId?collectionCompletion(view.latestBatch,shopId):null;
 const shop=view.latest,shopSummary=!view.active&&shop?.shop_id===shopId?skuSummaryText(shop.sku_summary):'';
 if(view.active||!batch&&!shopSummary)return null;
 return <details className="collection-failure-details"><summary>历史 SKU 采集结果</summary><div>{batch&&<p>{collectionCompletionTitle(batch)} · 结束时间：{batch.completedAt}（北京时间）<br/>{batch.skuSummary}</p>}{shopSummary&&<p>此前全店任务附带的 SKU 结果：{shopSummary}</p>}<p>需要更新规格时，请打开对应商品并点击“采集 SKU”。</p></div></details>;
}
export function CollectionControl(props){return <CollectionControlBody key={props.shopId} {...props}/>;}
export function CollectionProgress({view}){
 const value=collectionProgress(view.singleSku&&view.skuValidating?{...view.active,progress:{...view.active.progress,stage:'sku_validation',phase:'单品 SKU 已读取，正在核验并保存'}}:view.active);
 if(!value)return <span role="status">{view.progress}</span>;
 return <div className="collection-progress" data-reviewed-rows="true">
  <div className="collection-progress-heading" role="status"><span>{view.progress}</span>{value.determinate&&<strong>{value.percent}%</strong>}</div>
  <div className={`collection-progress-track${value.determinate?'':' collection-progress-indeterminate'}`} role="progressbar" aria-label={value.ariaLabel} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value.determinate?value.percent:undefined} aria-valuetext={value.ariaText}>
   {value.segments&&Object.entries(value.segments).map(([kind,width])=><span key={kind} className={`collection-progress-segment collection-progress-${kind}`} style={{width:`${width}%`}} aria-hidden="true"/>)}
  </div>
  {value.counts&&<div className="collection-progress-counts">{value.determinate&&!value.current&&value.stageLabel&&<small>{value.stageLabel}</small>}<span>完整 <b>{value.counts.complete}</b></span><span>部分 <b>{value.counts.partial}</b></span><span>失败 <b>{value.counts.failed}</b></span>{value.traversalNote&&<small>{value.traversalNote}</small>}</div>}
  {value.imageCounts&&<div className="collection-progress-counts"><span>{value.imageCounts.text}</span>{value.traversalNote&&<small>{value.traversalNote}</small>}</div>}
  {value.current&&<div className="collection-progress-current">
   <div className="collection-progress-heading"><span>当前商品 · {value.currentStage}</span><small>{value.current.read} / {value.current.total} 个规格 · {value.current.percent}%</small></div>
   <div className="collection-progress-track collection-progress-secondary" role="progressbar" aria-label="当前商品规格读取进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value.current.percent} aria-valuetext={`${value.current.read} / ${value.current.total} 个规格已读取；不代表图片和价格全部校验成功。`}><span className="collection-progress-segment collection-progress-current-read" style={{width:`${value.current.percent}%`}} aria-hidden="true"/></div>
  </div>}
 </div>;
}
function CollectionControlBody({shopId}){
 const {data,error,busy,action}=useCollection(shopId),[notice,setNotice]=useState(''),[entryUrl,setEntryUrl]=useState(''),[completionNotice,setCompletionNotice]=useState(null);
 const completionTracker=useRef({shopId,pending:new Set(),delivered:announcedCollectionJobs});
 const view=collectionView(data),disabled=busy||!data||Boolean(view.active);
 const completion=!view.active&&completionNotice?.kind==='sku_batch'&&completionNotice.id===view.latestBatch?.id?completionNotice:view.complete?collectionCompletion(view.latest,shopId):null;
 const incomplete=completion&&(completion.kind!=='sku_batch'&&completion.imageSummary?.status!=='complete'||completion.skuOutcome&&completion.skuOutcome!=='complete');
 const announced=completion&&completionNotice?.id===completion.id;
 useEffect(()=>{const event=collectionCompletionEvent(completionTracker.current,collectionView(data),shopId);if(event)setCompletionNotice(event);},[data,shopId]);
 const start=async(replacement=false)=>{
  setNotice('');setCompletionNotice(null);
  if(data?.browser_mode!=='local_chrome'||data?.local_worker_available===false&&!view.connection){
   setNotice('采集器未连接，暂时无法启动。');
   return;
  }
  if(disabled)return;
  const payload={shop_id:shopId,kind:'shop'};
  if(replacement){const url=entryUrl.trim();if(!url){setNotice('请粘贴本店最新分享链接。');return;}payload.entry_url=url;if(Number.isSafeInteger(data?.entry?.revision)&&data.entry.revision>=0)payload.entry_revision=data.entry.revision;}
  const result=await action('/__pdd_collection_start',payload,job=>{if(job.id&&job.shop_id===shopId)completionTracker.current.pending.add(job.id);});
  if(result?.job&&['queued','running','validating'].includes(result.job.status))setEntryUrl('');
  if(result?.job?.status==='awaiting_host')setNotice('采集器未连接，暂时无法启动。');
 };
 return <section className="collection-control collection-simple" aria-label="本店数据更新" data-reviewed-rows="true">
  <button type="button" className="collection-start" disabled={disabled} onClick={()=>start()}>{busy?'正在提交…':view.blocked?'开始采集':view.connecting?'连接 Chrome…':view.publication?view.publication.buttonLabel:view.collectingImages?'正在补图…':view.skuValidating?'SKU 校验中…':view.collectingSku?'SKU 采集中…':view.publishing?'更新数据舱…':view.validating?'正在校验…':view.active?.status==='running'?'采集中…':'开始采集'}</button>
  {view.standaloneSku&&<button type="button" className="collection-view-data" disabled={busy||view.active.cancel_requested===true} onClick={()=>action('/__pdd_collection_cancel',{shop_id:shopId,id:view.active.id})}>{view.active.cancel_requested?'正在停止 SKU…':'停止 SKU'}</button>}
  {(error||notice)?<span className="collection-error" role="alert">{error||notice}</span>:view.progress?<CollectionProgress view={view}/>:!data?<span role="status">连接中…</span>:view.failure?<span className="collection-error" role="alert">{view.failure}</span>:view.complete&&!completion?<span className="collection-error">完成结果待核验</span>:null}
  {view.failureDetails&&!error&&!notice&&<details className="collection-failure-details"><summary>查看原因与处理方式</summary><div><p>{view.failureDetails.cause}</p><p><strong>下一步：</strong>{view.failureDetails.nextStep}</p><dl><div><dt>任务状态</dt><dd>{view.failureDetails.status}</dd></div>{view.failureDetails.endedAt&&<div><dt>结束时间</dt><dd>{dataUpdateTime(view.failureDetails.endedAt)}（北京时间）</dd></div>}{view.failureDetails.jobId&&<div><dt>任务编号</dt><dd>{view.failureDetails.jobId}</dd></div>}{view.failureDetails.reason&&<div><dt>问题标识</dt><dd>{view.failureDetails.reason}</dd></div>}</dl></div></details>}
  {view.connection&&!view.active&&completion?.kind!=='sku_batch'&&<small className="collection-browser-settings">Chrome 设置地址：<code>chrome://inspect/#remote-debugging</code></small>}
  {completion&&!error&&!notice&&<div className={`collection-completion${announced?' collection-completion-new':''}${incomplete?' collection-completion-incomplete':''}`} role={announced?'status':undefined} aria-live={announced?'polite':'off'} aria-atomic="true">
   <span className="collection-completion-mark" aria-hidden="true">{completion.imageSummary&&completion.imageSummary.status!=='complete'||completion.skuOutcome&&completion.skuOutcome!=='complete'?'!':'✓'}</span>
   <div><strong>{collectionCompletionTitle(completion,announced)}</strong><span>{completion.kind==='sku_batch'?'本轮规格采集':completion.count===null?'本店商品':`本店 ${completion.count} 张商品`} · {completion.kind==='sku_batch'||completion.imageSummary&&completion.imageSummary.status!=='complete'?'结束':'完成'}时间：{completion.completedAt}（北京时间）</span>{completion.imageSummary&&<span>{completion.imageSummary.text}</span>}{completion.skuSummary&&<span>{completion.skuSummary}</span>}</div>
   <button type="button" className="collection-view-data" onClick={()=>window.location.reload()}>查看最新数据</button>
  </div>}
  <CollectionSkuHistory view={view} shopId={shopId} excludeId={completion?.kind==='sku_batch'?completion.id:null}/>
  {view.recoverable&&<form className="collection-recovery" onSubmit={event=>{event.preventDefault();start(true);}}>
   <label htmlFor={`collection-entry-${shopId}`}>粘贴本店最新分享链接</label>
   <div className="collection-recovery-entry"><input id={`collection-entry-${shopId}`} type="url" value={entryUrl} placeholder="https://mobile.yangkeduo.com/…" autoComplete="off" disabled={disabled} onChange={event=>setEntryUrl(event.target.value)}/><button type="submit" disabled={disabled||!entryUrl.trim()}>更新链接并采集</button></div>
  </form>}
 </section>;
}

export function SkuDialog({row,shopId,onClose}){
 return <Dialog open={Boolean(row)} onClose={onClose} title="商品 SKU" expanded>{row&&<SkuPanel key={`${shopId}:${row.observation_id}`} row={row} shopId={shopId}/>}</Dialog>;
}
export function SkuPanel({row,shopId=row?.shop_id}){
 const {data,error,busy,action}=useCollection(shopId,row.observation_id);
 const view=skuView(data,row,shopId),capture=view.capture,variants=capture?.variants||[];
 const activeProgress=view.active?collectionView({browser_mode:data?.browser_mode,jobs:[view.active]}):null;
 const start=()=>action('/__pdd_collection_start',{shop_id:shopId,kind:'sku',observation_id:row.observation_id});
 return <div className="pdd-sku-panel" data-reviewed-rows="true">
  <div className="sku-heading"><div><h3 title={row.title}>{productDisplayName(row.title)}</h3><small>原卡 #{row.observation_id} · SKU 价格以采集时页面展示为准</small></div>{view.eligible&&<button type="button" className="sku-refresh" disabled={!data||busy||!!view.active||!!view.pipelineActive||data.browser_mode!=='local_chrome'} onClick={start}>{busy?'正在提交…':view.validating||activeProgress?.skuValidating?'SKU 校验中…':view.active?'SKU 采集中…':view.pipelineActive?'全店任务进行中…':capture?'重新采集 SKU':'采集 SKU'}</button>}</div>
  {error&&<p className="sku-alert" role="alert">{error}</p>}
  {!data&&!error&&<p className="sku-note" role="status">正在读取 SKU 数据…</p>}
  {activeProgress&&<CollectionProgress view={activeProgress}/>}
  {!view.active&&view.pipelineActive&&<p className="sku-alert" role="status">{view.validating?'SKU 已读取，正在备份并校验保存。':view.pipelineActive.kind==='sku_batch'||Number.isSafeInteger(view.pipelineActive.progress?.sku_total)?'已有 SKU 任务正在运行，请等待结束。':'全店商品和主图正在采集；结束后可点击“采集 SKU”读取本商品。'}</p>}
  {view.failure&&(view.failureIsHistorical?<details className="sku-source"><summary>上次 SKU 采集结果</summary><p>{view.failure}{capture?' 下方保留上次已保存结果。':''}</p></details>:<p className="sku-alert" role="alert">{view.failure}{capture?' 下方保留上次已保存结果。':''}</p>)}
  {capture?<>
   <div className="sku-status"><strong>{variants.length} 个规格 · {view.coverage.complete?'完整采集':'部分采集'}</strong><span>更新于 <time dateTime={capture.observed_at}>{dataUpdateTime(capture.observed_at)}</time>（北京时间）</span></div>
   {view.stale&&<p className="sku-alert">这是上次保存的 SKU，早于当前商品清单；可重新采集更新。</p>}
   <p className="sku-note">点击“重新采集 SKU”才会重新读取本商品；采集期间保留已保存结果。</p>
   {!view.coverage.complete&&<p className="sku-note">{!view.coverage.catalogComplete?'部分规格尚未读取或目录完整性待核验。':''}待补图片 {view.coverage.missingImages}／当前价未显示 {view.coverage.missingCurrentPrices}。下表仅展示已保存结果。</p>}
   <div className="sku-table-wrap"><table className="sku-table" aria-label="商品 SKU 图片与价格" data-reviewed-rows="true"><thead><tr><th>图片</th><th>规格</th><th className="sku-price">原始价</th><th className="sku-price">当前价格</th><th>状态</th></tr></thead><tbody>{variants.map((variant,index)=><tr key={variant.sku_id||`${skuSpecs(variant)}:${index}`}>
    <td>{variant.image_data_url?<ZoomableImage className="sku-image" src={variant.image_data_url} alt={skuSpecs(variant)}/>:<span className="sku-image-missing">暂无图片</span>}</td>
    <td className="sku-spec">{(variant.specs||[]).map((spec,i)=><div key={`${spec.name}:${i}`}>{spec.name}：{spec.value}</div>)}{variant.sku_id&&<small>SKU {variant.sku_id}</small>}</td>
    <td className="sku-price" title={variant.original_price_raw||'页面未显示该规格原始价'}>{skuPrice(variant,'original')}{variant.original_price_label&&<small>{variant.original_price_label}</small>}</td>
    <td className="sku-price" title={variant.current_price_raw||variant.price_raw||'页面未显示该规格当前价'}><strong>{skuPrice(variant)}</strong>{variant.current_price_label&&<small>{variant.current_price_label}</small>}</td>
    <td>{variant.available===false?'不可购买':variant.available===true?'可购买':'未确认'}</td>
   </tr>)}</tbody></table></div>
   <p className="sku-note">原始价按页面标注区分券前价、原价或划线价；未显示时标为“未显示”。</p>
   <details className="sku-source"><summary>采集来源</summary><p>{capture.row_scope_note||(capture.identity_basis==='current_unique_card_candidate'?'按当前同标题同图候选读取，商品对应关系待确认。':'当前商品规格页面')}</p><p>商品 ID：{capture.goods_id||'未记录'} · 记录：{capture.capture_id}</p><p>{capture.goods_url}</p></details>
  </>:data&&!view.active&&!view.pipelineActive&&(!view.failure||view.failureIsHistorical)&&!error&&<p className="sku-note">{view.eligible?'尚未采集此商品 SKU。点击“采集 SKU”后才会读取本商品规格图片和价格。':'仅采集销量 1–10 和超过 10 件的商品 SKU。'}</p>}
 </div>;
}
