// Bounded live acceptance of the ordinary Chrome adapter; no business import.
import fs from 'node:fs/promises';
import path from 'node:path';
import {openBrowser,guard} from './local_browser.mjs';
import {readStorefrontState,waitForStorefront} from './local_storefront.mjs';
const request=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
let browser;
const result={started_at:new Date().toISOString(),business_import:false,samples:[]};
async function sample(label){
 const state=await browser.evaluate(readStorefrontState,{shopName:request.shopName});
 const position=await browser.evaluate(()=>({top:scrollY,width:innerWidth,height:innerHeight}));
 const item={label,...state,...position};result.samples.push(item);return item;
}
try{
 browser=await openBrowser(request.project,{entryUrl:request.currentUrl});
 await browser.goto(request.sourceUrl);
 const options={shopName:request.shopName,sourceUrl:request.sourceUrl};
 const ready=await waitForStorefront(browser,options);
 if(!ready.latestSelected)await browser.click(ready.latestPoint);
 await waitForStorefront(browser,{...options,requireSelected:true});
 let previous=await sample('after_sort');
 for(let i=0;i<2;i++){
  await guard(browser,request.shopName);
  await browser.scroll([Math.min(220,previous.width/2),previous.height*.7],'down',.8);
  const next=await sample(`after_scroll_${i+1}`);
  if(next.top<=previous.top||next.cards===0||!next.shopVerified)throw new Error('Scroll did not preserve a readable shop list');
  previous=next;
 }
 result.status='passed';result.execution=browser.executionEvidence;
 result.screenshot=await browser.screenshot(path.join(request.directory,'shop_after_scroll.jpg'));
}catch(error){result.status=error.status||'failed';result.reason=error.reason||error.code||error.name;result.message=error.message;}
finally{
 result.ended_at=new Date().toISOString();
 await fs.writeFile(path.join(request.directory,'result.json'),JSON.stringify(result,null,2));
 if(browser)await browser.close(false);
 console.log(JSON.stringify({status:result.status,reason:result.reason,samples:result.samples.map(({label,cards,top,latestSelected})=>({label,cards,top,latestSelected}))}));
}
