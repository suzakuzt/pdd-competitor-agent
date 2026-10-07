// Observation times come from the saved source; never substitute build/refresh time.
export function dataUpdateTime(value){
 if(!value)return '时间未知';
 const date=new Date(value);
 if(!Number.isFinite(date.valueOf()))return '时间未知';
 const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date).map(part=>[part.type,part.value]));
 return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
