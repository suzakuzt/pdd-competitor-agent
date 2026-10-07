import {focusRows} from './warehouse-focus-model.js';

// Title clues describe the catalogue card, not verified SKU attributes.
export const PRODUCT_CATEGORIES={standee:'立牌',badge:'吧唧 / 徽章',keychain:'钥匙扣',pendant:'挂件 / 吊坠',card:'卡片 / 小卡',sticker:'贴纸',plush:'毛绒 / 玩偶',other:'其他 / 待分类'};
const titleRules={standee:/立牌/,badge:/吧唧|徽章/,keychain:/钥匙扣|钥匙链/,pendant:/挂件|吊坠|挂饰/,card:/卡片|透卡|拍立得|小卡/,sticker:/贴纸/,plush:/毛绒|玩偶/};
export const TRACKING_PAGE_SIZE=25;
export function productCategories(title){
 const text=typeof title==='string'?title:'';
 const categories=Object.entries(titleRules).filter(([,rule])=>rule.test(text)).map(([key])=>key);
 return categories.length?categories:['other'];
}
export function productCategorySummary(rows){
 const counts=Object.fromEntries(Object.keys(PRODUCT_CATEGORIES).map(key=>[key,0]));
 for(const row of rows)for(const category of productCategories(row.title))counts[category]++;
 return {total:rows.length,counts,categoryCount:Object.values(counts).filter(count=>count>0).length};
}
export function isDailyDiscovery(row){
 return ['first_observed_after_complete','first_observed_candidate'].includes(row.discovery_status)&&typeof row.date==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(row.date)&&row.first_observed_date===row.date;
}
export function dailyDiscoverySummary(rows){
 const fresh=rows.filter(isDailyDiscovery);
 return {count:fresh.length,afterComplete:fresh.filter(row=>row.discovery_status==='first_observed_after_complete').length,candidates:fresh.filter(row=>row.discovery_status==='first_observed_candidate').length,unresolved:rows.filter(row=>row.discovery_status==='identity_unresolved').length};
}
export function trackingRows(rows,{lane='all',search='',comparison='yesterday',sort=null,productCategory='all'}={}){
 if(productCategory!=='all'&&!Object.hasOwn(PRODUCT_CATEGORIES,productCategory))return [];
 const scoped=rows.filter(row=>(lane!=='daily_new'||isDailyDiscovery(row))&&(productCategory==='all'||productCategories(row.title).includes(productCategory)));
 const filtered=focusRows(scoped,lane==='daily_new'?'all':lane,search,comparison,sort);
 if(!sort&&['all','daily_new'].includes(lane))filtered.sort((a,b)=>(a.view_order??Number.MAX_SAFE_INTEGER)-(b.view_order??Number.MAX_SAFE_INTEGER));
 return filtered;
}
export function trackingPage(rows,page=0){
 const maxPage=Math.max(1,Math.ceil(rows.length/TRACKING_PAGE_SIZE));
 const currentPage=Math.min(Math.max(0,Number.isInteger(page)?page:0),maxPage-1);
 return {maxPage,currentPage,shown:rows.slice(currentPage*TRACKING_PAGE_SIZE,(currentPage+1)*TRACKING_PAGE_SIZE)};
}
export function discoveryLabel(row){
 return ({initial_catalogue:'建档存量',existing:'已有记录',first_observed_after_complete:'首次监测记录',first_observed_candidate:'首见候选 · 待核实',identity_unresolved:'新旧待核实'})[row.discovery_status]||'新旧待核实';
}
