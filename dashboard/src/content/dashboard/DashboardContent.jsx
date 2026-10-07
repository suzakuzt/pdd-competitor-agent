import React,{useState} from 'react';
import {Tabs,TabPanel,Dropdown,useDataApp} from '../../data-app-public.jsx';
import {AgentSearch} from './AgentSearch.jsx';
import {ProductPool} from './ProductPool.jsx';
import {HistoryContent} from './HistoryContent.jsx';
import {NewArrivalsContent} from './NewArrivalsContent.jsx';
import {PortfolioContent} from './PortfolioContent.jsx';
import {ArtistResearchContent} from './ArtistResearchContent.jsx';
import {HeatWatchContent} from './HeatWatchContent.jsx';
import {ProfitWorkbench} from './ProfitWorkbench.jsx';
import {ProductTables} from './ProductTables.jsx';
import {availableShops} from './portfolio-model.js';
import {ShopTargetList,ShopTargetEmpty} from './ShopIntake.jsx';
import {ShopLinkIntake as ShopIntake,ShopOnboardingControl} from './ShopOnboarding.jsx';
import {resolveIntakeSelection,shopSelectionOptions,mergeShopDirectory,selectionOptionValue,selectionHref,readyShopTarget} from './shop-intake-model.js';
import {useShopDirectory} from './ShopDirectory.jsx';
import {CollectionControl} from './CollectionControls.jsx';
import {WarehouseContent} from './WarehouseContent.jsx';
import './history-dashboard.css';
import './new-arrivals.css';
import './portfolio.css';
export function DashboardContent(){
 const {queries}=useDataApp(),directory=useShopDirectory(),selectionQueries=mergeShopDirectory(queries,directory.directory),shops=availableShops(queries),shopOptions=shopSelectionOptions(selectionQueries,shops);
 const [view,setView]=useState(()=>{const requestedView=typeof window!=='undefined'?new URLSearchParams(window.location.search).get('pdd_view'):null;return ['products','profit','portfolio','pool','history','warehouse','artists','heat'].includes(requestedView)?requestedView:'warehouse';});
 const [requested,setRequested]=useState(()=>{const params=new URLSearchParams(typeof window!=='undefined'?window.location.search:'');return {shopId:params.get('pdd_shop')||'',targetId:params.get('pdd_target')||''};});
 const [drilldown,setDrilldown]=useState(null),[intakeOpen,setIntakeOpen]=useState(false),[historyExpanded,setHistoryExpanded]=useState(false);
 const selection=resolveIntakeSelection(selectionQueries,requested,shops),{shopId,shop}=selection;
 const readyTarget=selection.canAnalyze?readyShopTarget(selectionQueries,shopId):null;
 const rememberSelection=value=>{setRequested(value);if(typeof window!=='undefined'&&window.history?.replaceState){const next=new URL(selectionHref(window.location.href,value),window.location.href);next.searchParams.set('pdd_view',view);window.history.replaceState(null,'',next.pathname+next.search+next.hash);}};
 const selectShop=value=>{rememberSelection({shopId:value,targetId:''});setDrilldown(null);setHistoryExpanded(false);};
 const selectTarget=targetId=>{rememberSelection({shopId:'',targetId});setDrilldown(null);setHistoryExpanded(false);};
 const openHistory=targetId=>{rememberSelection({shopId:'',targetId});setDrilldown(null);setView('history');setHistoryExpanded(true);};
 const openCards=request=>{if(!shops.some(row=>row.shop_id===request.shop_id))return;rememberSelection({shopId:request.shop_id,targetId:''});setDrilldown({...request});setView('pool');};
 const viewTabs=<Tabs id="pdd-workspace-views" label="竞品记录视图" variant="pills" value={view} onChange={setView} items={[{id:'warehouse',label:'上新数据舱'},{id:'products',label:'商品档案'},{id:'profit',label:'选品与试品'},{id:'portfolio',label:'竞品总览'},{id:'pool',label:'商品池'},{id:'history',label:'更新与变化'},{id:'artists',label:'明星与活动'},{id:'heat',label:'新星观察'}]}/>;
 return <div className="pdd-workspace portfolio-workspace">
  <div className="portfolio-page-heading"><div><h1>{view==='warehouse'?'竞品数据舱':'多店竞品分析'}</h1>{view!=='warehouse'&&<p>查看竞品数据与商品档案。</p>}</div><div className="pdd-intake-header-actions">{shopOptions.choices.length>0&&<Dropdown label="当前分析店铺" allLabel="请选择店铺" showLabel value={selectionOptionValue(selection,shopOptions,selectionQueries)} choices={shopOptions.choices} choiceLabels={shopOptions.choiceLabels} onChange={value=>value.startsWith('target:')?selectTarget(value.slice(7)):selectShop(value)}/>}<button type="button" onClick={()=>setIntakeOpen(true)}>＋ 添加店铺</button></div></div>
  {directory.error&&<div className="pdd-directory-notice" role="status">{directory.error}<button type="button" onClick={directory.refresh}>刷新店铺列表</button></div>}
  {(directory.directory?.onboardings||[]).filter(job=>['queued','running'].includes(job.status)).map(job=><div key={job.id} className="pdd-directory-notice" role="status"><span>{job.shop_name||'新店铺'} · {job.collection_job_id?'采集中':'识别中'}</span><button type="button" onClick={()=>selectTarget(job.target_id)}>查看进度</button></div>)}
  {view!=='warehouse'&&<ShopTargetList selectedTargetId={selection.targetId} onSelectTarget={selectTarget} onOpenHistory={openHistory}/>}
  {selection.canAnalyze?<>{readyTarget?<ShopOnboardingControl key={`ready:${readyTarget.onboarding.id}`} target={readyTarget} onChange={directory.refresh}/>:<CollectionControl key={`collection:${shopId}`} shopId={shopId} shopName={shop?.shop_name||shopId} compact={view==='warehouse'}/>}<AgentSearch key={`agent:${shopId}`} shopId={shopId} shopName={shop?.shop_name||shopId} onDrilldown={openCards} captureNotice={readyTarget?'本店新链接已识别，请使用上方“开始采集”读取该链接。':''}/>{view==='warehouse'&&<details className="warehouse-more"><summary>设置与更多</summary><div><ShopTargetList selectedTargetId={selection.targetId} onSelectTarget={selectTarget} onOpenHistory={openHistory}/>{viewTabs}</div></details>}
   {view!=='warehouse'&&viewTabs}
   <TabPanel tabsId="pdd-workspace-views" tabId="warehouse" active={view==='warehouse'} keepMounted><WarehouseContent key={`warehouse:${shopId}`} shopId={shopId}/></TabPanel>
   <TabPanel tabsId="pdd-workspace-views" tabId="products" active={view==='products'} keepMounted><ProductTables key={`products:${shopId}`} shopId={shopId} onSelectShop={selectShop} onDrilldown={openCards} onOpenHistory={()=>{setView('history');setHistoryExpanded(true);}}/></TabPanel>
   <TabPanel tabsId="pdd-workspace-views" tabId="profit" active={view==='profit'} keepMounted><ProfitWorkbench key={`profit:${shopId}`} shopId={shopId} onDrilldown={openCards}/></TabPanel>
   <TabPanel tabsId="pdd-workspace-views" tabId="portfolio" active={view==='portfolio'} keepMounted><PortfolioContent shopId={shopId} onSelectShop={selectShop} onDrilldown={openCards} onAddShop={()=>setIntakeOpen(true)} onSelectTarget={selectTarget}/></TabPanel>
   <TabPanel tabsId="pdd-workspace-views" tabId="pool" active={view==='pool'} keepMounted><ProductPool key={shopId} shopId={shopId} drilldown={drilldown}/></TabPanel>
   <TabPanel tabsId="pdd-workspace-views" tabId="history" active={view==='history'} keepMounted><NewArrivalsContent key={`arrivals:${shopId}`} shopId={shopId}/><details className="pdd-arrivals-legacy" open={historyExpanded} onToggle={event=>setHistoryExpanded(event.currentTarget.open)}><summary><strong>{shop?.shop_name||'当前店铺'} · 旧存量与完整历史比较</strong><span>按需展开，保留原筛选、来源和完整文件</span></summary><HistoryContent key={`history:${shopId}`} shopId={shopId}/></details></TabPanel>
   <TabPanel tabsId="pdd-workspace-views" tabId="artists" active={view==='artists'} keepMounted><ArtistResearchContent key={`artists:${shopId}`} shopId={shopId} onDrilldown={openCards}/></TabPanel>
   <TabPanel tabsId="pdd-workspace-views" tabId="heat" active={view==='heat'} keepMounted><HeatWatchContent/></TabPanel>
  </>:<ShopTargetEmpty key={selection.targetId||requested.shopId||'empty'} selection={selection} onAdd={()=>setIntakeOpen(true)} onChange={directory.refresh}/>}
  <ShopIntake open={intakeOpen} onClose={()=>setIntakeOpen(false)} onChange={directory.refresh} onSelectTarget={targetId=>{selectTarget(targetId);setIntakeOpen(false);}}/>
 </div>;
}
