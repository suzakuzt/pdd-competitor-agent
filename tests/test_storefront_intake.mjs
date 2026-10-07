import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {intakeStoreIdentity,readStorefrontIntake,verifyStorefrontIntake} from '../scripts/storefront_intake.mjs';

const share='https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_SHARE';
const canonical='https://mobile.yangkeduo.com/mall_page.html?mall_sn=SYNTHETIC_STORE';
const name='SYNTHETIC SHOP';
const markup='<li class="optItem_kjR9AF_6 current_xHRVWZm0">上新</li>';
const ready={url:canonical,title:name,header:name,headerCount:1,listCount:1,sortCount:1,latestCount:1,
 latestPoint:{x:30,y:180},latestSelected:false,selectedMarkup:'',login:false,challenge:false};
const selected={...ready,latestSelected:true,selectedMarkup:markup};
const options={userShareUrl:share,maxPolls:4,sleep:async()=>{}};
function browserFor(states,{guardState={}}={}){
 let index=0;const events=[];
 return {events,async goto(url){events.push({goto:url});},async click(point){events.push({click:point});},
  async evaluate(fn){
   if(fn.name==='readPageGuard')return {url:canonical,text:name,login:false,challenge:false,...guardState};
   assert.equal(fn,readStorefrontIntake);events.push('read');return states[Math.min(index++,states.length-1)];
  }};
}
function rejects(reason){return error=>error.reason===reason;}

test('opaque share is navigated and a native sorting click is followed by fresh selected evidence',async()=>{
 const browser=browserFor([ready,ready,selected]);
 const result=await verifyStorefrontIntake(browser,options);
 assert.deepEqual({...result,observedAt:null},{userShareUrl:share,shopName:name,sourceUrl:canonical,header:name,selectedMarkup:markup,observedAt:null});
 assert.ok(Number.isFinite(Date.parse(result.observedAt)));
 assert.deepEqual(browser.events,[{goto:share},'read',{click:ready.latestPoint},'read','read']);
});
test('an already selected control is still natively clicked and independently reread',async()=>{
 const browser=browserFor([selected]);await verifyStorefrontIntake(browser,options);
 assert.deepEqual(browser.events,[{goto:share},'read',{click:ready.latestPoint},'read']);
});
test('same typed identity tolerates ordinary tracking changes, without deriving another identity type',async()=>{
 const url=canonical+'&refer_page_name=login';const browser=browserFor([{...ready,url},{...selected,url}]);
 assert.equal((await verifyStorefrontIntake(browser,{...options,userShareUrl:canonical})).sourceUrl,url);
 for(const actual of [canonical.replace('SYNTHETIC_STORE','OTHER_STORE'),'https://mobile.yangkeduo.com/mall_page.html?mall_id=123']){
  await assert.rejects(verifyStorefrontIntake(browserFor([{...ready,url:actual}]),{...options,userShareUrl:canonical}),rejects('identity_mismatch'));
 }
});
test('a pending original ps page can hydrate, but an unrelated opaque page cannot become evidence',async()=>{
 const pending={...ready,url:share,header:'',headerCount:0,listCount:0,latestPoint:null};
 assert.equal((await verifyStorefrontIntake(browserFor([pending,ready,selected]),options)).sourceUrl,canonical);
 await assert.rejects(verifyStorefrontIntake(browserFor([{...pending,url:share+'OTHER'}]),options),rejects('identity_unverified'));
});
test('source changes after the sort click and a same-named different storefront are rejected',async()=>{
 for(const changed of [{...selected,url:canonical.replace('SYNTHETIC_STORE','OTHER')},{...selected,header:'OTHER SHOP',title:'OTHER SHOP'}]){
  await assert.rejects(verifyStorefrontIntake(browserFor([ready,changed]),options),rejects('identity_mismatch'));
 }
});
test('a guessed body name, mismatched title or absent storefront list never supplies an intake',async()=>{
 for(const patch of [{headerCount:0,header:''},{title:'拼多多'},{listCount:0},{sortCount:0},{latestCount:0},{header:'SHOP\nSECOND'}]){
  const browser=browserFor([{...selected,...patch}]);
  await assert.rejects(verifyStorefrontIntake(browser,options),rejects('intake_unverified'));
  assert.equal(browser.events.filter(e=>e.click).length,0);
 }
});
test('multiple visible names, lists or sort controls are not chosen arbitrarily',async()=>{
 for(const field of ['headerCount','listCount','sortCount','latestCount']){
  const browser=browserFor([{...ready,[field]:2}]);
  await assert.rejects(verifyStorefrontIntake(browser,options),rejects('intake_ambiguous'));
  assert.equal(browser.events.filter(e=>e.click).length,0);
 }
});
test('a click without an actual selected marker cannot complete',async()=>{
 await assert.rejects(verifyStorefrontIntake(browserFor([ready]),options),rejects('sort_unverified'));
 await assert.rejects(verifyStorefrontIntake(browserFor([ready,{...selected,selectedMarkup:''}]),options),rejects('sort_unverified'));
});
test('login, challenge, wrong origin and public product pages stop without an identity receipt',async()=>{
 for(const [patch,reason] of [[{login:true},'login_required'],[{challenge:true,login:true},'access_restricted']]){
  await assert.rejects(verifyStorefrontIntake(browserFor([ready],{guardState:patch}),options),rejects(reason));
  await assert.rejects(verifyStorefrontIntake(browserFor([{...ready,...patch}]),options),rejects(reason));
 }
 for(const url of ['https://other.invalid/mall_page.html?mall_sn=SYNTHETIC_STORE','https://mobile.yangkeduo.com/goods.html?goods_id=123']){
  await assert.rejects(verifyStorefrontIntake(browserFor([{...ready,url}]),options),rejects('source_changed'));
 }
});
test('cancellation before navigation and after the native click preserves a stopped result',async()=>{
 const before=browserFor([ready]);
 await assert.rejects(verifyStorefrontIntake(before,{...options,cancelled:async()=>true}),rejects('operator_cancelled'));
 assert.deepEqual(before.events,[]);
 const after=browserFor([ready,selected]);
 await assert.rejects(verifyStorefrontIntake(after,{...options,cancelled:async()=>after.events.some(e=>e.click)}),rejects('operator_cancelled'));
 assert.equal(after.events.filter(e=>e==='read').length,1);
});
test('ambiguous identifiers, unsafe scheme, credentials and fragment are refused before navigation',async()=>{
 for(const url of [canonical+'&mall_sn=SYNTHETIC_STORE',canonical+'&mall_id=123',canonical+'#x',canonical.replace('https:','http:'),canonical.replace('https://','https://user@'),share+'&other=x','https://mobile.yangkeduo.com/mall_page.html?mall_id=0']){
  const browser=browserFor([ready]);await assert.rejects(verifyStorefrontIntake(browser,{...options,userShareUrl:url}));assert.deepEqual(browser.events,[]);
 }
 assert.deepEqual(intakeStoreIdentity('https://mobile.yangkeduo.com/mall_page.html?mall_id=123'),{kind:'mall_id',value:'123'});
 assert.equal(intakeStoreIdentity(share,{allowShare:true}),null);
});
test('transport errors retain their original cause instead of fabricated identity completion',async()=>{
 const failure=new Error('SYNTHETIC transport');const browser=browserFor([ready]);browser.click=async()=>{throw failure;};
 await assert.rejects(verifyStorefrontIntake(browser,options),error=>error===failure);
});

function element(text,{className='',hidden=false,aria=null,html='',children=[]}={}){
 return {innerText:text,className,outerHTML:html,hidden,getAttribute:key=>key==='aria-selected'?aria:null,
  getBoundingClientRect:()=>({x:10,y:20,left:10,right:150,top:20,bottom:50,width:140,height:30}),querySelectorAll:()=>children};
}
function readDOM({headers=[element(name)],sorts,lists=[element('')]}={}){
 const latest=element('上新',{className:'optItem_kjR9AF_6 current_xHRVWZm0',html:markup});
 const sort=element('',{children:[element('默认'),element('销量'),latest,element('价格')]});
 return vm.runInNewContext(`(${readStorefrontIntake.toString()})()`,{
  document:{title:name,body:{innerText:name},querySelectorAll:selector=>selector.startsWith('[class')?headers:selector==='#rc-opt-list'?(sorts||[sort]):lists},
  location:{href:canonical},innerHeight:800,innerWidth:600,getComputedStyle:e=>({display:e.hidden?'none':'block',visibility:'visible'})});
}
test('actual DOM reader uses the observed class prefix and unique visible selected li markup',()=>{
 const result=readDOM({headers:[element('HIDDEN OTHER',{hidden:true}),element(name)]});
 assert.equal(result.header,name);assert.equal(result.headerCount,1);assert.equal(result.latestSelected,true);assert.equal(result.selectedMarkup,markup);
 assert.equal(readDOM({headers:[element(name),element(name)]}).headerCount,2);
 assert.equal(readDOM({headers:[element('HIDDEN',{hidden:true})]}).header,'');
});
test('DOM reader rejects conflicting current markers and ignores hidden selected sort remnants',()=>{
 const latest=element('上新',{className:'current_xHRVWZm0',html:markup});
 const conflict=element('',{children:[element('默认',{aria:'true'}),latest]});
 assert.equal(readDOM({sorts:[conflict]}).latestSelected,false);assert.equal(readDOM({sorts:[conflict]}).selectedMarkup,'');
 const clean=element('',{children:[element('默认',{aria:'true',hidden:true}),latest]});
 assert.equal(readDOM({sorts:[clean]}).latestSelected,true);
});
