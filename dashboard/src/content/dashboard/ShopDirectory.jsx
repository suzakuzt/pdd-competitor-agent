import {useEffect,useRef,useState} from 'react';
import {readIntakeJson} from './shop-intake-model.js';

export function validateShopDirectory(data){
 if(data?.status!=='ok'||!Array.isArray(data.targets)||!Array.isArray(data.onboardings))throw new Error('店铺列表暂时无法更新，已保留当前列表。');
 const seen=new Set();
 for(const row of data.targets){
  if(!/^(?:target|observed_shop)_[0-9a-f]{24}$/.test(row?.target_id||'')||seen.has(row.target_id)||row.shop_id!=null&&!/^shop_[0-9a-f]{24}$/.test(row.shop_id))throw new Error('店铺列表身份不完整，已保留当前列表。');
  seen.add(row.target_id);
  if(row.onboarding){const own=row.onboarding.target_id===row.target_id,resolved=row.onboarding.resolved_target_id===row.target_id&&Boolean(row.shop_id)&&row.onboarding.shop_id===row.shop_id;if((!own&&!resolved)||row.shop_id&&row.onboarding.shop_id&&row.onboarding.shop_id!==row.shop_id)throw new Error('店铺进度与目标不一致，已保留当前列表。');}
  if(row.latest_collection&&row.latest_collection.shop_id!==row.shop_id)throw new Error('采集结果与目标不一致，已保留当前列表。');
 }
 for(const job of data.onboardings)if(!/^onboard_[0-9a-f]{32}$/.test(job?.id||'')||!seen.has(job.target_id))throw new Error('接入进度不属于当前店铺列表，已保留当前列表。');
 return data;
}
export function useShopDirectory(){
 const [directory,setDirectory]=useState(null),[error,setError]=useState(''),[version,setVersion]=useState(0),revision=useRef(0);
 useEffect(()=>{
  const current=++revision.current,controller=new AbortController();let timer=null,timeout=null,alive=true;
  const poll=async()=>{
   timeout=setTimeout(()=>controller.abort(),15000);
   try{const value=validateShopDirectory((await readIntakeJson(fetch,'/__pdd_shop_directory',{signal:controller.signal})).data);if(alive&&revision.current===current){setDirectory(value);setError('');}}
   catch(e){if(alive&&revision.current===current)setError(e.name==='AbortError'?'店铺列表读取超时，当前列表已保留。':e.message);}
   finally{clearTimeout(timeout);if(alive&&!controller.signal.aborted)timer=setTimeout(poll,5000);}
  };
  poll();return()=>{alive=false;controller.abort();clearTimeout(timer);clearTimeout(timeout);};
 },[version]);
 return {directory,error,refresh:()=>setVersion(value=>value+1)};
}
