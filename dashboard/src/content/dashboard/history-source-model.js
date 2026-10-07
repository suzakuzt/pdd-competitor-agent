// Explicit authored source scope: never inherit the product-pool global run.
export function historyReviewedSource(queries,component,queryId=component?.queryId){
 if(!component||!(component.queryIds||[component.queryId]).includes(queryId)||!queries[queryId])return null;
 const rows=component.sourceRowsByQuery?.[queryId]??(queryId===component.queryId?component.sourceRows:undefined)??[];
 const filters=queryId==='collection_schedule'?[{field:'slot_key',label:'计划范围',value:'全局计划；不代表当前店铺已接通，不限定商品池观察轮次'}]:[...(component.scopeFilters||[])];
 if(queryId!=='collection_schedule'){
  const runIds=[...new Set(rows.map(row=>row.run_id).filter(Boolean))];
  if(runIds.length&&!filters.some(filter=>filter.field==='run_id'))filters.push({field:'run_id',label:'此来源所含全部轮次',value:runIds.join('；')});
  if(queryId==='collection_attempts')filters.push({field:'attempt_id',label:'执行记录范围',value:'当前店铺全部执行记录；未绑定轮次的记录也保留，不限定商品池观察轮次'});
 }
 return {query:queries[queryId],rows,filters};
}
