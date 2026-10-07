// Portable synthetic model assertions; historical production counts are intentionally outside this suite.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {HEAT_QUERY_IDS,HEAT_FILTERS,emptyHeatPreferences,heatStorageKey,loadHeatPreferences,saveHeatPreferences,toggleHeatFollow,updateHeatPersonNote,upsertManualHeatPerson,removeManualHeatPerson,heatPreferenceBackup,filterHeatPeople,heatScopeFilters,heatEvidenceScope,reviewedHeatSource,readHeatData,readHeatStatus,startHeatRefresh,workChangeLabel,heatValueLabel,heatTimeNote,heatRefreshCompletion,heatReloadUrl} from '../../dashboard/src/content/dashboard/heat-watch-model.js';
const here=path.dirname(fileURLToPath(import.meta.url)),checks=[];
async function check(name,fn){const detail=await fn();checks.push({name,status:'passed',detail:detail??null});}
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const fakeQueries=Object.fromEntries(HEAT_QUERY_IDS.map(id=>[id,{rows:[],source:{label:id,sql:'reviewed source'},methods:[{language:'python',code:'reviewed transformation'}]}]));
await check('browser_storage_uses_only_own_app_key_and_read_failure_does_not_write',()=>{
 const calls=[],storage={getItem:key=>{calls.push(['get',key]);throw new Error('denied');},setItem:()=>calls.push(['write'])};
 const result=loadHeatPreferences(storage,'app-test');assert.match(result.error,/失败/);assert.equal(calls.length,1);assert.equal(calls[0][1],heatStorageKey('app-test'));assert.notEqual(heatStorageKey('app-test'),heatStorageKey('other-app'));
 const invalid=loadHeatPreferences({getItem:()=>'{bad json'},'app-test');assert.ok(invalid.error);assert.deepEqual(invalid.preferences,emptyHeatPreferences());
});
await check('follow_name_note_and_storage_backup_are_local_and_source_immutable',()=>{
 const person={person_id:'actor-source-1',name:'来源测试名',associated_works:[{value:321}]},before=JSON.stringify(person),empty=emptyHeatPreferences();
 const followed=toggleHeatFollow(empty,person),edited=updateHeatPersonNote(followed,person.person_id,'个人待查备注');assert.deepEqual(empty,emptyHeatPreferences());assert.equal(edited.people[person.person_id].name,person.name);assert.equal(edited.people[person.person_id].note,'个人待查备注');
 let saved;saveHeatPreferences({setItem:(key,value)=>saved={key,value}},'app-test',edited);assert.equal(JSON.parse(saved.value).people[person.person_id].note,'个人待查备注');
 const backup=JSON.parse(heatPreferenceBackup(edited,'app-test','2026-10-04T00:00:00Z'));assert.equal(backup.app_id,'app-test');assert.ok(!JSON.stringify(backup).includes('321'));assert.equal(JSON.stringify(person),before);
 const unfollowed=toggleHeatFollow(edited,person);assert.equal(unfollowed.follows.length,0);assert.equal(unfollowed.people[person.person_id],undefined);
});
await check('manual_same_name_remains_distinct_unverified_user_notes_and_removable',()=>{
 let state=emptyHeatPreferences();state=upsertManualHeatPerson(state,{id:'manual:1',name:'相同称呼',note:'用户填写'},'2026-10-04T00:00:00Z');state=upsertManualHeatPerson(state,{id:'manual:2',name:'相同称呼',note:'不同检索线索'},'2026-10-04T00:00:01Z');
 assert.equal(state.manual.length,2);assert.equal(state.follows.length,2);const changed=upsertManualHeatPerson(state,{id:'manual:1',name:'编辑称呼',note:'改备注'},'2026-10-04T00:00:02Z');assert.equal(state.manual[0].name,'相同称呼');assert.equal(changed.manual[0].created_at,'2026-10-04T00:00:00Z');assert.equal(changed.manual[0].note,'改备注');
 const removed=removeManualHeatPerson(changed,'manual:1');assert.deepEqual(removed.follows,['manual:2']);assert.equal(removed.manual.length,1);
 assert.throws(()=>saveHeatPreferences({setItem:()=>{throw new Error('quota');}},'app-test',removed),/quota/);assert.equal(removed.manual.length,1);
});
await check('discovery_search_follow_filters_do_not_sum_work_values_or_mutate_people',()=>{
 const rows=[{person_id:'new',name:'目录外',in_artist_directory:false,associated_works:[{title:'作品甲',value:900}]},{person_id:'known',name:'原目录称呼',in_artist_directory:true,associated_works:[{title:'作品乙',value:900}]}],before=JSON.stringify(rows),state=toggleHeatFollow(emptyHeatPreferences(),rows[1]);
 assert.deepEqual(filterHeatPeople(rows,HEAT_FILTERS,state).map(row=>row.person_id),['new']);assert.deepEqual(filterHeatPeople(rows,{search:'作品乙',discovery:'all',followedOnly:true},state).map(row=>row.person_id),['known']);assert.equal(JSON.stringify(rows),before);
 assert.ok(!heatScopeFilters(HEAT_FILTERS).some(filter=>['shop_id','run_id'].includes(filter.field)));assert.match(workChangeLabel({change_status:'first_observation'}),/暂无/);
});
await check('public_source_scope_contains_all_saved_history_of_selected_works_without_other_works',()=>{
 const people=[{person_id:'p',latest_snapshot_id:'s2',associated_works:[{work_url:'work-a',previous_snapshot_id:'s1'}]}],queries={...fakeQueries,artist_heat_history:{...fakeQueries.artist_heat_history,rows:[{snapshot_id:'s1',work_url:'work-a'},{snapshot_id:'s2',work_url:'work-a'},{snapshot_id:'s0',work_url:'work-a'},{snapshot_id:'s2',work_url:'work-b'}]}};
 const evidence=heatEvidenceScope(queries,people);assert.equal(evidence.history.length,3);assert.ok(evidence.history.every(row=>row.work_url==='work-a'));
 const component={queryId:'artist_heat_people',queryIds:['artist_heat_people','artist_heat_history'],sourceRowsByQuery:{artist_heat_people:people,artist_heat_history:evidence.history},scopeFilters:heatScopeFilters(HEAT_FILTERS)};
 const source=reviewedHeatSource(queries,component,'artist_heat_history');assert.equal(source.query,queries.artist_heat_history);assert.equal(source.rows,evidence.history);assert.equal(source.filters,component.scopeFilters);assert.equal(reviewedHeatSource(queries,component,'unreviewed'),null);
});
await check('fuzzy_display_preserves_raw_value_and_inferred_source_time_is_marked',()=>{
 const work={value:null,value_raw:'1万+',value_precision:'fuzzy',time_status:'trusted_with_timezone_assumption',change_status:'unknown'};
 assert.equal(heatValueLabel(work),'1万+');assert.match(heatTimeNote(work),/推定/);assert.equal(work.value,null);assert.equal(heatTimeNote({time_status:'unknown'}),'');assert.equal(heatValueLabel({value:0,value_raw:'0'}),'0');
});
await check('refresh_posts_only_fixed_json_empty_object_and_accepts_confirmed_running',async()=>{
 const calls=[],state=await startHeatRefresh(async(url,options)=>{calls.push({url,options});return response({status:'running',message:'开始'},202);});
 assert.equal(state.status,'running');assert.deepEqual(calls,[{url:'/__pdd_artist_heat_refresh',options:{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}}]);
});
await check('refresh_existing_job_and_cooldown_failure_never_fake_success',async()=>{
 let calls=0;const running=await startHeatRefresh(async url=>{calls++;return url.endsWith('_refresh')?response({message:'已有任务'},409):response({status:'running',message:'进行中'});});assert.equal(running.status,'running');assert.equal(calls,2);
 await assert.rejects(()=>startHeatRefresh(async()=>response({message:'冷却中'},429)),/冷却中/);
 await assert.rejects(()=>startHeatRefresh(async()=>response({message:'任务失败'},500)),/任务失败/);
 await assert.rejects(()=>startHeatRefresh(async()=>response({status:'idle'},202)),/未确认/);
});
await check('refresh_data_validation_preserves_original_queries_and_rejects_disconnect',async()=>{
 const queries=await readHeatData(async()=>response({status:'ok',queries:fakeQueries}));for(const id of HEAT_QUERY_IDS)assert.equal(queries[id],fakeQueries[id]);
 await assert.rejects(()=>readHeatData(async()=>response({status:'ok',queries:{}})),/不完整/);
 await assert.rejects(()=>readHeatData(async()=>({ok:true,status:200,json:async()=>{throw new Error('HTML response');}})),/未连接/);
 await assert.rejects(()=>readHeatStatus(async()=>response({status:'mystery'})),/无法识别/);
 const failure=await readHeatStatus(async()=>response({status:'failed',message:'来源不可读'}));assert.equal(failure.status,'failed');
});
await check('reload_requires_explicit_pending_refresh_and_canonical_build_receipt_without_init_loop',()=>{
 const success={status:'succeeded',receipt:{dashboard_built:true}};
 assert.equal(heatRefreshCompletion(success,false).action,'none');assert.equal(heatRefreshCompletion(success,true).action,'reload');
 assert.equal(heatRefreshCompletion({status:'succeeded'},true).action,'error');assert.equal(heatRefreshCompletion({status:'succeeded',receipt:{dashboard_built:false}},true).action,'error');assert.equal(heatRefreshCompletion({status:'failed',message:'构建失败'},true).action,'error');assert.equal(heatRefreshCompletion({status:'running'},true).action,'wait');
 const url=new URL(heatReloadUrl('http://127.0.0.1:8878/?view=1#evidence'));assert.equal(url.searchParams.get('pdd_view'),'heat');assert.equal(url.searchParams.get('view'),'1');assert.equal(url.hash,'#evidence');assert.equal(url.origin,'http://127.0.0.1:8878');
 const ui=fs.readFileSync(path.join(here,'../../dashboard/src/content/dashboard','HeatWatchContent.jsx'),'utf8');assert.ok(!ui.includes('liveQueries')&&!ui.includes('setLiveQueries')&&!ui.includes('readHeatData('));assert.match(ui,/const \{queries,snapshot\}=useDataApp/);
});
await check('work_change_displays_signed_delta_only_for_comparable_work_status',()=>{
 assert.equal(workChangeLabel({change_status:'up',delta:12}),'作品热度上升 +12');assert.equal(workChangeLabel({change_status:'down',delta:-9}),'作品热度下降 -9');assert.equal(workChangeLabel({change_status:'flat',delta:0}),'作品热度持平 0');assert.ok(!workChangeLabel({change_status:'first_observation',delta:null}).includes(' 0'));assert.ok(!workChangeLabel({change_status:'unknown',delta:null}).includes(' 0'));
});

export const acceptance={suite:"heat",passed:checks.length,total:checks.length,checks:checks};
