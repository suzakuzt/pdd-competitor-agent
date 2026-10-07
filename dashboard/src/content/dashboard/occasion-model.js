import {focusCategory} from './warehouse-focus-model.js';

// These are title clues from the current shop's reviewed source cards, not an
// event calendar. Neither publication dates nor real event dates are inferred.
const BIRTHDAY_RULES=[
 ['生日送礼',/生日\s*(?:礼物|礼品|送礼)/gu],
 ['生日 / 生贺',/生日(?!\s*(?:礼物|礼品|送礼))|生贺|诞生日|诞生祭|诞生纪念/gu],
 ['周年 / 纪念日',/(?:\d+|[一二三四五六七八九十百]+)?周年(?:纪念)?|纪念日/gu],
];
const ACTIVITY_RULES=[
 ['演唱会 / 巡演',/演唱会|巡回演出|巡演/gu],
 ['见面会',/见面会|签售会/gu],
 ['音乐节',/音乐节/gu],
 ['展会',/漫展|动漫展|同人展/gu],
 ['赛事',/赛事|总决赛|决赛|锦标赛|电竞赛/gu],
 ['品牌 / 线下活动',/线下活动|品牌活动|活动|时装周|红毯|庆典/gu],
 ['应援线索',/应援/gu],
];
const SALES={yipin_gt10:'gt10',yipin_1to10:'low',yipin_zero:'zero'};
const unique=values=>[...new Set(values)];

function matchRules(title,rules){
 const tags=[],matches=[];
 for(const [tag,pattern] of rules){
  const found=Array.from(title.matchAll(pattern),match=>match[0]);
  if(found.length){tags.push(tag);matches.push(...found);}
 }
 return {tags,matches:unique(matches)};
}

const DATE_EVENT_WORDS=/(?:生日|生贺|诞生祭|纪念日|周年|演唱会|音乐节|见面会|巡演|活动|直播|应援)/u;
function dateValid(year,month,day,calendar='solar'){
 const leap=!year||year%400===0||(year%4===0&&year%100!==0);
 return month>=1&&month<=12&&day>=1&&day<=(calendar==='lunar'?30:[31,leap?29:28,31,30,31,30,31,31,30,31,30,31][month-1]);
}
function titleDateDetails(title){
 const found=[],eventSpans=[];
 for(const [module,rules] of [['birthday',BIRTHDAY_RULES],['activity',ACTIVITY_RULES]])for(const [,pattern] of rules)for(const match of title.matchAll(pattern))eventSpans.push({module,start:match.index,end:match.index+match[0].length});
 const add=(match,year,month,day)=>{
  if(found.some(item=>match.index<item.end&&match.index+match[0].length>item.index))return;
  const before=title.slice(Math.max(0,match.index-16),match.index),after=title.slice(match.index+match[0].length);
  const markers=Array.from(before.matchAll(/农历|阴历|公历|阳历/gu)),marker=markers.at(-1)?.[0];
  const calendar=marker?(/农历|阴历/u.test(marker)?'lunar':'solar'):(/农历|阴历/u.test(title)&&!/公历|阳历/u.test(title)?'lunar':'solar');
  const shipping=/(?:发货|到货|出货|截单)(?:日期|时间)?[：:\s]*$/u.test(before)||/^\s*(?:前后|前|后|起|左右|开始|陆续)?\s*(?:发货|到货|出货|截单)/u.test(after);
  if(!dateValid(year,month,day,calendar)||shipping||/^\s*(?:天|小时|工作日|个|件|cm|mm|厘米|毫米)/iu.test(after))return;
  const end=match.index+match[0].length,near=eventSpans.map(span=>({...span,distance:Math.max(0,span.start-end,match.index-span.end)}));
  const distance=Math.min(...near.map(span=>span.distance)),available=unique(eventSpans.map(span=>span.module));
  // A mixed birthday/activity title may contain different dates. Use the
  // nearest event word, and leave remote ambiguous dates unassigned.
  const modules=distance<=8?unique(near.filter(span=>span.distance===distance).map(span=>span.module)):(available.length===1?available:[]);
  found.push({index:match.index,end,text:match[0],year:year||null,month,day,calendar,modules});
 };
 // Require day/month markers (or a complete year) to avoid sizes, quantities,
 // decimal prices and shipping promises. Keep exactly the original text.
 const patterns=[
  /(?<!\d)(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|号)?(?!\d)/gu,
  /(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)/gu,
 ];
 for(const pattern of patterns)for(const match of title.matchAll(pattern)){
  add(match,Number(match[1]),Number(match[2]),Number(match[3]));
 }
 // A compact MMDD is a date clue only directly beside birthday/event words.
 // It remains "0929", never a manufactured 2026-09-29 date.
 for(const pattern of [/(?<![\d/.-])(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)/gu,/(?<![\d/.-])(\d{1,2})[-/.](\d{1,2})(?![\d/.-])/gu])for(const match of title.matchAll(pattern)){
  const before=title.slice(Math.max(0,match.index-8),match.index).trimEnd(),after=title.slice(match.index+match[0].length).trimStart();
  if(new RegExp(`${DATE_EVENT_WORDS.source}$`,'u').test(before)||new RegExp(`^${DATE_EVENT_WORDS.source}`,'u').test(after))add(match,0,Number(match[1]),Number(match[2]));
 }
 return found.sort((a,b)=>a.index-b.index);
}

export function classifyOccasion(row){
 const title=typeof row?.title==='string'?row.title:'';
 const birthday=matchRules(title,BIRTHDAY_RULES),activity=matchRules(title,ACTIVITY_RULES);
 const dateDetails=titleDateDetails(title);
 return {birthday:!!birthday.tags.length,activity:!!activity.tags.length,
  tags:{birthday:birthday.tags,activity:activity.tags},
  matches:{birthday:birthday.matches,activity:activity.matches},
  dateClues:unique(dateDetails.map(item=>item.text)),dateDetails,hasLunar:/农历|阴历/u.test(title)};
}

export function occasionDates(classified,module){return (classified?.dateDetails||[]).filter(item=>item.modules.includes(module));}

function parsedDateSearch(value){
 const input=String(value||'').trim();
 const full=input.match(/^(\d{4})(?:年|[-/.])(\d{1,2})(?:月|[-/.])(\d{1,2})(?:日|号)?$/u),
  partial=input.match(/^(\d{1,2})(?:月|[-/.])(\d{1,2})(?:日|号)?$/u),compact=input.match(/^(\d{2})(\d{2})$/u);
 if(full)return {year:Number(full[1]),month:Number(full[2]),day:Number(full[3])};
 if(partial||compact){const match=partial||compact;return {year:null,month:Number(match[1]),day:Number(match[2])};}
 return null;
}
function dateMatches(classified,module,term){
 if(!String(term||'').trim())return true;
 const requested=parsedDateSearch(term);
 if(!requested)return false;
 return occasionDates(classified,module).some(item=>item.month===requested.month&&item.day===requested.day&&(!requested.year||item.year===requested.year));
}
function dateKey(row,module,sort){
 const dates=occasionDates(row.occasion,module).filter(item=>item.calendar==='solar'),full=dates.filter(item=>item.year),candidates=full.length?full:dates;
 if(!candidates.length)return {group:2,value:0};
 const values=candidates.map(item=>(item.year||0)*10000+item.month*100+item.day);
 return {group:full.length?0:1,value:sort==='date_asc'?Math.min(...values):Math.max(...values)};
}

function salesClass(row){return SALES[focusCategory(row)]||'unknown';}
function salesValue(row){return salesClass(row)==='unknown'?-1:(row.yipin_value??row.sales_value??-1);}
function cluePriority(row,module){
 const tags=row.occasion.tags[module];
 return module==='activity'?(tags.some(tag=>tag!=='应援线索')?0:1):(tags.some(tag=>tag!=='生日送礼')?0:1);
}

export function occasionRows(rows,module,{search='',sales='all',tag='all',dateSearch='',dateScope='all',sort='date_desc'}={}){
 if(!['birthday','activity'].includes(module))return [];
 const term=String(search||'').trim().toLocaleLowerCase();
 return (rows||[]).map(row=>({...row,occasion:classifyOccasion(row)}))
  .filter(row=>row.occasion[module]&&(sales==='all'||salesClass(row)===sales)&&(tag==='all'||row.occasion.tags[module].includes(tag))&&(!term||`${row.title||''} ${row.goods_id||''} ${row.observation_id??''} ${row.occasion.matches[module].join(' ')} ${row.occasion.dateClues.join(' ')}`.toLocaleLowerCase().includes(term)))
  .filter(row=>dateMatches(row.occasion,module,dateSearch)&&(dateScope==='all'||(dateScope==='dated'&&occasionDates(row.occasion,module).length>0)||(dateScope==='undated'&&occasionDates(row.occasion,module).length===0)||(dateScope==='lunar'&&row.occasion.hasLunar)))
  .sort((a,b)=>{
   if(['date_desc','date_asc'].includes(sort)){
    const left=dateKey(a,module,sort),right=dateKey(b,module,sort),difference=left.group-right.group||(sort==='date_asc'?left.value-right.value:right.value-left.value);
    if(difference)return difference;
   }
   return cluePriority(a,module)-cluePriority(b,module)||salesValue(b)-salesValue(a)||(a.view_order??Number.MAX_SAFE_INTEGER)-(b.view_order??Number.MAX_SAFE_INTEGER)||String(a.observation_id??'').localeCompare(String(b.observation_id??''),undefined,{numeric:true});
  });
}

export function occasionSummary(rows,module){
 const selected=occasionRows(rows,module),counts={total:selected.length,gt10:0,low:0,zero:0,unknown:0},tags=new Map();
 for(const row of selected){
  counts[salesClass(row)]++;
  for(const label of row.occasion.tags[module])tags.set(label,(tags.get(label)||0)+1);
 }
 return {...counts,tags:[...tags].map(([label,count])=>({label,count})).sort((a,b)=>b.count-a.count||a.label.localeCompare(b.label,'zh-CN'))};
}
