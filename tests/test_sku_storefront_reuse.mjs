import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {createBrowserAdapter,readSkuStorefrontCandidate} from '../scripts/local_browser.mjs';
import {prepareSkuShop} from '../scripts/local_sku_capture.mjs';
import {readStorefrontState} from '../scripts/local_storefront.mjs';

const project='C:/SYNTHETIC_ONLY';
const origin='https://mobile.yangkeduo.com';
const sourceUrl=`${origin}/mall_page.html?mall_id=123`;
const shareUrl=`${sourceUrl}&refer_page_name=share&ps=SYNTHETIC_NEW`;
const oldShareUrl=`${origin}/mall_page.html?ps=SYNTHETIC_OLD&mall_id=123#top`;
const originalCard={title:'SYNTHETIC 商品',image:'https://img.pddpic.com/SYNTHETIC_MAIN.png',shopName:'SYNTHETIC_SHOP'};
const result=value=>({structuredContent:{message:`Script ran on page and returned:\n\`\`\`json\n${JSON.stringify(value)}\n\`\`\``}});

function pageSandbox(page){
 const dom=page.dom||{},element=(text='',{top=50,hidden=false,selected=false}={})=>({textContent:text,innerText:text,hidden,isConnected:true,children:[],parentElement:null,className:selected?'active':'',
  getBoundingClientRect:()=>({x:20,y:top,top,bottom:top+100,left:20,right:220,width:200,height:100}),
  getAttribute:()=>null,querySelector:()=>null,querySelectorAll:()=>[],contains(other){return this===other||this.children.some(child=>child.contains(other));}});
 const body=element(dom.text??originalCard.shopName),makeList=value=>{
 const list=element('',value),cards=(value.cards||[]).map(value=>{
  const card=element('',value),title=element(value.title??originalCard.title),image=element();
  image.src=value.image??originalCard.image;image.currentSrc=image.src;image.getAttribute=name=>['src','data-src'].includes(name)?image.src:null;
  card.querySelector=selector=>selector==='.goodsName_dT8mZSNd'?title:selector==='.goodsImage_fU27dxbk img'?image:null;
  card.children=[title,image];title.parentElement=card;image.parentElement=card;card.parentElement=list;return card;
 });
 list.children=cards;list.querySelectorAll=selector=>selector==='.goodsItem_R1ok0MpS'?cards:[];list.parentElement=body;return list;
 };
 const markers=dom.boundaryTop===undefined?[]:[element('其他店铺的精选推荐',{top:dom.boundaryTop})];
 const modals=dom.modalVisible===undefined?[]:[element('',{hidden:!dom.modalVisible})];
 const listSpecs=dom.lists??[{cards:dom.cards||[]},...Array.from({length:dom.extraLists||0},()=>({top:600}))];
 const lists=dom.noList?[]:listSpecs.map(makeList),cards=lists.flatMap(list=>list.children);
 const latestNodes=[],latestRoots=[];
 for(const control of dom.latestControls??[{selected:dom.latestSelected===true}]){
  const latest=element('上新',control),parent=element(control.nested?'上新':'',{selected:control.parentSelected===true,hidden:control.parentHidden===true});
  if(control.ariaSelected)latest.getAttribute=name=>name==='aria-selected'?'true':null;
  parent.children=[latest];latest.parentElement=parent;parent.parentElement=body;latestRoots.push(parent);latestNodes.push(latest);if(control.nested)latestNodes.push(parent);
 }
 body.children=[...lists,...markers,...modals,...latestRoots];
 const document={body,querySelector:selector=>selector==='.waterfall-list-container_egohG8wQ'?lists[0]||null:null,
  querySelectorAll:selector=>selector==='.waterfall-list-container_egohG8wQ'?lists:selector==='.goodsItem_R1ok0MpS'?cards:selector.startsWith('.sku-plus1')?modals:selector==='div,p,span'?markers:selector==='button,a,[role="button"],[role="tab"],li,div,span'?latestNodes:[]};
 return {URL,location:new URL(page.actualUrl??page.url),document,innerWidth:1280,innerHeight:731,scrollY:0,window:{scrollY:0},
  getComputedStyle:node=>({display:node.hidden?'none':'block',visibility:'visible',opacity:'1'})};
}

// The adapter receives an in-memory MCP client; no browser or filesystem is used.
function fakeBrowser(initialPages,options={}){
 let pages=initialPages.map(page=>({...page})),closed=0;
 const calls=[];
 return {calls,get closed(){return closed;},async close(){closed++;},async callTool(name,args={},timeoutMs){
  calls.push({name,args,timeoutMs});
  if(name==='list_pages')return {structuredContent:{pages}};
  if(name==='select_page'){assert.ok(pages.some(page=>page.id===args.pageId));return {structuredContent:{pages}};}
  if(name==='new_page'){pages=[...pages.map(page=>({...page,selected:false})),...(options.newPages||[{id:99,url:options.newPageUrl??args.url,selected:options.newPageSelected??true,...(options.newPageActualUrl?{actualUrl:options.newPageActualUrl}:{})}])];return {structuredContent:{pages}};}
  if(name==='navigate_page'){const page=pages.find(page=>page.id===args.pageId);assert.ok(page);if(args.type==='url'){page.url=args.url;delete page.actualUrl;}return {structuredContent:{message:'Successfully navigated'}};}
  if(name==='evaluate_script'){const page=pages.find(page=>page.id===args.pageId);assert.ok(page);return result(await vm.runInNewContext(`(${args.function})()`,pageSandbox(page)));}
  if(name==='close_page')return {structuredContent:{message:'Done'}};
  assert.fail(`Unexpected fake MCP tool: ${name}`);
 }};
}
const open=(client,options={})=>createBrowserAdapter(client,project,{entryUrl:shareUrl,verifiedStorefrontUrl:sourceUrl,sleep:async()=>{},...options});
const selectedIds=client=>client.calls.filter(call=>call.name==='select_page').map(call=>call.args.pageId);
async function assertNewOwnedPage(browser,client,entryUrl=shareUrl){
 assert.equal(browser.tab.id,null);assert.deepEqual(selectedIds(client),[]);
 await browser.goto(entryUrl);assert.equal(browser.tab.id,99);
 const create=client.calls.find(call=>call.name==='new_page');assert.equal(create.args.url,entryUrl);assert.equal(create.args.isolatedContext,undefined);
 await browser.close(true);assert.deepEqual(client.calls.filter(call=>call.name==='close_page').map(call=>call.args.pageId),[99]);
}

test('verified storefront reuses a unique same-shop page across sharing parameters without navigation or ownership',async()=>{
 const client=fakeBrowser([{id:1,url:`${origin}/mall_page.html?mall_id=456`,selected:true},{id:7,url:oldShareUrl,selected:false}]),browser=await open(client);
 assert.equal(browser.tab.id,7);assert.deepEqual(selectedIds(client),[7]);assert.equal(await browser.evaluate(()=>location.href),oldShareUrl);
 assert.equal(client.calls.some(call=>['navigate_page','new_page'].includes(call.name)),false);
 await browser.close(true);assert.equal(client.calls.some(call=>call.name==='close_page'),false);assert.equal(client.closed,1);
});

test('one selected semantic match wins over another exact entry URL match',async()=>{
 const client=fakeBrowser([{id:4,url:shareUrl,selected:false},{id:7,url:oldShareUrl,selected:true}]),browser=await open(client);
 assert.equal(browser.tab.id,7);assert.deepEqual(selectedIds(client),[7]);await browser.close(true);
 assert.equal(client.calls.some(call=>call.name==='new_page'||call.name==='close_page'),false);
});

test('selected page from another shop cannot displace the unique matching ordinary storefront',async()=>{
 const client=fakeBrowser([{id:2,url:`${origin}/mall_page.html?mall_id=456`,title:'SAME SHOP NAME',selected:true},{id:7,url:oldShareUrl,title:'SAME SHOP NAME',selected:false}]),browser=await open(client);
 assert.equal(browser.tab.id,7);assert.deepEqual(selectedIds(client),[7]);await browser.close();
});

test('same visible shop name alone never reuses another shop page',async()=>{
 const client=fakeBrowser([{id:2,url:`${origin}/mall_page.html?mall_id=456`,title:'SYNTHETIC SAME NAME',selected:true}]),browser=await open(client);
 await assertNewOwnedPage(browser,client);
});

test('mall_id and mall_sn are separate identities even when their string values match',async()=>{
 for(const [verified,candidate] of [[sourceUrl,`${origin}/mall_page.html?mall_sn=123`],[`${origin}/mall_page.html?mall_sn=123`,sourceUrl]]){
  const client=fakeBrowser([{id:7,url:candidate,selected:true}]),browser=await open(client,{verifiedStorefrontUrl:verified});
  await assertNewOwnedPage(browser,client);
 }
 const verified=`${origin}/mall_page.html?mall_sn=SYNTHETIC_SN`,entry=`${verified}&ps=NEW`,client=fakeBrowser([{id:8,url:`${verified}&ps=OLD`,selected:false}]),browser=await open(client,{verifiedStorefrontUrl:verified,entryUrl:entry});
 assert.equal(browser.tab.id,8);assert.deepEqual(selectedIds(client),[8]);await browser.close();
});

test('multiple unselected semantic matches do not fall back to one exact URL candidate',async()=>{
 const client=fakeBrowser([{id:4,url:shareUrl,selected:false},{id:7,url:oldShareUrl,selected:false}]),browser=await open(client);
 await assertNewOwnedPage(browser,client);
});

test('contradictory selected semantic matches remain ambiguous and create a separate ordinary page',async()=>{
 const client=fakeBrowser([{id:4,url:shareUrl,selected:true},{id:7,url:oldShareUrl,selected:true}]),browser=await open(client);
 await assertNewOwnedPage(browser,client);
});

test('isolated same-shop tabs are excluded before choosing the unique ordinary candidate',async()=>{
 const client=fakeBrowser([{id:4,url:shareUrl,selected:true,isolatedContext:'private-context'},{id:7,url:oldShareUrl,selected:false}]),browser=await open(client);
 assert.equal(browser.tab.id,7);assert.deepEqual(selectedIds(client),[7]);await browser.close(true);
 assert.equal(client.calls.some(call=>call.name==='close_page'),false);
});

test('isolated, non-storefront, unsafe-origin and unresolved-share pages cannot be semantic matches',async()=>{
 const candidates=[
  {url:shareUrl,isolatedContext:'private-context'},
  {url:`${origin}/goods.html?mall_id=123&goods_id=88`},
  {url:`${origin}/goods1.html?mall_id=123&goods_id=88`},
  {url:`${origin}/mall_page.html/other?mall_id=123`},
  {url:'http://mobile.yangkeduo.com/mall_page.html?mall_id=123'},
  {url:'https://mobile.yangkeduo.com:8443/mall_page.html?mall_id=123'},
  {url:'https://example.org/mall_page.html?mall_id=123'},
  {url:'https://user:pass@mobile.yangkeduo.com/mall_page.html?mall_id=123'},
  {url:`${origin}/mall_page.html?ps=SYNTHETIC_ONLY`},
  {url:`${origin}/mall_page.html?mall_id=123&mall_id=456`},
  {url:'not a URL'},
 ];
 for(const candidate of candidates){
  const client=fakeBrowser([{id:7,selected:true,...candidate}]),browser=await open(client);
  await assertNewOwnedPage(browser,client);
 }
});

test('valid typed identity does not use a ps-only exact entry tab as semantic fallback',async()=>{
 const entryUrl=`${origin}/mall_page.html?ps=SYNTHETIC_ONLY`,client=fakeBrowser([{id:7,url:entryUrl,selected:true}]),browser=await open(client,{entryUrl});
 await assertNewOwnedPage(browser,client,entryUrl);
});

test('without verifiedStorefrontUrl only exact URL reuse remains allowed',async()=>{
 const exactClient=fakeBrowser([{id:3,url:shareUrl,selected:false},{id:7,url:oldShareUrl,selected:true}]),exact=await open(exactClient,{verifiedStorefrontUrl:undefined});
 assert.equal(exact.tab.id,3);assert.deepEqual(selectedIds(exactClient),[3]);await exact.close();
 const differentClient=fakeBrowser([{id:7,url:oldShareUrl,selected:true}]),different=await open(differentClient,{verifiedStorefrontUrl:undefined});
 await assertNewOwnedPage(different,differentClient);
});

test('invalid verified identity preserves exact matching and never derives an identity from ps or names',async()=>{
 const invalidSources=[undefined,null,'not a URL',`${origin}/mall_page.html?ps=UNRESOLVED`,`${origin}/mall_page.html?mall_id=0`,`${origin}/mall_page.html?mall_id=123&mall_id=456`];
 for(const verifiedStorefrontUrl of invalidSources){
  const client=fakeBrowser([{id:3,url:shareUrl,selected:false},{id:7,url:oldShareUrl,selected:true}]),browser=await open(client,{verifiedStorefrontUrl});
  assert.equal(browser.tab.id,3);assert.deepEqual(selectedIds(client),[3]);await browser.close();
 }
 const entryUrl=`${origin}/mall_page.html?ps=SYNTHETIC_ONLY`,client=fakeBrowser([{id:6,url:entryUrl,selected:true}]),browser=await open(client,{entryUrl,verifiedStorefrontUrl:entryUrl});
 assert.equal(browser.tab.id,6);assert.deepEqual(selectedIds(client),[6]);await browser.close(true);assert.equal(client.calls.some(call=>call.name==='close_page'),false);
 const otherClient=fakeBrowser([{id:6,url:`${origin}/mall_page.html?ps=OTHER`,selected:true}]),other=await open(otherClient,{entryUrl,verifiedStorefrontUrl:entryUrl});
 await assertNewOwnedPage(other,otherClient,entryUrl);
});

test('original-card DOM probing picks the only qualified page among unselected same-shop tabs',async()=>{
 const client=fakeBrowser([{id:4,url:shareUrl,selected:false,dom:{cards:[]}},{id:7,url:oldShareUrl,selected:false,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard});
 assert.equal(browser.tab.id,7);assert.deepEqual(selectedIds(client),[7]);assert.deepEqual(client.calls.filter(call=>call.name==='evaluate_script').map(call=>call.args.pageId),[4,7]);
 assert.equal(client.calls.some(call=>call.name==='navigate_page'||call.name==='new_page'),false);await browser.close(true);assert.equal(client.calls.some(call=>call.name==='close_page'),false);
});

test('multiple DOM-equivalent original-card pages choose the largest safe page id without selected evidence',async()=>{
 const client=fakeBrowser([{id:14,url:oldShareUrl,selected:false,dom:{cards:[{}]}},{id:7,url:shareUrl,selected:false,dom:{cards:[{}]}},{id:23,url:sourceUrl,selected:false,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard});
 assert.equal(browser.tab.id,23);assert.deepEqual(selectedIds(client),[23]);assert.equal(client.calls.filter(call=>call.name==='evaluate_script').length,3);await browser.close();
});

test('a unique selected DOM-qualified page wins over a larger equivalent page id',async()=>{
 const client=fakeBrowser([{id:4,url:shareUrl,selected:true,dom:{cards:[{}]}},{id:27,url:oldShareUrl,selected:false,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard});
 assert.equal(browser.tab.id,4);assert.deepEqual(selectedIds(client),[4]);await browser.close();
});

test('a selected but unqualified card cannot displace a verified unselected page',async()=>{
 const client=fakeBrowser([{id:24,url:shareUrl,selected:true,dom:{cards:[{image:'https://img.pddpic.com/DIFFERENT.png'}]}},{id:7,url:oldShareUrl,selected:false,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard});
 assert.equal(browser.tab.id,7);assert.deepEqual(selectedIds(client),[7]);await browser.close();
});

test('DOM reuse refuses wrong image, title, shop, existing modal, login, challenge and ambiguous or recommended cards',async()=>{
 const cases=[
  {cards:[{image:'https://img.pddpic.com/DIFFERENT.png'}]},
  {cards:[{title:'DIFFERENT TITLE'}]},
  {cards:[{}],text:'OTHER_SHOP'},
  {cards:[{}],modalVisible:true},
  {cards:[{}],text:`${originalCard.shopName} 请先登录`},
  {cards:[{}],text:`${originalCard.shopName} 请完成安全验证`},
  {cards:[{},{}]},
  {cards:[{top:500}],boundaryTop:400},
  {cards:[{hidden:true}]},
  {cards:[{}],noList:true},
 ];
 for(const dom of cases){const client=fakeBrowser([{id:7,url:oldShareUrl,selected:true,dom}]),browser=await open(client,{verifiedOriginalCard:originalCard});await assertNewOwnedPage(browser,client);}
});

test('a hidden prior modal and same-title recommendation beyond the boundary do not replace the unique original card',async()=>{
 const client=fakeBrowser([{id:7,url:oldShareUrl,selected:false,dom:{cards:[{}, {top:500}],boundaryTop:400,modalVisible:false}}]),browser=await open(client,{verifiedOriginalCard:originalCard});
 assert.equal(browser.tab.id,7);await browser.close();
});

test('an additional recommendation list does not exclude the verified original storefront card',async()=>{
 const client=fakeBrowser([{id:32,url:oldShareUrl,selected:false,dom:{cards:[{}],boundaryTop:400,extraLists:1}}]),browser=await open(client,{verifiedOriginalCard:originalCard});
 assert.equal(browser.tab.id,32);assert.deepEqual(selectedIds(client),[32]);await browser.close();
});

test('DOM-probe returned URL must still match the typed verified storefront identity',async()=>{
 for(const actualUrl of [`${origin}/mall_page.html?mall_id=456`,`${origin}/mall_page.html?mall_sn=123`,`${origin}/goods.html?mall_id=123&goods_id=5`,'https://example.org/mall_page.html?mall_id=123']){
  const client=fakeBrowser([{id:7,url:shareUrl,actualUrl,selected:true,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard});await assertNewOwnedPage(browser,client);
 }
});

test('missing original-card fields or typed storefront identity cannot enable DOM-based ambiguity resolution',async()=>{
 for(const verifiedOriginalCard of [undefined,null,{}, {...originalCard,title:''}, {...originalCard,image:' '}, {...originalCard,shopName:''}]){
  const client=fakeBrowser([{id:4,url:shareUrl,selected:false,dom:{cards:[{}]}},{id:7,url:oldShareUrl,selected:false,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard});
  assert.equal(client.calls.some(call=>call.name==='evaluate_script'),false);await assertNewOwnedPage(browser,client);
 }
 const client=fakeBrowser([{id:4,url:shareUrl,selected:false,dom:{cards:[{}]}},{id:7,url:shareUrl,selected:false,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard,verifiedStorefrontUrl:`${origin}/mall_page.html?ps=UNRESOLVED`});
 assert.equal(client.calls.some(call=>call.name==='evaluate_script'),false);await assertNewOwnedPage(browser,client);
});

test('new-page ownership uses its unique new ordinary id even when selected is false',async()=>{
 const client=fakeBrowser([],{newPageSelected:false}),browser=await open(client);await assertNewOwnedPage(browser,client);
});

test('single-SKU keepPage retains its owned storefront page while still releasing the client exactly once',async()=>{
 const client=fakeBrowser([],{newPageSelected:false}),browser=await open(client);
 await browser.goto(shareUrl);assert.equal(browser.tab.id,99);await browser.close(true,{keepPage:true});
 assert.equal(client.calls.filter(call=>call.name==='new_page').length,1);assert.equal(client.calls.some(call=>call.name==='close_page'),false);assert.equal(client.closed,1);
 await browser.close(true,{keepPage:true});assert.equal(client.closed,1);
});

test('multiple new ordinary page ids are rejected without binding or closing either candidate',async()=>{
 const client=fakeBrowser([],{newPages:[{id:98,url:shareUrl,selected:false},{id:99,url:shareUrl,selected:true}]}),browser=await open(client);
 await assert.rejects(browser.goto(shareUrl));assert.equal(client.calls.some(call=>call.name==='navigate_page'),false);await browser.close(true);assert.equal(client.calls.some(call=>call.name==='close_page'),false);
});

test('a new-page listing URL mismatch is accepted after a read-only exact live entry check',async()=>{
 const client=fakeBrowser([],{newPageUrl:'about:blank',newPageActualUrl:shareUrl,newPageSelected:false}),browser=await open(client);
 await assertNewOwnedPage(browser,client);const reads=client.calls.filter(call=>call.name==='evaluate_script');assert.ok(reads.length>=1);assert.ok(reads.every(call=>call.args.pageId===99));assert.equal(client.calls.some(call=>call.name==='navigate_page'),false);
});

test('a new page redirected to the verified typed storefront is accepted after live URL verification',async()=>{
 const client=fakeBrowser([],{newPageUrl:oldShareUrl,newPageSelected:false}),browser=await open(client);
 await assertNewOwnedPage(browser,client);assert.ok(client.calls.some(call=>call.name==='evaluate_script'&&call.args.pageId===99));assert.equal(client.calls.some(call=>call.name==='navigate_page'),false);
});

test('full-shop opaque ps share entry accepts only its verified typed redirect',async()=>{
 const entryUrl=`${origin}/mall_page.html?ps=SYNTHETIC_ONLY`;
 const client=fakeBrowser([],{newPageUrl:oldShareUrl,newPageSelected:false}),browser=await open(client,{entryUrl});
 assert.equal(browser.executionEvidence.pageSelection.originalCardVerified,false);
 assert.equal(browser.executionEvidence.pageSelection.sortPolicy,'require_latest');
 await assertNewOwnedPage(browser,client,entryUrl);
});

test('opaque ps entry without a verified canonical cannot authorize its redirect',async()=>{
 const entryUrl=`${origin}/mall_page.html?ps=SYNTHETIC_ONLY`;
 const client=fakeBrowser([],{newPageUrl:oldShareUrl}),browser=await open(client,{entryUrl,verifiedStorefrontUrl:undefined});
 await assert.rejects(browser.goto(entryUrl),error=>error.reason==='browser_operation_failed');
 assert.equal(browser.tab.id,null);await browser.close(false);
});

test('opaque ps entry rejects redirected wrong store, identity kind and foreign origin',async()=>{
 const entryUrl=`${origin}/mall_page.html?ps=SYNTHETIC_ONLY`;
 for(const address of [`${origin}/mall_page.html?mall_id=456`,`${origin}/mall_page.html?mall_sn=123`,'https://example.org/private']){
  const client=fakeBrowser([],{newPageUrl:address}),browser=await open(client,{entryUrl});
  await assert.rejects(browser.goto(entryUrl),error=>error.reason==='browser_operation_failed');
  assert.equal(browser.tab.id,null);await browser.close(false);
 }
});

test('new page showing another shop, typed identity, goods URL or origin is rejected',async()=>{
 for(const address of [`${origin}/mall_page.html?mall_id=456`,`${origin}/mall_page.html?mall_sn=123`,`${origin}/goods.html?goods_id=88&mall_id=123`,'https://example.org/private','about:blank']){
  const client=fakeBrowser([],{newPageUrl:address,newPageSelected:false}),browser=await open(client);
  await assert.rejects(browser.goto(shareUrl));assert.equal(client.calls.some(call=>call.name==='navigate_page'),false);await browser.close(true);assert.equal(client.calls.some(call=>call.name==='close_page'),false);
 }
});

const probeReasons=new Set(['qualified','verified_storefront_resume','source_list_ambiguous','source_list_missing','source_sort_unverified','invalid_url','source_mismatch','identity_ambiguous','identity_mismatch','original_card_missing','document_unavailable','shop_name_mismatch','login_required','access_restricted','visible_sku_modal','no_storefront_list','card_not_found','original_image_mismatch','card_hidden','card_after_boundary','card_ambiguous','non_primary_list','probe_unverified']);
function assertSelectionShape(selection){
 assert.deepEqual(Object.keys(selection).sort(),['strategy','candidateCount','qualifiedCount','resumeCandidateCount','selectedPageId','selectionReason','decision','originalCardVerified','sortPolicy','probes'].sort());
 assert.ok(['original_card','typed_storefront','exact_url'].includes(selection.strategy));
 for(const key of ['candidateCount','qualifiedCount','resumeCandidateCount'])assert.ok(Number.isSafeInteger(selection[key])&&selection[key]>=0);
 assert.ok(['original_card','verified_storefront_resume','typed_storefront','exact_url','fresh_required'].includes(selection.selectionReason));
 assert.ok(selection.selectedPageId===null||Number.isSafeInteger(selection.selectedPageId));
 assert.ok(['reused','fresh_required'].includes(selection.decision));assert.equal(typeof selection.originalCardVerified,'boolean');
 assert.ok(['preserve_verified_original_card','require_latest'].includes(selection.sortPolicy));assert.ok(Array.isArray(selection.probes));
 for(const probe of selection.probes){
  assert.deepEqual(Object.keys(probe).sort(),['pageId','verified','sourceVerified','latestSelected','loadedCardCount','reason','matchingCardCount','visibleModalCount','listCount'].sort());
  assert.ok(Number.isSafeInteger(probe.pageId));assert.equal(typeof probe.verified,'boolean');assert.ok(probeReasons.has(probe.reason));
  assert.equal(typeof probe.sourceVerified,'boolean');assert.ok(probe.latestSelected===null||typeof probe.latestSelected==='boolean');
  for(const key of ['matchingCardCount','visibleModalCount','listCount','loadedCardCount'])assert.ok(probe[key]===null||Number.isSafeInteger(probe[key])&&probe[key]>=0&&probe[key]<=1000000);
 }
}

test('selection diagnostics record only fixed decision metadata and bounded probe results without page text or source fields',async()=>{
 const client=fakeBrowser([
  {id:4,url:shareUrl,selected:false,title:'SYNTHETIC_PRIVATE_TITLE',dom:{cards:[{}],text:`${originalCard.shopName} 请先登录 SYNTHETIC_PRIVATE_BODY`}},
  {id:7,url:oldShareUrl,selected:false,dom:{cards:[{}]}},
 ]),browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
 assertSelectionShape(selection);assert.equal(selection.strategy,'original_card');assert.equal(selection.candidateCount,2);assert.equal(selection.qualifiedCount,1);assert.equal(selection.resumeCandidateCount,0);assert.equal(selection.selectionReason,'original_card');assert.equal(selection.selectedPageId,7);assert.equal(selection.decision,'reused');assert.equal(selection.originalCardVerified,true);assert.equal(selection.sortPolicy,'preserve_verified_original_card');
 assert.deepEqual(selection.probes.map(probe=>[probe.pageId,probe.verified]),[[4,false],[7,true]]);assert.equal(selection.probes[1].matchingCardCount,1);assert.equal(selection.probes[1].visibleModalCount,0);assert.equal(selection.probes[1].listCount,1);
 const serialized=JSON.stringify(selection);for(const secret of ['SYNTHETIC_PRIVATE',originalCard.title,originalCard.image,originalCard.shopName,'https://'])assert.equal(serialized.includes(secret),false);
 await browser.close();
});

test('probe diagnostics reject untyped or different sources before reading DOM and never return private text',()=>{
 const args={...originalCard,identity:{origin,identity_kind:'mall_id',stable_identifier:'123'}},read=context=>JSON.parse(JSON.stringify(vm.runInNewContext(`(${readSkuStorefrontCandidate.toString()})(${JSON.stringify(args)})`,context)));
 for(const [href,reason] of [[`${origin}/mall_page.html?mall_sn=123`,'identity_mismatch'],['https://example.org/mall_page.html?mall_id=123','source_mismatch'],[`${origin}/mall_page.html?mall_id=123&mall_id=456`,'identity_ambiguous']]){
  const context=pageSandbox({url:href});context.document=new Proxy({},{get(){assert.fail('unverified source must not read DOM');}});
  const observed=read(context);assert.equal(observed.verified,false);assert.equal(observed.sourceVerified,false);assert.equal(observed.latestSelected,null);assert.equal(observed.loadedCardCount,null);assert.equal(observed.reason,reason);assert.equal(observed.matchingCardCount,null);assert.equal(observed.visibleModalCount,null);assert.equal(observed.listCount,null);
 }
 for(const [text,reason] of [[{secret:'SYNTHETIC_PRIVATE'},'document_unavailable'],['OTHER_SHOP SYNTHETIC_PRIVATE','shop_name_mismatch'],[`${originalCard.shopName} 请先登录 SYNTHETIC_PRIVATE`,'login_required'],[`${originalCard.shopName} 请完成安全验证 SYNTHETIC_PRIVATE`,'access_restricted']]){
  const observed=read(pageSandbox({url:sourceUrl,dom:{text,cards:[{}]}}));assert.equal(observed.verified,false);assert.equal(observed.reason,reason);
  assert.equal(observed.sourceVerified,false);assert.deepEqual(Object.keys(observed).sort(),['verified','sourceVerified','latestSelected','loadedCardCount','url','reason','matchingCardCount','visibleModalCount','listCount'].sort());assert.equal(JSON.stringify(observed).includes('SYNTHETIC_PRIVATE'),false);
 }
});

test('adapter sanitizes unrecognized probe reasons, extra fields and malformed count values',async()=>{
 const client=fakeBrowser([{id:7,url:oldShareUrl,selected:true,dom:{cards:[{}]}}]),callTool=client.callTool.bind(client);
 client.callTool=async(name,args,timeout)=>name==='evaluate_script'?result({verified:false,sourceVerified:'true',latestSelected:1,loadedCardCount:1000001,url:oldShareUrl,reason:'SYNTHETIC_PRIVATE',matchingCardCount:'1',visibleModalCount:1000001,listCount:-1,raw_text:'SYNTHETIC_PRIVATE',cookies:{secret:'SYNTHETIC_PRIVATE'}}):callTool(name,args,timeout);
 const browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
 assertSelectionShape(selection);assert.deepEqual(selection.probes,[{pageId:7,verified:false,sourceVerified:false,latestSelected:null,loadedCardCount:null,reason:'probe_unverified',matchingCardCount:null,visibleModalCount:null,listCount:null}]);assert.equal(JSON.stringify(selection).includes('SYNTHETIC_PRIVATE'),false);assert.equal(selection.resumeCandidateCount,0);assert.equal(selection.selectionReason,'fresh_required');assert.equal(selection.decision,'fresh_required');await browser.close();
});

test('typed and exact URL strategies report no original-card verification or sorting bypass',async()=>{
 for(const [options,strategy] of [[{},'typed_storefront'],[{verifiedStorefrontUrl:undefined},'exact_url']]){
  const client=fakeBrowser([{id:7,url:shareUrl,selected:true}]),browser=await open(client,options),selection=browser.executionEvidence.pageSelection;
  assertSelectionShape(selection);assert.equal(selection.strategy,strategy);assert.equal(selection.selectionReason,strategy);assert.equal(selection.resumeCandidateCount,0);assert.equal(selection.candidateCount,1);assert.equal(selection.qualifiedCount,1);assert.equal(selection.selectedPageId,7);assert.equal(selection.decision,'reused');assert.equal(selection.originalCardVerified,false);assert.equal(selection.sortPolicy,'require_latest');assert.deepEqual(selection.probes,[]);await browser.close();
 }
 const client=fakeBrowser([{id:7,url:oldShareUrl,selected:true,dom:{cards:[]}}]),browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
 assertSelectionShape(selection);assert.equal(selection.strategy,'original_card');assert.equal(selection.candidateCount,1);assert.equal(selection.qualifiedCount,0);assert.equal(selection.selectedPageId,null);assert.equal(selection.decision,'fresh_required');assert.equal(selection.originalCardVerified,false);assert.equal(selection.sortPolicy,'require_latest');await browser.close();
});

test('sorting preservation is bound to the initially verified typed storefront and exact original-card fields',async()=>{
 const client=fakeBrowser([{id:7,url:oldShareUrl,selected:false,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard});
 const args={sourceUrl,currentUrl:oldShareUrl,...originalCard};assert.equal(browser.canPreserveSkuStorefront(args),true);assert.equal(browser.canPreserveSkuStorefront({...args,currentUrl:shareUrl}),true);
 for(const patch of [{sourceUrl:`${origin}/mall_page.html?mall_id=456`},{currentUrl:`${origin}/mall_page.html?mall_id=456`},{currentUrl:`${origin}/mall_page.html?mall_sn=123`},{title:'OTHER_CARD'},{image:'https://img.pddpic.com/OTHER.png'},{shopName:'OTHER_SHOP'},{title:undefined},{image:''},{shopName:''},{sourceUrl:undefined},{currentUrl:undefined}])assert.equal(browser.canPreserveSkuStorefront({...args,...patch}),false);
 await browser.close();assert.equal(browser.canPreserveSkuStorefront(args),false);
 for(const options of [{},{verifiedOriginalCard:{...originalCard,image:''}},{verifiedOriginalCard:originalCard,verifiedStorefrontUrl:undefined}]){
  const otherClient=fakeBrowser([{id:8,url:shareUrl,selected:true,dom:{cards:[{}]}}]),other=await open(otherClient,options);assert.equal(other.canPreserveSkuStorefront(args),false);await other.close();
 }
});

const skuRequest=(patch={})=>({kind:'sku',shop:{source_url:sourceUrl,shop_name:originalCard.shopName},observation:{title:originalCard.title,image_url:originalCard.image},...patch});
function prepareFixture(browser,{url=oldShareUrl,latestSelected=false}={}){
 const state={url,latestSelected,reads:0,clicks:[],navigations:[]},evaluate=browser.evaluate.bind(browser),goto=browser.goto.bind(browser);
 browser.evaluate=async(fn,arg)=>{
  if(fn!==readStorefrontState)return evaluate(fn,arg);
  state.reads++;return {url:state.url,shopVerified:true,cards:1,latestSelected:state.latestSelected,latestPoint:{x:100,y:50},empty:false};
 };
 browser.click=async point=>{state.clicks.push(point);state.latestSelected=true;};
 browser.goto=async address=>{state.navigations.push(address);await goto(address);state.url=address;};
 return state;
}

test('prepareSkuShop preserves non-latest sorting only for the same DOM-verified original card',async()=>{
 const client=fakeBrowser([{id:7,url:oldShareUrl,selected:false,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard}),state=prepareFixture(browser);
 await prepareSkuShop(browser,skuRequest(),async()=>false);
 assert.equal(state.latestSelected,false);assert.equal(state.reads,0);assert.deepEqual(state.clicks,[]);assert.deepEqual(state.navigations,[]);await browser.close();
});

test('prepareSkuShop keeps the original latest-sort checks for unverified, different or incomplete card requests',async()=>{
 const cases=[{options:{}},{options:{verifiedOriginalCard:originalCard},request:skuRequest({observation:{title:'OTHER_CARD',image_url:originalCard.image}})},
  {options:{verifiedOriginalCard:originalCard},request:skuRequest({observation:{title:originalCard.title}})},
  {options:{verifiedOriginalCard:originalCard},removeMethod:true}];
 for(const value of cases){
  const client=fakeBrowser([{id:7,url:oldShareUrl,selected:true,dom:{cards:[{}]}}]),browser=await open(client,value.options),state=prepareFixture(browser);
  if(value.removeMethod)delete browser.canPreserveSkuStorefront;
  await prepareSkuShop(browser,value.request||skuRequest(),async()=>false);
  assert.ok(state.reads>=2);assert.deepEqual(state.clicks,[{x:100,y:50}]);assert.equal(state.latestSelected,true);assert.deepEqual(state.navigations,[]);await browser.close();
 }
});

test('prepareSkuShop navigates and verifies the requested other storefront instead of reusing initial card proof',async()=>{
 const otherUrl=`${origin}/mall_page.html?mall_id=456`,client=fakeBrowser([{id:7,url:oldShareUrl,selected:true,dom:{cards:[{}]}}]),browser=await open(client,{verifiedOriginalCard:originalCard}),state=prepareFixture(browser);
 await prepareSkuShop(browser,skuRequest({shop:{source_url:otherUrl,shop_name:originalCard.shopName}}),async()=>false);
 assert.deepEqual(state.navigations,[otherUrl]);assert.ok(state.reads>=2);assert.deepEqual(state.clicks,[{x:100,y:50}]);assert.equal(state.latestSelected,true);await browser.close();
});

const otherCards=count=>Array.from({length:count},(_,i)=>({title:`OTHER_CARD_${i}`,image:`https://img.pddpic.com/OTHER_${i}.png`}));
function readCandidate(dom,{url=oldShareUrl,actualUrl}={}){
 const args={...originalCard,identity:{origin,identity_kind:'mall_id',stable_identifier:'123'}};
 return JSON.parse(JSON.stringify(vm.runInNewContext(`(${readSkuStorefrontCandidate.toString()})(${JSON.stringify(args)})`,pageSandbox({url,actualUrl,dom}))));
}

test('source-only verified latest storefront continues preparation without navigation, sorting or original-card proof',async()=>{
 const client=fakeBrowser([{id:7,url:oldShareUrl,selected:false,dom:{cards:otherCards(3),latestSelected:true}}]),browser=await open(client,{verifiedOriginalCard:originalCard});
 const selection=browser.executionEvidence.pageSelection;assertSelectionShape(selection);
 assert.equal(browser.tab.id,7);assert.deepEqual(selectedIds(client),[7]);assert.equal(selection.qualifiedCount,0);assert.equal(selection.resumeCandidateCount,1);assert.equal(selection.selectionReason,'verified_storefront_resume');assert.equal(selection.originalCardVerified,false);assert.equal(selection.sortPolicy,'require_latest');
 assert.deepEqual(selection.probes,[{pageId:7,verified:false,sourceVerified:true,latestSelected:true,loadedCardCount:3,reason:'verified_storefront_resume',matchingCardCount:0,visibleModalCount:0,listCount:1}]);
 assert.equal(browser.canPreserveSkuStorefront({sourceUrl,currentUrl:oldShareUrl,...originalCard}),false);
 let stateReads=0;const evaluate=browser.evaluate.bind(browser);browser.evaluate=async(fn,arg)=>{if(fn===readStorefrontState)stateReads++;return evaluate(fn,arg);};
 await prepareSkuShop(browser,skuRequest(),async()=>false);
 assert.equal(stateReads,1);assert.equal(client.calls.some(call=>['new_page','navigate_page','click_at'].includes(call.name)),false);
 await browser.close(true);assert.equal(client.calls.some(call=>call.name==='close_page'),false);assert.equal(client.closed,1);
});

test('source-only resume chooses most loaded cards then largest page id regardless of selected metadata',async()=>{
 const client=fakeBrowser([
  {id:90,url:shareUrl,selected:true,dom:{cards:otherCards(2),latestSelected:true}},
  {id:7,url:oldShareUrl,selected:false,dom:{cards:otherCards(4),latestSelected:true}},
  {id:31,url:sourceUrl,selected:false,dom:{cards:otherCards(4),latestSelected:true}},
 ]),browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
 assert.equal(browser.tab.id,31);assert.deepEqual(selectedIds(client),[31]);assertSelectionShape(selection);assert.equal(selection.resumeCandidateCount,3);assert.equal(selection.qualifiedCount,0);assert.equal(selection.selectionReason,'verified_storefront_resume');
 assert.deepEqual(selection.probes.map(probe=>[probe.pageId,probe.loadedCardCount]),[[90,2],[7,4],[31,4]]);await browser.close();
});

test('a complete original-card match takes priority over selected and more loaded source-only pages',async()=>{
 const client=fakeBrowser([
  {id:90,url:shareUrl,selected:true,dom:{cards:otherCards(8),latestSelected:true}},
  {id:7,url:oldShareUrl,selected:false,dom:{cards:[{}]}},
 ]),browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
 assert.equal(browser.tab.id,7);assert.equal(selection.selectionReason,'original_card');assert.equal(selection.qualifiedCount,1);assert.equal(selection.resumeCandidateCount,1);assert.equal(selection.originalCardVerified,true);assert.equal(selection.sortPolicy,'preserve_verified_original_card');assert.equal(browser.canPreserveSkuStorefront({sourceUrl,currentUrl:oldShareUrl,...originalCard}),true);await browser.close();
});

test('source-only DOM proof accepts same-title other-image cards without claiming an original match',()=>{
 const observed=readCandidate({cards:[{image:'https://img.pddpic.com/ANOTHER_ORIGINAL.png'}],latestSelected:true});
 assert.equal(observed.verified,false);assert.equal(observed.sourceVerified,true);assert.equal(observed.reason,'verified_storefront_resume');assert.equal(observed.matchingCardCount,0);assert.equal(observed.loadedCardCount,1);
});

test('source-only proof requires one rendered innermost latest control with explicit selected state',()=>{
 const accepted=[[{selected:true}],[{ariaSelected:true}],[{nested:true,parentSelected:true}],[{selected:true},{selected:true,hidden:true}]];
 for(const latestControls of accepted){const observed=readCandidate({cards:otherCards(1),latestControls});assert.equal(observed.sourceVerified,true);assert.equal(observed.latestSelected,true);}
 const rejected=[[],[{}],[{selected:true},{selected:true}],[{selected:true},{}],[{selected:true,hidden:true}],[{selected:true,parentHidden:true}]];
 for(const latestControls of rejected){const observed=readCandidate({cards:otherCards(1),latestControls});assert.equal(observed.sourceVerified,false);assert.equal(observed.latestSelected,false);assert.equal(observed.reason,'source_sort_unverified');}
});

test('source-only loaded count includes only rendered own-list cards before the recommendation boundary',()=>{
 const observed=readCandidate({latestSelected:true,boundaryTop:400,lists:[
  {cards:[...otherCards(2),{...otherCards(1)[0],top:500},{...otherCards(1)[0],hidden:true}]},
  {top:500,cards:otherCards(7)},
  {hidden:true,cards:otherCards(9)},
 ]});
 assert.equal(observed.verified,false);assert.equal(observed.sourceVerified,true);assert.equal(observed.loadedCardCount,2);assert.equal(observed.listCount,3);assert.equal(observed.reason,'verified_storefront_resume');
});

test('source-only resume rejects missing, empty, hidden, non-primary and ambiguous own-storefront lists',async()=>{
 const cases=[
  [{noList:true},'no_storefront_list'],
  [{cards:[]},'source_list_missing'],
  [{cards:[{...otherCards(1)[0],hidden:true}]},'source_list_missing'],
  [{boundaryTop:400,lists:[{top:500,cards:otherCards(1)}]},'source_list_ambiguous'],
  [{lists:[{hidden:true,cards:otherCards(1)}]},'source_list_ambiguous'],
  [{lists:[{hidden:true,cards:otherCards(1)},{cards:otherCards(2)}]},'source_list_ambiguous'],
  [{lists:[{cards:otherCards(1)},{cards:otherCards(2)}]},'source_list_ambiguous'],
  [{cards:otherCards(1),extraLists:1},'source_list_ambiguous'],
  [{boundaryTop:400,cards:[{...otherCards(1)[0],top:500}]},'source_list_missing'],
 ];
 for(const [dom,reason] of cases){
  const client=fakeBrowser([{id:7,url:oldShareUrl,selected:true,dom:{...dom,latestSelected:true}}]),browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
  assert.equal(browser.tab.id,null);assert.equal(selection.resumeCandidateCount,0);assert.equal(selection.selectionReason,'fresh_required');assert.equal(selection.probes[0].sourceVerified,false);assert.equal(selection.probes[0].reason,reason);await browser.close();
 }
});

test('source-only fallback never rescues hidden, recommended, non-primary or ambiguous exact originals',async()=>{
 const cases=[
  [{cards:[{hidden:true},...otherCards(2)]},'card_hidden'],
  [{cards:[{top:500},...otherCards(2)],boundaryTop:400},'card_after_boundary'],
  [{cards:[{},{}]},'card_ambiguous'],
  [{lists:[{cards:otherCards(2)},{cards:[{}]}]},'non_primary_list'],
 ];
 for(const [dom,reason] of cases){
  const client=fakeBrowser([{id:7,url:oldShareUrl,selected:true,dom:{...dom,latestSelected:true}}]),browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
  assert.equal(browser.tab.id,null);assert.equal(selection.qualifiedCount,0);assert.equal(selection.resumeCandidateCount,0);assert.equal(selection.probes[0].sourceVerified,false);assert.equal(selection.probes[0].reason,reason);await browser.close();
 }
});

test('source-only resume still rejects changed typed source, other shop name, login, restriction and visible SKU modal',async()=>{
 const cases=[
  {actualUrl:`${origin}/mall_page.html?mall_id=456`},{actualUrl:`${origin}/mall_page.html?mall_sn=123`},
  {actualUrl:`${origin}/goods.html?goods_id=77&mall_id=123`},{actualUrl:'https://example.org/mall_page.html?mall_id=123'},
  {text:'OTHER_SHOP'},{text:`${originalCard.shopName} 请先登录`},{text:`${originalCard.shopName} 请完成安全验证`},{modalVisible:true},
 ];
 for(const {actualUrl,...dom} of cases){
  const client=fakeBrowser([{id:7,url:oldShareUrl,actualUrl,selected:true,dom:{cards:otherCards(2),latestSelected:true,...dom}}]),browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
  assert.equal(browser.tab.id,null);assert.equal(selection.resumeCandidateCount,0);assert.equal(selection.probes[0].sourceVerified,false);assert.equal(selection.selectionReason,'fresh_required');await browser.close();
 }
});

test('adapter refuses incomplete or contradictory source-only probe proof and rechecks its typed URL',async()=>{
 const valid={verified:false,sourceVerified:true,latestSelected:true,loadedCardCount:3,url:oldShareUrl,reason:'verified_storefront_resume',matchingCardCount:0,visibleModalCount:0,listCount:1};
 const cases=[{sourceVerified:'true'},{latestSelected:1},{verified:undefined},{verified:true,url:`${origin}/mall_page.html?mall_id=456`},
  {loadedCardCount:0},{loadedCardCount:'3'},{loadedCardCount:-1},{loadedCardCount:1000001},{loadedCardCount:2.5},
  {matchingCardCount:1},{matchingCardCount:null},{visibleModalCount:1},{visibleModalCount:'0'},{listCount:0},{listCount:null},
  {reason:'qualified'},{reason:'SYNTHETIC_PRIVATE'},
  {url:`${origin}/mall_page.html?mall_id=456`},{url:`${origin}/mall_page.html?mall_sn=123`},{url:`${origin}/mall_page.html?ps=UNRESOLVED`},{url:'https://example.org/mall_page.html?mall_id=123'}];
 for(const patch of cases){
  const client=fakeBrowser([{id:7,url:oldShareUrl,selected:true}]),callTool=client.callTool.bind(client);
  client.callTool=async(name,args,timeout)=>name==='evaluate_script'?result({...valid,...patch}):callTool(name,args,timeout);
  const browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
  assertSelectionShape(selection);assert.equal(browser.tab.id,null);assert.equal(selection.resumeCandidateCount,0);assert.equal(selection.probes[0].sourceVerified,false);assert.equal(selection.selectionReason,'fresh_required');await browser.close();
 }
});

test('source-only candidates cannot resolve typed ambiguity when original-card options are incomplete',async()=>{
 for(const verifiedOriginalCard of [undefined,{...originalCard,title:''},{...originalCard,image:''},{...originalCard,shopName:''}]){
  const client=fakeBrowser([{id:7,url:oldShareUrl,selected:false,dom:{cards:otherCards(2),latestSelected:true}},{id:9,url:shareUrl,selected:false,dom:{cards:otherCards(4),latestSelected:true}}]),browser=await open(client,{verifiedOriginalCard});
  assert.equal(browser.tab.id,null);assert.equal(browser.executionEvidence.pageSelection.resumeCandidateCount,0);assert.equal(client.calls.some(call=>call.name==='evaluate_script'),false);await browser.close();
 }
});

test('complete original-card proof stays independent from source-only sorting and list continuation gates',()=>{
 const observed=readCandidate({lists:[{cards:[{}]},{cards:otherCards(2)}],latestSelected:false});
 assert.equal(observed.verified,true);assert.equal(observed.reason,'qualified');assert.equal(observed.sourceVerified,true);assert.equal(observed.latestSelected,null);assert.equal(observed.loadedCardCount,null);
});

test('warm original-card adapter diagnostics confirm source and keep unread sorting and loaded count unknown',async()=>{
 const client=fakeBrowser([{id:7,url:oldShareUrl,selected:false,dom:{cards:[{}],latestSelected:true}}]),browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
 assertSelectionShape(selection);assert.equal(selection.selectionReason,'original_card');assert.equal(selection.originalCardVerified,true);assert.equal(selection.resumeCandidateCount,0);
 assert.deepEqual(selection.probes,[{pageId:7,verified:true,sourceVerified:true,latestSelected:null,loadedCardCount:null,reason:'qualified',matchingCardCount:1,visibleModalCount:0,listCount:1}]);
 assert.equal(browser.canPreserveSkuStorefront({sourceUrl,currentUrl:oldShareUrl,...originalCard}),true);await browser.close();
});

test('adapter keeps absent or malformed latest diagnostics unknown and preserves actual boolean observations',async()=>{
 for(const latestSelected of [undefined,null,1,'true',{},false,true]){
  const client=fakeBrowser([{id:7,url:oldShareUrl,selected:false}]),callTool=client.callTool.bind(client);
  client.callTool=async(name,args,timeout)=>name==='evaluate_script'?result({verified:false,url:oldShareUrl,reason:'source_sort_unverified',latestSelected}):callTool(name,args,timeout);
  const browser=await open(client,{verifiedOriginalCard:originalCard}),selection=browser.executionEvidence.pageSelection;
  assertSelectionShape(selection);assert.equal(selection.probes[0].latestSelected,typeof latestSelected==='boolean'?latestSelected:null);assert.equal(selection.probes[0].loadedCardCount,null);assert.equal(selection.probes[0].sourceVerified,false);assert.equal(selection.decision,'fresh_required');await browser.close();
 }
});
