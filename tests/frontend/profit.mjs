// Portable synthetic model assertions; historical production counts are intentionally outside this suite.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {blankTrial,trialPayload,filterOpportunities,opportunityDrilldown,profitEvidence,profitScope,profitReviewedSource,previewProfit,saveProfit,readProfitStatus,profitSaveCompletion,profitReloadUrl,amountLabel,trialExport} from '../../dashboard/src/content/dashboard/profit-model.js';
const here=path.dirname(fileURLToPath(import.meta.url)),checks=[];
async function check(name,fn){const detail=await fn();checks.push({name,status:'passed',detail:detail??null});}
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const anchor={opportunity_id:'observation:10',observation_id:10,shop_id:'shop-a',run_id:'run-a',title:'原卡标题',price_raw:'券后¥4.50',sales_value:20,priority_order:3,priority_key:'eligible_reference',view_order:5};
const draft=blankTrial(anchor,'00000000-0000-4000-8000-000000000001');
await check('opportunity_creates_numeric_anchor_with_no_own_price_cost_or_budget_defaults',()=>{
 assert.equal(draft.observation_id,10);assert.equal(draft.shop_id,'shop-a');assert.equal(draft.revision,0);assert.equal(draft.status,'draft');
 assert.ok(Object.values(draft.plan).every(value=>value===null));assert.ok(Object.values(draft.actual).every(value=>value===null));assert.ok(Object.values(draft.checks).every(value=>value===null));
 for(const invalid of ['10',true,0,-1,1.5,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>blankTrial({...anchor,observation_id:invalid},draft.trial_id),/正整数/);
 assert.equal(blankTrial({...anchor,observation_id:null},draft.trial_id).observation_id,null);
});
await check('payload_preserves_null_explicit_zero_decimal_strings_counts_and_tristate',()=>{
 const input={...draft,channel:'  本人渠道  ',own_product_ref:'A-1',plan:{...draft.plan,price:'12.50',goods_cost:'0',platform_fee_rate_pct:'0.6',target_orders:'20',shipping_cost:'  '},actual:{...draft.actual,net_receipts:'0',paid_orders:'0',period_start:'2026-10-01',period_end:'2026-10-04',settlement_complete:false,evidence_reference:'结算表'},checks:{supply_confirmed:false,rights_confirmed:true,spec_confirmed:null},analysis:{must_not_save:true},created_at:'metadata',input_source:'metadata'},before=JSON.stringify(input),payload=trialPayload(input);
 assert.equal(payload.channel,'本人渠道');assert.equal(payload.plan.price,'12.50');assert.equal(payload.plan.goods_cost,'0');assert.equal(payload.plan.shipping_cost,null);assert.equal(payload.plan.platform_fee_rate_pct,'0.6');assert.equal(payload.plan.target_orders,20);assert.equal(payload.actual.paid_orders,0);assert.equal(payload.actual.net_receipts,'0');assert.equal(payload.actual.settlement_complete,false);assert.deepEqual(payload.checks,input.checks);assert.equal(payload.actual.period_start,'2026-10-01');assert.equal(payload.actual.evidence_reference,'结算表');assert.equal(payload.observation_id,10);assert.ok(!Object.hasOwn(payload,'analysis')&&!Object.hasOwn(payload,'created_at'));assert.equal(JSON.stringify(input),before);
 for(const invalid of ['-1','1.5','1e2','not a number'])assert.throws(()=>trialPayload({...draft,plan:{target_orders:invalid}}),/非负整数/);
 assert.equal(amountLabel(null),'未知');assert.equal(amountLabel('0'),'0');assert.equal(amountLabel('-2.50'),'-2.50');
});
await check('filters_are_current_shop_independent_original_cards_and_do_not_mutate',()=>{
 const rows=[{...anchor,observation_id:11,opportunity_id:'observation:11',priority_order:4,priority_key:'watch',view_order:2},anchor,{...anchor,shop_id:'shop-b',observation_id:99}],before=JSON.stringify(rows);
 assert.equal(filterOpportunities(rows,'shop-a',{search:'',priority:'all'}).length,2);assert.equal(filterOpportunities(rows,'shop-a',{search:'原卡',priority:'eligible_reference'})[0],anchor);assert.equal(filterOpportunities(rows,'shop-a',{search:'5',priority:'all'})[0],anchor);assert.equal(JSON.stringify(rows),before);
 const request=opportunityDrilldown(anchor);assert.deepEqual(request.observation_ids,[10]);assert.equal(request.run_id,'run-a');assert.equal(request.shop_id,'shop-a');
});
await check('evidence_scope_excludes_other_shop_and_preserves_old_new_growth_cards',()=>{
 const chosen={...anchor,evidence_observation_ids:[10,9,99],comparison_item_ids:['c-a','c-b'],new_arrival_item_ids:['n-a','n-b']};
 const queries={runs:{rows:[{shop_id:'shop-a',run_id:'run-a'},{shop_id:'shop-a',run_id:'run-old'},{shop_id:'shop-b',run_id:'run-b'}]},observations:{rows:[{observation_id:10,run_id:'run-a'},{observation_id:9,run_id:'run-old'},{observation_id:99,run_id:'run-b'}]},comparison_items:{rows:[{comparison_item_id:'c-a',target_run_id:'run-a'},{comparison_item_id:'c-b',target_run_id:'run-b'}]},new_arrival_items:{rows:[{arrival_item_id:'n-a',shop_id:'shop-a'},{arrival_item_id:'n-b',shop_id:'shop-b'}]}};
 const evidence=profitEvidence(queries,[chosen],'shop-a');assert.deepEqual(evidence.observations.map(row=>row.observation_id),[10,9]);assert.equal(evidence.comparison_items.length,1);assert.equal(evidence.new_arrival_items.length,1);
});
await check('trials_scope_excludes_global_run_and_source_resolver_preserves_reviewed_sql_methods',()=>{
 const query={rows:[draft],source:{query:{sql:'SELECT reviewed_user_trials'}},methods:[{name:'backend Decimal evaluation'}]},scope=profitScope('shop-a'),component={queryId:'profit_trials',queryIds:['profit_trials'],sourceRows:[draft],scopeFilters:scope};
 assert.ok(!scope.some(filter=>filter.field==='run_id'));assert.ok(scope.every(filter=>Object.hasOwn(draft,filter.field)));const resolved=profitReviewedSource({profit_trials:query},component);assert.equal(resolved.query,query);assert.equal(resolved.rows,component.sourceRows);assert.equal(resolved.filters,scope);assert.equal(profitReviewedSource({profit_trials:query},component,'other'),null);
 const exported=JSON.parse(trialExport({...draft,analysis:{plan_result:{contribution_per_order:null}}}));assert.equal(exported.trial.observation_id,10);assert.equal(exported.trial.analysis.plan_result.contribution_per_order,null);assert.match(exported.scope,/已保存/);
});
await check('trial_source_history_files_only_include_current_component_trial_ids_without_mutation',()=>{
 const files={'C:\\state\\trials\\trial-a\\000001.json':'sha-a1','C:/state/trials/trial-a/000002.json':'sha-a2','C:/state/trials/trial-b/000001.json':'sha-b1','C:/state/trials/trial-a-other/000001.json':'sha-other'},query={rows:[],source:{files,query:{sql:'reviewed'},provider:'user input'},methods:[{name:'version history'}]},queries={profit_trials:query,profit_trial_summary:query},component={queryId:'profit_trials',queryIds:['profit_trials','profit_trial_summary'],sourceRowsByQuery:{profit_trials:[{trial_id:'trial-a',shop_id:'shop-a'}],profit_trial_summary:[{shop_id:'shop-a'}]},scopeFilters:profitScope('shop-a')},before=JSON.stringify(queries);
 for(const queryId of component.queryIds){const result=profitReviewedSource(queries,component,queryId);assert.deepEqual(Object.values(result.query.source.files),['sha-a1','sha-a2']);assert.equal(result.query.methods,query.methods);assert.equal(result.query.source.query,query.source.query);assert.equal(result.rows,component.sourceRowsByQuery[queryId]);}
 const empty=profitReviewedSource(queries,{...component,sourceRowsByQuery:{profit_trials:[],profit_trial_summary:[]}},'profit_trial_summary');assert.deepEqual(empty.query.source.files,{});assert.equal(JSON.stringify(queries),before);
});
await check('preview_posts_only_raw_user_trial_and_uses_backend_analysis_unchanged',async()=>{
 const calls=[],analysis={plan_result:{contribution_per_order:'-1.25',max_ad_after_fixed_per_order:'-2.00'},actual_result:{actual_profit:null},user_input_only:true};
 const result=await previewProfit(async(url,options)=>{calls.push({url,options});return response({status:'ok',analysis});},draft);assert.equal(result,analysis);assert.equal(calls.length,1);assert.equal(calls[0].url,'/__pdd_profit_preview');assert.equal(calls[0].options.method,'POST');assert.equal(calls[0].options.headers['Content-Type'],'application/json');assert.deepEqual(JSON.parse(calls[0].options.body),{trial:trialPayload(draft)});
 await assert.rejects(()=>previewProfit(async()=>response({status:'ok'}),draft),/有效计算/);await assert.rejects(()=>previewProfit(async()=>({ok:true,status:200,json:async()=>{throw Error('html');}}),draft),/未连接/);
});
await check('save_uses_expected_revision_and_preserves_draft_on_validation_or_conflict',async()=>{
 const existing={...draft,revision:4},before=JSON.stringify(existing);let request;const result=await saveProfit(async(url,options)=>{request={url,options};return response({status:'running'},202);},existing);assert.equal(result.status,'running');assert.equal(request.url,'/__pdd_profit_save');assert.equal(JSON.parse(request.options.body).expected_revision,4);assert.equal(JSON.parse(request.options.body).trial.observation_id,10);
 await assert.rejects(()=>saveProfit(async()=>response({message:'版本冲突'},409),existing),/版本冲突/);await assert.rejects(()=>saveProfit(async()=>response({message:'锚点无效'},422),existing),/锚点无效/);assert.equal(JSON.stringify(existing),before);
});
await check('reload_only_explicit_save_with_canonical_build_receipt_and_keeps_current_shop_tab',async()=>{
 const success={status:'succeeded',receipt:{dashboard_built:true}};assert.equal(profitSaveCompletion(success,false).action,'none');assert.equal(profitSaveCompletion(success,true).action,'reload');assert.equal(profitSaveCompletion({status:'succeeded'},true).action,'error');assert.equal(profitSaveCompletion({status:'failed',message:'构建失败'},true).action,'error');assert.equal(profitSaveCompletion({status:'idle'},true).action,'error');assert.equal(profitSaveCompletion({status:'running'},true).action,'wait');
 const url=new URL(profitReloadUrl('http://127.0.0.1:8878/?other=1#evidence','shop-b'));assert.equal(url.origin,'http://127.0.0.1:8878');assert.equal(url.searchParams.get('pdd_view'),'profit');assert.equal(url.searchParams.get('pdd_shop'),'shop-b');assert.equal(url.searchParams.get('other'),'1');assert.equal(url.hash,'#evidence');
 const state=await readProfitStatus(async(url,options)=>{assert.equal(url,'/__pdd_profit_status');assert.equal(options.cache,'no-store');return response(success);});assert.equal(state,success);await assert.rejects(()=>readProfitStatus(async()=>response({status:'unexpected'})),/无法识别/);
});

export const acceptance={suite:"profit",passed:checks.length,total:checks.length,checks:checks};
