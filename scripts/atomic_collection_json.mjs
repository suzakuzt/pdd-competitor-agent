import fs from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

const transientRenameCodes=new Set(['EPERM','EACCES','EBUSY']);
const renameWaitsMs=[20,35,50];

// Mutable collection state only. Each writer owns a unique same-directory file;
// a busy Windows reader may delay replacement, but must not expose partial JSON.
// Keep any failed write/rename evidence, including older fixed-name .tmp files.
export async function writeCollectionJson(file,value,{io=fs,sleep=delay,space=2}={}){
 const payload=JSON.stringify(value,null,space);
 const temporary=file+'.node-'+randomUUID()+'.tmp';
 await io.writeFile(temporary,payload,{encoding:'utf8',flag:'wx'});
 for(let attempt=0;;attempt++){
  try{await io.rename(temporary,file);return;}
  catch(error){
   if(!transientRenameCodes.has(error.code)||attempt>=renameWaitsMs.length)throw error;
   await sleep(renameWaitsMs[attempt]);
  }
 }
}
