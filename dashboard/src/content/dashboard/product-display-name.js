// Display only: original titles, identifiers and matching evidence stay unchanged.
export function productDisplayName(value){
 const original=typeof value==='string'?value.trim():'';
 if(!original)return '未记录商品名称';
 let concise=original
  .replace(/[【\[]\s*\d+\s*天内发货\s*[】\]]/gu,'')
  .replace(/自制\s*diy|diy|精致|高清透明|高颜值|收藏送礼|收藏送人|学生礼物|送人|送礼|应援物料/giu,'')
  .replace(/\s+/gu,' ').replace(/^[\s·，、]+|[\s·，、]+$/gu,'').trim();
 if(/立牌|吧唧|书签/u.test(concise))concise=concise.replace(/桌面摆件|桌面装饰/gu,'').trim();
 return concise.length>=4?concise:original;
}
