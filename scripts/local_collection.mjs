import fs from 'node:fs/promises';
import path from 'node:path';
import {createCaptureSession} from './capture_session.mjs';
import {openBrowser,guard,ReviewError,delay} from './local_browser.mjs';
import {captureSku} from './local_sku_capture.mjs';
import {captureSkuBatch} from './local_sku_batch.mjs';
import {waitForStorefront} from './local_storefront.mjs';
import {repairStoreImages,imageRepairBudgetMs} from './local_image_repair.mjs';
import {writeCollectionJson} from './atomic_collection_json.mjs';
import {verifyStorefrontIntake} from './storefront_intake.mjs';

const request=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const directory=request.directory;
const storefrontIntake=request.kind==='probe'&&request.verify_storefront===true;
async function write(name,value){await writeCollectionJson(path.join(directory,name),value);}
async function cancelled(){try{await fs.stat(request.cancel_file||path.join(directory,'cancel.json'));return true;}catch{return false;}}
const progress=async value=>{const stamped={...value,at:new Date().toISOString()};await write('progress.json',stamped);if(request.progress_file)await writeCollectionJson(request.progress_file,stamped,{space:0});};
let browser,session,success=false;
async function runSkuBatch(batchRequest){
 const dir=path.resolve(batchRequest.directory);
 if(request.kind!=='sku_batch'||batchRequest.kind!=='sku_batch'||batchRequest.project!==request.project||dir!==path.resolve(directory))throw new ReviewError('SKU 独立任务范围无效。');
 async function stageWrite(name,value){await writeCollectionJson(path.join(dir,name),value);}
 const stageProgress=async value=>{const stamped={...value,at:new Date().toISOString()};await stageWrite('progress.json',stamped);if(batchRequest.progress_file)await writeCollectionJson(batchRequest.progress_file,stamped,{space:0});};
 const stageCancelled=async()=>{try{await fs.stat(batchRequest.cancel_file||path.join(dir,'cancel.json'));return true;}catch{return false;}};
 browser.discardImages?.();
 try{return await captureSkuBatch(browser,{...batchRequest,reuse_shop:false},{cancelled:stageCancelled,progress:stageProgress,
  checkpoint:sku_batch=>stageWrite('browser_result.json',{status:sku_batch.status,sku_batch,website_collection_performed:true}),
  saveItem:async item=>{const file=`sku_${item.observation_id}.json`;await stageWrite(file,item);return file;}});
 }catch(error){
  let prior={items:[]};try{prior=JSON.parse(await fs.readFile(path.join(dir,'browser_result.json'),'utf8')).sku_batch||prior;}catch{}
  const batch={...prior,status:error.status||'manual_review',reason:error.reason||'sku_batch_failed',message:error instanceof ReviewError?error.message:'SKU 未完成，已读取记录保留。'};
  await stageWrite('browser_result.json',{status:batch.status,sku_batch:batch});return batch;
 }
}
async function collectBatchImages(current){
 if(!['ready','running','complete'].includes(current.phase))return;
 const images=await browser.collectImages({shopName:request.shop.shop_name,
  skipUrls:request.skip_image_urls||[],blockedUrls:request.blocked_image_urls||[]});
 await write('image_progress.json',images);
}
try{
 if(!['shop','sku','sku_batch','probe'].includes(request.kind))throw new ReviewError('采集任务类型无效。');
 if(await cancelled())throw new ReviewError('任务已取消，未打开网站。','cancelled');
 await progress({phase:'正在连接你已打开的 Chrome',cards:0});
 // A saved share entry may redirect to the separately verified storefront.
 // Full-shop navigation still opens the entry and performs fresh preflight;
 // only explicit single-SKU requests carry an original-card reuse proof.
 browser=await openBrowser(request.project,{entryUrl:request.entry_url||request.shop?.source_url,verifiedStorefrontUrl:['shop','sku','sku_batch'].includes(request.kind)?request.shop?.source_url:undefined,
  ...(request.kind==='probe'?{publicProbe:true}:{}),
  verifiedOriginalCard:request.kind==='sku'?{title:request.observation?.title,image:request.observation?.image_url,shopName:request.shop?.shop_name}:undefined});
 await write('browser_connection.json',{...browser.executionEvidence,connected_at:new Date().toISOString()});
 if(storefrontIntake){
  const intakeEvidence=await verifyStorefrontIntake(browser,{userShareUrl:request.entry_url,cancelled,progress});
  const page={url:intakeEvidence.sourceUrl,title:intakeEvidence.shopName,text:intakeEvidence.header,observed_at:intakeEvidence.observedAt};
  await write('public_page.json',page);
  await write('browser_result.json',{status:'complete',message:'店铺名称、来源与上新已核验。',intakeEvidence,page,website_collection_performed:false,website_page_read_performed:true});
  success=true;
 }else if(request.kind==='probe'){
  await progress({phase:'检查公开商品或店铺页面'});
  await browser.goto(request.entry_url);
  for(let i=0;i<20;i++){
   const page=await guard(browser);
   if(page.text.trim().length>100)break;
   if(await cancelled())throw new ReviewError('链接检查已取消。','cancelled');
   await delay(500);
  }
  await guard(browser);
  const page=await browser.evaluate(()=>{
   const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
   return {url:location.href,title:document.title,text:document.body.innerText.slice(0,16000),
    links:[...document.querySelectorAll('a[href]')].filter(visible).map(e=>({text:e.innerText.trim().slice(0,120),url:e.href})).filter(e=>/^https:\/\/mobile\.yangkeduo\.com\/(goods1?|mall_page)\.html(?:\?|$)/.test(e.url)).slice(0,150),
    images:[...document.images].filter(visible).map(e=>({url:e.currentSrc,alt:e.alt,width:e.naturalWidth,height:e.naturalHeight})).slice(0,60)};
  });
  const screenshot=await browser.screenshot(path.join(directory,'public_page.jpg'));
  await write('public_page.json',{...page,observed_at:new Date().toISOString()});
  await write('browser_result.json',{status:'complete',message:'公开页面已读取；未采集SKU、未写业务库。',page,screenshot,website_collection_performed:false,website_page_read_performed:true});
  // Retain the inspected public page in the user's Chrome window.
 }else if(request.kind==='sku_batch'){
  const batch=await runSkuBatch(request);
  success=batch.status==='complete';
 }else if(request.kind==='sku'){
  const sku=await captureSku(browser,request,cancelled,progress);
  // SKU evidence is the per-selection DOM receipt and exact loaded images.
  // An optional screenshot must not delay a finished product or time out CDP.
  await write('browser_result.json',{status:sku.status,sku,images:await browser.flush(),website_collection_performed:true});success=true;
 }else{
  await browser.goto(request.entry_url||request.shop.source_url);
  const preflight={shopName:request.shop.shop_name,sourceUrl:request.shop.source_url,cancelled};
  await progress({phase:'正在检查登录和店铺链接',cards:0});
  const ready=await waitForStorefront(browser,preflight);
  if(!ready.latestSelected)await browser.click(ready.latestPoint);
  await waitForStorefront(browser,{...preflight,requireSelected:true});
  const geometry=await browser.evaluate(()=>({top:window.scrollY,height:window.innerHeight}));
  if(geometry.top>2)await browser.scroll([220,Math.min(400,geometry.height/2)],'up',geometry.top/geometry.height);
  session=await createCaptureSession(browser.tab,request.capture.session_directory,{...request.capture.session_options,limits:{maxSteps:250,maxBatches:300,maxCards:10000,maxDurationMs:7200000,maxFreshRetries:0}});
  let current=await session.capture();
  await collectBatchImages(current);
  while(current.phase==='running'||current.phase==='ready'){
   await progress({phase:'逐批采集上新列表',...current.progress});
   if(await cancelled()){current=await session.seal('operator_cancelled_local_job');break;}
   await guard(browser,request.shop.shop_name);current=await session.step();
   if(!(await cancelled()))await collectBatchImages(current);
  }
  let imageRepair=null,imageRepairStopped=false;
  if(current.phase==='complete'){
   const sealed=JSON.parse(await fs.readFile(path.join(request.capture.session_directory,current.snapshot.file),'utf8'));
   try{
    const imageRequest={shop:request.shop,rows:sealed.rows,skipUrls:request.skip_image_urls||[],blockedUrls:request.blocked_image_urls||[]};
    const maxDurationMs=imageRepairBudgetMs({...imageRequest,cachedImages:browser.images});
    imageRepair=await repairStoreImages(browser,imageRequest,{cancelled,progress,maxDurationMs});
   }catch(error){
    imageRepairStopped=true;
    imageRepair={status:'partial',stop_status:error.status||'manual_review',reason:error.reason||'image_repair_failed',message:error instanceof ReviewError?error.message:'补图未完成，已采集商品数据和图片保留。'};
   }
   await write('image_repair.json',imageRepair);
  }
  // Sealed DOM/card evidence already covers a complete shop capture. Finish the
  // shop task immediately; even a legacy followup_sku flag cannot start SKU work.
  const screenshot=current.phase==='complete'
   ?{saved:false,reason:'not_required_for_sealed_shop'}
   :await browser.screenshot(path.join(directory,'capture_end.jpg'));
  await write('browser_result.json',{status:current.phase,message:current.statusLabel,reason:current.lastError||current.progress.stop||null,session:current.snapshot,images:await browser.flush(),screenshot,image_repair:imageRepair,image_repair_stopped:imageRepairStopped,sku_collection_mode:'on_demand',website_collection_performed:true,entry_verified:current.phase==='complete'});success=current.phase==='complete'&&!imageRepairStopped;
 }
}catch(error){
 const ioCodes=new Set(['EACCES','EPERM','EBUSY','ENOENT','EINVAL','EEXIST','ENOSPC','EIO']);
 const errorCode=ioCodes.has(error.code)?error.code:null;
 const errorFrames=String(error.stack||'').split('\n').slice(1).flatMap(line=>{
  const match=line.match(/(?:[\\/]|file:\/\/\/)([a-z_]+\.mjs):(\d+):(\d+)/i);
  return match?[{file:match[1],line:Number(match[2]),column:Number(match[3])}]:[];
 }).slice(0,6);
 if(session){try{await session.seal(error instanceof ReviewError?'manual_review_local_browser':'local_browser_error');}catch{}}
 if(browser&&error.status!=='needs_login'&&!storefrontIntake){
  await browser.screenshot(path.join(directory,'stopped_page.jpg'));
  try{await write('rendered_controls.json',await browser.evaluate(()=>[...document.querySelectorAll('div,span,button,a')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return e.children.length===0&&r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';}).slice(0,2500).map(e=>({text:e.textContent.trim(),class:e.className,role:e.getAttribute('role'),aria_selected:e.getAttribute('aria-selected'),position:{top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom}}))));}catch{}
 }
 await write('browser_result.json',{status:error.status||'manual_review',message:error instanceof ReviewError?error.message:'本机浏览器读取未完成，原始批次已保留，请检查窗口后重试。',reason:error.reason||(error.status==='needs_login'?'login_required':'local_browser_requires_review'),...(error.connection_detail?{connection_detail:error.connection_detail}:{}),...(error.sku_diagnostics?{sku_diagnostics:error.sku_diagnostics}:{}),error_type:error.name,...(errorCode?{error_code:errorCode}:{}),...(errorFrames.length?{error_frames:errorFrames}:{})});
}finally{if(browser)await browser.close(success,{keepPage:request.kind==='sku'||storefrontIntake});}
