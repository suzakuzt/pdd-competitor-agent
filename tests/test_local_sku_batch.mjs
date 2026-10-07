// Isolated orchestration tests: no Chrome, network, production files or database.
import test from 'node:test';
import assert from 'node:assert/strict';
import {captureSkuBatch} from '../scripts/local_sku_batch.mjs';
import {ReviewError} from '../scripts/local_browser.mjs';

const image='https://img.pddpic.com/SYNTHETIC_SKU.png';
const otherImage='https://img.pddpic.com/SYNTHETIC_OTHER.png';
const shop={shop_name:'SYNTHETIC_ONLY',source_url:'https://mobile.yangkeduo.com/mall_page.html?mall_id=123'};
const row=(id,patch={})=>({observation_id:id,title:'SYNTHETIC_'+id,sales_label:'已拼',sales_precision:'exact_display',sales_unit:'件',sales_value:11,...patch});
const sku=(status='complete')=>({status,variants:[{image_url:image,price_raw:'¥4.5',selection_verified:true}]});

function setup(rows,{capture:customCapture,cancelled:customCancelled}={}){
 const events=[],captures=[],checkpoints=[],saved=[];let pageUrl=shop.source_url;
 const browser={
  async flush(){events.push('flush');return {[image]:{mime:'image/png',base64:'SYNTHETIC'},[otherImage]:{mime:'image/png',base64:'SYNTHETIC_OTHER'}};},
  async evaluate(fn){assert.equal(fn.name,'readPageGuard');return {url:pageUrl,text:shop.shop_name,login:false,challenge:false};},
  async back(){events.push('back');pageUrl=shop.source_url;},
  discardImages(){events.push('discard');},
  async close(){assert.fail('batch must not close shared Chrome connection');},
 };
 const options={
  cancelled:customCancelled||(async()=>false),
  async progress(value){events.push('progress:'+String(value.sku_observation_id||''));},
  async checkpoint(batch){const copy=structuredClone(batch);checkpoints.push(copy);events.push('checkpoint:'+copy.items.length);},
  async saveItem(item){saved.push(structuredClone(item));events.push('save:'+item.observation_id);return 'sku_'+item.observation_id+'.json';},
  async capture(actual,request,cancelled,progress){
   assert.equal(actual,browser);captures.push(request);events.push('capture:'+request.observation.observation_id);
   const result=customCapture?await customCapture(request,cancelled,progress):sku();pageUrl='https://mobile.yangkeduo.com/goods.html?goods_id='+request.observation.observation_id;return result;
  },
 };
 return {browser,request:{kind:'sku_batch',shop,observations:rows},options,events,captures,checkpoints,saved,run(){return captureSkuBatch(browser,this.request,options);}};
}

test('batch checkpoints first, reuses one browser and saves each product before moving on',async()=>{
 const f=setup([row(1),row(2)]),result=await f.run();
 assert.equal(f.events[0],'checkpoint:0');assert.equal(result.status,'complete');assert.equal(result.completed,2);assert.equal(result.remaining,0);
 assert.deepEqual(f.captures.map(r=>r.reuse_shop),[false,true]);assert.equal(f.events.filter(e=>e==='back').length,2);
 const saveIndex=f.events.indexOf('save:1'),checkpointIndex=f.events.indexOf('checkpoint:1'),secondCapture=f.events.indexOf('capture:2');
 assert.ok(saveIndex<checkpointIndex&&checkpointIndex<secondCapture);
 assert.ok(saveIndex<f.events.indexOf('discard')&&f.events.indexOf('discard')<secondCapture);
 assert.deepEqual(Object.keys(f.saved[0].images),[image]);assert.equal(f.saved[0].observation_id,1);
 assert.deepEqual(f.checkpoints.at(-1),result);assert.equal(f.checkpoints[0].items.length,0);
});

test('SKU-only work reports supplementation without claiming a fresh store capture',async()=>{
 const f=setup([row(1)]),progress=[];f.request.sku_only=true;
 f.options.progress=async value=>progress.push(value);
 assert.equal((await f.run()).status,'complete');
 assert.ok(progress.some(p=>p.phase==='正在补采商品 SKU'));
 assert.ok(progress.every(p=>!String(p.phase).includes('全店数据已入库')));
});

test('only positive exact 已拼/已抢 items are eligible, including 1 and 10 boundaries',async()=>{
 const good=setup([row(1,{sales_value:1}),row(2,{sales_value:10,sales_label:'已抢'}),row(3,{sales_value:11,sales_label:'已抢'})]);assert.equal((await good.run()).completed,3);
 for(const patch of [{sales_value:0},{sales_value:-1},{sales_value:null},{sales_value:1.5},{sales_value:'11'},{sales_label:'总售'},{sales_label:null},{sales_precision:'approximate'},{sales_unit:'人'}]){
  const bad=setup([row(1,patch)]);await assert.rejects(bad.run(),/精确销量/);assert.equal(bad.captures.length,0);assert.equal(bad.checkpoints.length,0);
 }
 const duplicate=setup([row(1),row(1)]);await assert.rejects(duplicate.run(),/精确销量/);assert.equal(duplicate.captures.length,0);
});

test('global login, restriction, connection and identity failures halt without losing prior product',async()=>{
 for(const error of [new ReviewError('SYNTHETIC login','needs_login','login_required'),new ReviewError('SYNTHETIC restricted','manual_review','access_restricted'),new ReviewError('SYNTHETIC connection','needs_browser','connection_required'),new ReviewError('SYNTHETIC identity','manual_review','sku_identity_changed')]){
  const f=setup([row(1),row(2),row(3)],{capture:async request=>{if(request.observation.observation_id===2)throw error;return sku();}}),result=await f.run();
  assert.equal(result.status,error.status);assert.equal(result.reason,error.reason);assert.equal(result.completed,1);assert.equal(result.failed,1);assert.equal(result.remaining,1);
  assert.deepEqual(f.captures.map(r=>r.observation.observation_id),[1,2]);assert.deepEqual(f.saved.map(r=>r.observation_id),[1]);
  assert.equal(result.items[0].capture_file,'sku_1.json');assert.deepEqual(f.checkpoints.at(-1),result);
 }
});

test('one card not found is recorded and later eligible products continue independently',async()=>{
 const f=setup([row(1),row(2)],{capture:async request=>{if(request.observation.observation_id===1)throw new ReviewError('SYNTHETIC not found','manual_review','sku_card_not_found');return sku();}}),result=await f.run();
 assert.equal(result.status,'partial');assert.equal(result.completed,1);assert.equal(result.failed,1);assert.equal(result.remaining,0);
 assert.equal(result.items[0].reason,'sku_card_not_found');assert.deepEqual(f.saved.map(r=>r.observation_id),[2]);assert.equal(f.captures[1].reuse_shop,true);
});

test('APP-only price page is a local skip and the next product still saves its own SKU',async()=>{
 const f=setup([row(1),row(2)],{capture:async request=>{
  if(request.observation.observation_id===1)throw new ReviewError('SYNTHETIC 前往APP查看价格，无网页规格入口','manual_review','sku_web_unavailable');
  return sku();
 }}),result=await f.run();
 assert.equal(result.status,'partial');assert.equal(result.failed,1);assert.equal(result.completed,1);assert.equal(result.remaining,0);
 assert.deepEqual(f.captures.map(r=>r.observation.observation_id),[1,2]);assert.deepEqual(f.saved.map(r=>r.observation_id),[2]);
 assert.equal(result.items[0].reason,'sku_web_unavailable');assert.equal(result.items[0].capture_file,undefined);
 assert.equal(result.items[1].capture_file,'sku_2.json');assert.deepEqual(f.checkpoints.at(-1),result);
});

test('local partial product is saved and the next product still runs',async()=>{
 for(const reason of ['sku_option_obscured','sku_catalog_changed','sku_selection_unverified']){
  const f=setup([row(1),row(2)],{capture:async request=>request.observation.observation_id===1?{...sku('partial'),stop_status:'manual_review',stop_reason:reason}:sku()}),result=await f.run();
  assert.equal(result.status,'partial');assert.equal(result.partial,1);assert.equal(result.completed,1);assert.equal(result.remaining,0);
  assert.deepEqual(f.saved.map(r=>r.observation_id),[1,2]);assert.equal(f.saved[0].sku.status,'partial');assert.equal(f.captures.length,2);assert.equal(f.captures[1].reuse_shop,true);
  assert.equal(result.items[0].capture_file,'sku_1.json');assert.equal(result.items[0].reason,reason);assert.deepEqual(f.checkpoints.at(-1),result);
 }
});

test('global failure after partial work preserves it then stops the queue',async()=>{
 for(const [status,reason] of [['cancelled','operator_cancelled'],['needs_login','login_required'],['needs_browser','connection_required'],['manual_review','access_restricted'],['manual_review','sku_identity_changed']]){
  const f=setup([row(1),row(2)],{capture:async()=>({...sku('partial'),stop_status:status,stop_reason:reason})}),result=await f.run();
  assert.equal(result.status,status);assert.equal(result.reason,reason);assert.equal(result.partial,1);assert.equal(result.remaining,1);
  assert.deepEqual(f.saved.map(r=>r.observation_id),[1]);assert.equal(f.captures.length,1);assert.ok(!f.events.includes('back'));
  assert.equal(result.items[0].capture_file,'sku_1.json');assert.deepEqual(f.checkpoints.at(-1),result);
 }
});

test('unexplained partial is review required, never falsely labelled operator cancelled',async()=>{
 const f=setup([row(1),row(2)],{capture:async()=>sku('partial')}),result=await f.run();
 assert.equal(result.status,'manual_review');assert.equal(result.reason,'sku_read_failed');assert.equal(result.partial,1);assert.equal(result.remaining,1);assert.equal(f.captures.length,1);
});

test('cancellation before start or after one product never starts the next product',async()=>{
 let f=setup([row(1),row(2)],{cancelled:async()=>true}),result=await f.run();assert.equal(result.status,'cancelled');assert.equal(result.remaining,2);assert.equal(f.captures.length,0);assert.equal(f.saved.length,0);
 let cancelled=false;f=setup([row(1),row(2)],{cancelled:async()=>cancelled,capture:async()=>{cancelled=true;return sku();}});result=await f.run();
 assert.equal(result.status,'cancelled');assert.equal(result.completed,1);assert.equal(result.remaining,1);assert.equal(f.captures.length,1);assert.deepEqual(f.saved.map(r=>r.observation_id),[1]);
});

test('missing entries and unsupported product layouts do not prevent later products',async()=>{
 for(const reason of ['sku_entry_unavailable','sku_layout_unrecognized','sku_catalog_incomplete']){
  const f=setup([row(1),row(2),row(3),row(4),row(5)],{capture:async request=>{if(request.observation.observation_id<5)throw new ReviewError('SYNTHETIC unreadable','manual_review',reason);return sku();}}),result=await f.run();
  assert.equal(result.status,'partial');assert.equal(result.failed,4);assert.equal(result.completed,1);assert.equal(result.remaining,0);assert.equal(f.captures.length,5);
  assert.deepEqual(f.saved.map(r=>r.observation_id),[5]);assert.ok(result.items.slice(0,4).every(item=>item.reason===reason&&!item.capture_file));
 }
});

test('empty fields stay absent and neither block the next product nor borrow an image',async()=>{
 const f=setup([row(1),row(2)],{capture:async request=>request.observation.observation_id===1?{status:'complete',variants:[{image_url:null,price_raw:null,selection_verified:true}]}:sku()}),result=await f.run();
 assert.equal(result.completed,2);assert.deepEqual(f.saved[0].images,{});assert.equal(f.saved[0].sku.variants[0].price_raw,null);assert.deepEqual(Object.keys(f.saved[1].images),[image]);
});

test('local storage failure and unknown program errors stop, without losing prior receipts',async()=>{
 const f=setup([row(1),row(2),row(3)]),save=f.options.saveItem;
 f.options.saveItem=async item=>{if(item.observation_id===2)throw new Error('SYNTHETIC disk full');return save(item);};
 const result=await f.run();assert.equal(result.status,'manual_review');assert.equal(result.completed,1);assert.equal(result.failed,1);assert.equal(result.remaining,1);
 assert.deepEqual(f.captures.map(r=>r.observation.observation_id),[1,2]);assert.deepEqual(f.saved.map(r=>r.observation_id),[1]);assert.equal(result.items[0].capture_file,'sku_1.json');
});
