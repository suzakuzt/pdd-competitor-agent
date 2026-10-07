import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import * as images from '../src/content/dashboard/verified-image.js';
import * as pddModel from '../src/content/dashboard/pdd-model.js';
import * as profitModel from '../src/content/dashboard/profit-model.js';
import {trendCardImage} from '../src/content/dashboard/trend-decision-model.js';

const sha='a'.repeat(64),otherSha='b'.repeat(64),local=`/__pdd_image/${sha}`;
const inline='data:image/png;base64,c3ludGhldGlj';
const asset={sha256:sha,data_url:local,integrity_status:'verified_local',mime:'image/png',byte_count:9};
const card={shop_id:'A',run_id:'a',observation_id:1,title:'合成原卡',asset_sha256:sha,image_content_status:'verified_local'};
const table=value=>new Map([[sha,value]]);
const project=path.resolve(import.meta.dirname,'../..');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(project,'runtime/data-analytics/1.0.11/scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler();

test('verified assets support exact bound local paths and existing inline formats without changing evidence',()=>{
 const before=JSON.stringify({asset,card});
 for(const src of [local,...['png','jpeg','webp','gif'].map(mime=>inline.replace('/png;',`/${mime};`))]){
  const value={...asset,data_url:src};
  assert.equal(images.verifiedAssetImage(value),src);
  assert.equal(images.verifiedCardImage(card,table(value)),src);
  assert.equal(trendCardImage(card,table(value)),src);
 }
 assert.equal(JSON.stringify({asset,card}),before);
});

test('only the exact SHA route is accepted; external, encoded, cross-SHA and malformed references fail closed',()=>{
 const rejected=[`/__pdd_image/${otherSha}`,`/__pdd_image/${sha.toUpperCase()}`,`${local}?size=small`,`${local}#image`,
  `${local}/`,`${local}\n`,`${local}\r\n`,` ${local}`,local.slice(1),local.replace('__pdd_image','%5f%5fpdd_image'),
  `/__pdd_image/%61${sha.slice(1)}`,`/__pdd_image/${sha.slice(1)}`,`/__pdd_image/${sha}a`,
  `https://example.invalid${local}`,`http://127.0.0.1:8878${local}`,`//example.invalid${local}`,
  'blob:https://example.invalid/image','javascript:alert(1)','data:image/svg+xml;base64,AAAA',
  'data:image/png;base64,','data:image/png;base64,<script>',null,{},42];
 for(const data_url of rejected)assert.equal(images.verifiedAssetImage({...asset,data_url}),null,String(data_url));
});

test('row binding and both verification statuses are required for inline and local images',()=>{
 for(const data_url of [inline,local]){
  const value={...asset,data_url};
  for(const integrity_status of [undefined,null,'unknown','saved','failed'])
   assert.equal(images.verifiedCardImage(card,table({...value,integrity_status})),null);
  for(const image_content_status of [undefined,null,'unknown','saved','unavailable_or_invalid'])
   assert.equal(images.verifiedCardImage({...card,image_content_status},table(value)),null);
  assert.equal(images.verifiedCardImage(card,table({...value,sha256:otherSha})),null);
  assert.equal(images.verifiedCardImage({...card,asset_sha256:otherSha},table(value)),null);
  for(const sha256 of [undefined,'',sha+'\n','asset-label'])
   assert.equal(images.verifiedAssetImage({...value,sha256}),null);
  assert.equal(images.verifiedCardImage(card,new Map()),null);
  assert.equal(images.verifiedCardImage(card,table(data_url)),null,'A URL alone is not asset evidence');
 }
 assert.equal(images.verifiedCardImage(null,table(asset)),null);
 assert.equal(images.verifiedAssetImage(null),null);
});

function harness(file,{privateExport,queries={}}={}){
 let source=fs.readFileSync(path.join(project,'dashboard/src/content/dashboard',file),'utf8');
 if(privateExport)source+=`\nexport {${privateExport}};`;
 let cursor=0;const states=[];
 const react={useMemo:fn=>fn(),useEffect(){},useRef:value=>({current:value}),useState(initial){
  const index=cursor++;if(!(index in states))states[index]=typeof initial==='function'?initial():initial;
  return [states[index],value=>states[index]=typeof value==='function'?value(states[index]):value];
 }};
 const jsx=(type,props)=>({type,props}),shared=new Proxy({useDataApp:()=>({queries})},{get:(obj,key)=>obj[key]??key});
 const context={exports:{},module:{exports:{}},require:id=>id==='react'?{__esModule:true,default:react,...react}:
  id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='../../data-app-public.jsx'?shared:
  id.endsWith('verified-image.js')?images:id.endsWith('pdd-model.js')?pddModel:id.endsWith('profit-model.js')?profitModel:
  id.endsWith('ZoomableImage.jsx')?{ZoomableImage:'ZoomableImage'}:{},Map,Set,Date,Math,JSON,Number,String,Boolean,Object,Array};
 context.module.exports=context.exports;
 vm.runInNewContext(compiler.transform(source,{commonjs:true}),context,{filename:file});
 return {exports:context.exports,render(name,props){cursor=0;return context.exports[name](props);}};
}
function collect(node,result=[]){
 if(Array.isArray(node)){for(const child of node)collect(child,result);return result;}
 if(!node||typeof node!=='object')return result;
 result.push(node);collect(node.props?.children,result);return result;
}

for(const [file,name,prop,type] of [
 ['ProductPool.jsx','LocalImage','images','ZoomableImage'],
 ['ProductTables.jsx','LocalImage','assets','ZoomableImage'],
 ['HistoryContent.jsx','HistoryImage','images','img'],
 ['NewArrivalsContent.jsx','ArrivalImage','images','img'],
])test(`${file} accepts bound local thumbnails and rejects unverified or mismatched assets`,()=>{
 const ui=harness(file,{privateExport:name});
 for(const src of [local,inline])for(const large of [false,true]){
  const node=ui.render(name,{row:card,[prop]:table({...asset,data_url:src}),large});
  assert.equal(node.type,type);assert.equal(node.props.src,src);
  if(type==='img')assert.equal(node.props.loading,'lazy');
 }
 for(const value of [{...asset,integrity_status:'unknown'},{...asset,sha256:otherSha},
  {...asset,data_url:`/__pdd_image/${otherSha}`},{...asset,data_url:'https://example.invalid/image.png'}]){
  const node=ui.render(name,{row:card,[prop]:table(value)});
  assert.equal(node.props.src,undefined);
  assert.notEqual(node.type,type);
 }
});

test('assistant and trend image helpers use the same original-card binding',()=>{
 const agent=harness('AgentSearch.jsx').exports.agentLocalImage;
 for(const read of [agent,trendCardImage]){
  assert.equal(read(card,table(asset)),local);
  assert.equal(read(card,table({...asset,data_url:inline})),inline);
  assert.equal(read(card,table({...asset,sha256:otherSha})),null);
  assert.equal(read({...card,image_content_status:'unknown'},table(asset)),null);
 }
});

test('profit cards retain local lazy thumbnails and opening the same verified original image',()=>{
 const opportunity={...card,opportunity_id:'op1',priority_key:'watch',sales_raw:'已拼11件'};
 const ui=harness('ProfitWorkbench.jsx',{queries:{profit_opportunities:{rows:[opportunity]},image_assets:{rows:[asset]}}});
 let nodes=collect(ui.render('ProfitWorkbench',{shopId:'A'}));
 const thumbnail=nodes.find(node=>node.type==='img');
 assert.equal(thumbnail.props.src,local);assert.equal(thumbnail.props.loading,'lazy');
 const button=nodes.find(node=>node.type==='button'&&node.props['aria-label']==='查看原图：合成原卡');
 button.props.onClick();nodes=collect(ui.render('ProfitWorkbench',{shopId:'A'}));
 const enlarged=nodes.find(node=>node.props?.className==='profit-image-detail');
 assert.equal(collect(enlarged).find(node=>node.type==='img').props.src,local);
 for(const value of [{...asset,integrity_status:'unknown'},{...asset,data_url:`/__pdd_image/${otherSha}`}]){
  const blocked=harness('ProfitWorkbench.jsx',{queries:{profit_opportunities:{rows:[opportunity]},image_assets:{rows:[value]}}});
  assert.equal(collect(blocked.render('ProfitWorkbench',{shopId:'A'})).some(node=>node.type==='img'),false);
 }
});

test('zoomable image keeps default lazy loading and passes the exact local path to the enlarged view',()=>{
 const ui=harness('ZoomableImage.jsx');
 let nodes=collect(ui.render('ZoomableImage',{src:local,alt:'合成原卡'}));
 assert.equal(nodes.find(node=>node.type==='img').props.loading,'lazy');
 nodes.find(node=>node.type==='button').props.onClick();
 nodes=collect(ui.render('ZoomableImage',{src:local,alt:'合成原卡'}));
 assert.equal(nodes.find(node=>node.type==='Dialog').props.open,true);
 assert.ok(nodes.filter(node=>node.type==='img').every(node=>node.props.src===local));
});
