export const ARTIST_KIND_LABELS={artist:'娱乐艺人',esports:'电竞称呼',unresolved:'身份待核验称呼',all:'全部类型'};
export const ARTIST_SOURCE_LABELS={all:'全部入口状态',verified:'有核实入口',pending:'入口待核验'};
export const ARTIST_FILTERS={search:'',kind:'artist',source:'all'};

export function filterArtists(rows,shopId,filters=ARTIST_FILTERS){
 const needle=String(filters.search||'').trim().toLocaleLowerCase();
 return rows.filter(row=>row.shop_id===shopId)
  .filter(row=>filters.kind==='all'||row.entity_kind===(filters.kind||'artist'))
  .filter(row=>filters.source==='verified'?row.source_status==='有核实入口':filters.source==='pending'?row.source_status!=='有核实入口':true)
  .filter(row=>!needle||[row.artist_name,row.identity_note,row.title_search_text,...(row.title_event_clues||[]).map(clue=>clue.label),...(row.latest_title_event_clues||[]).map(clue=>clue.label),...(row.title_examples||[]).map(card=>card.title)].filter(Boolean).join(' ').toLocaleLowerCase().includes(needle))
  .sort((a,b)=>(Number(b.eligible_card_count)||0)-(Number(a.eligible_card_count)||0)||(Number(b.reference_card_count)||0)-(Number(a.reference_card_count)||0)||String(a.artist_id).localeCompare(String(b.artist_id),'zh-CN'));
}

export function artistSourceScope(queries,shopId,rows){
 const shopRuns=(queries.runs?.rows||[]).filter(row=>row.shop_id===shopId);
 const allowedRuns=new Set(shopRuns.map(row=>row.run_id)),pairs=new Set();
 for(const row of rows){
  if(row.shop_id!==shopId)continue;
  for(const id of row.observation_ids||[])if(allowedRuns.has(row.run_id))pairs.add(`${row.run_id}:${id}`);
  for(const id of row.latest_observation_ids||[])if(allowedRuns.has(row.latest_run_id))pairs.add(`${row.latest_run_id}:${id}`);
 }
 const observations=(queries.observations?.rows||[]).filter(row=>allowedRuns.has(row.run_id)&&pairs.has(`${row.run_id}:${row.observation_id}`));
 const actualRuns=new Set(rows.filter(row=>row.shop_id===shopId).flatMap(row=>[row.run_id,row.latest_run_id]));
 return {observations,runs:shopRuns.filter(row=>actualRuns.has(row.run_id))};
}

export function artistDrilldown(row,which='reference'){
 const latest=which==='latest';
 return {shop_id:row.shop_id,run_id:latest?row.latest_run_id:row.run_id,observation_ids:[...(latest?row.latest_observation_ids||[]:row.observation_ids||[])],
  label:`${row.artist_name} · ${latest?'最新记录':'分析参考轮'} · 全部提及原卡`,source_query:'artist_watchlist',source_id:row.artist_id,request_id:`artist:${row.shop_id}:${row.artist_id}:${latest?'latest':'reference'}`};
}

export function artistScopeFilters(shopId,filters){
 return [{field:'shop_id',label:'当前店铺',value:shopId},{field:'entity_kind',label:'人物类型',value:ARTIST_KIND_LABELS[filters.kind]||filters.kind},
  {field:'source_status',label:'入口状态',value:ARTIST_SOURCE_LABELS[filters.source]||filters.source},
  ...(filters.search?.trim()?[{field:'artist_search',label:'姓名或原标题活动词',value:filters.search.trim()}]:[]),
  {field:'sort',label:'排列依据',value:'参考轮单卡销量 >10 的卡数降序；仅人工核验优先级'}];
}

export function artistEvidenceFilters(kind,shopId,rows,summaryRows,filters=ARTIST_FILTERS){
 if(kind==='channels')return [{field:'public_channel_scope',label:'渠道范围',value:'通用公开入口；不限定店铺或观察轮次'}];
 const referenceIds=[...new Set((kind==='watchlist'?rows:summaryRows).filter(row=>row.shop_id===shopId).map(row=>row.run_id).filter(Boolean))];
 if(kind==='reference')return [{field:'shop_id',label:'当前店铺',value:shopId},{field:'run_id',label:'卡片统计与研究假设的参考轮',value:referenceIds.join('；')||'无参考轮'},
  {field:'name_scope',label:'名称覆盖',value:'检索名称保留参考与最新轮；卡片统计使用参考轮'}];
 const latestIds=[...new Set(rows.filter(row=>row.shop_id===shopId).map(row=>row.latest_run_id).filter(Boolean))];
 return [...artistScopeFilters(shopId,filters),{field:'run_id',label:'参考与最新轮范围（独立展示、不相加）',value:[...new Set([...referenceIds,...latestIds])].join('；')||'当前筛选无原卡轮次'},
  {field:'reference_run_id',label:'参考轮',value:referenceIds.join('；')||'无参考轮'},{field:'latest_run_id',label:'最新轮',value:latestIds.join('；')||'无最新轮'}];
}

// DataComponent's shell appends global filters. The public SourceSidebar API
// lets these multi-run components retain their explicit reviewed scope instead.
export function artistReviewedSource(queries,component,queryId=component?.queryId){
 if(!component||!(component.queryIds||[component.queryId]).includes(queryId)||!queries[queryId])return null;
 return {query:queries[queryId],rows:component.sourceRowsByQuery?.[queryId]??(queryId===component.queryId?component.sourceRows:undefined)??[],filters:component.scopeFilters||[]};
}

export function evidenceText(value){
 if(value===null||value===undefined||value==='')return '未记录';
 return typeof value==='string'?value:JSON.stringify(value,null,2);
}
