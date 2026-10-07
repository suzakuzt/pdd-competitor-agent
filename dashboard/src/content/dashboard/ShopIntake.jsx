import React,{useEffect,useRef,useState} from 'react';
import {DataComponent,Dialog,SourceSidebar,useDataApp} from '../../data-app-public.jsx';
import {beijing} from './pdd-model.js';
import {historyReviewedSource} from './history-source-model.js';
import {targetCoverage,safeIntakeLink,intakePayload,intakeCompletion,intakeReloadHref,intakeCollectionRequest,readIntakeJson,targetTracking,trackingPayload,trackingCompletion,trackingReloadHref,targetDataHref} from './shop-intake-model.js';
import './shop-intake.css';
import {ShopOnboardingControl} from './ShopOnboarding.jsx';

export function ShopIntake({open,onClose}){
 const [name,setName]=useState(''),[url,setUrl]=useState(''),[state,setState]=useState({status:'idle',message:''}),[elapsed,setElapsed]=useState(0);
 const busy=useRef(false),controller=useRef(null),timer=useRef(null),alive=useRef(true),sequence=useRef(0);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;sequence.current+=1;controller.current?.abort();clearTimeout(timer.current);};},[]);
 useEffect(()=>{if(state.status!=='running')return;const tick=()=>setElapsed(Math.max(0,Math.floor((Date.now()-state.startedAt)/1000)));tick();const clock=setInterval(tick,1000);return()=>clearInterval(clock);},[state.status,state.startedAt]);
 const run=async(existingJob=null)=>{
  if(busy.current)return;let payload;try{if(!existingJob)payload=intakePayload(name,url);}catch(error){setState({status:'failed',message:error.message});return;}
  busy.current=true;const current=++sequence.current;controller.current?.abort();const request=new AbortController();controller.current=request;
  const valid=()=>alive.current&&current===sequence.current,started=Date.now();let jobId=existingJob,lastReceipt=existingJob?state.receipt:null,startedAt=existingJob&&Number.isFinite(state.startedAt)?state.startedAt:started;
  const failed=(error,receipt)=>{if(!valid())return;busy.current=false;setState({status:'failed',jobId,startedAt,message:error.message||'登记未完成，请核对后再试。',receipt:receipt||error.receipt||lastReceipt,errorCode:error.errorCode});};
  const read=async(endpoint,options={})=>{const timeout=setTimeout(()=>request.abort(),20000);try{return await readIntakeJson(fetch,endpoint,{...options,signal:request.signal});}finally{clearTimeout(timeout);}};
  setState({status:'running',jobId,startedAt,receipt:lastReceipt,message:existingJob?'正在核对上次添加结果…':'正在保存店铺链接…'});
  const poll=async()=>{
   if(!valid())return;
   if(Date.now()-started>180000){failed(new Error('等待登记结果超时；尚未确认完成，可继续检查这次任务，勿重复登记。'));return;}
   try{const {data}=await read(`/__pdd_competitor_status?job_id=${encodeURIComponent(jobId)}`);if(!valid())return;
    if(data.status==='running'){if(data.receipt)lastReceipt=data.receipt;timer.current=setTimeout(poll,1200);return;}
    if(data.status==='failed'){const error=new Error(data.message||data.error||'本次登记或页面更新失败。');error.errorCode=data.error_code;failed(error,data.receipt);return;}
    const receipt=intakeCompletion(data);busy.current=false;setState({status:'succeeded',jobId,receipt,message:receipt.registration_status==='unchanged'?'已找到同一登记目标，正在刷新列表；本次没有采集。':'目标已登记，正在刷新列表；本次没有采集。'});
    window.location.assign(intakeReloadHref(window.location.href,receipt.target_id));
   }catch(error){failed(error.name==='AbortError'?new Error('读取登记结果超时，可检查这次任务；没有自动重复提交。'):error);}
  };
  try{
   if(!jobId){const {data,httpStatus}=await read('/__pdd_competitor_add',{method:'POST',body:JSON.stringify(payload)});if(!valid())return;if(httpStatus!==202||data.status!=='running'||typeof data.job_id!=='string'||!data.job_id||data.job_id.length>200)throw new Error('服务没有确认接收登记；请核对目标列表，输入已保留。');jobId=data.job_id;lastReceipt=data.receipt;setState({status:'running',jobId,startedAt:started,receipt:lastReceipt,message:'店铺链接已保存，正在更新店铺列表…'});}
   await poll();
  }catch(error){failed(error.name==='AbortError'?new Error('提交等待超时，尚未确认接收；输入已保留，请先核对目标列表。'):error);}
 };
 const registered=state.receipt&&['registered','unchanged'].includes(state.receipt.registration_status)&&state.receipt.website_collection_performed===false&&typeof state.receipt.target_id==='string'&&state.receipt.target_id.length>0;
 return <Dialog open={open} onClose={onClose} title="添加拼多多店铺"><form className="pdd-intake-form" onSubmit={event=>{event.preventDefault();run();}}>
  <p>保存目标后再核实与采集。登记不会打开店铺或抓取商品；新店没有真实数据前不显示其他店的分析。</p>
  <label>店铺链接<textarea value={url} maxLength={4096} onChange={event=>setUrl(event.target.value)} placeholder="粘贴拼多多店铺网址或分享链接" rows={3} disabled={state.status==='running'}/></label>
  <label>店铺名称 / 备注（可选）<input value={name} maxLength={200} onChange={event=>setName(event.target.value)} placeholder="填写完整店铺名称，方便后续查找" disabled={state.status==='running'}/></label>
  <small>相同名字不自动合并；分享链接及网址身份线索仍需核实。</small>
  <div className="pdd-intake-actions"><button type="submit" disabled={state.status==='running'||(!name.trim()&&!url.trim())}>{state.status==='running'?'正在处理…':'加入待分析列表'}</button>{state.status==='failed'&&state.jobId&&<button type="button" onClick={()=>run(state.jobId)}>检查这次任务</button>}</div>
  <p className={`pdd-intake-message ${state.status==='failed'?'is-error':''}`} role="status">{state.message}{state.status==='running'&&` 已用 ${elapsed} 秒`}</p>
  {registered&&state.status==='running'&&<div className="pdd-intake-saved"><strong>✓ {state.receipt.registration_status==='unchanged'?'已找到这家店铺，无需重复添加':'链接已添加成功'}</strong><p>{name.trim()||'待核实店名'} · {state.receipt.target_status==='needs_identity'?'待核验':state.receipt.target_status==='observed'?'已有商品数据':'待采集'}</p><small>列表更新完成后自动进入这家店铺。商品将在核验、采集后显示。</small></div>}
  {registered&&state.status==='failed'&&state.receipt.dashboard_built!==true&&<p className="pdd-intake-warning">链接已保存，但店铺列表更新失败。请点击“检查这次任务”，无需重新添加。</p>}
  {state.status==='running'&&<small>可以关闭窗口；后台会继续处理，完成后自动打开这家店铺。</small>}
 </form></Dialog>;
}

export function ShopTrackingControl({target}){
 const setting=targetTracking(target),[state,setState]=useState({status:'idle',message:''});
 const busy=useRef(false),controller=useRef(null),timer=useRef(null),alive=useRef(true),sequence=useRef(0);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;sequence.current+=1;controller.current?.abort();clearTimeout(timer.current);};},[target.target_id]);
 const run=async(status,existingJob=null)=>{
  if(busy.current)return;let payload;try{payload=trackingPayload(target,status);}catch(error){setState({status:'failed',message:error.message});return;}
  busy.current=true;const current=++sequence.current;controller.current?.abort();const request=new AbortController();controller.current=request;
  const valid=()=>alive.current&&current===sequence.current,started=Date.now();let jobId=existingJob,lastReceipt=existingJob?state.receipt:null;
  const failed=error=>{if(!valid())return;busy.current=false;setState({status:'failed',jobId,requestedStatus:status,receipt:error.receipt||lastReceipt,errorCode:error.errorCode,message:error.errorCode==='revision_conflict'?'另一处已更新此店设置。请刷新读取最新版本后再选择；本次没有覆盖。':error.message||'设置未确认保存，请核对后再试。'});};
  const read=async(endpoint,options={})=>{const timeout=setTimeout(()=>request.abort(),20000);try{return await readIntakeJson(fetch,endpoint,{...options,signal:request.signal});}finally{clearTimeout(timeout);}};
  setState({status:'running',requestedStatus:status,jobId,message:existingJob?'正在核对本次设置结果…':'正在保存跟踪设置并更新面板；没有启动采集…'});
  const poll=async()=>{
   if(!valid())return;
   if(Date.now()-started>180000){failed(new Error('等待设置结果超时。请检查本次任务，不要重复提交。'));return;}
   try{const {data}=await read(`/__pdd_competitor_status?job_id=${encodeURIComponent(jobId)}`);if(!valid())return;
    if(data.receipt)lastReceipt=data.receipt;
    if(data.status==='running'){timer.current=setTimeout(poll,1200);return;}
    if(data.status==='failed'){const error=new Error(data.message||'设置或页面更新失败。');error.receipt=data.receipt;error.errorCode=data.error_code;failed(error);return;}
    const receipt=trackingCompletion(data,target,status);busy.current=false;setState({status:'succeeded',receipt,message:'设置已保存，正在载入更新后的面板；没有启动采集。'});
    window.location.assign(trackingReloadHref(window.location.href,target.target_id));
   }catch(error){failed(error.name==='AbortError'?new Error('读取设置结果超时，可继续检查本次任务。'):error);}
  };
  try{if(!jobId){const {data,httpStatus}=await read('/__pdd_competitor_tracking',{method:'POST',body:JSON.stringify(payload)});if(!valid())return;if(httpStatus!==202||data.status!=='running'||typeof data.job_id!=='string'||!data.job_id||data.job_id.length>200)throw new Error('服务没有确认接收设置。请先核对状态，不要重复提交。');jobId=data.job_id;lastReceipt=data.receipt;setState({status:'running',requestedStatus:status,jobId,receipt:lastReceipt,message:'设置任务处理中，等待面板更新完成…'});}await poll();}
  catch(error){failed(error.name==='AbortError'?new Error('提交超时，是否保存尚未确认；请刷新核对后再操作。'):error);}
 };
 const saved=state.receipt?.operation==='tracking'&&['saved','unchanged'].includes(state.receipt.setting_status);
 return <div className="pdd-tracking-control"><div className="pdd-tracking-heading"><span className={`pdd-tracking-status is-${setting.status}`}>{setting.label}</span><button type="button" disabled={!setting.canConfigure||state.status==='running'||state.status==='succeeded'} onClick={()=>run(setting.status==='enabled'?'paused':'enabled')}>{setting.status==='enabled'?'暂停跟踪':'开启跟踪'}</button></div>
  <small>{setting.note}</small>{setting.status==='unconfigured'&&setting.canConfigure&&<button type="button" className="pdd-tracking-defer" disabled={state.status==='running'} onClick={()=>run('paused')}>暂停跟踪，保留历史</button>}
  {state.message&&<p className={`pdd-intake-message ${state.status==='failed'?'is-error':''}`} role="status">{state.message}</p>}
  {saved&&state.status==='failed'&&state.receipt.dashboard_built!==true&&<p className="pdd-intake-warning">设置已经保存，但面板更新尚未成功。当前开关仍显示旧快照；历史与固定起点未删除，请先处理页面更新。</p>}
  {state.status==='failed'&&state.jobId&&state.errorCode!=='revision_conflict'&&<button type="button" onClick={()=>run(state.requestedStatus,state.jobId)}>检查本次设置</button>}
  {state.status==='failed'&&<button type="button" onClick={()=>window.location.assign(trackingReloadHref(window.location.href,target.target_id))}>刷新核对状态</button>}
 </div>;
}

export function ShopTargetList({selectedTargetId,onSelectTarget,onOpenHistory}){
 const {queries}=useDataApp(),targets=queries.competitor_targets?.rows||[];
 const [sourceRequest,setSourceRequest]=useState(null),source=sourceRequest?.selectedTargetId===selectedTargetId?sourceRequest.component:null;
 useEffect(()=>setSourceRequest(null),[selectedTargetId]);
 const runIds=new Set(targets.filter(row=>row.shop_id&&!row.identity_conflict).map(row=>row.shop_id)),targetRuns=(queries.runs?.rows||[]).filter(row=>runIds.has(row.shop_id));
 return <section className="pdd-intake-targets"><details open={Boolean(selectedTargetId)}><summary>店铺与跟踪 <span>{targets.length} 个目标 · 每店独立保存，暂停保留历史</span></summary>
  <p className="pdd-tracking-boundary">跟踪开关管理本店新品起点与分析设置。选择店铺后，可在下方“更新本店数据”手动重采或设置本机采集时间；采集时间独立保存。</p>
  {queries.competitor_targets?<DataComponent id="pdd-intake-target-list" queryId="competitor_targets" queryIds={['competitor_targets','runs']} kind="table" title="店铺跟踪设置与实际覆盖" variant="plain" sourceRows={targets} displayRows={targets} sourceRowsByQuery={{competitor_targets:targets,runs:targetRuns}} onOpen={(type,component)=>{if(type==='source')setSourceRequest({selectedTargetId,component});}} description="全部目标分别展示真实设置与同店历史；启用只保存跟踪意向，暂停不删除历史或重置固定起点。完整/部分覆盖独立于跟踪状态，不继承商品池单轮范围。">
   <div className="pdd-intake-target-grid" data-reviewed-rows="true">{targets.map(target=>{const coverage=targetCoverage(target,queries),link=safeIntakeLink(target.source_url);return <article key={target.target_id} className={`pdd-intake-target${selectedTargetId===target.target_id?' is-selected':''}`}>
    <div><strong>{target.display_name||target.observed_shop_name||'待核验店铺'}</strong><span className={`pdd-intake-badge is-${coverage.state}`}>{coverage.label}</span></div>
    <ShopTrackingControl key={target.target_id} target={target}/>
    {coverage.hasData?<p className="pdd-tracking-coverage"><strong>{coverage.coverageLabel}</strong><span>最新{coverage.latestComplete?'完整':'部分'} {coverage.latest.card_count} 张 · {beijing(coverage.latest.observed_to)}</span><span>{coverage.reference?`最近完整：${coverage.reference.card_count} 张 · ${beijing(coverage.reference.observed_to)}`:'最近完整：尚无，不能认定全店商品已覆盖'}</span><span>{coverage.historyCount} 轮历史分别保留，不拼接销量</span></p>:<p>{coverage.state==='needs_identity'?'还需核实店铺身份':'已登记，尚未取得商品数据'}</p>}
    {target.next_action&&<p className="pdd-tracking-next"><strong>{target.sop_label||'下一步'}</strong>：{target.next_action}</p>}
    <div className="pdd-intake-actions"><button type="button" onClick={()=>onSelectTarget(target.target_id)}>{coverage.canAnalyze?'查看分析':'查看接入状态'}</button>{coverage.canAnalyze&&<button type="button" onClick={()=>onOpenHistory?.(target.target_id)}>本店历史 ↗</button>}{link&&<a href={link} target="_blank" rel="noopener noreferrer">原始链接 ↗</a>}</div>
    <details className="pdd-tracking-evidence"><summary>手动采集 / 补扫请求与设置来源</summary><textarea readOnly rows={5} value={intakeCollectionRequest(target)} onFocus={event=>event.currentTarget.select()} aria-label={`${target.display_name||'本店'}的采集请求`}/><small>复制到当前对话后继续处理；这里不会启动采集。</small><p>设置更新时间：{target.tracking_updated_at?beijing(target.tracking_updated_at):'未记录'} · 版本 {Number.isSafeInteger(target.tracking_revision)?target.tracking_revision:'未载入'}</p>{coverage.latest?.collection_stop_reason&&<p>最近停止原因原文：{coverage.latest.collection_stop_reason}</p>}</details>
   </article>;})}</div>{targets.length===0&&<p>还没有已登记目标，请从“添加店铺”开始。</p>}
  </DataComponent>:<p>目标列表尚未载入，请先核对数据连接；这里不代表没有店铺。</p>}
  <details className="pdd-tracking-sop"><summary>每家新店都按这 4 步处理</summary><ol><li><strong>登记并核实身份</strong>：店名和稳定链接对应本店；同名不合并。</li><li><strong>首次采集与补扫</strong>：逐卡记录原文、图片和时间；部分轮标明缺口，补扫另存一轮。</li><li><strong>保留起点与历史</strong>：固定起点不随刷新移动，暂停后仍可查全部记录。</li><li><strong>逐店比较与核验</strong>：先看本店完整/部分窗口；身份或口径不足时变化为未知。</li></ol></details>
 </details>{source&&<SourceSidebar component={source} queries={queries} getSource={queryId=>historyReviewedSource(queries,source,queryId)} onClose={()=>setSourceRequest(null)}/>}</section>;
}

export function ShopTargetEmpty({selection,onAdd,onChange}){
 const {queries}=useDataApp(),target=selection.target;
 const [copyMessage,setCopyMessage]=useState(''),[source,setSource]=useState(null);
 const request=target?intakeCollectionRequest(target):'',link=safeIntakeLink(target?.source_url);
 const dataHref=typeof window!=='undefined'?targetDataHref(target,window.location.href):null;
 const copy=async()=>{try{if(!navigator.clipboard?.writeText)throw new Error();await navigator.clipboard.writeText(request);setCopyMessage('采集请求已复制；请发到当前对话继续处理。尚未启动采集。');}catch{setCopyMessage('浏览器未允许复制，请从下方文本区手动复制。尚未启动采集。');}};
 const content=<div className="pdd-intake-empty" {...(target?{'data-reviewed-rows':true}:{})}><h2>{target?.display_name||target?.observed_shop_name||'尚未选择可分析店铺'} · {selection.label||'目标未载入'}</h2>
  <p>{selection.state==='needs_identity'?'店铺链接已保存，先识别店铺身份；完成后由你点击“开始采集”。':selection.state==='pending_capture'?'店铺已识别，尚未采集商品。点击“开始采集”读取本店商品和图片。':selection.state==='awaiting_snapshot'?'本店数据已经保存，当前页面仍是之前载入的版本。点击下方按钮载入本店最新数据。':selection.state==='saved_unpublished'?'本店已有保存记录，但当前页面尚未载入，页面发布还未确认完成。请先查看处理进度，无需重新采集。':'指定目标尚未出现在当前快照，请选择列表中的店铺或核对登记结果。'}</p>
  {dataHref?<div className="pdd-onboarding-progress"><a className="pdd-intake-result" href={dataHref}>查看本店数据</a></div>:target?.source_url&&!target.identity_conflict&&(selection.state!=='saved_unpublished'||target.onboarding)?<ShopOnboardingControl key={target.target_id} target={target} onChange={onChange}/>:selection.state==='saved_unpublished'&&onChange&&<button type="button" onClick={onChange}>刷新本店状态</button>}
  {target&&<><dl><dt>目标 ID</dt><dd>{target.target_id}</dd><dt>身份线索</dt><dd>{target.identity_conflict?'存在冲突，待核验':target.shop_id||'尚未取得稳定店铺身份'}</dd></dl>{link?<a href={link} target="_blank" rel="noopener noreferrer">打开原始店铺链接 ↗</a>:target.source_url?<p>原始输入：{target.source_url}</p>:<p>原始店铺链接待补。</p>}<details><summary>手动处理请求</summary><label>需要协助时，可复制以下请求<textarea readOnly value={request} rows={6} onFocus={event=>event.currentTarget.select()}/></label><button type="button" onClick={copy}>复制采集请求</button><p role="status">{copyMessage}</p></details></>}
  <button type="button" className="pdd-intake-secondary" onClick={onAdd}>添加另一个店铺</button>
 </div>;
 return <>{target&&!target.directory_live&&queries.competitor_targets?<DataComponent id="pdd-intake-selected-target" queryId="competitor_targets" kind="table" title="当前目标的接入状态" variant="plain" sourceRows={[target]} displayRows={[target]} scopeFilters={[{field:'target_id',label:'当前目标',value:target.target_id}]} onOpen={(type,component)=>{if(type==='source')setSource(component);}}>{content}</DataComponent>:content}{source&&<SourceSidebar component={source} queries={queries} getSource={queryId=>historyReviewedSource(queries,source,queryId)} onClose={()=>setSource(null)}/>}</>;
}
