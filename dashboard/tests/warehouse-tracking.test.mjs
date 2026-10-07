import test from 'node:test';
import assert from 'node:assert/strict';
import {PRODUCT_CATEGORIES,productCategories,productCategorySummary,dailyDiscoverySummary,trackingRows,TRACKING_PAGE_SIZE,trackingPage} from '../src/content/dashboard/warehouse-tracking-model.js';
import {focusView} from '../src/content/dashboard/warehouse-focus-model.js';

const card=(observation_id,patch={})=>({shop_id:'A',run_id:'full',date:'2026-10-07',observation_id,view_order:observation_id,
 title:'合成角色立牌',image_url:'https://img.pddpic.com/SYNTHETIC_MAIN.png',goods_id:null,
 sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:25,sales_raw:'已拼25件',yipin_value:25,category:'yipin_gt10',...patch});
const ids=rows=>rows.map(row=>row.observation_id);
const frozen=rows=>Object.freeze(rows.map(row=>Object.freeze(row)));

test('product categories expose the fixed title-clue choices and recognize each supported synonym',()=>{
 assert.deepEqual(Object.keys(PRODUCT_CATEGORIES),['standee','badge','keychain','pendant','card','sticker','plush','other']);
 assert.ok(Object.values(PRODUCT_CATEGORIES).every(label=>typeof label==='string'&&label.length>0));
 const examples=[['合成角色立牌','standee'],['合成角色吧唧','badge'],['合成角色徽章','badge'],['钥匙扣','keychain'],['钥匙链','keychain'],
  ['挂件','pendant'],['吊坠','pendant'],['挂饰','pendant'],['卡片','card'],['透卡','card'],['拍立得','card'],['小卡','card'],['贴纸','sticker'],['毛绒','plush'],['玩偶','plush']];
 for(const [title,category] of examples)assert.deepEqual(productCategories(title),[category],title);
});

test('title clues can have multiple categories but repeated synonyms do not duplicate a category',()=>{
 assert.deepEqual(productCategories('角色立牌、立牌、吧唧徽章和钥匙链挂件附赠贴纸小卡毛绒玩偶'),['standee','badge','keychain','pendant','card','sticker','plush']);
 assert.deepEqual(productCategories('吧唧徽章吧唧'),['badge']);
 for(const title of ['',null,undefined,17,{},'普通收纳袋'])assert.deepEqual(productCategories(title),['other']);
});

test('category summary counts independent cards and overlapping labels without presenting their sum as card total',()=>{
 const rows=frozen([card(1,{title:'立牌吧唧钥匙扣'}),card(2,{title:'立牌吧唧钥匙扣'}),card(3,{title:'普通收纳袋'}),card(4,{title:'毛绒玩偶贴纸'}),card(5,{title:null})]);
 const before=JSON.stringify(rows),summary=productCategorySummary(rows);
 assert.deepEqual(summary,{total:5,counts:{standee:2,badge:2,keychain:2,pendant:0,card:0,sticker:1,plush:1,other:2},categoryCount:6});
 assert.equal(Object.values(summary.counts).reduce((sum,count)=>sum+count,0),10);assert.equal(JSON.stringify(rows),before);
 assert.deepEqual(productCategorySummary([]),{total:0,counts:{standee:0,badge:0,keychain:0,pendant:0,card:0,sticker:0,plush:0,other:0},categoryCount:0});
});

test('all tracking rows retain every one of 130 independent cards including zero and unknown sales',()=>{
 const classes=[['yipin_gt10',25],['yipin_1to10',7],['yipin_zero',0],['unknown',null]];
 const rows=frozen(Array.from({length:130},(_,i)=>{
  const [category,value]=classes[i%4];return card(i+1,{category,sales_value:value,yipin_value:value,sales_raw:value===null?null:`已拼${value}件`,sales_precision:value===null?'unknown':'exact_display'});
 })),before=JSON.stringify(rows),result=trackingRows(rows);
 assert.equal(result.length,130);assert.equal(new Set(ids(result)).size,130);assert.deepEqual([...ids(result)].sort((a,b)=>a-b),Array.from({length:130},(_,i)=>i+1));
 assert.equal(result.filter(row=>row.yipin_value===0).length,32);assert.equal(result.filter(row=>row.yipin_value===null).length,32);
 assert.equal(JSON.stringify(rows),before);assert.equal(new Set(result.map(row=>row.image_url)).size,1,'shared images must not merge original cards');
});

test('search, product category and sales lane intersect without mutating or combining original cards',()=>{
 const rows=frozen([
  card(1,{title:'Alpha 立牌吧唧',goods_id:'889'}),card(2,{title:'Alpha 立牌吧唧',goods_id:'889'}),
  card(3,{title:'Alpha 吊坠'}),card(4,{title:'Beta 立牌'}),
  card(5,{title:'Alpha 立牌',category:'yipin_zero',sales_value:0,yipin_value:0,sales_raw:'已拼0件'}),
  card(6,{title:'Alpha 立牌',category:'unknown',sales_value:null,yipin_value:null,sales_raw:null}),
 ]),before=JSON.stringify(rows);
 assert.deepEqual(ids(trackingRows(rows,{search:' ALPHA ',productCategory:'standee',lane:'yipin_gt10'})),[1,2]);
 assert.deepEqual(ids(trackingRows(rows,{search:'alpha',productCategory:'badge',lane:'all'})),[1,2]);
 assert.deepEqual(ids(trackingRows(rows,{search:'889',productCategory:'standee'})),[1,2]);
 assert.deepEqual(ids(trackingRows(rows,{search:'5',productCategory:'standee',lane:'yipin_zero'})),[5]);
 assert.deepEqual(ids(trackingRows(rows,{productCategory:'standee',lane:'unknown'})),[6]);
 assert.deepEqual(trackingRows(rows,{search:'missing',productCategory:'standee'}),[]);assert.equal(JSON.stringify(rows),before);
});

test('daily-new lane uses explicit first-observed status on the displayed date and preserves duplicate original cards',()=>{
 const rows=frozen([
  card(1,{discovery_status:'first_observed_after_complete',first_observed_date:'2026-10-07'}),
  card(2,{discovery_status:'first_observed_candidate',first_observed_date:'2026-10-07'}),
  card(3,{discovery_status:'existing',first_observed_date:'2026-10-07'}),
  card(4,{discovery_status:'identity_unresolved',first_observed_date:'2026-10-07'}),
  card(5,{first_observed_date:'2026-10-07'}),
  card(6,{discovery_status:'first_observed_after_complete',first_observed_date:'2026-10-06'}),
  card(7,{discovery_status:'first_observed_candidate',first_observed_date:'2026-10-06'}),
  card(8,{discovery_status:'first_observed_candidate',first_observed_date:null}),
  card(9,{discovery_status:'first_observed_after_complete',first_observed_date:null,date:null}),
  card(10,{discovery_status:'initial_catalogue',first_observed_date:'2026-10-07'}),
 ]);
 const result=trackingRows(rows,{lane:'daily_new'});assert.deepEqual(ids(result),[1,2]);assert.equal(result[0].title,result[1].title);assert.equal(result[0].image_url,result[1].image_url);assert.notEqual(result[0].observation_id,result[1].observation_id);
 assert.deepEqual(result.map(row=>row.discovery_status),['first_observed_after_complete','first_observed_candidate']);
 assert.deepEqual(dailyDiscoverySummary(rows),{count:2,afterComplete:1,candidates:1,unresolved:1});
});

test('daily-new includes zero and unknown sales and intersects with category and search',()=>{
 const rows=[card(1,{title:'Alpha 钥匙扣',discovery_status:'first_observed_candidate',first_observed_date:'2026-10-07',category:'yipin_zero',sales_value:0,yipin_value:0,sales_raw:'已拼0件'}),
  card(2,{title:'Alpha 钥匙扣',discovery_status:'first_observed_after_complete',first_observed_date:'2026-10-07',category:'unknown',sales_value:null,yipin_value:null,sales_raw:null}),
  card(3,{title:'Beta 钥匙扣',discovery_status:'first_observed_candidate',first_observed_date:'2026-10-07'}),
  card(4,{title:'Alpha 吧唧',discovery_status:'first_observed_candidate',first_observed_date:'2026-10-07'})];
 const result=trackingRows(rows,{lane:'daily_new',search:'Alpha',productCategory:'keychain'});assert.deepEqual(ids(result),[1,2]);assert.deepEqual(result.map(row=>row.yipin_value),[0,null]);
});

test('all and daily-new default to original view order while positive-sales lanes retain sales ordering',()=>{
 const rows=frozen([
  card(1,{view_order:30,yipin_value:12,sales_value:12,sales_raw:'已拼12件',discovery_status:'first_observed_candidate',first_observed_date:'2026-10-07'}),
  card(2,{view_order:10,yipin_value:0,sales_value:0,sales_raw:'已拼0件',category:'yipin_zero',discovery_status:'first_observed_after_complete',first_observed_date:'2026-10-07'}),
  card(3,{view_order:20,yipin_value:null,sales_value:null,sales_raw:null,category:'unknown'}),
  card(4,{view_order:40,yipin_value:40,sales_value:40,sales_raw:'已拼40件'}),
 ]);
 assert.deepEqual(ids(trackingRows(rows)),[2,3,1,4]);assert.deepEqual(ids(trackingRows(rows,{lane:'daily_new'})),[2,1]);
 assert.deepEqual(ids(trackingRows(rows,{lane:'yipin_gt10'})),[4,1]);assert.deepEqual(ids(rows),[1,2,3,4]);
});

test('unknown product category keys fail closed and an empty catalogue stays empty',()=>{
 for(const productCategory of ['missing','__proto__','toString'])assert.deepEqual(trackingRows([card(1)],{productCategory}),[]);
 assert.deepEqual(trackingRows([]),[]);assert.deepEqual(dailyDiscoverySummary([]),{count:0,afterComplete:0,candidates:0,unresolved:0});
});

test('comparison sorting is numeric and puts missing or invalid comparisons last in both directions',()=>{
 const rows=frozen([
  card(1,{yesterday_status:'comparable',yesterday_delta:2,yesterday_growth_rate:.1001}),
  card(2,{yesterday_status:'comparable',yesterday_delta:12,yesterday_growth_rate:.1002}),
  card(3,{yesterday_status:'zero_baseline',yesterday_delta:7,yesterday_growth_rate:99}),
  card(4,{yesterday_status:'missing_baseline',yesterday_delta:999,yesterday_growth_rate:999}),
  card(5,{yesterday_status:'comparable',yesterday_delta:null,yesterday_growth_rate:null}),
  card(6,{yesterday_status:'comparable',yesterday_delta:'100',yesterday_growth_rate:Infinity}),
 ]);
 assert.deepEqual(ids(trackingRows(rows,{sort:{field:'delta',direction:'desc'}})),[2,3,1,4,5,6]);
 assert.deepEqual(ids(trackingRows(rows,{sort:{field:'delta',direction:'asc'}})),[1,3,2,4,5,6]);
 assert.deepEqual(ids(trackingRows(rows,{sort:{field:'growth_rate',direction:'desc'}})),[2,1,3,4,5,6]);
 assert.deepEqual(ids(trackingRows(rows,{sort:{field:'growth_rate',direction:'asc'}})),[1,2,3,4,5,6]);
 assert.deepEqual(ids(rows),[1,2,3,4,5,6]);
});

test('selected comparison fields and whole-filter ordering apply before any table pagination',()=>{
 const rows=frozen(Array.from({length:130},(_,i)=>card(i+1,{title:i%2?'Alpha 立牌':'Alpha 吧唧',yesterday_status:'comparable',yesterday_delta:i,day_before_yesterday_status:'comparable',day_before_yesterday_delta:130-i})));
 const options={search:'Alpha',productCategory:'standee',sort:{field:'delta',direction:'desc'}};
 const yesterday=trackingRows(rows,options),prior=trackingRows(rows,{...options,comparison:'day_before_yesterday'});
 assert.equal(yesterday.length,65);assert.equal(prior.length,65);assert.deepEqual(ids(yesterday).slice(0,3),[130,128,126]);assert.deepEqual(ids(prior).slice(0,3),[2,4,6]);assert.equal(yesterday[25].observation_id,80);assert.equal(prior[25].observation_id,52);
});

test('pagination makes all 130 filtered cards reachable in 25-card pages without gaps, repeats or source mutation',()=>{
 const rows=frozen(Array.from({length:130},(_,i)=>card(i+1))),result=trackingRows(rows);assert.equal(TRACKING_PAGE_SIZE,25);
 const pages=[0,1,2,3,4,5].map(page=>trackingPage(result,page));
 assert.deepEqual(pages.map(page=>page.maxPage),[6,6,6,6,6,6]);assert.deepEqual(pages.map(page=>page.currentPage),[0,1,2,3,4,5]);assert.deepEqual(pages.map(page=>page.shown.length),[25,25,25,25,25,5]);
 assert.deepEqual(ids(pages[0].shown),Array.from({length:25},(_,i)=>i+1));assert.deepEqual(ids(pages[5].shown),[126,127,128,129,130]);
 assert.deepEqual(pages.flatMap(page=>ids(page.shown)),ids(rows));assert.equal(result.length,130);
});

test('pagination clamps stale or invalid page selections after filters change and handles empty results',()=>{
 const rows=Array.from({length:30},(_,i)=>card(i+1));
 assert.deepEqual(trackingPage(rows,99),{maxPage:2,currentPage:1,shown:rows.slice(25)});
 for(const page of [-1,0.5,'1',NaN,undefined])assert.deepEqual(trackingPage(rows,page),{maxPage:2,currentPage:0,shown:rows.slice(0,25)});
 assert.deepEqual(trackingPage([],99),{maxPage:1,currentPage:0,shown:[]});assert.deepEqual(trackingPage([rows[0]],5),{maxPage:1,currentPage:0,shown:[rows[0]]});
});

test('tracking starts from focusView complete catalogue scope without bringing other shops or later partial records into the result',()=>{
 const old=card(1,{run_id:'old',date:'2026-10-06',title:'旧日立牌'}),current=card(2,{run_id:'full',date:'2026-10-07',title:'本日立牌'}),second=card(3,{run_id:'full',date:'2026-10-07',title:'本日立牌',sales_value:0,yipin_value:0,sales_raw:'已拼0件'}),partial=card(4,{run_id:'partial',date:'2026-10-08',title:'未完整立牌'}),foreign=card(5,{shop_id:'B',run_id:'full',date:'2026-10-07',title:'别店立牌'});
 const queries={warehouse_days:{rows:[{shop_id:'A',run_id:'old',date:'2026-10-06',is_complete:true},{shop_id:'A',run_id:'full',date:'2026-10-07',is_complete:true},{shop_id:'A',run_id:'partial',date:'2026-10-08',is_complete:false},{shop_id:'B',run_id:'full',date:'2026-10-07',is_complete:true}]},
  warehouse_records:{rows:[old,current,second,partial,foreign]},warehouse_focus_summary:{rows:[{shop_id:'A',run_id:'full'}]},warehouse_focus_items:{rows:[current,second,partial,foreign]}};
 const latest=focusView(queries,'A');assert.equal(latest.date,'2026-10-07');assert.equal(latest.queryId,'warehouse_focus_items');assert.deepEqual(ids(trackingRows(latest.rows,{productCategory:'standee'})),[2,3]);assert.deepEqual(ids(latest.source),[2,3]);
 const historical=focusView(queries,'A','2026-10-06');assert.equal(historical.queryId,'warehouse_records');assert.deepEqual(ids(trackingRows(historical.rows)),[1]);assert.ok(historical.source.every(row=>row.shop_id==='A'&&row.date==='2026-10-06'));
 assert.deepEqual(trackingRows(focusView(queries,'UNKNOWN').rows),[]);
});
