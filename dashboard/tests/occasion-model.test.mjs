import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyOccasion,occasionDates,occasionRows,occasionSummary} from '../src/content/dashboard/occasion-model.js';
const exact=(value,title='甲生日立牌',extra={})=>({title,sales_label:'已拼',sales_unit:'件',sales_precision:'exact_display',sales_value:value,sales_raw:`已拼${value}件`,...extra});

test('birthday and anniversary are title clues; gift language is separated',()=>{
 const birthday=classifyOccasion({title:'甲生日快乐十周年亚克力立牌'});
 assert.deepEqual(birthday.tags.birthday,['生日 / 生贺','周年 / 纪念日']);
 assert.deepEqual(birthday.matches.birthday,['生日','十周年']);
 assert.deepEqual(classifyOccasion({title:'角色立牌生日礼物送朋友'}).tags.birthday,['生日送礼']);
 assert.equal(classifyOccasion({title:'封面纪念收藏送礼全新设计诞生'}).birthday,false);
 assert.equal(classifyOccasion({title:'角色诞生祭'}).birthday,true);
});

test('specific activities and general support retain different labels',()=>{
 const result=classifyOccasion({title:'乙生日音乐节演唱会周边应援'});
 assert.equal(result.birthday,true);assert.equal(result.activity,true);
 assert.deepEqual(result.tags.activity,['演唱会 / 巡演','音乐节','应援线索']);
 assert.deepEqual(classifyOccasion({title:'乙通用应援物料'}).tags.activity,['应援线索']);
 assert.equal(classifyOccasion({title:'普通明星立牌收藏送礼'}).activity,false);
});

test('all classes include unsold and unknown while exact yiqiang joins yipin sales',()=>{
 const rows=[exact(11),exact(10),exact(1),exact(0),exact(null),exact(20,'甲生日',{sales_label:'已抢'}),exact(10,'甲生日',{sales_precision:'approximate',sales_raw:'已拼10+件'})];
 assert.deepEqual(occasionSummary(rows,'birthday'),{total:7,gt10:2,low:2,zero:1,unknown:2,tags:[{label:'生日 / 生贺',count:7}]});
 for(const [sales,count] of [['all',7],['gt10',2],['low',2],['zero',1],['unknown',2]])assert.equal(occasionRows(rows,'birthday',{sales}).length,count);
});

test('dates default to descending within explicit-year and yearless groups with undated last',()=>{
 const rows=[exact(999,'甲生日',{observation_id:1}),exact(1,'乙0929生日',{observation_id:2}),exact(1,'丙1005生日',{observation_id:3}),exact(1,'丁2025年12月1日生日',{observation_id:4}),exact(1,'戊2026-01-01生日',{observation_id:5})];
 assert.deepEqual(occasionRows(rows,'birthday').map(row=>row.observation_id),[5,4,3,2,1]);
 assert.deepEqual(occasionRows(rows,'birthday',{sort:'date_asc'}).map(row=>row.observation_id),[4,5,2,3,1]);
 assert.equal(occasionRows(rows,'birthday',{sort:'clue_sales'})[0].observation_id,1);
 assert.equal(classifyOccasion(rows[1]).dateDetails[0].year,null);
});

test('date search normalizes MMDD, marked and separated dates while a requested year stays exact',()=>{
 const rows=[exact(1,'甲0929生日',{observation_id:1}),exact(2,'乙2026年9月29日生日',{observation_id:2}),exact(3,'丙9-29生日',{observation_id:3}),exact(4,'丁1005生日',{observation_id:4})];
 for(const dateSearch of ['0929','9月29日','09-29','9/29','9.29'])assert.deepEqual(occasionRows(rows,'birthday',{dateSearch}).map(row=>row.observation_id),[2,3,1]);
 for(const dateSearch of ['2026-09-29','2026年9月29日'])assert.deepEqual(occasionRows(rows,'birthday',{dateSearch}).map(row=>row.observation_id),[2]);
 for(const dateSearch of ['2025-09-29','02-30','not a date'])assert.equal(occasionRows(rows,'birthday',{dateSearch}).length,0);
});

test('birthday and activity take their own nearby date in a mixed title',()=>{
 const row=exact(1,'甲10月5日生日 2026-11-01演唱会'),classified=classifyOccasion(row);
 assert.deepEqual(occasionDates(classified,'birthday').map(date=>date.text),['10月5日']);
 assert.deepEqual(occasionDates(classified,'activity').map(date=>date.text),['2026-11-01']);
 assert.equal(occasionRows([row],'birthday',{dateSearch:'11-01'}).length,0);
 assert.equal(occasionRows([row],'activity',{dateSearch:'2026-11-01'}).length,1);
});

test('lunar clues remain searchable but never become Gregorian sorting dates',()=>{
 const rows=[exact(100,'甲农历12月20日生日',{observation_id:1}),exact(1,'乙01月01日生日',{observation_id:2}),exact(0,'丙农历生日',{observation_id:3}),exact(0,'丁生日',{observation_id:4})];
 assert.deepEqual(occasionRows(rows,'birthday').map(row=>row.observation_id),[2,1,3,4]);
 assert.equal(classifyOccasion(rows[0]).dateDetails[0].calendar,'lunar');
 assert.deepEqual(occasionRows(rows,'birthday',{dateScope:'lunar'}).map(row=>row.observation_id),[1,3]);
 assert.deepEqual(occasionRows(rows,'birthday',{dateScope:'dated'}).map(row=>row.observation_id),[2,1]);
 assert.deepEqual(occasionRows(rows,'birthday',{dateScope:'undated'}).map(row=>row.observation_id),[3,4]);
 assert.equal(occasionRows(rows,'birthday',{dateSearch:'12-20'})[0].observation_id,1);
});

test('activity dates sort newest first without using collection or discovery times',()=>{
 const rows=[exact(99,'甲演唱会',{observation_id:1,observed_at:'2026-10-06'}),exact(1,'乙2026-10-05音乐节',{observation_id:2}),exact(2,'丙2026-09-20见面会',{observation_id:3}),exact(3,'丁巡演10-06',{observation_id:4})];
 assert.deepEqual(occasionRows(rows,'activity').map(row=>row.observation_id),[2,3,4,1]);
 assert.deepEqual(occasionRows(rows,'activity',{sort:'date_asc'}).map(row=>row.observation_id),[3,2,4,1]);
 assert.equal(occasionRows(rows,'activity',{dateSearch:'2026-10-06'}).length,0);
});

test('sales category uses reviewed focus models and keeps exact ten in low',()=>{
 assert.equal(occasionRows([{title:'甲生日',category:'yipin_1to10',yipin_value:10}],'birthday',{sales:'gt10'}).length,0);
 assert.equal(occasionRows([{title:'甲生日',category:'yipin_1to10',yipin_value:10}],'birthday',{sales:'low'}).length,1);
});

test('each source card stays independent across same titles, shops and modules',()=>{
 const rows=[exact(5,'甲生日演唱会',{shop_id:'A',observation_id:1}),exact(5,'甲生日演唱会',{shop_id:'A',observation_id:2}),exact(5,'甲生日演唱会',{shop_id:'B',observation_id:3})];
 assert.equal(occasionRows(rows,'birthday').length,3);assert.equal(occasionRows(rows,'activity').length,3);
 assert.deepEqual(occasionRows(rows.slice(0,2),'birthday').map(row=>row.observation_id),[1,2]);
 assert.equal(rows[0].occasion,undefined);
});

test('specific event clues sort before general support even with lower sales',()=>{
 const rows=[exact(999,'乙应援物料',{observation_id:1}),exact(0,'乙演唱会',{observation_id:2}),exact(5,'乙生日礼物',{observation_id:3}),exact(0,'乙生贺',{observation_id:4})];
 assert.deepEqual(occasionRows(rows,'activity').map(row=>row.observation_id),[2,1]);
 assert.deepEqual(occasionRows(rows,'birthday').map(row=>row.observation_id),[4,3]);
});

test('title, source card, and tag filtering work together without changing evidence',()=>{
 const rows=[exact(11,'甲生日演唱会',{observation_id:42}),exact(10,'乙生日',{observation_id:43}),exact(0,'甲生日礼物',{observation_id:44})];
 assert.equal(occasionRows(rows,'birthday',{search:'甲',sales:'gt10',tag:'生日 / 生贺'})[0].observation_id,42);
 assert.equal(occasionRows(rows,'birthday',{search:'43'})[0].observation_id,43);
 assert.equal(occasionRows(rows,'birthday',{tag:'生日送礼'})[0].observation_id,44);
 assert.equal(occasionRows(rows,'activity',{search:'不存在'}).length,0);
});

test('original dates are retained without inferred year, including contextual MMDD',()=>{
 const result=classifyOccasion({title:'甲0929生日 9月29日 2026-09-29 2026年9月29日【5天内发货】'});
 assert.deepEqual(result.dateClues,['0929','9月29日','2026-09-29','2026年9月29日']);
 assert.deepEqual(classifyOccasion({title:'云旗9月25直播应援'}).dateClues,['9月25']);
});

test('sizes, prices, invalid dates and shipping durations are not date clues',()=>{
 const result=classifyOccasion({title:'生日礼物1005个 10.5cm 9/29cm 10-20件 13月40日 2月30日 2026-02-29 15天内发货'});
 assert.deepEqual(result.dateClues,[]);
 assert.deepEqual(classifyOccasion({title:'0229生日 2024-02-29生贺'}).dateClues,['0229','2024-02-29']);
 assert.deepEqual(classifyOccasion({title:'0230生日 1232生日'}).dateClues,[]);
 assert.deepEqual(classifyOccasion({title:'生日立牌9月25日前发货 发货日期：2026-09-29'}).dateClues,[]);
});

test('classification never reads image, discovery date or prior attached classifications',()=>{
 const result=classifyOccasion({title:'普通立牌',display_title:'生日',image_alt:'音乐节',first_observed_at:'2026-10-05',occasion:{birthday:true,activity:true}});
 assert.equal(result.birthday,false);assert.equal(result.activity,false);assert.deepEqual(result.dateClues,[]);
});

test('unknown module and absent title produce empty views, summary totals reconcile',()=>{
 assert.equal(classifyOccasion(null).birthday,false);
 assert.deepEqual(occasionRows([{}],'other'),[]);
 assert.deepEqual(occasionSummary([], 'birthday'),{total:0,gt10:0,low:0,zero:0,unknown:0,tags:[]});
 const summary=occasionSummary([exact(11),exact(0),{title:'甲生日'}],'birthday');
 assert.equal(summary.total,summary.gt10+summary.low+summary.zero+summary.unknown);
});
