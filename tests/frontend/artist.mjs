// Portable synthetic model assertions; historical production counts are intentionally outside this suite.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {ARTIST_FILTERS,filterArtists,artistSourceScope,artistDrilldown,artistScopeFilters,artistEvidenceFilters,artistReviewedSource} from '../../dashboard/src/content/dashboard/artist-research-model.js';
const here=path.dirname(fileURLToPath(import.meta.url)),checks=[];
function check(name,fn){const detail=fn();checks.push({name,status:'passed',detail:detail??null});}
const records=[
 {artist_id:'a',artist_name:'测试甲',entity_kind:'artist',shop_id:'one',run_id:'old',eligible_card_count:1,reference_card_count:2,observation_ids:[1,2],latest_run_id:'new',latest_card_count:1,latest_observation_ids:[3],source_status:'有核实入口',title_event_clues:[{label:'时装周',count:2}],title_examples:[{title:'原标题测试甲街拍'}]},
 {artist_id:'latest',artist_name:'测试新',entity_kind:'artist',shop_id:'one',run_id:'old',eligible_card_count:0,reference_card_count:0,observation_ids:[],latest_run_id:'new',latest_card_count:1,latest_observation_ids:[4],source_status:'入口待核验'},
 {artist_id:'b',artist_name:'测试乙',entity_kind:'artist',shop_id:'one',run_id:'old',eligible_card_count:2,reference_card_count:3,observation_ids:[5,6,7],source_status:'入口待核验'},
 {artist_id:'a',artist_name:'测试甲',entity_kind:'artist',shop_id:'two',run_id:'other',eligible_card_count:999,reference_card_count:999,observation_ids:[1],source_status:'有核实入口'},
 {artist_id:'esport',artist_name:'电竞称呼',entity_kind:'esports',shop_id:'one',run_id:'old',eligible_card_count:1,reference_card_count:1,observation_ids:[8],source_status:'入口待核验'},
];
const before=JSON.stringify(records);
check('default_scope_shop_type_sort_and_input_immutable',()=>{
 const selected=filterArtists(records,'one',ARTIST_FILTERS);
 assert.deepEqual(selected.map(x=>x.artist_id),['b','a','latest']);assert.equal(JSON.stringify(records),before);
});
check('name_event_and_original_title_search_respect_shop_and_source',()=>{
 assert.deepEqual(filterArtists(records,'one',{...ARTIST_FILTERS,search:'时装周',source:'verified'}).map(x=>x.artist_id),['a']);
 assert.equal(filterArtists(records,'one',{...ARTIST_FILTERS,search:'街拍'}).length,1);
 assert.equal(filterArtists(records,'one',{...ARTIST_FILTERS,search:'测试甲',source:'pending'}).length,0);
 assert.equal(filterArtists(records,'one',{...ARTIST_FILTERS,kind:'esports'}).length,1);
});
check('search_finds_full_title_beyond_first_five_examples_without_cross_shop_leak',()=>{
 const row={...records[0],title_examples:Array.from({length:5},(_,i)=>({title:`参考示例${i+1}`})),title_search_text:'参考示例1 参考示例2 参考示例3 参考示例4 参考示例5 第六张独有关键词'};
 const other={...row,shop_id:'two',artist_id:'other'};
 assert.ok(!row.title_examples.some(card=>card.title.includes('独有关键词')));
 assert.deepEqual(filterArtists([other,row],'one',{...ARTIST_FILTERS,search:'独有关键词'}).map(x=>x.artist_id),['a']);
 assert.equal(filterArtists([row],'one',{...ARTIST_FILTERS,search:'独有关键词',source:'pending'}).length,0);
});
check('search_finds_latest_only_title_and_latest_event_label_with_no_reference_cards',()=>{
 const row={...records[1],title_search_text:'最新轮专有造型词',latest_title_event_clues:[{label:'最新轮杂志发布线索',count:1}]};
 for(const term of ['专有造型词','杂志发布'])assert.deepEqual(filterArtists([row],'one',{...ARTIST_FILTERS,search:term}).map(x=>x.artist_id),['latest']);
 assert.equal(row.reference_card_count,0);assert.equal(row.observation_ids.length,0);
});
check('latest_only_drilldown_zero_reference_and_independent_latest_ids',()=>{
 const row=records[1],ref=artistDrilldown(row),latest=artistDrilldown(row,'latest');
 assert.deepEqual(ref.observation_ids,[]);assert.equal(ref.run_id,'old');assert.deepEqual(latest.observation_ids,[4]);assert.equal(latest.run_id,'new');
 assert.equal(latest.source_id,'latest');assert.notEqual(latest.request_id,ref.request_id);latest.observation_ids.push(999);assert.deepEqual(row.latest_observation_ids,[4]);
});
check('source_evidence_joins_shop_run_and_id_and_preserves_all_card_thresholds',()=>{
 const queries={runs:{rows:[{shop_id:'one',run_id:'old'},{shop_id:'one',run_id:'new'},{shop_id:'two',run_id:'other'}]},observations:{rows:[{run_id:'old',observation_id:1},{run_id:'old',observation_id:2},{run_id:'new',observation_id:3},{run_id:'other',observation_id:1},{run_id:'old',observation_id:999}]}};
 const result=artistSourceScope(queries,'one',[records[0],records[3]]);
 assert.deepEqual(result.observations.map(x=>x.observation_id),[1,2,3]);assert.deepEqual(result.runs.map(x=>x.run_id),['old','new']);
 const drilldown=artistDrilldown(records[0]);assert.deepEqual(drilldown.observation_ids,[1,2]);
 const filters=artistScopeFilters('one',{...ARTIST_FILTERS,search:' 时装周 '});assert.equal(filters.find(x=>x.field==='artist_search').value,'时装周');
});
check('explicit_source_filters_keep_reference_both_runs_and_public_channels_separate',()=>{
 const summary=[{shop_id:'one',run_id:'old'},{shop_id:'two',run_id:'other'}],selected=[records[0],records[1]];
 const reference=artistEvidenceFilters('reference','one',selected,summary),watch=artistEvidenceFilters('watchlist','one',selected,summary,{...ARTIST_FILTERS,search:'时装周'}),channels=artistEvidenceFilters('channels','one',selected,summary);
 assert.equal(reference.find(x=>x.field==='run_id').value,'old');
 assert.equal(watch.find(x=>x.field==='run_id').value,'old；new');assert.equal(watch.find(x=>x.field==='artist_search').value,'时装周');
 assert.ok(!channels.some(x=>['shop_id','run_id','reference_run_id','latest_run_id'].includes(x.field)));
 assert.ok(!JSON.stringify([...reference,...watch]).includes('other'));
});
check('public_source_sidebar_resolver_keeps_query_metadata_and_explicit_rows_without_global_filters',()=>{
 const queries={artist_watchlist:{rows:records,source:{sql:'SELECT reviewed_rows',filters:['original source condition']},methods:[{language:'python',code:'reviewed_transform'}]},observations:{rows:[{observation_id:1},{observation_id:2}]},runs:{rows:[{run_id:'old'},{run_id:'new'}]}};
 const selected=[records[0]],scope=artistEvidenceFilters('watchlist','one',selected,[],ARTIST_FILTERS);
 const component={queryId:'artist_watchlist',queryIds:['artist_watchlist','observations','runs'],sourceRows:selected,sourceRowsByQuery:{artist_watchlist:selected,observations:[queries.observations.rows[0]],runs:queries.runs.rows},scopeFilters:scope};
 for(const queryId of component.queryIds){const resolved=artistReviewedSource(queries,component,queryId);assert.equal(resolved.query,queries[queryId]);assert.equal(resolved.rows,component.sourceRowsByQuery[queryId]);assert.equal(resolved.filters,scope);assert.equal(resolved.filters.find(x=>x.field==='run_id').value,'old；new');}
 assert.equal(artistReviewedSource(queries,component,'unrelated'),null);
 const publicQuery={rows:[{channel_id:'public'}],source:{sql:'SELECT public_channels'}},publicFilters=artistEvidenceFilters('channels','one',selected,[]);
 const publicSource=artistReviewedSource({artist_source_channels:publicQuery},{queryId:'artist_source_channels',sourceRows:publicQuery.rows,scopeFilters:publicFilters});
 assert.equal(publicSource.query,publicQuery);assert.equal(publicSource.rows.length,1);assert.ok(!publicSource.filters.some(x=>['shop_id','run_id'].includes(x.field)));
 return {global_filters_read:false,metadata_preserved:'source SQL, methods, filters and original query objects'};
});

export const acceptance={suite:"artist",passed:checks.length,total:checks.length,checks:checks};
