import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,stat,readdir,rm,copyFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join,sep} from 'node:path';
import {createHash} from 'node:crypto';
import {exportHistory,forEachHistoryFile,HISTORY_IO_WORKERS} from '../scripts/export_history.mjs';

const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
const nextTurn=()=>new Promise(done=>setImmediate(done));
async function fixture(t,count=8){
 const project=await mkdtemp(join(tmpdir(),'pdd_history_export_test_'));
 t.after(async()=>{
  assert.ok(resolve(project).startsWith(resolve(tmpdir())+sep));
  await rm(project,{recursive:true,force:true});
 });
 const app=join(project,'dashboard'),source=join(app,'src/data.json');
 await mkdir(join(app,'src/content/dashboard'),{recursive:true});
 await writeFile(join(app,'package.json'),' {"type":"module"}\n');
 await copyFile(new URL('../dashboard/src/content/dashboard/history-model.js',import.meta.url),join(app,'src/content/dashboard/history-model.js'));
 const summaries=Array.from({length:count},(_,index)=>({comparison_id:`synthetic:${index}`,item_count:1,target_run_id:`new-${index}`,baseline_run_id:`old-${index}`}));
 const items=summaries.map((summary,index)=>({comparison_id:summary.comparison_id,comparison_item_id:`item-${index}`,old_observation_id:index*2+1,new_observation_id:index*2+2,title:`合成原卡${index}`,display_delta:index===0?null:0,status:index===0?'unknown':'comparable'}));
 const observations=Array.from({length:count*2},(_,index)=>({observation_id:index+1,title:`独立观察${index+1}`,price_raw:'¥4.50',sales_value:index===0?null:5,asset_sha256:'a'.repeat(64)}));
 const snapshot={id:'SYNTHETIC HISTORY TEST',generatedAt:'2026-10-07T02:00:00.000Z',queries:{comparison_summaries:{rows:summaries},comparison_items:{rows:items},observations:{rows:observations}}};
 const save=()=>writeFile(source,JSON.stringify(snapshot));await save();
 return {project,app,source,snapshot,save,count};
}
async function fileState(directory){
 const output={};
 for(const name of await readdir(directory)){
  const path=join(directory,name),info=await stat(path,{bigint:true});
  output[name]={sha256:sha(await readFile(path)),inode:info.ino,mtime:info.mtimeNs};
 }
 return output;
}

test('file work overlaps at exactly four operations while every item executes once', {timeout:5000},async()=>{
 assert.equal(HISTORY_IO_WORKERS,4);
 const release=deferred(),firstWave=deferred();let active=0,maximum=0;const started=[],finished=[];
 const running=forEachHistoryFile(Array.from({length:13},(_,index)=>index),async value=>{
  started.push(value);active++;maximum=Math.max(maximum,active);
  if(started.length===4)firstWave.resolve();
  await release.promise;finished.push(value);active--;
 });
 try{await firstWave.promise;assert.equal(active,4);assert.deepEqual(started,[0,1,2,3]);}
 finally{release.resolve();}
 await running;assert.equal(maximum,4);assert.equal(active,0);
 assert.deepEqual([...finished].sort((a,b)=>a-b),Array.from({length:13},(_,index)=>index));
});

test('first failure stops new files and awaits all in-flight file operations before rejecting', {timeout:5000},async()=>{
 const failure=new Error('SYNTHETIC WRITE FAILURE'),firstWave=deferred(),failed=deferred(),release=deferred();
 const started=[],finished=[];let settled=false;
 const running=forEachHistoryFile([0,1,2,3,4,5,6],async value=>{
  started.push(value);if(started.length===4)firstWave.resolve();
  await firstWave.promise;
  if(value===0){failed.resolve();throw failure;}
  await release.promise;finished.push(value);
 });
 const observed=running.then(()=>({ok:true}),error=>({error})).finally(()=>{settled=true;});
 try{
  await failed.promise;await nextTurn();assert.equal(settled,false);
  assert.deepEqual(started,[0,1,2,3]);assert.deepEqual(finished,[]);
 }finally{release.resolve();}
 assert.equal((await observed).error,failure);
 assert.deepEqual(finished.sort(),[1,2,3]);assert.equal(settled,true);
});

test('full export preserves every independent comparison and verified immutable plus static copies',async t=>{
 const value=await fixture(t);
 const preflight=await exportHistory({projectRoot:value.project,checkOnly:true});
 assert.equal(preflight.files_written,0);
 await assert.rejects(stat(preflight.report_directory),{code:'ENOENT'});
 const result=await exportHistory({projectRoot:value.project});
 assert.equal(result.status,'exported');assert.equal(result.files_written,value.count*2+1);
 const manifest=JSON.parse(await readFile(result.manifest_path,'utf8'));
 assert.equal(manifest.generated_at,value.snapshot.generatedAt);
 assert.equal(manifest.comparison_count,value.count);assert.equal(manifest.total_item_count,value.count);
 assert.equal(manifest.source_snapshot_sha256,sha(await readFile(value.source)));
 for(const file of manifest.files){
  const immutable=join(result.report_directory,file.filename),published=join(result.static_directory,file.filename);
  const bytes=await readFile(immutable),copy=await readFile(published),payload=JSON.parse(bytes);
  assert.deepEqual(copy,bytes);assert.equal(sha(bytes),file.sha256);assert.equal(bytes.length,file.bytes);
  assert.equal(payload.item_count,1);assert.equal(payload.observations.length,2);
  assert.equal(payload.comparison.comparison_id,file.comparison_id);
  assert.equal(bytes.includes(Buffer.from('data:image/')),false);
  assert.notEqual((await stat(immutable)).ino,(await stat(published)).ino);
 }
 const beforeReports=await fileState(result.report_directory),beforeStatic=await fileState(result.static_directory);
 const again=await exportHistory({projectRoot:value.project});
 assert.equal(again.status,'unchanged');assert.equal(again.files_written,0);
 assert.deepEqual(await fileState(result.report_directory),beforeReports);
 assert.deepEqual(await fileState(result.static_directory),beforeStatic);
});

test('new generation keeps all earlier evidence and only rewrites changed static comparisons',async t=>{
 const value=await fixture(t),first=await exportHistory({projectRoot:value.project});
 const reports=await fileState(first.report_directory),published=await fileState(first.static_directory);
 value.snapshot.generatedAt='2026-10-07T03:00:00.000Z';
 value.snapshot.queries.comparison_items.rows[0].title='合成变更，原卡仍独立';await value.save();
 const result=await exportHistory({projectRoot:value.project});
 assert.equal(result.files_written,value.count+2,'One new immutable generation and only one changed static file');
 assert.deepEqual(await fileState(first.report_directory),reports);
 const after=await fileState(result.static_directory);
 for(const [name,before] of Object.entries(published)){
  if(name==='synthetic_0.json')assert.notEqual(after[name].sha256,before.sha256);
  else assert.deepEqual(after[name],before,'Unchanged static bytes and mtimes must survive');
 }
});

test('same timestamp with changed content fails before writes and preserves static and permanent evidence',async t=>{
 const value=await fixture(t),result=await exportHistory({projectRoot:value.project});
 const reports=await fileState(result.report_directory),published=await fileState(result.static_directory);
 value.snapshot.queries.comparison_items.rows[1].title='SYNTHETIC CONFLICT';await value.save();
 await assert.rejects(exportHistory({projectRoot:value.project}),/History export conflict/);
 assert.deepEqual(await fileState(result.report_directory),reports);
 assert.deepEqual(await fileState(result.static_directory),published);
});

test('invalid counts and colliding IDs remain rejected before output creation',async t=>{
 const value=await fixture(t,2);
 value.snapshot.queries.comparison_summaries.rows[0].item_count=2;await value.save();
 await assert.rejects(exportHistory({projectRoot:value.project}),/item count mismatch/);
 value.snapshot.queries.comparison_summaries.rows[0].item_count=1;
 value.snapshot.queries.comparison_summaries.rows[1].comparison_id='synthetic_0';
 value.snapshot.queries.comparison_items.rows[1].comparison_id='synthetic_0';await value.save();
 await assert.rejects(exportHistory({projectRoot:value.project}),/colliding/);
 await assert.rejects(stat(join(value.project,'reports')),{code:'ENOENT'});
});
