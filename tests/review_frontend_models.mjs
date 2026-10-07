import fs from 'node:fs/promises';
import path from 'node:path';
const args=process.argv.slice(2);
if(args.length!==2||args[0]!=='--output'||!args[1])throw Error('Usage: node tests/review_frontend_models.mjs --output <new receipt.json>');
const results=[];
for(const suite of ['history','portfolio','arrivals','profit','heat','artist','tracking_priority','product_tables']){
  try{const {acceptance}=await import(`./frontend/${suite}.mjs`);results.push({...acceptance,status:'passed'});}
  catch(error){results.push({suite,status:'failed',error:String(error.stack)});}
}
const report={status:results.every(r=>r.status==='passed')?'passed':'failed',total:results.reduce((n,r)=>n+(r.total||0),0),passed:results.reduce((n,r)=>n+(r.passed||0),0),results,synthetic_only:true,browser_performed:false,production_writes:false};
const output=path.resolve(args[1]);await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(report,null,2));process.exitCode=report.status==='passed'?0:1;
