import {verifiedCardImage} from './verified-image.js';
import React,{useEffect,useMemo,useRef,useState} from 'react';
import {DataComponent,SourceSidebar,useDataApp} from '../../data-app-public.jsx';
import {historyReviewedSource} from './history-source-model.js';
import {beijing,ID_LABELS,PRECISION_LABELS} from './pdd-model.js';
import './agent-search.css';
import {productDisplayName} from './product-display-name.js';
import {AgentCollectionShortcut} from './AgentCollectionShortcut.jsx';

const MAX_TURNS=12,MAX_HISTORY_TURNS=6,MAX_MESSAGE=2000,POLL_MS=1200,WAIT_MS=180000;
const SUGGESTIONS=[{label:'增长观察',query:'哪些款有趋势'},{label:'销量前5',query:'热销前5'},{label:'首次发现',query:'新增记录'},{label:'第2项商品来源',query:'第二个商品的参考来源'}];
const questionLabel=question=>SUGGESTIONS.find(item=>item.query===question)?.label||question;
const STATUS_LABELS={checking:'本店数据可查询',ready:'Agent 可查询',configuration_required:'Agent 待配置',login_required:'Agent 尚未登录',unavailable:'Agent 暂不可用'};
const text=value=>typeof value==='string'?value:'';
const validId=value=>Number.isSafeInteger(value)&&value>0;

export function safeAgentReference(value){
 if(typeof value!=='string'||!value.trim()||/[\u0000-\u0020\u007f]/u.test(value))return null;
 try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}
}

// Every displayed card is an unchanged reviewed row, scoped through its actual run.
export function agentSnapshotMatches(result,snapshot){
 return Boolean(result?.snapshot?.id&&result?.snapshot?.generated_at&&result.snapshot.id===snapshot?.id&&result.snapshot.generated_at===snapshot?.generatedAt);
}

export function agentEvidence(queries,shopId,result,snapshot){
 const requestedIds=Array.isArray(result?.observation_ids)?result.observation_ids:[];
 if(result?.scope?.shop_id!==shopId||!agentSnapshotMatches(result,snapshot))return {rows:[],runs:[],rejected_count:requestedIds.length};
 const scopeIds=Array.isArray(result?.scope?.run_ids)?result.scope.run_ids:[];
 const scoped=new Set(scopeIds.filter(value=>typeof value==='string'));
 const runs=(queries.runs?.rows||[]).filter(row=>row.shop_id===shopId&&scoped.has(row.run_id));
 const runIds=new Set(runs.map(row=>row.run_id));
 const byId=new Map((queries.observations?.rows||[]).filter(row=>runIds.has(row.run_id)).map(row=>[row.observation_id,row]));
 const selected=new Set(),rows=[];
 for(const id of requestedIds)if(validId(id)&&byId.has(id)&&!selected.has(id)){selected.add(id);rows.push(byId.get(id));}
 const actualRunIds=new Set(rows.map(row=>row.run_id));
 return {rows,runs:runs.filter(row=>actualRunIds.has(row.run_id)),rejected_count:requestedIds.filter(id=>!validId(id)||!byId.has(id)).length};
}

export function agentHistory(turns){
 const recent=turns.filter(turn=>turn.status==='completed'&&text(turn.question).trim()).slice(-MAX_HISTORY_TURNS);
 const history=[],encoder=new TextEncoder();
 for(const turn of recent.reverse()){
  const candidate=[{role:'user',text:turn.question},...history];
  // Keep whole recent questions in chronological order; reserve room for the new request.
  if(encoder.encode(JSON.stringify(candidate)).byteLength>20000)break;
  history.unshift(candidate[0]);
 }
 return history;
}

export function agentLocalImage(row,images){
 return verifiedCardImage(row,images);
}

export function agentQuestionPacket({message,shopId,shopName,turns,queries,snapshot}){
 const last=[...turns].reverse().find(turn=>turn.status==='completed');
 const evidence=agentEvidence(queries,shopId,last,snapshot);
 return ['请在当前对话中处理这条竞品查询；本页复制操作尚未调用 Agent。',
  `店铺：${shopName||shopId}（${shopId}）`,`问题：${message.trim()}`,
  `上一答复按顺序关联的原卡ID：${evidence.rows.map(row=>row.observation_id).join('、')||'无'}`,
  `对应轮次：${evidence.runs.map(run=>run.run_id).join('、')||'尚未限定，请区分完整参考与最新部分采集'}`,
  ...evidence.runs.map(run=>`轮次证据：${run.run_id}；${run.status}；${run.observed_from} 至 ${run.observed_to}；SHA ${run.snapshot_sha256}`),
  '项目：PDDCompetitorAgent。请从当前已审阅数据取原卡、已有图片与参考来源，不猜商品ID、增长、上架或利润；不同标签/单位分开，缺值保留未知。'].join('\n');
}

async function readAgentJson(url,options={}){
 const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...options,
  headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});
 let data;
 try{data=await response.json();}catch{throw new Error('本机 Agent 未返回有效 JSON；没有取得查询结果。');}
 if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('Agent 返回格式不完整；没有取得查询结果。');
 if(!response.ok){const error=new Error(text(data.message)||`查询未成功（HTTP ${response.status}）`);error.agentStatus=data.status;throw error;}
 return {data,httpStatus:response.status};
}

function AgentAnswer({turn,queries,snapshot,shopId,shopName,images,onDrilldown,onOpenSource}){
 const [showAll,setShowAll]=useState(false);
 const sameSnapshot=agentSnapshotMatches(turn,snapshot);
 const evidence=useMemo(()=>agentEvidence(queries,shopId,turn,snapshot),[queries,shopId,turn,snapshot]);
 const rows=evidence.rows,shown=showAll?rows:rows.slice(0,8),action=turn.tool_calls?.[0]?.tool;
 const compactAnswer=rows.length>0&&sameSnapshot&&['search','trends','new_arrivals'].includes(action);
 const trendRows=action==='trends'?(queries.trend_signals?.rows||[]).filter(signal=>signal.shop_id===shopId&&rows.some(row=>row.run_id===signal.run_id&&row.observation_id===signal.observation_id)):[];
 const evidenceQueries=['observations','runs',...(trendRows.length?['trend_signals']:[])],evidenceSources={observations:rows,runs:evidence.runs,...(trendRows.length?{trend_signals:trendRows}:{})};
 const latestCapture=[...evidence.runs].filter(run=>run.observed_to).sort((a,b)=>Date.parse(b.observed_to)-Date.parse(a.observed_to))[0]?.observed_to;
 const references=(Array.isArray(turn.references)?turn.references:[]).map(item=>({label:text(item?.label),url:safeAgentReference(item?.url)})).filter(item=>item.url);
 const filters=[{field:'run_id',label:`${shopName||'当前店铺'} · 本回答证据轮次`,value:evidence.runs.map(run=>run.run_id).join('；')},
  {field:'observation_id',label:'本回答关联的全部原卡',value:rows.map(row=>row.observation_id).join('、')}];
 return <div className="pdd-agent-answer" data-reviewed-rows="true">
  {!sameSnapshot&&<p className="pdd-agent-warning">这次答复的快照版本与本页不同或未提供版本依据。文字仅保留为该次答复，不绑定当前商品和图片；请刷新页面后重新查询。</p>}
  {compactAnswer?<><p className="pdd-agent-result-summary">{action==='search'?`本次返回 ${rows.length} 项商品 · 按累计销量排序`:action==='trends'?`本次返回 ${rows.length} 项增长观察 · 按变化线索排序`:`本次返回 ${rows.length} 条首次发现记录`}</p><details className="pdd-agent-tools"><summary>查看完整说明</summary><div className="pdd-agent-answer-text">{turn.answer}</div></details></>:<div className="pdd-agent-answer-text">{turn.answer}</div>}
  {latestCapture&&<small className="pdd-agent-result-time">数据截至 {beijing(latestCapture)}</small>}
  {evidence.rejected_count>0&&<p className="pdd-agent-warning">{evidence.rejected_count} 条商品引用无法匹配当前店铺和轮次的已审阅原卡，未作为商品结果展示。</p>}
  {rows.length>0&&<DataComponent onOpen={onOpenSource} id={`pdd-agent-evidence-${turn.localId}`} queryId="observations" queryIds={evidenceQueries} kind="table" title={action==='trends'?'增长观察商品':action==='new_arrivals'?'首次发现商品':'查询结果'} variant="plain"
   sourceRows={rows} displayRows={rows} sourceRowsByQuery={evidenceSources} scopeFilters={filters}
   description="响应ID仅映射到当前店铺、回答指定轮次的原始审阅行；来源保留全部匹配原卡，不继承商品池的单一轮次。Agent文字解释不是新增的已核实商品事实。">
   <div className="pdd-agent-cards" data-reviewed-rows="true">{shown.map((row,index)=>{const src=agentLocalImage(row,images),matchingSignals=trendRows.filter(item=>item.observation_id===row.observation_id),signal=matchingSignals.length===1?matchingSignals[0]:null;return <article className="pdd-agent-card" key={row.observation_id}>
    {src?<img className="pdd-agent-thumbnail" src={src} alt={row.title||'商品主图'} loading="lazy"/>:<div className="pdd-agent-noimage">本地图待补</div>}
    <div className="pdd-agent-card-body"><span className="pdd-agent-card-index">结果 {index+1}</span><strong className="pdd-agent-card-title" title={row.title}>{productDisplayName(row.title)}</strong>
     <p>{row.sales_raw||'销量未显示'}<span> · {row.price_raw||'价格未显示'}</span></p>
     {Number.isFinite(signal?.latest_delta)&&<p>本次 +{signal.latest_delta} 件<span> · {signal.label||'增长线索'}</span></p>}
     <details className="pdd-agent-card-evidence"><summary>原始名称与采集依据</summary><small>{row.title}</small><small>记录编号 {row.observation_id} · 采集位置 {row.view_order}</small><small>{ID_LABELS[row.identity_status]||'商品身份待核验'} · {beijing(row.observed_at)} · {PRECISION_LABELS[row.observed_at_precision]||'时间精度未知'}</small></details>
     <button type="button" onClick={()=>onDrilldown?.({shop_id:shopId,run_id:row.run_id,observation_ids:[row.observation_id],label:`商品记录 ${row.observation_id}`,source_query:'observations'})}>查看商品详情 ↗</button>
    </div></article>;})}</div>
   {rows.length>8&&<button type="button" className="pdd-agent-link" onClick={()=>setShowAll(value=>!value)}>{showAll?'收起商品':'展开全部 '+rows.length+' 张原卡'}</button>}
  </DataComponent>}
  {references.length>0&&<div className="pdd-agent-references"><span>相关参考链接</span>{references.map((item,index)=><a key={`${item.url}:${index}`} href={item.url} target="_blank" rel="noopener noreferrer">{item.label||new URL(item.url).hostname} ↗</a>)}</div>}
  {Array.isArray(turn.tool_calls)&&turn.tool_calls.length>0&&<details className="pdd-agent-tools"><summary>查看筛选条件</summary><pre>{JSON.stringify(turn.tool_calls,null,2)}</pre></details>}
 </div>;
}

export function AgentSearch({shopId,shopName,onDrilldown,captureNotice=''}){
 const {queries,snapshot}=useDataApp();
 const [open,setOpen]=useState(false),[draft,setDraft]=useState(''),[turns,setTurns]=useState([]),[busy,setBusy]=useState(false);
 const [connection,setConnection]=useState({status:'checking',message:''}),[notice,setNotice]=useState(''),[sourceRequest,setSourceRequest]=useState(null);
 const epoch=useRef(0),sequence=useRef(0),timer=useRef(null),requestController=useRef(null),statusController=useRef(null),inputRef=useRef(null),busyRef=useRef(false),liveShop=useRef(shopId);
 liveShop.current=shopId;
 const images=useMemo(()=>new Map((queries.image_assets?.rows||[]).map(asset=>[asset.sha256,asset])),[queries.image_assets]);
 const stopRequests=()=>{epoch.current+=1;busyRef.current=false;clearTimeout(timer.current);requestController.current?.abort();statusController.current?.abort();};
 const checkConnection=async()=>{
  statusController.current?.abort();const controller=new AbortController();statusController.current=controller;const current=epoch.current;
  setConnection({status:'checking',message:''});const timeout=setTimeout(()=>controller.abort(),12000);
  try{const {data}=await readAgentJson('/__pdd_agent_status',{signal:controller.signal});
   if(current!==epoch.current||liveShop.current!==shopId||statusController.current!==controller)return;
   if(!['ready','configuration_required','login_required','unavailable'].includes(data.status))throw new Error('本机 Agent 未返回可识别的连接状态。');
   setConnection({status:data.status,message:text(data.message),provider:data.provider==='deepseek'?'deepseek':'codex'});
  }catch(error){if(current===epoch.current&&liveShop.current===shopId&&statusController.current===controller)setConnection({status:'unavailable',message:error.name==='AbortError'?'检查连接超时，尚未连接 Agent。':error.message});}
  finally{clearTimeout(timeout);}
 };
 useEffect(()=>{
  stopRequests();setDraft('');setTurns([]);setBusy(false);setNotice('');setSourceRequest(null);checkConnection();
  return stopRequests;
 },[shopId]);
 const completeFailure=(localId,message,question)=>{if(question)setDraft(value=>value||question);setTurns(current=>current.map(turn=>turn.localId===localId?{...turn,status:'failed',message}:turn));busyRef.current=false;setBusy(false);};
 const send=async (event,provided)=>{
  event?.preventDefault();const requestedMessage=(provided??draft).trim(),message=SUGGESTIONS.find(item=>item.label===requestedMessage)?.query||requestedMessage;if(!message||busyRef.current||!shopId)return;
  const current=epoch.current,localId=++sequence.current,controller=new AbortController();requestController.current=controller;busyRef.current=true;
  const isCurrent=()=>current===epoch.current&&liveShop.current===shopId;
  const last=[...turns].reverse().find(turn=>turn.status==='completed');
  const lastIds=agentEvidence(queries,shopId,last,snapshot).rows.map(row=>row.observation_id);
  const payload={shop_id:shopId,message,history:agentHistory(turns),last_observation_ids:lastIds};
  setDraft('');setBusy(true);setNotice('');setSourceRequest(null);setTurns(previous=>[...previous,{localId,shop_id:shopId,question:message,status:'running'}].slice(-MAX_TURNS));
  const started=Date.now();
  const poll=async requestId=>{
   if(!isCurrent())return;
   if(Date.now()-started>WAIT_MS){completeFailure(localId,'等待结果超时，问题已保留。',message);return;}
   const timeout=setTimeout(()=>controller.abort(),20000);
   try{const {data}=await readAgentJson(`/__pdd_agent_result?id=${encodeURIComponent(requestId)}`,{signal:controller.signal});
    if(!isCurrent())return;
    if(data.status==='running'){timer.current=setTimeout(()=>poll(requestId),POLL_MS);return;}
    if(data.status==='failed'){completeFailure(localId,text(data.message)||'Agent 查询失败，没有得到有效结果。',message);return;}
    if(data.status!=='completed'||!text(data.answer).trim()||!Array.isArray(data.observation_ids)||!Array.isArray(data.scope?.run_ids)||data.scope.shop_id!==shopId)throw new Error('Agent 返回结果不完整或店铺不符；没有标记查询成功。');
    setTurns(previous=>previous.map(turn=>turn.localId===localId?{...turn,...data,localId,shop_id:shopId,question:message,status:'completed'}:turn));busyRef.current=false;setBusy(false);setDraft(value=>value===message?'':value);
   }catch(error){if(isCurrent())completeFailure(localId,error.name==='AbortError'?'读取结果超时，问题已保留。':error.message,message);}
   finally{clearTimeout(timeout);}
  };
  const timeout=setTimeout(()=>controller.abort(),20000);
  try{const {data,httpStatus}=await readAgentJson('/__pdd_agent_query',{method:'POST',body:JSON.stringify(payload),signal:controller.signal});
   if(!isCurrent())return;
   if(httpStatus!==202||typeof data.request_id!=='string'||!data.request_id||data.request_id.length>200)throw new Error('Agent 未确认接收查询，问题仍保留在输入框。');
   clearTimeout(timeout);await poll(data.request_id);
  }catch(error){if(isCurrent()){if(['configuration_required','login_required','unavailable'].includes(error.agentStatus))setConnection(previous=>({...previous,status:error.agentStatus,message:error.message}));completeFailure(localId,error.name==='AbortError'?'提交超时，问题已保留。':error.message,message);}}
  finally{clearTimeout(timeout);}
 };
 const clearConversation=()=>{const wasBusy=busy;stopRequests();setTurns([]);setDraft('');setBusy(false);setSourceRequest(null);setNotice(wasBusy?'已清空本页对话并停止等待；已提交的后台任务不会因此撤销。':'已清空本页对话。');if(connection.status==='checking')checkConnection();};
 const activeSource=sourceRequest?.shopId===shopId?sourceRequest.component:null;
 const scopedTurns=turns.filter(turn=>turn.shop_id===shopId),latest=scopedTurns.at(-1),older=scopedTurns.slice(0,-1);
 const renderTurn=turn=><div className="pdd-agent-turn" key={turn.localId} data-reviewed-rows="true"><p className="pdd-agent-question">查询：{questionLabel(turn.question)}</p>{turn.status==='completed'?<AgentAnswer turn={turn} queries={queries} snapshot={snapshot} shopId={shopId} shopName={shopName} images={images} onDrilldown={onDrilldown} onOpenSource={(type,component)=>{if(type==='source')setSourceRequest({shopId,component});}}/>:<p className={turn.status==='failed'?'pdd-agent-warning':'pdd-agent-wait'} role="status">{turn.status==='failed'?turn.message:'正在检索本店商品…'}</p>}</div>;
 return <section className="pdd-agent-search" aria-label="商品助手" data-reviewed-rows="true">
  <button type="button" className="pdd-agent-heading" aria-expanded={open} aria-controls="pdd-agent-conversation" onClick={()=>setOpen(value=>!value)}>
   <span><strong>商品助手</strong><small>查销量 · 看增长</small></span><span className={`pdd-agent-status pdd-agent-status-${connection.status}`}>{connection.provider==='deepseek'&&connection.status==='ready'?'可查询':STATUS_LABELS[connection.status]}</span><span aria-hidden="true">{open?'−':'＋'}</span>
  </button>
  <AgentCollectionShortcut key={shopId} shopId={shopId} shopName={shopName} queries={queries} captureNotice={captureNotice}/>
  {open&&<div id="pdd-agent-conversation" className="pdd-agent-body">
   <div className="pdd-agent-context"><span>当前店铺：{shopName||shopId||'尚未选择店铺'}</span>{scopedTurns.length>0&&<button type="button" onClick={clearConversation}>清空查询</button>}</div>
   <form onSubmit={send} className="pdd-agent-compose">
    <label htmlFor="pdd-agent-message">查询本店商品</label><div className="pdd-agent-input-row"><input ref={inputRef} type="search" autoComplete="off" id="pdd-agent-message" maxLength={MAX_MESSAGE} value={draft} onChange={event=>setDraft(event.target.value)} placeholder="例如：哪些商品在增长？"/><button type="submit" className="pdd-agent-send" disabled={busy||!draft.trim()||!shopId}>{busy?'查询中…':'查询'}</button></div>
    <div className="pdd-agent-suggestions">{SUGGESTIONS.map(item=><button key={item.query} type="button" disabled={busy||!shopId} onClick={()=>send(null,item.query)}>{item.label}</button>)}</div>
    <p className="pdd-agent-query-note">销量排行看累计销量（含已拼、已抢）；增长观察看前后变化。首次发现不等于刚上架。</p>
   </form>
   {latest&&renderTurn(latest)}
   {older.length>0&&<details className="pdd-agent-history"><summary>之前查询（{older.length} 条）</summary>{older.map(renderTurn)}</details>}
   {connection.status!=='ready'&&connection.status!=='checking'&&<details className="pdd-agent-tools"><summary>复杂问题的 Agent 连接状态</summary><p>{connection.message||'模型连接暂不可用，常用本机查询仍可执行。'}</p><button type="button" disabled={busy} onClick={checkConnection}>重新连接</button></details>}
   <p className="pdd-agent-notice" role="status" aria-live="polite">{notice}</p>
  </div>}
  {activeSource&&<SourceSidebar key={activeSource.id} component={activeSource} queries={queries} getSource={queryId=>historyReviewedSource(queries,activeSource,queryId)} onClose={()=>setSourceRequest(null)}/>}
 </section>;
}
