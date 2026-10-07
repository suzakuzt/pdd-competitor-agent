// Execute the actual entry script against local stub modules, never a browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const execute=promisify(execFile);
const source=new URL('../scripts/local_collection.mjs',import.meta.url);

async function run(t,kind,{stopped=false,requestOverrides={},intakeFailure=false}={}){
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'pdd_on_demand_'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 await fs.copyFile(source,path.join(root,'local_collection.mjs'));
 await fs.copyFile(new URL('../scripts/atomic_collection_json.mjs',import.meta.url),path.join(root,'atomic_collection_json.mjs'));
 await fs.writeFile(path.join(root,'events.json'),'[]');
 await fs.writeFile(path.join(root,'events.mjs'),`import fs from 'node:fs/promises'; export async function event(value){const p=new URL('./events.json',import.meta.url);const a=JSON.parse(await fs.readFile(p,'utf8'));a.push(value);await fs.writeFile(p,JSON.stringify(a));}`);
 const stubs={
  'storefront_intake.mjs':`import {event} from './events.mjs'; import {ReviewError} from './local_browser.mjs'; export async function verifyStorefrontIntake(browser,{userShareUrl,progress}){await event('intake');await browser.goto(userShareUrl);await progress({phase:'SYNTHETIC intake verification'});${intakeFailure?"throw new ReviewError('SYNTHETIC intake refusal');":"return {userShareUrl,shopName:'SYNTHETIC VERIFIED SHOP',sourceUrl:'https://mobile.yangkeduo.com/mall_page.html?mall_id=123',header:'SYNTHETIC VERIFIED SHOP',selectedMarkup:'<li class=\"current_x\">上新</li>',observedAt:'2026-10-07T00:00:00Z'};"}}`,
  'local_browser.mjs':`import fs from 'node:fs/promises'; import {event} from './events.mjs'; export class ReviewError extends Error{constructor(message,status='manual_review'){super(message);this.status=status;}} export const guard=async()=>({text:'SYNTHETIC public page '.repeat(10)}); export const delay=async()=>{throw new Error('Unexpected wait');}; export async function openBrowser(project,options){await fs.writeFile(new URL('./open_options.json',import.meta.url),JSON.stringify(options));await event('open');return {executionEvidence:{synthetic:true},tab:{id:777},images:{},canPreserveSkuStorefront(){throw new Error('Shop must never invoke SKU preserve');},goto:async url=>{await fs.writeFile(new URL('./goto_url.json',import.meta.url),JSON.stringify(url));await event('goto');},evaluate:async()=>({top:0,height:600}),collectImages:async()=>({}),flush:async()=>({}),discardImages(){},screenshot:async()=>({saved:false}),close:async (success,options)=>event({closed:success,options})};}`,
  'capture_session.mjs':`export async function createCaptureSession(){return {capture:async()=>({phase:'complete',statusLabel:'Synthetic complete',snapshot:{file:'snapshot.json'},progress:{cards:1}}),seal:async()=>{}};}`,
  'local_storefront.mjs':`import fs from 'node:fs/promises'; export async function waitForStorefront(browser,options){if(options.requireSelected)await fs.writeFile(new URL('./required_storefront.json',import.meta.url),JSON.stringify(options));return {latestSelected:true};}`,
  'local_image_repair.mjs':`import {ReviewError} from './local_browser.mjs'; export const imageRepairBudgetMs=()=>100; export async function repairStoreImages(){${stopped?"throw new ReviewError('Synthetic login','needs_login');":"return {status:'complete'};"}}`,
  'local_sku_capture.mjs':`import {event} from './events.mjs'; export async function captureSku(browser,request,cancelled,progress){await event('sku');await progress({phase:'SYNTHETIC SKU',scrolls:19});return {status:'complete',variants:[]};}`,
  'local_sku_batch.mjs':`import {event} from './events.mjs'; export async function captureSkuBatch(browser,request,options){await event({batch:true,reuse_shop:request.reuse_shop});await options.progress({phase:'SYNTHETIC SKU batch',scrolls:19});await options.saveItem({observation_id:1,status:'complete'});const value={status:'complete',items:[]};await options.checkpoint(value);return value;}`,
 };
 if(requestOverrides.verify_storefront===true)stubs['local_browser.mjs']=stubs['local_browser.mjs'].replace('screenshot:async()=>({saved:false})','screenshot:async()=>{throw new Error("Intake must never depend on screenshot");}');
 for(const [file,text] of Object.entries(stubs))await fs.writeFile(path.join(root,file),text);
 await fs.writeFile(path.join(root,'snapshot.json'),JSON.stringify({rows:[]}));
 const historical={};
 for(const name of ['sku_queue.json','sku_request.json','sku_receipt.json','sku_followup_error.json','progress.json.tmp','mirrored_progress.json.sku.tmp']){
  historical[name]='SYNTHETIC old evidence '+name;await fs.writeFile(path.join(root,name),historical[name]);
 }
 const request={kind,directory:root,project:root,shop:{shop_name:'SYNTHETIC',source_url:'https://example.invalid/shop'},entry_url:'https://example.invalid/shop',
  capture:{session_directory:root,session_options:{}},followup_sku:true,observations:[],progress_file:path.join(root,'mirrored_progress.json'),...requestOverrides};
 await fs.writeFile(path.join(root,'request.json'),JSON.stringify(request));
 await execute(process.execPath,[path.join(root,'local_collection.mjs'),path.join(root,'request.json')],{timeout:5000});
 return {root,result:JSON.parse(await fs.readFile(path.join(root,'browser_result.json'),'utf8')),
  events:JSON.parse(await fs.readFile(path.join(root,'events.json'),'utf8')),historical,
  openOptions:await fs.readFile(path.join(root,'open_options.json'),'utf8').then(JSON.parse).catch(error=>{if(error.code==='ENOENT')return null;throw error;}),
  gotoUrl:await fs.readFile(path.join(root,'goto_url.json'),'utf8').then(JSON.parse).catch(error=>{if(error.code==='ENOENT')return null;throw error;})};
}

test('complete shop ignores stale followup flag and old SKU queue, then closes immediately',async t=>{
 const f=await run(t,'shop');
 assert.equal(f.result.status,'complete');assert.equal(f.result.sku_collection_mode,'on_demand');
 assert.equal(f.result.image_repair_stopped,false);assert.equal('sku_followup_available' in f.result,false);
 assert.deepEqual(f.events,['open','goto',{closed:true,options:{keepPage:false}}]);
 for(const [name,bytes] of Object.entries(f.historical))assert.equal(await fs.readFile(path.join(f.root,name),'utf8'),bytes);
});
test('image repair pause stays visible without ever starting SKU',async t=>{
 const f=await run(t,'shop',{stopped:true});
 assert.equal(f.result.image_repair_stopped,true);assert.equal(f.result.image_repair.stop_status,'needs_login');
 assert.deepEqual(f.events,['open','goto',{closed:false,options:{keepPage:false}}]);
});
test('explicit single SKU remains available and requests retention of its storefront page',async t=>{
 const f=await run(t,'sku');assert.equal(f.result.status,'complete');assert.deepEqual(f.events,['open','sku',{closed:true,options:{keepPage:true}}]);
 const progress=JSON.parse(await fs.readFile(path.join(f.root,'progress.json'),'utf8'));
 assert.equal(progress.scrolls,19);assert.equal(progress.phase,'SYNTHETIC SKU');
 assert.deepEqual(JSON.parse(await fs.readFile(path.join(f.root,'mirrored_progress.json'),'utf8')),progress);
 for(const [name,bytes] of Object.entries(f.historical))assert.equal(await fs.readFile(path.join(f.root,name),'utf8'),bytes);
});
test('explicit SKU batch uses its own request without shop handoff',async t=>{
 const f=await run(t,'sku_batch');assert.equal(f.result.status,'complete');
 assert.deepEqual(f.events,['open',{batch:true,reuse_shop:false},{closed:true,options:{keepPage:false}}]);
 assert.equal(f.openOptions.verifiedStorefrontUrl,'https://example.invalid/shop');assert.equal(Object.hasOwn(f.openOptions,'verifiedOriginalCard'),false);assert.equal(Object.hasOwn(f.openOptions,'publicProbe'),false);
 const progress=JSON.parse(await fs.readFile(path.join(f.root,'progress.json'),'utf8'));
 assert.equal(progress.scrolls,19);assert.equal(progress.phase,'SYNTHETIC SKU batch');
 assert.deepEqual(JSON.parse(await fs.readFile(path.join(f.root,'mirrored_progress.json'),'utf8')),progress);
 assert.deepEqual(JSON.parse(await fs.readFile(path.join(f.root,'sku_1.json'),'utf8')),{observation_id:1,status:'complete'});
 for(const [name,bytes] of Object.entries(f.historical))assert.equal(await fs.readFile(path.join(f.root,name),'utf8'),bytes);
});
test('unknown task kind cannot fall back to shop collection',async t=>{
 const f=await run(t,'stale_automatic_sku');assert.equal(f.result.status,'manual_review');assert.deepEqual(f.events,[]);
});

const canonical='https://mobile.yangkeduo.com/mall_page.html?mall_sn=SYNTHETIC_VERIFIED_STORE';
const shortEntry='https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_SHARE';
const verifiedShop={shop_name:'SYNTHETIC VERIFIED SHOP',source_url:canonical};

test('shop sends the verified canonical identity separately from its ps entry without SKU original-card proof',async t=>{
 const f=await run(t,'shop',{requestOverrides:{shop:verifiedShop,entry_url:shortEntry,
  observation:{title:'STALE SKU CARD MUST NOT ENABLE RESUME',image_url:'https://img.pddpic.com/STALE.png'}}});
 assert.equal(f.result.status,'complete');assert.equal(f.openOptions.entryUrl,shortEntry);
 assert.equal(f.openOptions.verifiedStorefrontUrl,canonical);assert.equal(Object.hasOwn(f.openOptions,'verifiedOriginalCard'),false);
 assert.equal(f.gotoUrl,shortEntry,'even a selected existing tab must navigate the explicitly supplied shop entry');
 assert.deepEqual(JSON.parse(await fs.readFile(path.join(f.root,'required_storefront.json'),'utf8')),{shopName:verifiedShop.shop_name,sourceUrl:canonical,requireSelected:true});
 assert.deepEqual(f.events,['open','goto',{closed:true,options:{keepPage:false}}]);
});

test('single SKU keeps canonical identity and exact original-card proof when entry is a ps share',async t=>{
 const observation={title:'SYNTHETIC ORIGINAL CARD',image_url:'https://img.pddpic.com/SYNTHETIC_ORIGINAL.png'};
 const f=await run(t,'sku',{requestOverrides:{shop:verifiedShop,entry_url:shortEntry,observation}});
 assert.equal(f.result.status,'complete');assert.equal(f.openOptions.entryUrl,shortEntry);assert.equal(f.openOptions.verifiedStorefrontUrl,canonical);
 assert.deepEqual(f.openOptions.verifiedOriginalCard,{title:observation.title,image:observation.image_url,shopName:verifiedShop.shop_name});
 assert.equal(f.gotoUrl,null,'entry must leave the single-SKU locator in charge of its own page preparation');
 assert.deepEqual(f.events,['open','sku',{closed:true,options:{keepPage:true}}]);
});

test('explicit SKU batch receives the verified canonical for a ps share but never single-card or public-probe authority',async t=>{
 const f=await run(t,'sku_batch',{requestOverrides:{shop:verifiedShop,entry_url:shortEntry,observation:{title:'IGNORED OLD CARD',image_url:'https://img.pddpic.com/OLD.png'}}});
 assert.equal(f.openOptions.entryUrl,shortEntry);assert.equal(f.openOptions.verifiedStorefrontUrl,canonical);
 assert.equal(Object.hasOwn(f.openOptions,'verifiedOriginalCard'),false);assert.equal(Object.hasOwn(f.openOptions,'publicProbe'),false);
 assert.equal(f.result.status,'complete');assert.equal(f.gotoUrl,null);
});

test('only explicit public inspection opts into share resolution and never claims website collection or shop identity',async t=>{
 const f=await run(t,'probe',{requestOverrides:{shop:undefined,entry_url:shortEntry}});
 assert.equal(f.openOptions.publicProbe,true);assert.equal(f.openOptions.entryUrl,shortEntry);
 assert.equal(Object.hasOwn(f.openOptions,'verifiedStorefrontUrl'),false);assert.equal(Object.hasOwn(f.openOptions,'verifiedOriginalCard'),false);
 assert.equal(f.result.status,'complete');assert.equal(f.result.website_collection_performed,false);assert.equal(f.result.website_page_read_performed,true);
 assert.equal(Object.hasOwn(f.result,'entry_verified'),false);assert.deepEqual(f.events,['open','goto',{closed:false,options:{keepPage:false}}]);
});

test('explicit storefront verification writes complete DOM intake and the same public page URL without screenshots',async t=>{
 const f=await run(t,'probe',{requestOverrides:{shop:undefined,entry_url:shortEntry,verify_storefront:true}});
 assert.equal(f.result.status,'complete');assert.equal(f.result.intakeEvidence.userShareUrl,shortEntry);
 const page=JSON.parse(await fs.readFile(path.join(f.root,'public_page.json'),'utf8'));
 assert.equal(page.url,f.result.intakeEvidence.sourceUrl);assert.equal(page.title,f.result.intakeEvidence.shopName);
 assert.equal(f.result.website_collection_performed,false);assert.equal(f.result.website_page_read_performed,true);
 assert.deepEqual(f.events,['open','intake','goto',{closed:true,options:{keepPage:true}}]);
 assert.equal(f.openOptions.publicProbe,true);assert.equal(Object.hasOwn(f.openOptions,'verifiedStorefrontUrl'),false);
});
test('failed storefront verification preserves the failure without a screenshot or success page receipt',async t=>{
 const f=await run(t,'probe',{intakeFailure:true,requestOverrides:{shop:undefined,entry_url:shortEntry,verify_storefront:true}});
 assert.equal(f.result.status,'manual_review');assert.equal(Object.hasOwn(f.result,'intakeEvidence'),false);
 assert.deepEqual(f.events,['open','intake','goto',{closed:false,options:{keepPage:true}}]);
 await assert.rejects(fs.stat(path.join(f.root,'public_page.json')),error=>error.code==='ENOENT');
});
test('a truthy string does not change an ordinary public probe into storefront verification',async t=>{
 const f=await run(t,'probe',{requestOverrides:{shop:undefined,entry_url:shortEntry,verify_storefront:'true'}});
 assert.equal(f.result.status,'complete');assert.equal(Object.hasOwn(f.result,'intakeEvidence'),false);
 assert.deepEqual(f.events,['open','goto',{closed:false,options:{keepPage:false}}]);
});
