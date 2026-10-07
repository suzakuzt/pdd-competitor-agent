export function displayHistoryRange(rows,selected,endDate,length='all'){
 if(!selected||!/^\d{4}-\d{2}-\d{2}$/.test(endDate||''))return {source:[],plot:[],startDate:'',observationIds:[],hasClues:false};
 const available=rows.filter(row=>row.shop_id===selected.shop_id&&row.anchor_observation_id===selected.observation_id&&row.anchor_run_id===selected.run_id&&row.date<=endDate).sort((a,b)=>a.date.localeCompare(b.date));
 if(!available.length)return {source:[],plot:[],startDate:'',observationIds:[],hasClues:false};
 const days=[30,90,365].includes(Number(length))?Number(length):30,end=Date.parse(`${endDate}T00:00:00Z`),start=length==='all'?Date.parse(`${available[0].date}T00:00:00Z`):end-(days-1)*86400000,startDate=new Date(start).toISOString().slice(0,10);
 const source=available.filter(row=>row.date>=startDate),byDate=new Map(source.map(row=>[row.date,row]));
 const dailyPlot=Array.from({length:Math.floor((end-start)/86400000)+1},(_,index)=>{const date=new Date(start+index*86400000).toISOString().slice(0,10);return byDate.get(date)||{date,cumulative_yipin:null,daily_delta:null,display_price_yuan:null,missing_day:true};});
 // Two valid daily values may still lack evidence for a connecting segment.
 // A null midpoint breaks the rendered line without inventing a source day.
 const plot=dailyPlot.flatMap((row,index)=>row.break_before&&index>0&&Number.isFinite(row.cumulative_yipin)&&Number.isFinite(dailyPlot[index-1].cumulative_yipin)?[{date:new Date((Date.parse(`${dailyPlot[index-1].date}T00:00:00Z`)+Date.parse(`${row.date}T00:00:00Z`))/2).toISOString(),cumulative_yipin:null,daily_delta:null,display_price_yuan:null,comparison_break:true},row]:[row]);
 return {source,plot,startDate,observationIds:[...new Set(source.flatMap(row=>[row.observation_id,row.baseline_observation_id]).filter(id=>id!=null))],hasClues:source.some(row=>row.match_basis==='provisional_title_image')};
}
export function displayHistoryStatus(row){
 const unavailable=({unmatched:'未匹配',ambiguous_identity:'匹配有歧义',identity_conflict:'对应关系待核实',time_unverified:'时间待核验',incomplete_day:'当日仅部分采集',target_incomplete:'当前仅部分采集',missing_day:'当日缺采'})[row.point_status];
 if(unavailable)return unavailable;
 if(row.match_basis==='current_card')return '当前原卡';
 if(row.match_basis==='confirmed_goods_id')return '已确认';
 if(row.match_basis==='provisional_title_image')return '同图线索';
 return '待核验';
}
