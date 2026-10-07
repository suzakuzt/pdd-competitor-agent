// Deterministic page-state sequences; no Chrome, network or business records.
import test from 'node:test';
import assert from 'node:assert/strict';
import {waitForStableSku} from '../scripts/local_sku_capture.mjs';

const combination=[{name:'款式',value:'A'}];
const state=extra=>({supported:true,option_catalog_verified:true,selection_summary_verified:true,
 groups:[{name:'款式',options:[{label:'A',selected:true},{label:'B',selected:false}]}],
 price_raw:'券后¥4.50',current_price_raw:'券后¥4.50',original_price_raw:'券前¥9.50',
 original_price_evidence:'explicit_before_coupon_price',image_url:'https://img.pddpic.com/SYNTHETIC_A.png',image_pending:false,...extra});
function clock(){let elapsed=0;return {now:()=>elapsed,sleep:async ms=>{elapsed+=ms;}};}

test('unchanged selected price and image finish after two bounded observations',async()=>{
 const time=clock();let reads=0;
 const result=await waitForStableSku(async()=>{reads++;return state();},combination,time);
 assert.equal(reads,2);assert.equal(time.now(),100);assert.equal(result.value_stability_verified,true);
 assert.equal(result.price_raw,'券后¥4.50');assert.equal(result.original_price_raw,'券前¥9.50');
});

test('late price/image change restarts the stable window and retains the actual selected values',async()=>{
 const time=clock();let reads=0;
 const result=await waitForStableSku(async()=>{reads++;return time.now()<100?state():state({price_raw:'券后¥6.50',current_price_raw:'券后¥6.50',original_price_raw:'原价¥11.00',original_price_evidence:'explicit_original_price_label',image_url:'https://img.pddpic.com/SYNTHETIC_NEW.png'});},combination,time);
 assert.equal(reads,3);assert.equal(time.now(),200);assert.equal(result.current_price_raw,'券后¥6.50');
 assert.equal(result.original_price_evidence,'explicit_original_price_label');assert.match(result.image_url,/NEW/);
});

test('a newly selected label cannot prematurely authenticate unchanged previous SKU values',async()=>{
 const time=clock(),previous=state();let reads=0;
 const result=await waitForStableSku(async()=>{reads++;return time.now()<300?state():state({price_raw:'券后¥6.50',current_price_raw:'券后¥6.50',image_url:'https://img.pddpic.com/SYNTHETIC_NEW.png'});},combination,{...time,previousState:previous});
 assert.equal(time.now(),400);assert.equal(reads,5);assert.equal(result.price_raw,'券后¥6.50');assert.match(result.image_url,/NEW/);
});

test('unchanged previous SKU values without refresh evidence remain unknown at the deadline',async()=>{
 const time=clock();
 const result=await waitForStableSku(async()=>state(),combination,{...time,previousState:state(),timeoutMs:300});
 assert.equal(time.now(),300);assert.equal(result.value_stability_verified,false);
 assert.equal(result.price_raw,null);assert.equal(result.original_price_raw,null);assert.equal(result.image_url,null);
});

test('loading selected image is awaited and cannot be replaced with a stale/main image',async()=>{
 const time=clock();
 const result=await waitForStableSku(async()=>state(time.now()<300?{image_pending:true,image_url:null}:{}),combination,time);
 assert.equal(time.now(),400);assert.match(result.image_url,/SYNTHETIC_A/);
});

test('a permanently missing image keeps independently stable prices at the bounded deadline',async()=>{
 const time=clock();
 const result=await waitForStableSku(async()=>state({image_pending:true,image_url:null}),combination,{...time,timeoutMs:300});
 assert.equal(time.now(),300);assert.equal(result.image_url,null);assert.equal(result.price_raw,'券后¥4.50');
 assert.equal(result.original_price_raw,'券前¥9.50');assert.equal(result.value_stability_verified,true);
});

test('stable absent fields stay unknown without waiting the full deadline',async()=>{
 const time=clock();
 const result=await waitForStableSku(async()=>state({price_raw:null,current_price_raw:null,original_price_raw:null,original_price_evidence:null,image_url:null}),combination,time);
 assert.equal(time.now(),100);assert.equal(result.price_raw,null);assert.equal(result.image_url,null);
});

test('continuously changing prices/images are not attributed on timeout',async()=>{
 const time=clock();
 const result=await waitForStableSku(async()=>state({price_raw:`¥${time.now()+1}`,current_price_raw:`¥${time.now()+1}`}),combination,{...time,timeoutMs:300});
 assert.equal(time.now(),300);assert.equal(result.value_stability_verified,false);
 for(const name of ['price_raw','current_price_raw','original_price_raw','original_price_evidence','image_url'])assert.equal(result[name],null,name);
});

test('selection summary must catch up and ambiguous multi-selection is rejected',async()=>{
 const time=clock();
 const result=await waitForStableSku(async()=>state({selection_summary_verified:time.now()>=200}),combination,time);
 assert.equal(time.now(),300);assert.equal(result.selection_summary_verified,true);
 for(const extra of [{selection_summary_verified:false},{groups:[{name:'款式',options:[{label:'A',selected:true},{label:'B',selected:true}]}]}]){
  const other=clock();await assert.rejects(waitForStableSku(async()=>state(extra),combination,{...other,timeoutMs:200}),e=>e.reason==='sku_selection_unverified');
 }
});

test('cancellation and identity errors stop polling without further reads or retries',async()=>{
 const time=clock();let reads=0;
 await assert.rejects(waitForStableSku(async()=>{reads++;return state();},combination,{...time,cancelled:async()=>time.now()>0}),e=>e.reason==='operator_cancelled');
 assert.equal(reads,1);
 const error=Object.assign(new Error('identity changed'),{reason:'sku_identity_changed'});
 reads=0;await assert.rejects(waitForStableSku(async()=>{reads++;throw error;},combination,clock()),e=>e===error);assert.equal(reads,1);
 let cancelled=false;reads=0;
 await assert.rejects(waitForStableSku(async()=>{reads++;cancelled=true;return state();},combination,{...clock(),cancelled:async()=>cancelled}),e=>e.reason==='operator_cancelled');assert.equal(reads,1,'cancellation during a slow read is checked before image collection');
});
