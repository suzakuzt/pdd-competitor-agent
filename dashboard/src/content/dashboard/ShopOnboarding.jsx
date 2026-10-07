import React,{useEffect,useRef,useState} from 'react';
import {Dialog} from '../../data-app-public.jsx';
import {readIntakeJson} from './shop-intake-model.js';
import './shop-intake.css';

const active=job=>['queued','running'].includes(job?.status);
export function validateOnboarding(data,scope={}){
 if(!data||typeof data!=='object'||typeof data.status!=='string')throw new Error('未读取到有效的采集进度。');
 if(data.status==='idle'){
  if(!data.id&&!scope.id&&scope.targetId&&data.target_id===scope.targetId)return data;
  throw new Error('采集任务与指定店铺不一致，已停止读取。');
 }
 if(!/^onboard_[0-9a-f]{32}$/.test(data.id||'')||!/^target_[0-9a-f]{24}$/.test(data.target_id||'')||scope.id&&data.id!==scope.id||scope.targetId&&data.target_id!==scope.targetId)throw new Error('采集任务与指定店铺不一致，已停止读取。');
 return data;
}
export function onboardingDataHref(job,currentHref){
 if(!['complete','partial'].includes(job?.status)||job.job?.dashboard_built!==true||job.job?.shop_id!==job.shop_id||!/^shop_[0-9a-f]{24}$/.test(job.shop_id||''))return null;
 const url=new URL(currentHref);url.searchParams.set('pdd_view','warehouse');url.searchParams.set('pdd_shop',job.shop_id);url.searchParams.delete('pdd_target');url.searchParams.delete('f.run_id');return url.pathname+url.search+url.hash;
}
function useOnboarding(targetId='',onChange){
 const [job,setJob]=useState(null),[error,setError]=useState(''),[submitting,setSubmitting]=useState(false);
 const changed=useRef(onChange);changed.current=onChange;
 const control=useRef({alive:true,busy:false,timer:null,controller:null,job:null,revision:0});
 useEffect(()=>{const c={alive:true,busy:false,timer:null,controller:new AbortController(),job:null,revision:0};control.current=c;setJob(null);setError('');setSubmitting(false);if(targetId)poll(c,{targetId});return()=>{c.alive=false;clearTimeout(c.timer);c.controller.abort();};},[targetId]);
 const request=async(c,path,payload)=>{const controller=c.controller,timer=setTimeout(()=>controller.abort(),20000);try{return (await readIntakeJson(fetch,path,{...(payload?{method:'POST',body:JSON.stringify(payload)}:{}),signal:controller.signal})).data;}finally{clearTimeout(timer);}};
 const accept=(c,value)=>{c.job=value;setJob(value);setError('');changed.current?.(value);};
 async function poll(c,scope){
  clearTimeout(c.timer);const revision=c.revision;
  try{const suffix=scope.id?`id=${encodeURIComponent(scope.id)}`:`target_id=${encodeURIComponent(scope.targetId)}`;const value=validateOnboarding(await request(c,`/__pdd_collection_onboard?${suffix}`),scope);if(!c.alive||revision!==c.revision)return;accept(c,value);if(active(value))c.timer=setTimeout(()=>poll(c,{id:value.id,targetId:value.target_id}),2500);}
  catch(e){if(c.alive&&revision===c.revision)setError(e.name==='AbortError'?'读取进度超时，任务仍会在后台继续。请点击刷新进度。':e.message);}
 }
 const start=async(payload)=>{
  const c=control.current;if(!c.alive||c.busy||active(c.job))return;c.busy=true;c.revision++;clearTimeout(c.timer);c.controller.abort();c.controller=new AbortController();setSubmitting(true);setError('');
  try{const value=validateOnboarding(await request(c,'/__pdd_collection_onboard',payload),{targetId:payload.target_id});if(!c.alive)return;accept(c,value);if(active(value))c.timer=setTimeout(()=>poll(c,{id:value.id,targetId:value.target_id}),800);}
  catch(e){if(c.alive)setError(e.name==='AbortError'?'请求等待超时，请刷新进度确认，不要重复提交。':e.message);}
  finally{c.busy=false;if(c.alive)setSubmitting(false);}
 };
 const refresh=()=>{const c=control.current;if(c.busy)return;c.revision++;c.controller.abort();c.controller=new AbortController();const scope=c.job?.id?{id:c.job.id,targetId:c.job.target_id}:targetId?{targetId}:null;if(scope)poll(c,scope);};
 const cancel=async()=>{const c=control.current;if(!c.job?.id||c.busy)return;c.busy=true;c.revision++;clearTimeout(c.timer);c.controller.abort();c.controller=new AbortController();try{const value=validateOnboarding(await request(c,'/__pdd_collection_onboard_cancel',{id:c.job.id}),{id:c.job.id,targetId:c.job.target_id});if(c.alive){accept(c,value);if(active(value))c.timer=setTimeout(()=>poll(c,{id:value.id,targetId:value.target_id}),800);}}catch(e){if(c.alive)setError(e.message);}finally{c.busy=false;}};
 const collect=async()=>{const c=control.current;if(c.busy||c.job?.status!=='ready')return;c.busy=true;c.revision++;clearTimeout(c.timer);c.controller.abort();c.controller=new AbortController();setSubmitting(true);setError('');try{const value=validateOnboarding(await request(c,'/__pdd_collection_onboard_collect',{id:c.job.id}),{id:c.job.id,targetId:c.job.target_id});if(c.alive){accept(c,value);if(active(value))c.timer=setTimeout(()=>poll(c,{id:value.id,targetId:value.target_id}),800);}}catch(e){if(c.alive)setError(e.name==='AbortError'?'采集请求等待超时，请刷新进度确认，不要重复提交。':e.message);}finally{c.busy=false;if(c.alive)setSubmitting(false);}};
 return {job,error,submitting,start,refresh,cancel,collect};
}
function OnboardingProgress({flow,onSelectTarget,identificationOnly=false}){
 const {job,error,submitting,refresh,cancel}=flow,progress=job?.job?.progress;
 const href=typeof window!=='undefined'?onboardingDataHref(job,window.location.href):null;
 return <div className="pdd-onboarding-progress" role="status">
  {submitting&&<p>{job?.status==='ready'?'正在提交本店采集…':'正在保存链接并识别店铺…'}</p>}
  {job&&job.status!=='idle'&&<><strong>{job.shop_name||'新店铺'} · {job.status==='ready'?'已识别 · 待采集':identificationOnly&&['complete','partial'].includes(job.status)?'已识别 · 已有数据':job.status==='complete'?'采集已完成':job.status==='partial'?'部分完成':active(job)?job.collection_job_id?'采集中':'识别中':'需要处理'}</strong><p>{identificationOnly&&['complete','partial'].includes(job.status)?'这家店已在列表中，下面是上次保存的结果；本次添加没有重新采集。':active(job)&&progress?.phase||job.message||'正在连接 Chrome 并核验店铺…'}</p>
   {job.collection_job_id&&job.job?.kind==='shop'&&Number.isSafeInteger(progress?.cards)&&<p>已读取 {progress.cards} 件商品{Number.isSafeInteger(progress.image_saved)?` · 图片 ${progress.image_saved} / ${progress.image_total}`:''}</p>}
   {href&&<a className="pdd-intake-result" href={href}>查看本店数据</a>}
   {job.status==='ready'&&<><p>已加入“当前分析店铺”。选择本店并点击“开始采集”后，才会读取商品和图片。</p>{onSelectTarget&&<button type="button" onClick={()=>onSelectTarget(job.target_id)}>查看店铺</button>}{!identificationOnly&&<button type="button" disabled={submitting} onClick={flow.collect}>开始采集</button>}</>}
   {active(job)&&<button type="button" onClick={cancel}>{job.collection_job_id?'停止采集':'停止识别'}</button>}
  </>}
  {error&&<p className="pdd-intake-message is-error">{error}</p>}
  {(error||job&&job.status!=='idle'&&job.status!=='ready'&&!active(job)&&!href)&&<button type="button" onClick={refresh}>刷新进度</button>}
  {job?.id&&!active(job)&&!['complete','ready'].includes(job.status)&&<button type="button" disabled={submitting} onClick={()=>flow.start({target_id:job.target_id,retry:true})}>重新识别</button>}
 </div>;
}
export function ShopLinkIntake({open,onClose,onChange,onSelectTarget}){
 const [name,setName]=useState(''),[url,setUrl]=useState('');const flow=useOnboarding('',onChange);
 const blocked=flow.submitting||active(flow.job),hasJob=!!flow.job?.id;
 return <Dialog open={open} onClose={onClose} title="添加店铺"><form className="pdd-intake-form" onSubmit={event=>{event.preventDefault();if(url.trim())flow.start({name:name.trim()||null,url:url.trim()});}}>
  <p>先在已登录的 Chrome 中识别店铺，加入“当前分析店铺”。添加完成后，由你点击“开始采集”读取商品和图片。</p>
  <label>店铺链接<textarea value={url} onChange={e=>setUrl(e.target.value)} rows={3} maxLength={4096} placeholder="粘贴拼多多店铺网址或分享链接" disabled={blocked}/></label>
  <label>店铺名称（可选）<input value={name} onChange={e=>setName(e.target.value)} maxLength={200} placeholder="填写完整店铺名称，便于识别" disabled={blocked}/></label>
  <div className="pdd-intake-actions"><button type="submit" disabled={blocked||!url.trim()}>{blocked?'正在识别…':'识别并添加'}</button></div>
  <OnboardingProgress flow={flow} identificationOnly onSelectTarget={onSelectTarget}/>
  {hasJob&&<small>可以关闭窗口；在“当前分析店铺”列表中可继续查看本店状态。</small>}
 </form></Dialog>;
}
export function ShopOnboardingControl({target,onChange}){
 const scopedTarget=target.onboarding?.resolved_target_id===target.target_id&&target.shop_id&&target.onboarding.shop_id===target.shop_id?target.onboarding.target_id:target.target_id;
 const flow=useOnboarding(scopedTarget,onChange),blocked=flow.submitting||active(flow.job);
 return <div className="pdd-intake-form">
  {!flow.job?.id&&<div className="pdd-intake-actions"><button type="button" disabled={blocked||!target.source_url} onClick={()=>flow.start({target_id:scopedTarget})}>{blocked?'正在识别…':'识别店铺'}</button></div>}
  <OnboardingProgress flow={flow}/>
 </div>;
}
