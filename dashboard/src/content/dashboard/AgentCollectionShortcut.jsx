import React,{useEffect,useRef,useState} from 'react';
import {CollectionProgress,CollectionSkuHistory,collectionView,collectionCompletion,collectionCompletionTitle} from './CollectionControls.jsx';

export function agentCollectionShopReady(queries,shopId){
 if(typeof shopId!=='string'||!shopId||!(queries?.runs?.rows||[]).some(run=>run.shop_id===shopId&&typeof run.run_id==='string'&&run.run_id))return false;
 if(queries.competitor_shops&&!queries.competitor_shops.rows?.some(shop=>shop.shop_id===shopId))return false;
 return !(queries.competitor_targets?.rows||[]).some(target=>target.shop_id===shopId&&(target.identity_conflict===true||target.status==='needs_identity'));
}

export function agentCollectionStatus(data,shopId){
 const jobs=[...(Array.isArray(data?.jobs)?data.jobs:[]),data?.latest_job,data?.latest_sku_batch_job].filter(Boolean);
 if(!data||!Array.isArray(data.jobs)||data.shop_id&&data.shop_id!==shopId||jobs.some(job=>job.shop_id!==shopId))throw new Error('采集状态与当前店铺不符，请刷新后核对。');
 return data;
}

export function agentCollectionSkillPrompt(){
 return '使用 $pdd-collect 采集店铺：【填写店铺完整名称或编号】。\n请先核对我指定的采集目标，只将结果更新到该店铺，不默认使用当前分析店铺。\n已有目标店铺采集任务时只查看进度，不重复提交。';
}

async function collectionJson(url,options={}){
 const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...options,headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{})}});
 let data;try{data=await response.json();}catch{throw new Error('本机采集服务没有返回有效结果，请核对采集状态。');}
 if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('本机采集服务返回格式不完整，请核对采集状态。');
 if(!response.ok)throw new Error(typeof data.message==='string'?data.message:'本机采集服务暂不可用。');
 return {data,status:response.status};
}

export function AgentCollectionShortcut({shopId,shopName,queries,captureNotice=''}){
 const ready=agentCollectionShopReady(queries,shopId),captureBlocked=Boolean(captureNotice);
 const [loaded,setLoaded]=useState(null),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[skillCopy,setSkillCopy]=useState(null);
 const sessionRef=useRef(null);
 if(!sessionRef.current||sessionRef.current.shopId!==shopId||sessionRef.current.ready!==ready||sessionRef.current.captureBlocked!==captureBlocked){
  if(sessionRef.current)sessionRef.current.active=false;
  sessionRef.current={shopId,ready,captureBlocked,active:true,busy:false,revision:0};
 }
 const session=sessionRef.current,isCurrent=()=>session.active&&sessionRef.current===session;
 const url=`/__pdd_collection_status?shop_id=${encodeURIComponent(shopId||'')}`;
 useEffect(()=>{
  session.active=true;session.noticeFromStart=false;setLoaded(null);setNotice('');setBusy(false);setSkillCopy(null);
  if(!ready||captureBlocked)return ()=>{session.active=false;};
  let loading=false;const controller=new AbortController();session.controller=controller;
  const load=async()=>{
   if(loading||session.busy)return;loading=true;const revision=session.revision;
   try{const {data}=await collectionJson(url,{signal:controller.signal});const value=agentCollectionStatus(data,shopId);if(isCurrent()&&revision===session.revision){setLoaded({session,shopId,value});if(!session.noticeFromStart)setNotice('');}}
   catch(error){if(isCurrent()&&revision===session.revision&&error.name!=='AbortError'){setLoaded(null);setNotice(error.message);}}
   finally{loading=false;}
  };
  load();const timer=setInterval(load,2500);
  return ()=>{session.active=false;controller.abort();session.startController?.abort();clearInterval(timer);};
 },[shopId,ready,captureBlocked]);
 const data=ready&&loaded?.session===session&&loaded.shopId===shopId?loaded.value:null,view=collectionView(data);
 const completion=view.complete?collectionCompletion(view.latest,shopId):null;
 const skillPrompt=agentCollectionSkillPrompt(),copyState=skillCopy?.session===session?skillCopy:null;
 const copySkill=async()=>{
  if(!skillPrompt||!isCurrent()||session.copyBusy)return;
  session.copyBusy=true;setSkillCopy({session,phase:'copying'});
  try{
   if(!navigator.clipboard?.writeText)throw new Error('clipboard unavailable');
   await navigator.clipboard.writeText(skillPrompt);
   if(isCurrent())setSkillCopy({session,phase:'copied'});
  }catch{if(isCurrent())setSkillCopy({session,phase:'manual'});}
  finally{session.copyBusy=false;}
 };
 const start=async()=>{
  if(captureBlocked||!ready||!data||session.busy||!isCurrent()||view.active)return;
  session.busy=true;session.revision++;session.noticeFromStart=false;setBusy(true);setNotice('');
  const controller=new AbortController();session.startController=controller;
  try{
   // Recheck after the click: the other capture control may have started a task.
   const status=await collectionJson(url,{signal:controller.signal});if(!isCurrent())return;
   const current=agentCollectionStatus(status.data,shopId),currentView=collectionView(current);setLoaded({session,shopId,value:current});
   if(currentView.active)return;
   if(current.browser_mode!=='local_chrome'||current.local_worker_available===false&&!currentView.connection){session.noticeFromStart=true;setNotice('采集器未连接，暂时无法启动。');return;}
   if(currentView.recoverable){session.noticeFromStart=true;setNotice('请在上方采集区域更新本店链接后再采集。');return;}
   const result=await collectionJson('/__pdd_collection_start',{method:'POST',body:JSON.stringify({shop_id:shopId,kind:'shop'}),signal:controller.signal});if(!isCurrent())return;
   const job=result.data.job;
   if(result.status!==202||typeof job?.id!=='string'||!job.id||job.id.length>80||job.shop_id!==shopId||!['shop','sku','sku_batch'].includes(job.kind)||!['queued','running','validating','host_claiming','awaiting_browser','manual_review'].includes(job.status))throw new Error('采集服务未确认本店任务，请核对上方采集状态后再操作。');
   setLoaded({session,shopId,value:{...current,...(job.kind==='shop'?{latest_job:job}:job.kind==='sku_batch'?{latest_sku_batch_job:job}:{}),jobs:[job,...current.jobs.filter(item=>item.id!==job.id)]}});
  }catch(error){if(isCurrent()&&error.name!=='AbortError'){session.noticeFromStart=true;setLoaded(null);setNotice(error.message);}}
  finally{session.busy=false;if(isCurrent())setBusy(false);}
 };
 return <div className="pdd-agent-capture" data-reviewed-rows="true">
  <div className="pdd-agent-capture-action"><button type="button" className="pdd-agent-capture-start" disabled={captureBlocked||!ready||!data||busy||Boolean(view.active)} onClick={start}>{busy?'正在提交…':'采集当前店铺'}</button><button type="button" className="pdd-agent-skill-copy" disabled={!skillPrompt||copyState?.phase==='copying'} onClick={copySkill}>{copyState?.phase==='copying'?'正在复制…':'复制 Skill 指令'}</button><small>当前店铺：{shopName||shopId||'尚未选择店铺'}</small></div>
  {captureNotice&&<p role="status">{captureNotice}</p>}
  <p className="pdd-agent-skill-hint">在 Codex 说：采集店铺“完整店铺名称”。采集目标由你指定，与当前分析店铺独立。</p>
  {copyState?.phase==='copied'&&<div className="pdd-agent-skill-manual"><p className="pdd-agent-skill-feedback" role="status">已复制，粘贴到 Codex 后填写采集店铺，再发送。</p><details className="pdd-agent-skill-hint"><summary>查看调用指令</summary><textarea aria-label="采集 Skill 指令" readOnly rows={3} value={skillPrompt} onFocus={event=>event.currentTarget.select()}/></details></div>}
  {copyState?.phase==='manual'&&<div className="pdd-agent-skill-manual"><p className="pdd-agent-skill-feedback" role="status">自动复制未成功，请复制下面的指令，填写采集店铺后发给 Codex。</p><textarea aria-label="采集 Skill 指令" readOnly rows={3} value={skillPrompt} onFocus={event=>event.currentTarget.select()}/></div>}
  {!ready?<p className="pdd-agent-warning" role="status">先选择已核实且已有采集数据的店铺。</p>:view.active?<CollectionProgress view={view}/>:notice?<p className="pdd-agent-warning" role="alert">{notice}</p>:!data?<p className="pdd-agent-notice" role="status">正在核对本店采集状态…</p>:view.failure?<p className="pdd-agent-warning" role="alert">{view.failure}</p>:view.complete&&!completion?<p className="pdd-agent-warning" role="status">完成结果待核验</p>:null}
  {view.failureDetails&&!view.active&&!notice&&<details className="pdd-agent-tools"><summary>查看采集原因与处理方式</summary><p>{view.failureDetails.cause}</p><p>{view.failureDetails.nextStep}</p></details>}
  {completion&&!view.active&&!notice&&<div className="pdd-agent-capture-result" role="status"><strong>{collectionCompletionTitle(completion)}</strong>{completion.imageSummary&&<small>{completion.imageSummary.text}</small>}{completion.skuSummary&&<small>{completion.skuSummary}</small>}<button type="button" onClick={()=>window.location.reload()}>查看最新数据</button></div>}
  {!notice&&<CollectionSkuHistory view={view} shopId={shopId}/>}
 </div>;
}
