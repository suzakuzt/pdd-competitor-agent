import test from 'node:test';
import assert from 'node:assert/strict';
import {shopSelectionOptions,resolveIntakeSelection,mergeShopDirectory,targetDataHref,selectionHref,selectionOptionValue} from '../src/content/dashboard/shop-intake-model.js';

const shop=(shop_id,shop_name)=>({shop_id,shop_name});
const target=(target_id,patch={})=>({target_id,display_name:'合成待核验店',shop_id:null,status:'needs_identity',...patch});
const run=(shop_id,patch={})=>({run_id:`run-${shop_id}`,shop_id,status:'complete',end_boundary_observed:true,observed_to_epoch:1,...patch});
const queries=(targets=[],runs=[])=>({competitor_targets:{rows:targets},runs:{rows:runs}});
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};

test('an empty dashboard exposes each registered target without requiring an observed shop',()=>{
 const data=freeze(queries([
  target('share-1',{display_name:'第一家待核验店'}),
  target('stable-2',{display_name:'第二家待采集店',shop_id:'shop-2',status:'pending_capture'}),
  target('name-3',{display_name:'第三家待核验店'}),
 ]));
 assert.deepEqual(shopSelectionOptions(data,[]),{
  choices:['target:share-1','target:stable-2','target:name-3'],
  choiceLabels:{'target:share-1':'第一家待核验店 · 待核验','target:stable-2':'第二家待采集店 · 待采集','target:name-3':'第三家待核验店 · 待核验'},
 });
 assert.deepEqual(shopSelectionOptions({},[]),{choices:[],choiceLabels:{}});
});

test('observed shop values and ordering remain unchanged while partial and complete registrations do not duplicate them',()=>{
 const shops=freeze([shop('B','第二店'),shop('A','第一店')]);
 const data=freeze(queries([
  target('target-A',{shop_id:'A',status:'observed'}),
  target('target-B',{shop_id:'B',status:'observed'}),
  target('new',{display_name:'第三店',shop_id:'C',status:'pending_capture'}),
 ],[run('A'),run('B',{status:'partial',end_boundary_observed:false})]));
 assert.deepEqual(shopSelectionOptions(data,shops),{choices:['B','A','target:new'],choiceLabels:{B:'第二店',A:'第一店','target:new':'第三店 · 待采集'}});
});

test('same display names never bind independent pending targets to another shop with data',()=>{
 const shops=[shop('A','同名店')],data=queries([
  target('unresolved',{display_name:'同名店'}),
  target('stable',{display_name:'同名店',shop_id:'B',status:'pending_capture'}),
 ],[run('A')]);
 assert.deepEqual(shopSelectionOptions(data,shops).choices,['A','target:unresolved','target:stable']);
 for(const targetId of ['unresolved','stable']){
  const selection=resolveIntakeSelection(data,{targetId,shopId:'A'},shops);
  assert.equal(selection.canAnalyze,false);assert.equal(selection.shopId,'');assert.equal(selection.targetId,targetId);assert.equal(selection.shop,null);
 }
});

test('identity conflicts remain explicit target choices even when the same shop ID has historical observations',()=>{
 const shops=[shop('A','已采历史店')],conflict=target('collision',{display_name:'冲突登记',shop_id:'A',status:'observed',identity_conflict:true});
 const data=queries([conflict],[run('A')]),options=shopSelectionOptions(data,shops);
 assert.deepEqual(options,{choices:['A','target:collision'],choiceLabels:{A:'已采历史店','target:collision':'冲突登记 · 身份冲突'}});
 const selection=resolveIntakeSelection(data,{targetId:'collision',shopId:'A'},shops);
 assert.equal(selection.target,conflict);assert.equal(selection.canAnalyze,false);assert.equal(selection.shopId,'');assert.equal(selection.state,'needs_identity');
});

test('declared observed status alone cannot hide a registration when it has no scoped data',()=>{
 const data=queries([target('no-data',{shop_id:'B',status:'observed',display_name:'尚无记录店'})],[run('A')]);
 const options=shopSelectionOptions(data,[shop('A','有记录店')]);
 assert.equal(options.choiceLabels['target:no-data'],'尚无记录店 · 待采集');
 const selection=resolveIntakeSelection(data,{targetId:'no-data'},[shop('A','有记录店')]);
 assert.equal(selection.hasData,false);assert.equal(selection.canAnalyze,false);assert.equal(selection.shopId,'');
});

test('a target missing from available shop rows stays accessible and cannot display a different shop',()=>{
 const data=queries([target('unavailable',{shop_id:'B',status:'observed'})],[run('A'),run('B')]);
 assert.deepEqual(shopSelectionOptions(data,[shop('A','第一店')]).choices,['A','target:unavailable']);
 const selection=resolveIntakeSelection(data,{targetId:'unavailable'},[shop('A','第一店')]);
 assert.equal(selection.canAnalyze,false);assert.equal(selection.shopId,'');assert.equal(selection.shop,null);
});

test('unknown explicit targets never fall back to the first shop or the requested historical shop',()=>{
 const shops=[shop('A','第一店'),shop('B','第二店')],data=queries([target('known')],[run('A'),run('B')]);
 const selection=resolveIntakeSelection(data,{targetId:'missing',shopId:'B'},shops);
 assert.deepEqual(selection,{shopId:'',shop:null,target:null,targetId:'missing',state:'unknown',canAnalyze:false});
 assert.ok(!shopSelectionOptions(data,shops).choices.includes('target:missing'));
});

test('label fallbacks and repeated target rows do not create duplicate or empty-valued choices',()=>{
 const data=queries([target('named',{display_name:'',observed_shop_name:'已核验显示名'}),target('named'),target('id-only',{display_name:''}),target('')]);
 assert.deepEqual(shopSelectionOptions(data,[]),{choices:['target:named','target:id-only'],choiceLabels:{'target:named':'已核验显示名 · 待核验','target:id-only':'id-only · 待核验'}});
});

test('a live identified shop appears before snapshot data, with share aliases deduplicated and no foreign rows',()=>{
 const snapshot=freeze(queries([target('old',{shop_id:'A',status:'observed'})],[run('A')]));
 const ready={status:'ready',target_id:'share',resolved_target_id:'stable',shop_id:'B'};
 const directory={targets:[target('stable',{shop_id:'B',status:'pending_capture',display_name:'新识别店',onboarding:ready}),target('share',{shop_id:'B',status:'pending_capture',display_name:'新识别店',onboarding:ready})]};
 const merged=mergeShopDirectory(snapshot,directory),shops=[shop('A','当前店')],options=shopSelectionOptions(merged,shops);
 assert.deepEqual(options.choices,['A','target:stable']);assert.equal(options.choiceLabels['target:stable'],'新识别店 · 待采集');
 const selection=resolveIntakeSelection(merged,{targetId:'share'},shops);
 assert.equal(selection.canAnalyze,false);assert.equal(selection.shop,null);assert.equal(selection.shopId,'');
 assert.equal(selectionOptionValue(selection,options,merged),'target:stable');
 assert.equal(snapshot.competitor_targets.rows.length,1);assert.equal(merged.runs,snapshot.runs);
 assert.equal(resolveIntakeSelection(merged,{shopId:'B'},shops).target.shop_id,'B');
});

test('published live data missing from snapshot gives a same-shop load link without inventing observations',()=>{
 const shopId='shop_'+'2'.repeat(24),otherShop='shop_'+'3'.repeat(24);
 const newTarget=target('new',{shop_id:shopId,status:'observed',latest_collection:{shop_id:shopId,status:'complete',dashboard_built:true}});
 const merged=mergeShopDirectory(queries([], [run(otherShop)]),{targets:[newTarget]});
 const selection=resolveIntakeSelection(merged,{targetId:'new'},[shop(otherShop,'旧店')]);
 assert.equal(selection.state,'awaiting_snapshot');assert.equal(selection.hasData,false);assert.equal(selection.canAnalyze,false);
 const current='http://localhost:8878/?pdd_shop='+otherShop+'&pdd_target=stale&f.run_id=old&view=1#here';
 const href=new URL(targetDataHref(newTarget,current),current);
 assert.equal(href.searchParams.get('pdd_shop'),shopId);assert.equal(href.searchParams.has('pdd_target'),false);assert.equal(href.searchParams.has('f.run_id'),false);
 assert.equal(href.searchParams.get('view'),'1');assert.equal(href.hash,'#here');
 assert.equal(targetDataHref({...newTarget,latest_collection:{...newTarget.latest_collection,shop_id:otherShop}},current),null);
 assert.equal(targetDataHref({...newTarget,identity_conflict:true},current),null);
 assert.equal(targetDataHref({...newTarget,latest_collection:{...newTarget.latest_collection,dashboard_built:false}},current),null);
});

test('explicit shop or target selection keeps URL scope exclusive and removes an old run filter',()=>{
 const original='http://localhost:8878/?pdd_shop=A&pdd_target=old&f.run_id=old-run&view=1&tab=dashboard#products';
 for(const [selection,key,value,absent] of [[{shopId:'B'},'pdd_shop','B','pdd_target'],[{targetId:'new'},'pdd_target','new','pdd_shop']]){
  const result=new URL(selectionHref(original,selection),original);
  assert.equal(result.searchParams.get(key),value);assert.equal(result.searchParams.has(absent),false);assert.equal(result.searchParams.has('f.run_id'),false);
  assert.equal(result.searchParams.get('view'),'1');assert.equal(result.searchParams.get('tab'),'dashboard');assert.equal(result.hash,'#products');
 }
});
