#!/usr/bin/env node
// Build-time, immutable history exports. No network, database writes or deletion.
import {readFile,writeFile,mkdir,lstat,rename,unlink} from 'node:fs/promises';
import {resolve,join,dirname,parse} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash,randomUUID} from 'node:crypto';
import {parseArgs} from 'node:util';

const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const jsonBytes=value=>Buffer.from(`${JSON.stringify(value,null,2)}\n`,'utf8');
export const HISTORY_IO_WORKERS=4;
export async function forEachHistoryFile(files,operation){
 let next=0,failed=false,failure;
 const worker=async()=>{
  while(!failed&&next<files.length){
   const file=files[next++];
   try{await operation(file);}
   catch(error){if(!failed){failed=true;failure=error;}throw error;}
  }
 };
 // A failed operation stops new work, but every in-flight file operation must
 // settle before the caller can leave this phase or report failure.
 await Promise.allSettled(Array.from({length:Math.min(HISTORY_IO_WORKERS,files.length)},worker));
 if(failed)throw failure;
}
async function regularOrMissing(path){
 try{const info=await lstat(path);if(info.isSymbolicLink()||!info.isFile())throw new Error(`Expected a regular file: ${path}`);return await readFile(path);}
 catch(error){if(error.code==='ENOENT')return null;throw error;}
}
async function regularDirectoryChain(path){
 const absolute=resolve(path),root=parse(absolute).root;
 for(let current=absolute;current!==root;current=dirname(current)){
  try{const info=await lstat(current);if(info.isSymbolicLink()||!info.isDirectory())throw new Error(`Expected a regular directory: ${current}`);}
  catch(error){if(error.code!=='ENOENT')throw error;}
 }
}
function safeComparisonFilename(id){
 if(typeof id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(id))throw new Error('Invalid comparison_id for a static filename');
 const name=id.replaceAll(':','_');
 if(/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name))throw new Error('Reserved comparison filename');
 return `${name}.json`;
}
export async function planHistoryExports(projectRoot){
 const project=resolve(projectRoot),app=join(project,'dashboard'),snapshotPath=join(app,'src','data.json');
 const snapshotBytes=await readFile(snapshotPath),snapshot=JSON.parse(snapshotBytes.toString('utf8'));
 const generatedAt=snapshot.generatedAt;
 if(typeof generatedAt!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(generatedAt)||!Number.isFinite(Date.parse(generatedAt)))throw new Error('Snapshot generatedAt must be an ISO timestamp');
 const generation=generatedAt.replaceAll(':','-').replaceAll('+','plus');
 const staticDirectory=join(app,'dist','history-exports'),reportDirectory=join(project,'reports','history_exports',generation);
 for(const directory of [staticDirectory,reportDirectory])await regularDirectoryChain(directory);
 const queries=snapshot.queries||{},summaries=queries.comparison_summaries?.rows,items=queries.comparison_items?.rows,observations=queries.observations?.rows;
 if(!Array.isArray(summaries)||!Array.isArray(items)||!Array.isArray(observations))throw new Error('History export requires comparison_summaries, comparison_items and observations rows');
 const {makeHistoryExport}=await import(pathToFileURL(join(app,'src','content','dashboard','history-model.js')).href);
 if(typeof makeHistoryExport!=='function')throw new Error('history-model.js does not export makeHistoryExport');
 const seen=new Set(),ids=new Set(),outputs=[];
 for(const summary of summaries){
  const filename=safeComparisonFilename(summary.comparison_id);
  if(seen.has(filename.toLowerCase())||ids.has(summary.comparison_id))throw new Error('Duplicate or colliding comparison IDs');
  seen.add(filename.toLowerCase());ids.add(summary.comparison_id);
  const comparisonItems=items.filter(item=>item.comparison_id===summary.comparison_id);
  if(summary.item_count!==comparisonItems.length)throw new Error(`Comparison item count mismatch: ${summary.comparison_id}`);
  const payload=makeHistoryExport(summary,comparisonItems,{search:'',category:'all'},observations);
  const bytes=jsonBytes(payload);
  if(bytes.includes(Buffer.from('data:image/')))throw new Error(`Image base64 is not allowed in history JSON: ${summary.comparison_id}`);
  outputs.push({filename,comparison_id:summary.comparison_id,item_count:payload.item_count,observation_count:payload.observations.length,bytes,sha256:sha(bytes)});
 }
 if(items.some(item=>!ids.has(item.comparison_id)))throw new Error('Comparison items refer to an absent summary');
 const manifest={version:1,export_type:'pdd_full_history_comparisons',generated_at:generatedAt,app_id:snapshot.id||null,
  source_snapshot_sha256:sha(snapshotBytes),comparison_count:outputs.length,total_item_count:outputs.reduce((sum,item)=>sum+item.item_count,0),
  files:outputs.map(({filename,comparison_id,item_count,observation_count,sha256,bytes})=>({filename,comparison_id,item_count,observation_count,sha256,bytes:bytes.length}))};
 const persistentFiles=outputs.map(item=>({path:join(reportDirectory,item.filename),bytes:item.bytes}));
 persistentFiles.push({path:join(reportDirectory,'manifest.json'),bytes:jsonBytes(manifest)});
 const staticFiles=outputs.map(item=>({path:join(staticDirectory,item.filename),bytes:item.bytes}));
 // Immutable timestamp evidence must agree before any write. Static files are current derived views.
 await forEachHistoryFile(persistentFiles,async file=>{const previous=await regularOrMissing(file.path);if(previous!==null&&!previous.equals(file.bytes))throw new Error(`History export conflict; existing report preserved: ${file.path}`);});
 await forEachHistoryFile(staticFiles,file=>regularOrMissing(file.path));
 return {project,snapshotPath,snapshotSha256:sha(snapshotBytes),staticDirectory,reportDirectory,manifest,persistentFiles,staticFiles};
}
export async function exportHistory({projectRoot,checkOnly=false}){
 const plan=await planHistoryExports(projectRoot);
 if(checkOnly)return {status:'validated',comparison_count:plan.manifest.comparison_count,total_item_count:plan.manifest.total_item_count,source_snapshot_sha256:plan.snapshotSha256,static_directory:plan.staticDirectory,report_directory:plan.reportDirectory,files_written:0};
 // Refuse a snapshot race before creating any output.
 if(sha(await readFile(plan.snapshotPath))!==plan.snapshotSha256)throw new Error('Snapshot changed during history export; retry after build is stable');
 let written=0;
 const persist=async file=>{
  await mkdir(dirname(file.path),{recursive:true});
  try{await writeFile(file.path,file.bytes,{flag:'wx'});written++;}
  catch(error){if(error.code!=='EEXIST')throw error;const previous=await regularOrMissing(file.path);if(!previous?.equals(file.bytes))throw new Error(`History export conflict; existing file preserved: ${file.path}`);}
  if(sha(await readFile(file.path))!==sha(file.bytes))throw new Error(`History export verification failed: ${file.path}`);
 };
 // Keep the generation manifest last, after all immutable comparison files
 // have been written and verified successfully.
 await forEachHistoryFile(plan.persistentFiles.slice(0,-1),persist);
 await persist(plan.persistentFiles.at(-1));
 // Publish only after the complete permanent report exists. Replace individual derived files atomically.
 if(sha(await readFile(plan.snapshotPath))!==plan.snapshotSha256)throw new Error('Snapshot changed before static history publication; retained reports remain intact');
 await mkdir(plan.staticDirectory,{recursive:true});
 await forEachHistoryFile(plan.staticFiles,async file=>{
  const previous=await regularOrMissing(file.path);if(previous?.equals(file.bytes))return;
  const temporary=join(plan.staticDirectory,`.history-export-${process.pid}-${randomUUID()}.tmp`);
  try{await writeFile(temporary,file.bytes,{flag:'wx'});await rename(temporary,file.path);written++;}
  finally{try{await unlink(temporary);}catch(error){if(error.code!=='ENOENT')throw error;}}
  if(sha(await readFile(file.path))!==sha(file.bytes))throw new Error(`Static history verification failed: ${file.path}`);
 });
 return {status:written?'exported':'unchanged',comparison_count:plan.manifest.comparison_count,total_item_count:plan.manifest.total_item_count,
  static_directory:plan.staticDirectory,report_directory:plan.reportDirectory,manifest_path:join(plan.reportDirectory,'manifest.json'),
  manifest_sha256:sha(jsonBytes(plan.manifest)),source_snapshot_sha256:plan.snapshotSha256,files_written:written};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 try{const {values}=parseArgs({options:{'project-root':{type:'string'},'check-only':{type:'boolean',default:false}},strict:true});
  if(!values['project-root'])throw new Error('Use --project-root <PDDCompetitorAgent project> [--check-only]');
  console.log(JSON.stringify(await exportHistory({projectRoot:values['project-root'],checkOnly:values['check-only']})));
 }catch(error){console.error(`History export failed: ${error.message}`);process.exitCode=1;}
}
