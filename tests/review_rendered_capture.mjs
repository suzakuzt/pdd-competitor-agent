import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';
import {createRenderedCapture,readRenderedDom} from '../scripts/rendered_capture_driver.mjs';

const HERE=path.dirname(fileURLToPath(import.meta.url)),STAGE=path.dirname(HERE);
const args=process.argv.slice(2);
if(args.length!==0 && (args.length!==2 || args[0]!=='--output' || !args[1] || args[1].startsWith('--'))){
 throw new Error('Usage: node review_rendered_capture.mjs [--output <receipt.json>]');
}
const TMP_ROOT=path.resolve(os.tmpdir());
const output=args.length?path.resolve(args[1]):path.join(TMP_ROOT,`pdd-rendered-capture-review-${crypto.randomUUID()}.json`);
const TEMP=await fs.mkdtemp(path.join(TMP_ROOT,'pdd-rendered-synthetic-'));
const URL='https://mobile.yangkeduo.com/mall_page.html?mall_id=101';
const results=[];let serial=0;
const at=n=>`2026-10-04T10:00:${String(n).padStart(2,'0')}.000Z`;
const card=(column,row,options={})=>{
 const top=100+row*180,left=column*220;
 return {slot:`${column}:${row}`,domColumn:column,domRow:row,title:`SYNTHETIC ${column}-${row}`,identityTitle:`SYNTHETIC ${column}-${row}`,
  imageUrl:`https://example.invalid/${column}-${row}.jpg`,rawImageUrl:`https://example.invalid/${column}-${row}.jpg`,goodsUrl:null,goodsId:null,
  salesRaw:null,displayTipRaw:null,priceRaw:'¥2',rawText:'SYNTHETIC',ready:true,pendingReasons:[],inViewport:top<300,
  position:{top,left,bottom:top+160,viewportTop:top,viewportBottom:top+160},...options};
};
const page=({cards=[card(0,0)],hidden=[],excluded=[],time=1,y=0,end=false,scrollHeight=8000,...rest}={})=>{
 const updated=cards.map(c=>({...structuredClone(c),position:{...c.position,viewportTop:c.position.top-y,viewportBottom:c.position.bottom-y},inViewport:c.position.bottom>y&&c.position.top<y+300}));
 const manifest=[...updated.map(c=>({slot:c.slot,domColumn:c.domColumn,domRow:c.domRow,state:c.ready?'ready':'pending',position:c.position,pendingReasons:c.pendingReasons})),...hidden,...excluded];
 const count=Math.max(...manifest.map(s=>s.domColumn),0)+1;
 const counts=Array.from({length:count},(_,i)=>Math.max(-1,...manifest.filter(s=>s.domColumn===i).map(s=>s.domRow))+1);
 return {at:at(time),pageUrl:URL,shopVerified:true,listPresent:true,scrollTop:y,viewportHeight:300,viewportWidth:1000,scrollHeight,
  columnLayoutVerified:true,columnSlotCounts:counts,loadedCardCount:manifest.length,slotManifest:manifest,cards:updated,
  readCoverageBottom:updated.length?Math.max(...updated.map(c=>c.position.bottom)):null,endBoundaryObserved:end,
  boundaries:end?[{visible:true,rendered:true,documentTop:y+280,top:280,bottom:295}]:[],recommendations:[],...rest};
};
async function harness(pages,options={}){
 const dir=path.join(TEMP,String(++serial));let index=0;const scrolls=[];
 const tab={id:'SYNTHETIC_TAB',playwright:{evaluate:async(fn,arg)=>{
  assert.equal(fn,readRenderedDom);assert.equal(arg.shopName,'SYNTHETIC SHOP');
  if(index>=pages.length)throw new Error('synthetic exhausted');
  const value=pages[index++];if(value instanceof Error)throw value;return structuredClone(value);
 }},scroll:async(point,direction,amount)=>{scrolls.push({point,direction,amount});}};
 const capture=await createRenderedCapture(tab,dir,{sourceUrl:URL,shopName:'SYNTHETIC SHOP',checkpointReference:'synthetic/batches',attemptId:'synthetic-attempt',...options});
 return {capture,dir,tab,scrolls};
}
async function test(name,fn){try{await fn();results.push({name,status:'passed'});}catch(error){results.push({name,status:'failed',error:error.stack});}}

class Node{
 constructor({text='',className='',rect={top:0,left:0,width:200,height:160},attrs={},hidden=false}={}){this.textContent=text;this.className=className;this.rect=rect;this.attrs=attrs;this.hidden=hidden;this.children=[];this.parentElement=null;this.selectors={};this.innerText=text;}
 append(...children){for(const child of children){this.children.push(child);child.parentElement=this;}return this;}
 getBoundingClientRect(){const {top,left,width,height}=this.rect,y=globalThis.__syntheticY||0;return {top:top-y,bottom:top-y+height,left,width,height};}
 getAttribute(name){return this.attrs[name]??null;}
 matches(selector){return selector==='.goodsItem_R1ok0MpS'&&this.className==='goodsItem_R1ok0MpS';}
 querySelector(selector){return this.selectors[selector]||null;}
 querySelectorAll(selector){if(selector==='.goodsItem_R1ok0MpS')return this.children.flatMap(c=>c.matches(selector)?[c]:c.querySelectorAll(selector));return this.selectors[selector]||[];}
 closest(){return null;}
}
function domCard(row,{hidden=false,pending=false,price='',src=null}={}){
 const node=new Node({className:'goodsItem_R1ok0MpS',rect:{top:100+row*180,left:0,width:200,height:160},hidden});
 node.selectors['.goodsName_dT8mZSNd']=new Node({text:pending?'':`DOM ${row}`});
 node.selectors['.goodsImage_fU27dxbk img']=new Node({attrs:{src:src??(pending?'data:image/gif;base64,placeholder':`https://example.invalid/dom${row}.jpg`)}});
 node.selectors['.salesTip_qUlLfJr3']=new Node({text:row===0?'已拼0件':'限量新品'});
 node.selectors['.priceDesc_Rt9mZDhD,.priceIcon_vjoyxmSd,.price_af78GRrq,.fraction_EQGvgNnL']=price?[new Node({text:price})]:[];
 return node;
}
function seenSetTransport(value){
 const seen=new WeakSet();
 const visit=item=>{
  if(item===null||typeof item!=='object')return item;
  if(seen.has(item))return '[Circular]';seen.add(item);
  if(Array.isArray(item))return item.map(visit);
  return Object.fromEntries(Object.entries(item).map(([key,nested])=>[key,visit(nested)]));
 };
 return visit(value);
}
function domRead({items,markers=[],y=0},callback=readRenderedDom,arg={shopName:'SYNTHETIC SHOP'},transport=value=>value){
 const old={document:globalThis.document,innerHeight:globalThis.innerHeight,innerWidth:globalThis.innerWidth,
  location:globalThis.location,getComputedStyle:globalThis.getComputedStyle,__syntheticY:globalThis.__syntheticY};
 const column=new Node().append(...items),list=new Node({rect:{top:50,left:0,width:600,height:5000}}).append(column);
 try{globalThis.__syntheticY=y;globalThis.innerHeight=300;globalThis.innerWidth=1000;globalThis.location={href:URL};
  globalThis.getComputedStyle=e=>({display:e.hidden?'none':'block',visibility:'visible',opacity:'1'});
  globalThis.document={scrollingElement:{scrollTop:y,scrollHeight:8000},body:{innerText:'SYNTHETIC SHOP 本店已拼3.2万件'},
   querySelector:selector=>selector==='.waterfall-list-container_egohG8wQ'?list:null,
   querySelectorAll:selector=>selector==='div,p,span'?markers:[]};
  return transport(callback(arg));
 }finally{for(const[key,value]of Object.entries(old))if(value===undefined)delete globalThis[key];else globalThis[key]=value;}
}

await test('DOM reads offviewport cards but preserves actual hidden slot hole',async()=>{
 const result=domRead({items:[domCard(0),domCard(1,{hidden:true}),domCard(2)]});
 assert.equal(result.loadedCardCount,3);assert.equal(result.cards.length,2);assert.deepEqual(result.cards.map(c=>c.domRow),[0,2]);
 assert.equal(result.cards[1].inViewport,false);assert.equal(result.slotManifest[1].state,'excluded_hidden');
 assert.equal(result.cards[0].salesRaw,'已拼0件');assert.equal(result.cards[1].salesRaw,null);
});
await test('actual DOM callback survives seen-WeakSet browser transport without shared references',async()=>{
 const dir=path.join(TEMP,String(++serial));
 const items=[domCard(0),domCard(1,{pending:true}),domCard(2,{hidden:true}),domCard(3)];
 let transported;
 const tab={id:'SYNTHETIC_TRANSPORT',playwright:{evaluate:async(callback,arg)=>{
  assert.equal(callback,readRenderedDom);
  transported=domRead({items},callback,arg,seenSetTransport);
  return transported;
 }},scroll:async()=>assert.fail('initial DOM capture must not scroll')};
 const capture=await createRenderedCapture(tab,dir,{sourceUrl:URL,shopName:'SYNTHETIC SHOP',checkpointReference:'synthetic/transport'});
 const result=await capture.capture();
 assert.equal(JSON.stringify(transported).includes('[Circular]'),false);
 assert.equal(typeof transported.cards[0].position,'object');assert.ok(Array.isArray(transported.cards[0].pendingReasons));
 assert.equal(result.stop,null);assert.equal(result.seen,2);assert.equal(result.pending,1);
 const raw=JSON.parse(await fs.readFile(path.join(dir,'batch_0001.json'),'utf8'));
 assert.equal(raw.stop,null);assert.equal(raw.cards[0].position.top,raw.slotManifest[0].position.top);
 assert.equal((await capture.snapshot()).rows.length,2);
});
await test('DOM skeleton retains pending raw data and missing fields never become zero',async()=>{
 const result=domRead({items:[domCard(0,{pending:true})]});assert.equal(result.cards[0].ready,false);
 assert.equal(result.cards[0].imageUrl,null);assert.ok(result.cards[0].rawImageUrl.startsWith('data:'));
 assert.ok(result.cards[0].pendingReasons.includes('title_not_ready'));
});
await test('DOM end must be viewport-visible and recommendations are excluded',async()=>{
 const end=new Node({text:'本店暂无更多商品',rect:{top:440,left:0,width:300,height:20}});
 const rec=new Node({text:'其他店铺的精选推荐',rect:{top:480,left:0,width:300,height:20}});
 let result=domRead({items:[domCard(0),domCard(1),domCard(2),domCard(3)],markers:[end,rec]});
 assert.equal(result.endBoundaryObserved,false);assert.equal(result.cards.length,2);assert.equal(result.slotManifest[2].state,'excluded_after_boundary');
 result=domRead({items:[domCard(0),domCard(1),domCard(2)],markers:[end,rec],y:300});assert.equal(result.endBoundaryObserved,true);
});
await test('all rendered cards captured once without title or image merging',async()=>{
 const cards=[card(0,0),card(1,0,{title:'same',identityTitle:'same',imageUrl:'https://example.invalid/same.jpg'}),card(0,1,{title:'same',identityTitle:'same',imageUrl:'https://example.invalid/same.jpg'}),card(0,2)];
 const {capture}=await harness([page({cards})]);await capture.capture();const s=await capture.snapshot();
 assert.equal(s.rows.length,4);assert.equal(s.status,'partial');assert.equal(s.collectionEvidence.driverVersion,5);
 assert.ok(s.rows.some(r=>!r.inViewport));
});
await test('hidden slot gaps do not cause false incomplete coverage',async()=>{
 const data={cards:[card(0,0),card(0,2)],hidden:[{slot:'0:1',domColumn:0,domRow:1,state:'excluded_hidden'}]};
 const {capture}=await harness([page(data),page({...data,time:2,y:400,end:true})]);
 await capture.capture();await capture.step();assert.equal((await capture.snapshot()).status,'complete');assert.equal(capture.status().cards,2);
});
await test('pending card prevents completeness then late fields become a distinct observation',async()=>{
 const pending=card(0,1,{title:'',identityTitle:null,imageUrl:null,ready:false,pendingReasons:['title_not_ready','original_image_url_not_ready']});
 const {capture,dir}=await harness([page({cards:[card(0,0),pending]}),page({cards:[card(0,0),pending],end:true,time:2,y:200}),page({cards:[card(0,0),card(0,1)],end:true,time:3,y:200})]);
 await capture.capture();let s=await capture.snapshot();assert.equal(s.status,'partial');assert.equal(s.rows.length,1);assert.equal(s.collectionEvidence.pendingSlotCount,1);
 await capture.step();s=await capture.snapshot();assert.equal(s.status,'partial');assert.equal(s.endBoundaryObserved,true);
 await capture.capture();s=await capture.snapshot();assert.equal(s.status,'complete');assert.equal(s.rows[1].firstRenderedAt,at(1));assert.equal(s.rows[1].firstObservedAt,at(3));
 assert.equal(JSON.parse(await fs.readFile(path.join(dir,'batch_0001.json'),'utf8')).cards[1].imageUrl,null);
});
await test('sales and price updates retain both raw batches and latest real read time',async()=>{
 const {capture,dir}=await harness([page({cards:[card(0,0,{salesRaw:'已拼1件',priceRaw:'¥2'})]}),page({cards:[card(0,0,{salesRaw:'已拼11件',priceRaw:'券后¥3'})],time:2})]);
 await capture.capture();await capture.capture();const row=(await capture.snapshot()).rows[0];
 assert.equal(row.salesRaw,'已拼11件');assert.equal(row.priceRaw,'券后¥3');assert.equal(row.firstObservedAt,at(1));assert.equal(row.observedAt,at(2));
 assert.match(row.observationTimeMeaning,/not listing time/);assert.equal(JSON.parse(await fs.readFile(path.join(dir,'batch_0001.json'),'utf8')).cards[0].salesRaw,'已拼1件');
});
await test('missing sales remains null on every real read',async()=>{
 const {capture}=await harness([page({end:true})]);await capture.capture();assert.equal((await capture.snapshot()).rows[0].salesRaw,null);
});
await test('offviewport existing slot identity replacement stops and excludes invalid batch',async()=>{
 const cards=[card(0,0),card(0,1),card(0,2)],changed=cards.map(c=>({...c}));changed[0]={...changed[0],title:'changed',identityTitle:'changed'};
 const {capture}=await harness([page({cards}),page({cards:changed,time:2,y:300})]);await capture.capture();await capture.step();
 assert.equal(capture.status().stop,'slot_content_conflict');const s=await capture.snapshot();assert.equal(s.observedTo,at(1));assert.equal(s.rows[0].title,cards[0].title);
});
await test('image and goods URL changes both stop stable slot reuse',async()=>{
 for(const field of ['imageUrl','goodsUrl']){
  const first=card(0,0,{[field]:'https://example.invalid/first'}),second={...first,[field]:'https://example.invalid/second'};
  const {capture}=await harness([page({cards:[first]}),page({cards:[second],time:2})]);await capture.capture();await capture.capture();assert.equal(capture.status().stop,'slot_content_conflict');
 }
});
await test('previous unknown DOM goods link can enrich with actual URL',async()=>{
 const {capture}=await harness([page(),page({cards:[card(0,0,{goodsUrl:'https://mobile.yangkeduo.com/goods.html?goods_id=123',goodsId:'123'})],time:2})]);
 await capture.capture();await capture.capture();assert.equal(capture.status().stop,null);assert.equal((await capture.snapshot()).rows[0].goodsId,'123');
});
await test('layout drift over one CSS pixel stops without accepting moved cards',async()=>{
 const c=card(0,0);const moved={...c,position:{...c.position,top:c.position.top+1.01}};
 const {capture}=await harness([page(),page({cards:[moved],time:2})]);await capture.capture();await capture.capture();assert.equal(capture.status().stop,'document_layout_changed');
});
await test('one pixel rounding jitter preserves actual raw position and original first-read anchor',async()=>{
 const cards=[0,1,0,-1,0].map(delta=>{const c=card(0,0);return {...c,position:{...c.position,top:c.position.top+delta,left:c.position.left+delta}};});
 const {capture}=await harness(cards.map((c,i)=>page({cards:[c],time:i+1})));
 for(let i=0;i<cards.length;i++)await capture.capture();
 assert.equal(capture.status().stop,null);assert.equal(capture.status().cards,1);
 assert.equal((await capture.snapshot()).rows[0].firstObservedAt,at(1));
 assert.equal((await capture.snapshot()).rows[0].position.top,100);
});
await test('successive one-pixel movements cannot accumulate past first rendered anchor',async()=>{
 for(const key of ['top','left']){
  const pages=[0,1,2].map((delta,i)=>{const c=card(0,0);c.position[key]+=delta;return page({cards:[c],time:i+1});});
  const {capture}=await harness(pages);await capture.capture();await capture.capture();assert.equal(capture.status().stop,null);
  await capture.capture();assert.equal(capture.status().stop,'document_layout_changed');
  assert.equal((await capture.snapshot()).observedTo,at(2));
 }
});
await test('pending slot rounding uses its first rendered position after becoming ready',async()=>{
 const pending=card(0,0,{identityTitle:null,imageUrl:null,ready:false,pendingReasons:['title_not_ready']});
 const ready=card(0,0);ready.position.top++;
 const moved=card(0,0);moved.position.top+=2;
 const {capture}=await harness([page({cards:[pending,card(0,1)]}),page({cards:[ready,card(0,1)],time:2}),page({cards:[moved,card(0,1)],time:3})]);
 await capture.capture();await capture.capture();assert.equal(capture.status().stop,null);
 await capture.capture();assert.equal(capture.status().stop,'document_layout_changed');
});
await test('virtualization and ready card hidden or pending are rejected',async()=>{
 for(const second of [page({cards:[card(0,0)],time:2}),page({cards:[card(0,0)],hidden:[{slot:'0:1',domColumn:0,domRow:1,state:'excluded_hidden'}],time:2}),
  page({cards:[card(0,0),card(0,1,{ready:false,pendingReasons:['card_marked_busy']})],time:2})]){
  const {capture}=await harness([page({cards:[card(0,0),card(0,1)]}),second]);await capture.capture();await capture.capture();assert.ok(capture.status().stop);
 }
});
await test('normal larger scroll bounded by previously read coverage',async()=>{
 const cards=Array.from({length:20},(_,i)=>card(0,i));
 const {capture,scrolls}=await harness([page({cards}),page({cards,time:2,y:900})]);await capture.capture();await capture.step();
 assert.equal(scrolls[0].direction,'down');assert.equal(scrolls[0].amount,3);assert.equal(capture.status().stop,null);
});
await test('End-sized step remains bounded by inspected coverage and records configured maximum',async()=>{
 const cards=Array.from({length:100},(_,i)=>card(0,i));
 const first=page({cards,scrollHeight:22000});const limit=first.readCoverageBottom;
 const {capture,scrolls}=await harness([first,page({cards,time:2,y:limit,scrollHeight:22000})],{maxScrollViewports:100});
 await capture.capture();await capture.step();assert.equal(capture.status().stop,null);
 assert.equal(scrolls[0].amount,limit/300);assert.ok(scrolls[0].amount>6);
 assert.equal((await capture.snapshot()).collectionEvidence.maxScrollViewports,100);
 const bad=await harness([first,page({cards,time:2,y:limit+3,scrollHeight:22000})],{maxScrollViewports:100});
 await bad.capture.capture();await bad.capture.step();assert.equal(bad.capture.status().stop,'scroll_beyond_read_coverage');
 await assert.rejects(harness([first],{maxScrollViewports:100.01}),/<=100/);
});
await test('actual scroll exceeding read coverage stops even if new DOM appeared',async()=>{
 const {capture}=await harness([page(),page({cards:[card(0,0),card(0,1)],time:2,y:1000})]);await capture.capture();await capture.step();assert.equal(capture.status().stop,'scroll_beyond_read_coverage');
});
await test('external scroll without recorded action is not silently accepted',async()=>{
 const {capture}=await harness([page(),page({time:2,y:50})]);await capture.capture();await capture.capture();assert.equal(capture.status().stop,'unrecorded_scroll');
});
await test('moving through already-read long list does not falsely trip no-new guard',async()=>{
 const cards=Array.from({length:30},(_,i)=>card(0,i));
 const {capture}=await harness(Array.from({length:5},(_,i)=>page({cards,time:i+1,y:i*900})));
 await capture.capture();for(let i=0;i<4;i++)await capture.step();assert.equal(capture.status().stop,null);assert.equal(capture.status().cards,30);
});
await test('three stagnant frontier scrolls stop instead of claiming complete',async()=>{
 const {capture}=await harness([page(),page({time:2}),page({time:3}),page({time:4})]);
 await capture.capture();for(let i=0;i<3;i++)await capture.step();assert.equal(capture.status().stop,'three_frontier_reads_without_ready_progress');
});
await test('zero scroll room counts stationary step reads and stops after exactly three',async()=>{
 const {capture,scrolls,dir}=await harness([1,2,3,4].map(time=>page({time,scrollHeight:300})));
 await capture.capture();await capture.step();assert.equal(capture.status().stop,null);await capture.step();assert.equal(capture.status().stop,null);
 await capture.step();assert.equal(capture.status().stop,'three_frontier_reads_without_ready_progress');assert.equal(capture.status().noProgressReads,3);assert.equal(scrolls.length,0);
 const raw=JSON.parse(await fs.readFile(path.join(dir,'batch_0004.json'),'utf8'));assert.equal(raw.scrollEvidence.kind,'stationary_read');assert.equal(raw.noProgressReads,3);
 assert.equal((await capture.snapshot()).status,'partial');
});
await test('visible end with pending card stops after three no-ready reads despite excluded additions',async()=>{
 const pending=card(1,0,{ready:false,pendingReasons:['card_marked_busy']});
 const pages=[1,2,3,4].map(time=>page({cards:[card(0,0),pending],time,end:true,scrollHeight:300,
  hidden:Array.from({length:time-1},(_,i)=>({slot:`0:${i+1}`,domColumn:0,domRow:i+1,state:'excluded_hidden'})),
  excluded:Array.from({length:time-1},(_,i)=>({slot:`1:${i+1}`,domColumn:1,domRow:i+1,state:'excluded_after_boundary'}))}));
 const {capture}=await harness(pages);await capture.capture();await capture.step();await capture.step();assert.equal(capture.status().stop,null);
 await capture.step();assert.equal(capture.status().stop,'three_boundary_reads_without_ready_progress');
 const s=await capture.snapshot();assert.equal(s.status,'partial');assert.equal(s.endBoundaryObserved,true);assert.equal(s.collectionEvidence.pendingSlotCount,1);
});
await test('third stationary retry may become ready and complete instead of stopping',async()=>{
 const pending=card(1,0,{ready:false,pendingReasons:['card_marked_busy']});
 const pages=[1,2,3,4].map(time=>page({cards:[card(0,0),time===4?card(1,0):pending],time,end:true,scrollHeight:300}));
 const {capture,scrolls}=await harness(pages);await capture.capture();await capture.step();await capture.step();await capture.step();
 assert.equal(capture.status().stop,null);assert.equal(capture.status().ended,true);assert.equal(scrolls.length,0);
 const s=await capture.snapshot();assert.equal(s.status,'complete');assert.equal(s.rows.find(r=>r.slot==='1:0').observedAt,at(4));
});
await test('a genuine new relevant pending slot resets stall count',async()=>{
 const pending=card(0,1,{ready:false,pendingReasons:['card_marked_busy']});
 const {capture}=await harness([page({scrollHeight:300}),page({time:2,scrollHeight:300}),page({time:3,scrollHeight:300}),
  page({cards:[card(0,0),pending],time:4,scrollHeight:500}),page({cards:[card(0,0),pending],time:5,scrollHeight:500})]);
 await capture.capture();await capture.step();await capture.step();await capture.step();assert.equal(capture.status().noProgressReads,0);
 await capture.step();assert.equal(capture.status().stop,null);assert.equal(capture.status().noProgressReads,1);
});
await test('identity change rejected before adding rows',async()=>{
 const {capture}=await harness([page(),page({time:2,pageUrl:'https://mobile.yangkeduo.com/mall_page.html?mall_id=202'})]);
 await capture.capture();await capture.capture();assert.equal(capture.status().stop,'shop_identity_changed');assert.equal((await capture.snapshot()).rows[0].observedAt,at(1));
});
await test('fresh top and fixed viewport required',async()=>{
 const a=await harness([page({y:30})]);await a.capture.capture();assert.equal(a.capture.status().stop,'scan_not_started_at_top');
 const b=await harness([page(),page({time:2,viewportWidth:1001})]);await b.capture.capture();await b.capture.capture();assert.equal(b.capture.status().stop,'viewport_changed');
});
await test('manifest holes duplicates or missing card payload cannot complete',async()=>{
 for(const mutate of [p=>p.slotManifest.push(p.slotManifest[0]),p=>p.cards=[],p=>p.columnSlotCounts=[2]]){
  const p=page({end:true});mutate(p);const {capture}=await harness([p]);await capture.capture();assert.ok(capture.status().stop);assert.equal(capture.status().ended,false);
 }
});
await test('completion rejects an unbacked end flag and recommendation card payload',async()=>{
 const a=await harness([page({end:true,boundaries:[{visible:false,rendered:true,documentTop:500,top:500,bottom:520}]})]);
 await a.capture.capture();assert.equal(a.capture.status().stop,'inconsistent_boundary_evidence');
 const b=await harness([page({end:true,recommendations:[{rendered:true,documentTop:90,top:90,bottom:100}]})]);
 await b.capture.capture();assert.equal(b.capture.status().stop,'card_after_shop_boundary_or_recommendations');
});
await test('unrecognized but readable layout is durably recorded without accepting rows',async()=>{
 const {capture,dir}=await harness([page(),page({time:2,columnLayoutVerified:false})]);await capture.capture();await capture.capture();
 assert.equal(capture.status().stop,'unrecognized_column_layout');const raw=JSON.parse(await fs.readFile(path.join(dir,'batch_0002.json'),'utf8'));
 assert.equal(raw.columnLayoutVerified,false);assert.equal(raw.stop,'unrecognized_column_layout');assert.equal((await capture.snapshot()).rows[0].observedAt,at(1));
});
await test('checkpoint collision preserves old memory and durable file',async()=>{
 const {capture,dir}=await harness([page(),page({time:2})]);await capture.capture();const collision=path.join(dir,'batch_0002.json');await fs.writeFile(collision,'SYNTHETIC KEEP');
 await assert.rejects(capture.capture());assert.equal(capture.status().stop,'checkpoint_write_failed');assert.equal(capture.status().batches,1);assert.equal(await fs.readFile(collision,'utf8'),'SYNTHETIC KEEP');
});
await test('read failure permits only durable verified partial prefix',async()=>{
 const {capture}=await harness([page(),new Error('synthetic timeout')]);await capture.capture();await assert.rejects(capture.capture());
 const s=await capture.snapshot();assert.equal(s.status,'partial');assert.equal(s.observedTo,at(1));assert.equal(s.collectionEvidence.stopReason,'page_read_failed');
});
await test('nonempty attempt directory and unverifiable v4 resume are rejected',async()=>{
 const existing=await harness([page()]);await existing.capture.capture();
 await assert.rejects(createRenderedCapture(existing.tab,existing.dir,{sourceUrl:URL,shopName:'SYNTHETIC SHOP',checkpointReference:'x'}),/Fresh/);
 await assert.rejects(harness([page()],{resumeV4:{directory:'anything'}}),/resume unsupported/);
});
await test('complete terminal state does not issue more reads or scrolls',async()=>{
 const {capture,scrolls}=await harness([page({end:true})]);await capture.capture();await capture.step();await capture.capture();assert.equal(capture.status().batches,1);assert.equal(scrolls.length,0);
});

const source=path.join(STAGE,'scripts/rendered_capture_driver.mjs');
const report={status:results.every(r=>r.status==='passed')?'passed':'failed',checked_at:new Date().toISOString(),
 simulated_only:true,real_browser_run:false,production_writes:false,test_count:results.length,results,
 source,source_sha256:crypto.createHash('sha256').update(await fs.readFile(source)).digest('hex'),
 limitations:['Current DOM selectors require a real next-run check.','v4 resume unsupported because same-document continuity was not recorded.',
  'Nonhidden offviewport DOM is read; nothing is fetched directly and no hidden application state is consulted.',
  'Required title/original image or marked-empty price stays pending; missing sales stays unknown.','Layout movement or stable identity content changes stop the prefix.']};
await fs.mkdir(path.dirname(output),{recursive:true});
await fs.writeFile(output,JSON.stringify(report,null,2));
assert.equal(path.dirname(path.resolve(TEMP)),TMP_ROOT);
assert.ok(path.basename(TEMP).startsWith('pdd-rendered-synthetic-'));
await fs.rm(TEMP,{recursive:true});
console.log(JSON.stringify({status:report.status,test_count:report.test_count,source_sha256:report.source_sha256,report_path:output,failures:results.filter(r=>r.status==='failed')},null,2));
if(report.status!=='passed')process.exitCode=1;
