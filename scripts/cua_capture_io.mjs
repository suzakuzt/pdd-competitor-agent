// The authorized host supplies an existing tab and its documented ordinary
// wheel capability. No browser creation, HTTP, credentials or page mutation.
import {setTimeout,clearTimeout} from 'node:timers';

export function createBoundedCuaTab(tab,{wheel,endKey,viewportHeight,timeoutMs=40000}={}){
 if(!tab?.playwright?.evaluate||((typeof wheel==='function')===(typeof endKey==='function'))||!Number.isFinite(viewportHeight)||viewportHeight<=0||
    !Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>45000)throw new Error('Existing authorized tab, ordinary wheel, viewport and bounded deadline required');
 let poisoned=false,pending=0,lastPage=null;
 const timings=[];
 async function call(kind,action){
  if(poisoned)throw new Error('CUA_IO_POISONED: no further browser calls after a deadline');
  const start=Date.now();pending++;let timer;
  const work=Promise.resolve().then(action);
  // A late browser reply is observed but never consumed as a new DOM batch.
  work.then(()=>{pending--;},()=>{pending--;});
  try{
   const result=await Promise.race([work,new Promise((_,reject)=>{timer=setTimeout(()=>{poisoned=true;reject(new Error(`CUA_IO_DEADLINE: ${kind} exceeded ${timeoutMs} ms; preserve only prior verified batches`));},timeoutMs);})]);
   timings.push({kind,at:new Date().toISOString(),elapsedMs:Date.now()-start,status:'returned'});return result;
  }catch(error){poisoned=true;timings.push({kind,at:new Date().toISOString(),elapsedMs:Date.now()-start,status:'failed'});throw error;}
  finally{clearTimeout(timer);}
 }
 return {id:tab.id,playwright:{evaluate:async(fn,arg)=>{const page=await call('dom_read',()=>tab.playwright.evaluate(fn,arg,{timeoutMs:timeoutMs}));lastPage=page;return page;}},
  scroll:(point,direction,units)=>{
   const maxUnits=endKey?100:6;
   if(direction!=='down'||!Number.isFinite(units)||units<=0||units>maxUnits)throw new Error(`Only bounded forward ordinary input up to ${maxUnits} viewports is supported`);
   if(endKey){
    // End is allowed only when the requested, already-inspected target is the
    // current document bottom. It cannot jump over uninspected list content.
    const bottom=lastPage?.scrollHeight-lastPage?.viewportHeight;
    const target=lastPage?.scrollTop+units*viewportHeight;
    if(!lastPage||![bottom,target,lastPage.readCoverageBottom].every(Number.isFinite)||
       Math.abs(lastPage.viewportHeight-viewportHeight)>2||Math.abs(target-bottom)>2||
       bottom>lastPage.readCoverageBottom+2)throw new Error('END_TARGET_NOT_INSPECTED: use only an already-read document bottom');
    return call('ordinary_end_key',endKey);
   }
   return call('ordinary_wheel',()=>wheel({x:point[0],y:point[1],deltaY:units*viewportHeight}));
  },ioStatus:()=>({poisoned,pending,timings:timings.map(x=>({...x}))})};
}

const boundaryWrapped=new WeakSet();

// Installs in place so an already running, idle capture session keeps the same
// adapter reference. The caller supplies only the documented ordinary click on
// the exact public shop-end text. No input event or DOM evidence is fabricated.
export function withBoundedBoundaryFallback(adapter,{boundaryClick,timeoutMs=40000}={}){
 if(!adapter?.playwright?.evaluate||typeof adapter.scroll!=='function'||typeof adapter.ioStatus!=='function'||
    typeof boundaryClick!=='function'||!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>45000||boundaryWrapped.has(adapter))
  throw new Error('An unwrapped bounded adapter, ordinary boundary click and bounded deadline are required');
 const originalEvaluate=adapter.playwright.evaluate.bind(adapter.playwright),originalScroll=adapter.scroll.bind(adapter),originalStatus=adapter.ioStatus.bind(adapter);
 if(originalStatus().poisoned||originalStatus().pending)throw new Error('CUA_IO_POISONED: boundary fallback requires an idle healthy adapter');
 let lastPage=null,poisoned=false,pending=0;
 const timings=[];
 const enter=()=>{
  const state=originalStatus();
  if(poisoned||state.poisoned)throw new Error('CUA_IO_POISONED: no further browser calls after a failure');
  if(pending||state.pending)throw new Error('CUA_IO_BUSY: wait for the current browser operation');
 };
 const inspect=(direction,units)=>{
  const page=lastPage,finite=Number.isFinite;
  const fail=()=>{throw new Error('BOUNDARY_TARGET_NOT_INSPECTED: exact rendered shop end and bounded target required');};
  if(direction!=='down'||!finite(units)||units<=0||units>100||!page||page.shopVerified!==true||page.listPresent!==true||page.columnLayoutVerified!==true||
     ![page.scrollTop,page.viewportHeight,page.scrollHeight,page.readCoverageBottom].every(finite)||page.scrollTop<0||page.viewportHeight<=0||
     page.scrollHeight<page.viewportHeight||page.readCoverageBottom<page.scrollTop)fail();
  const maxAllowed=Math.max(page.scrollTop,Math.min(page.readCoverageBottom,page.scrollHeight-page.viewportHeight));
  const requested=page.scrollTop+units*page.viewportHeight;
  if(requested<=page.scrollTop||Math.abs(requested-maxAllowed)>2)fail();
  const boundaries=page.boundaries?.filter(item=>item?.rendered===true);
  if(!boundaries||boundaries.length!==1)fail();
  const boundary=boundaries[0],height=boundary.bottom-boundary.top;
  if(![boundary.documentTop,boundary.top,boundary.bottom,height].every(finite)||height<=0||height>page.viewportHeight/2||
     Math.abs(boundary.documentTop-(page.scrollTop+boundary.top))>2||boundary.visible!==false||
     boundary.top<page.viewportHeight||boundary.documentTop<page.readCoverageBottom-2||
     boundary.documentTop+height>page.scrollHeight+2)fail();
  if(page.recommendations?.some(item=>item?.rendered===true&&(!finite(item.documentTop)||item.documentTop<boundary.documentTop)))fail();
  // A normal locator click scrolls its target into view. Admit only an adjacent
  // end marker whose centered position lies within already read content. Its
  // actual final position still MUST pass v5's post-input coverage validation.
  const expectedCenter=Math.max(0,Math.min(page.scrollHeight-page.viewportHeight,boundary.documentTop+height/2-page.viewportHeight/2));
  if(expectedCenter<page.scrollTop-2||expectedCenter>maxAllowed+2)fail();
  return {maxAllowedScrollTop:maxAllowed,expectedCenterScrollTop:expectedCenter,boundaryDocumentTop:boundary.documentTop};
 };
 async function click(evidence){
  const start=Date.now();let timer;pending++;
  const work=Promise.resolve().then(boundaryClick);
  work.then(()=>{pending--;},()=>{pending--;});
  try{
   const result=await Promise.race([work,new Promise((_,reject)=>{timer=setTimeout(()=>{poisoned=true;reject(new Error(`CUA_IO_DEADLINE: ordinary_boundary_click exceeded ${timeoutMs} ms; preserve only prior verified batches`));},timeoutMs);})]);
   timings.push({kind:'ordinary_boundary_click',at:new Date().toISOString(),elapsedMs:Date.now()-start,status:'returned',...evidence});return result;
  }catch(error){poisoned=true;timings.push({kind:'ordinary_boundary_click',at:new Date().toISOString(),elapsedMs:Date.now()-start,status:'failed',...evidence});throw error;}
  finally{clearTimeout(timer);}
 }
 adapter.playwright.evaluate=async(fn,arg)=>{
  enter();
  try{const page=await originalEvaluate(fn,arg);lastPage=page;return page;}
  catch(error){poisoned=true;throw error;}
 };
 adapter.scroll=async(point,direction,units)=>{
  enter();
  try{return await originalScroll(point,direction,units);}
  catch(error){
   if(!String(error?.message||'').startsWith('END_TARGET_NOT_INSPECTED:')){poisoned=true;throw error;}
   try{enter();return await click(inspect(direction,units));}
   catch(failure){poisoned=true;throw failure;}
  }
 };
 adapter.ioStatus=()=>{const state=originalStatus();return {...state,poisoned:poisoned||state.poisoned,pending:pending+state.pending,
  timings:[...state.timings,...timings.map(item=>({...item}))].sort((a,b)=>Date.parse(a.at)-Date.parse(b.at))};};
 boundaryWrapped.add(adapter);return adapter;
}
