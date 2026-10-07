import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {writeCollectionJson} from '../scripts/atomic_collection_json.mjs';

async function fixture(t){
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'pdd_atomic_json_'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const file=path.join(root,'progress.json');
 await fs.writeFile(file,'{"old":true}');
 await fs.writeFile(file+'.tmp','KEEP old Python evidence');
 await fs.writeFile(file+'.sku.tmp','KEEP old Node evidence');
 return {root,file};
}
const fail=code=>Object.assign(new Error('Synthetic I/O failure'),{code});

test('atomic JSON replacement uses an exclusive unique sibling and preserves old fixed temporary evidence',async t=>{
 const {root,file}=await fixture(t),writes=[];
 const io={writeFile:async(...args)=>{writes.push(args);return fs.writeFile(...args);},rename:fs.rename};
 await writeCollectionJson(file,{phase:'翻页',scrolls:19},{io,space:0});
 assert.equal(writes.length,1);assert.equal(path.dirname(writes[0][0]),root);
 assert.match(path.basename(writes[0][0]),/^progress\.json\.node-[0-9a-f-]{36}\.tmp$/);
 assert.deepEqual(writes[0][2],{encoding:'utf8',flag:'wx'});
 assert.equal(await fs.readFile(file,'utf8'),'{"phase":"翻页","scrolls":19}');
 assert.equal(await fs.readFile(file+'.tmp','utf8'),'KEEP old Python evidence');
 assert.equal(await fs.readFile(file+'.sku.tmp','utf8'),'KEEP old Node evidence');
 assert.deepEqual((await fs.readdir(root)).sort(),['progress.json','progress.json.sku.tmp','progress.json.tmp']);
});

test('concurrent writers keep independent complete temporary files until each atomic replacement',async t=>{
 const {file}=await fixture(t),pending=[];
 let ready;const bothReady=new Promise(resolve=>{ready=resolve;});
 const io={writeFile:fs.writeFile,rename:(temporary,target)=>new Promise((resolve,reject)=>{
  pending.push({temporary,target,resolve,reject});if(pending.length===2)ready();
 })};
 const first=writeCollectionJson(file,{writer:1},{io}),second=writeCollectionJson(file,{writer:2},{io});
 await bothReady;
 assert.notEqual(pending[0].temporary,pending[1].temporary);
 assert.deepEqual(new Set(await Promise.all(pending.map(async item=>JSON.parse(await fs.readFile(item.temporary,'utf8')).writer))),new Set([1,2]));
 assert.deepEqual(JSON.parse(await fs.readFile(file,'utf8')),{old:true});
 for(const item of pending){await fs.rename(item.temporary,item.target);item.resolve();}
 await Promise.all([first,second]);
 assert.ok([1,2].includes(JSON.parse(await fs.readFile(file,'utf8')).writer));
 assert.equal(await fs.readFile(file+'.tmp','utf8'),'KEEP old Python evidence');
});

test('only Windows transient rename errors receive the bounded short retry sequence',async t=>{
 for(const code of ['EPERM','EACCES','EBUSY']){
  const {file}=await fixture(t),waits=[],attempts=[];
  const io={writeFile:fs.writeFile,rename:async(temporary,target)=>{
   attempts.push(temporary);
   if(attempts.length<=3){assert.deepEqual(JSON.parse(await fs.readFile(file,'utf8')),{old:true});throw fail(code);}
   return fs.rename(temporary,target);
  }};
  await writeCollectionJson(file,{code},{io,sleep:async ms=>waits.push(ms)});
  assert.deepEqual(waits,[20,35,50]);assert.equal(attempts.length,4);assert.equal(new Set(attempts).size,1);
  assert.deepEqual(JSON.parse(await fs.readFile(file,'utf8')),{code});
 }
});

test('exhausted transient rename failure rejects, preserves complete old JSON and retains new evidence',async t=>{
 const {root,file}=await fixture(t),error=fail('EBUSY'),waits=[];let attempts=0;
 const io={writeFile:fs.writeFile,rename:async()=>{attempts++;throw error;}};
 await assert.rejects(writeCollectionJson(file,{scrolls:19},{io,sleep:async ms=>waits.push(ms)}),actual=>actual===error);
 assert.equal(attempts,4);assert.deepEqual(waits,[20,35,50]);
 assert.equal(await fs.readFile(file,'utf8'),'{"old":true}');
 const temporary=(await fs.readdir(root)).filter(name=>name.includes('.node-'));
 assert.equal(temporary.length,1);assert.deepEqual(JSON.parse(await fs.readFile(path.join(root,temporary[0]),'utf8')),{scrolls:19});
 assert.equal(await fs.readFile(file+'.tmp','utf8'),'KEEP old Python evidence');
 assert.equal(await fs.readFile(file+'.sku.tmp','utf8'),'KEEP old Node evidence');
});

test('non-allowlisted rename errors propagate immediately without sleeps or deleting temporary evidence',async()=>{
 for(const code of ['ENOENT','EEXIST','EIO','ENOSPC','EINVAL',undefined]){
  const error=fail(code),files=new Map();let attempts=0;
  const io={writeFile:async(file,data)=>files.set(file,data),rename:async()=>{attempts++;throw error;}};
  await assert.rejects(writeCollectionJson('progress.json',{scrolls:19},{io,sleep:async()=>assert.fail('Unexpected retry')}),actual=>actual===error);
  assert.equal(attempts,1);assert.equal(files.size,1);assert.deepEqual(JSON.parse([...files.values()][0]),{scrolls:19});
 }
});

test('temporary write failures preserve partial evidence and never rename or reopen it',async()=>{
 const error=fail('EPERM'),files=new Map();let writes=0;
 const io={writeFile:async(file,data)=>{writes++;files.set(file,data.slice(0,5));throw error;},rename:async()=>assert.fail('Unexpected rename')};
 await assert.rejects(writeCollectionJson('progress.json',{scrolls:19},{io,sleep:async()=>assert.fail('Unexpected retry')}),actual=>actual===error);
 assert.equal(writes,1);assert.equal(files.size,1);assert.equal([...files.values()][0].length,5);
});

test('serialization failure happens before creating any temporary file',async()=>{
 const value={};value.self=value;
 const io={writeFile:async()=>assert.fail('Unexpected temporary write'),rename:async()=>assert.fail('Unexpected rename')};
 await assert.rejects(writeCollectionJson('progress.json',value,{io}),TypeError);
});
