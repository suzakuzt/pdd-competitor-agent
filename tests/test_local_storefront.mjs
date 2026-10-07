import test from 'node:test';
import assert from 'node:assert/strict';
import {waitForStorefront,verifyStoreIdentity,readStorefrontState} from '../scripts/local_storefront.mjs';

const sourceUrl='https://mobile.yangkeduo.com/mall_page.html?mall_id=123';
const ready={url:sourceUrl,shopVerified:true,cards:20,latestPoint:{x:60,y:150},latestSelected:true};
function browserFor(states,{login=false,challenge=false}={}){
 let index=0,reads=0;
 return {get reads(){return reads;},async evaluate(fn){
  if(fn.name==='readPageGuard')return {url:sourceUrl,text:'SYNTHETIC_ONLY',login,challenge};
  assert.equal(fn,readStorefrontState);reads++;
  return states[Math.min(index++,states.length-1)];
 }};
}
const options={sourceUrl,shopName:'SYNTHETIC_ONLY',sleep:async()=>{},maxPolls:3};
test('new share tracking parameters preserve original canonical identity',()=>{
 assert.equal(verifyStoreIdentity(sourceUrl,sourceUrl+'&ps=FRESH'),true);
 assert.throws(()=>verifyStoreIdentity(sourceUrl,sourceUrl.replace('123','124')),e=>e.status==='needs_url'&&e.reason==='identity_mismatch');
 assert.throws(()=>verifyStoreIdentity(sourceUrl,'https://example.com/mall_page.html?mall_id=123'),e=>e.reason==='identity_mismatch');
 assert.throws(()=>verifyStoreIdentity(sourceUrl,'https://mobile.yangkeduo.com/mall_page.html?ps=FRESH'),e=>e.reason==='entry_unavailable');
});
test('short URL redirect and incomplete hydration are awaited before a valid list is accepted',async()=>{
 const browser=browserFor([{...ready,url:'https://mobile.yangkeduo.com/mall_page.html?ps=FRESH',shopVerified:false,cards:0,latestPoint:null},ready]);
 assert.deepEqual(await waitForStorefront(browser,options),ready);assert.equal(browser.reads,2);
});
test('zero products cannot become a successful empty update',async()=>{
 const browser=browserFor([{...ready,cards:0,empty:true}]);
 await assert.rejects(waitForStorefront(browser,options),e=>e.status==='needs_url'&&e.reason==='zero_products');
 assert.equal(browser.reads,3);
});
test('an absent shop name is a link recovery request, not a made-up login diagnosis',async()=>{
 await assert.rejects(waitForStorefront(browserFor([{...ready,shopVerified:false,cards:0}]),options),e=>e.status==='needs_url'&&e.reason==='entry_unavailable');
});
test('a same-named page for another shop cannot be bound to existing history',async()=>{
 await assert.rejects(waitForStorefront(browserFor([{...ready,url:sourceUrl.replace('123','456')}]),options),e=>e.reason==='identity_mismatch');
});
test('visible login and challenge require separate recovery actions',async()=>{
 const login=browserFor([ready],{login:true});
 await assert.rejects(waitForStorefront(login,options),e=>e.status==='needs_login'&&e.reason==='login_required');
 assert.equal(login.reads,0);
 const challenge=browserFor([ready],{login:true,challenge:true});
 await assert.rejects(waitForStorefront(challenge,options),e=>e.status==='manual_review'&&e.reason==='access_restricted');
 assert.equal(challenge.reads,0);
});
test('a sorting click is not counted as selected until page evidence confirms it',async()=>{
 const browser=browserFor([{...ready,latestSelected:false},ready]);
 assert.deepEqual(await waitForStorefront(browser,{...options,requireSelected:true}),ready);
 assert.equal(browser.reads,2);
 await assert.rejects(waitForStorefront(browserFor([{...ready,latestSelected:false}]),{...options,requireSelected:true}),e=>e.reason==='sort_unverified');
});
test('cancellation stops before any DOM read',async()=>{
 const browser=browserFor([ready]);
 await assert.rejects(waitForStorefront(browser,{...options,cancelled:async()=>true}),e=>e.status==='cancelled');
 assert.equal(browser.reads,0);
});
test('transport failure is not misreported as an expired link',async()=>{
 const error=new Error('SYNTHETIC transport failure');
 await assert.rejects(waitForStorefront({evaluate:async()=>{throw error;}},options),e=>e===error&&!e.reason);
});
