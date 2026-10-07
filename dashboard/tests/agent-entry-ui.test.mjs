import test from 'node:test';
import * as intakeModel from '../src/content/dashboard/shop-intake-model.js';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
const project=path.resolve(import.meta.dirname,'../..');
const {loadPrebuiltCompiler}=await import(pathToFileURL(path.join(project,'runtime/data-analytics/1.0.11/scripts/data-app-runtime.mjs')));
const compiler=await loadPrebuiltCompiler();
const text=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node?.props?.children||'');
function harness(name){
 const source=fs.readFileSync(path.join(project,`dashboard/src/content/dashboard/${name}.jsx`),'utf8');let cursor=0;const states=[];
 const react={useMemo:fn=>fn(),useEffect(){},useRef:value=>({current:value}),useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return [states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];}};
 const jsx=(type,props)=>({type,props}),shared=new Proxy({useDataApp:()=>({queries:{},snapshot:{}})},{get:(obj,key)=>obj[key]??key});
 const context={exports:{},module:{exports:{}},require:id=>id==='react'?{__esModule:true,default:react,...react}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id==='../../data-app-public.jsx'?shared:id.endsWith('ShopDirectory.jsx')?{useShopDirectory:()=>({directory:null,error:'',refresh(){}})}:id.endsWith('portfolio-model.js')?{availableShops:()=>[{shop_id:'A',shop_name:'合成店'}]}:id.endsWith('shop-intake-model.js')?{...intakeModel,resolveIntakeSelection:()=>({canAnalyze:true,shopId:'A',shop:{shop_id:'A',shop_name:'合成店'}})}:{[path.basename(id,'.jsx')]:path.basename(id,'.jsx')},URLSearchParams,Map,Set,Date,Number,String,Boolean,Array,Object};
 context.module.exports=context.exports;vm.runInNewContext(compiler.transform(source,{commonjs:true}),context);
 const collect=(node,nodes)=>{if(Array.isArray(node))return node.forEach(n=>collect(n,nodes));if(!node||typeof node!=='object')return;nodes.push(node);collect(node.props?.children,nodes);};
 return {collect,render(){cursor=0;const nodes=[];collect(context.exports[name]({shopId:'A',shopName:'合成店'}),nodes);return nodes;}};
}
test('every dashboard view mounts one visible product assistant outside warehouse settings',()=>{
 const h=harness('DashboardContent');let nodes=h.render();assert.equal(nodes.filter(n=>n.type==='AgentSearch').length,1);const more=nodes.find(n=>n.props?.className==='warehouse-more'),nested=[];h.collect(more,nested);assert.equal(nested.some(n=>n.type==='AgentSearch'),false);assert.ok(nodes.findIndex(n=>n.type==='AgentSearch')<nodes.indexOf(more));
 nodes.find(n=>n.props?.id==='pdd-workspace-views').props.onChange('profit');nodes=h.render();assert.equal(nodes.filter(n=>n.type==='AgentSearch').length,1);assert.equal(nodes.some(n=>n.props?.className==='warehouse-more'),false);
});
test('product assistant starts closed and opens concise labels with explicit ranking meanings',()=>{
 const h=harness('AgentSearch');let nodes=h.render();const heading=nodes.find(n=>n.props?.className==='pdd-agent-heading');assert.equal(heading.props['aria-expanded'],false);assert.match(text(heading),/商品助手/);assert.match(text(heading),/查销量 · 看增长/);assert.equal(nodes.some(n=>n.type==='input'),false);heading.props.onClick();nodes=h.render();assert.match(nodes.find(n=>n.type==='input').props.placeholder,/哪些商品在增长/);const suggestions=nodes.find(n=>n.props?.className==='pdd-agent-suggestions');assert.deepEqual(Array.from(suggestions.props.children,text),['增长观察','销量前5','首次发现','第2项商品来源']);assert.match(text(nodes.find(n=>n.props?.className==='pdd-agent-query-note')),/销量排行看累计销量（含已拼、已抢）；增长观察看前后变化/);assert.match(text(nodes),/首次发现不等于刚上架/);assert.doesNotMatch(text(nodes),/排名按已拼销量从多到少/);
});
test('current-shop capture shortcut stays visible without opening product queries',()=>{
 const h=harness('AgentSearch'),nodes=h.render(),shortcut=nodes.find(node=>node.type==='AgentCollectionShortcut');
 assert.ok(shortcut);assert.equal(shortcut.props.shopId,'A');assert.equal(shortcut.props.shopName,'合成店');assert.equal(nodes.some(node=>node.type==='input'),false);
 nodes.find(node=>node.props?.className==='pdd-agent-heading').props.onClick();assert.equal(h.render().filter(node=>node.type==='AgentCollectionShortcut').length,1);
});
