export const HEAT_QUERY_IDS=['artist_heat_people','artist_heat_history','artist_heat_summary','artist_heat_sources'];
export const HEAT_FILTERS={search:'',discovery:'new',followedOnly:false};
export function emptyHeatPreferences(){return {version:1,follows:[],people:{},manual:[]};}
export function heatStorageKey(appId){if(typeof appId!=='string'||!appId.trim())throw new Error('面板身份缺失，无法保存本机关注。');return `pdd:artist-heat:preferences:v1:${appId}`;}
export function normalizeHeatPreferences(value){
 if(!value||value.version!==1||!Array.isArray(value.follows)||!value.follows.every(id=>typeof id==='string')||!Array.isArray(value.manual)||!value.manual.every(row=>typeof row.id==='string'&&row.id.startsWith('manual:')&&typeof row.name==='string'&&row.name.trim()&&typeof row.note==='string'))throw new Error('本机关注记录格式无法识别；保留原记录，请先检查浏览器存储。');
 if(value.people!==undefined&&(!value.people||typeof value.people!=='object'||Array.isArray(value.people)||Object.values(value.people).some(row=>!row||typeof row.name!=='string'||typeof row.note!=='string')))throw new Error('本机关注名称或备注格式无法识别。');
 return {version:1,follows:[...new Set(value.follows)],people:Object.fromEntries(Object.entries(value.people||{}).map(([id,row])=>[id,{...row}])),manual:value.manual.map(row=>({...row}))};
}
export function loadHeatPreferences(storage,appId){try{const raw=storage.getItem(heatStorageKey(appId));return {preferences:raw?normalizeHeatPreferences(JSON.parse(raw)):emptyHeatPreferences(),error:''};}catch(error){return {preferences:emptyHeatPreferences(),error:`读取本机关注失败：${error.message||'浏览器未允许存储'}。未覆盖已有记录。`};}}
export function saveHeatPreferences(storage,appId,preferences){const normalized=normalizeHeatPreferences(preferences);storage.setItem(heatStorageKey(appId),JSON.stringify(normalized));return normalized;}
export function toggleHeatFollow(preferences,person){const id=person.person_id,ids=new Set(preferences.follows),people={...preferences.people};if(ids.has(id)){ids.delete(id);delete people[id];}else{ids.add(id);people[id]={name:person.name,note:''};}return {...preferences,follows:[...ids],people};}
export function updateHeatPersonNote(preferences,id,note){if(!preferences.follows.includes(id)||!preferences.people[id])throw new Error('请先关注该来源名称。');return {...preferences,people:{...preferences.people,[id]:{...preferences.people[id],note:String(note||'').trim()}}};}
export function upsertManualHeatPerson(preferences,input,now){
 const name=String(input.name||'').trim(),note=String(input.note||'').trim();if(!name)throw new Error('请填写人物名称或称呼。');if(!input.id?.startsWith('manual:'))throw new Error('手工记录身份无效。');
 const old=preferences.manual.find(row=>row.id===input.id),entry={id:input.id,name,note,created_at:old?.created_at||now,updated_at:now};
 return {...preferences,manual:old?preferences.manual.map(row=>row.id===entry.id?entry:row):[...preferences.manual,entry],follows:old?[...preferences.follows]:[...new Set([...preferences.follows,entry.id])]};
}
export function removeManualHeatPerson(preferences,id){return {...preferences,manual:preferences.manual.filter(row=>row.id!==id),follows:preferences.follows.filter(value=>value!==id)};}
export function heatPreferenceBackup(preferences,appId,now){return JSON.stringify({export_type:'local_artist_heat_preferences',app_id:appId,exported_at:now,scope:'浏览器本机个人关注与用户自填笔记；不是核验数据',...normalizeHeatPreferences(preferences)},null,2);}
export function filterHeatPeople(rows,filters,preferences){const search=String(filters.search||'').trim().toLocaleLowerCase(),followed=new Set(preferences.follows);return rows.filter(row=>filters.discovery==='all'||!row.in_artist_directory).filter(row=>!filters.followedOnly||followed.has(row.person_id)).filter(row=>!search||[row.name,row.identity_brief,row.person_intro,...(row.associated_works||[]).map(work=>work.title)].filter(Boolean).join(' ').toLocaleLowerCase().includes(search));}
export function heatScopeFilters(filters){return [{field:'scope',label:'公开榜单范围',value:'独立公开作品榜；与当前店铺和拼多多观察轮次无关'},
 {field:'discovery',label:'人物范围',value:filters.discovery==='new'?'原艺人目录之外的本轮名字，不代表新出道':'本轮全部可识别人物'},
 {field:'followed',label:'个人关注范围',value:filters.followedOnly?'仅浏览器本机关注':'不限定关注'},...(filters.search.trim()?[{field:'search',label:'姓名或作品',value:filters.search.trim()}]:[])];}
export function heatEvidenceScope(queries,people){
 const works=new Set(people.flatMap(person=>(person.associated_works||[]).map(work=>work.work_url)));
 return {history:(queries.artist_heat_history?.rows||[]).filter(row=>works.has(row.work_url)),sources:queries.artist_heat_sources?.rows||[]};
}
export function reviewedHeatSource(queries,component,queryId=component?.queryId){if(!component||!(component.queryIds||[component.queryId]).includes(queryId)||!queries[queryId])return null;return {query:queries[queryId],rows:component.sourceRowsByQuery?.[queryId]??(queryId===component.queryId?component.sourceRows:undefined)??[],filters:component.scopeFilters||[]};}
export function validateHeatQueries(queries){if(!queries||HEAT_QUERY_IDS.some(id=>!queries[id]||!Array.isArray(queries[id].rows)||!queries[id].source))throw new Error('公开榜单数据响应不完整，保留已有显示。');return Object.fromEntries(HEAT_QUERY_IDS.map(id=>[id,queries[id]]));}
async function apiJson(response){let body;try{body=await response.json();}catch{throw new Error('本机公开榜单服务未连接或返回非 JSON；请通过本项目面板服务打开。');}if(!response.ok)throw new Error(body.message||`公开榜单请求失败（HTTP ${response.status}），未更新数据。`);return body;}
export async function readHeatData(fetcher){const body=await apiJson(await fetcher('/__pdd_artist_heat_data',{cache:'no-store'}));if(body.status!=='ok')throw new Error(body.message||'公开榜单数据尚不可用。');return validateHeatQueries(body.queries);}
export async function readHeatStatus(fetcher){const body=await apiJson(await fetcher('/__pdd_artist_heat_status',{cache:'no-store'}));if(!['idle','running','succeeded','failed'].includes(body.status))throw new Error('公开榜单执行状态无法识别。');return body;}
export async function startHeatRefresh(fetcher){const response=await fetcher('/__pdd_artist_heat_refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(response.status===409){const state=await readHeatStatus(fetcher);if(state.status==='running')return state;throw new Error(state.message||'已有榜单任务，请稍后重试。');}const body=await apiJson(response);if(body.status!=='running'&&body.status!=='succeeded')throw new Error(body.message||'后台未确认开始更新，未视作成功。');return body;}
export function workChangeLabel(work){if(work.change_status==='first_observation')return '作品首次记录，暂无较上次变化';if(['up','down','flat'].includes(work.change_status)&&Number.isFinite(work.delta))return work.delta>0?`作品热度上升 +${work.delta}`:work.delta<0?`作品热度下降 ${work.delta}`:'作品热度持平 0';if(work.change_label)return work.change_label;return {unknown:'暂无同口径可比值',flat:'较上次持平',up:'作品榜单值上升',down:'作品榜单值下降'}[work.change_status]||'比较依据待核验';}
export function heatValueLabel(work){return String(work.value_raw??'').trim()||(work.value===null||work.value===undefined?'未取得':String(work.value));}
export function heatTimeNote(row){return row.time_status==='trusted_with_timezone_assumption'||row.timezone_assumption?'年份 / 时区按采集时间推定，非来源完整时间戳':'';}
export function heatRefreshCompletion(state,awaiting){
 if(!awaiting)return {action:'none'};
 if(state.status==='succeeded')return state.receipt?.dashboard_built===true?{action:'reload'}:{action:'error',message:'榜单记录已返回，但缺少面板构建成功凭据；保留当前快照，未重新载入。'};
 if(state.status==='failed'||state.status==='idle')return {action:'error',message:state.message||'本次更新未确认完成，保留当前快照。'};
 return {action:'wait'};
}
export function heatReloadUrl(href){const url=new URL(href);url.searchParams.set('pdd_view','heat');return url.toString();}
