// Read-only connectivity receipt. Does not navigate, inspect page contents,
// export login state, or print browser tab titles/URLs.
import {startChromeMcp} from './chrome_mcp_client.mjs';
let client;
try{
 client=await startChromeMcp(process.cwd());
 const result=await client.callTool('list_pages',{},120000);
 if(!Array.isArray(result.structuredContent?.pages))throw new Error('invalid_response');
 console.log(JSON.stringify({status:'connected',persistent:client.persistent===true,at:new Date().toISOString()}));
}catch(error){
 console.log(JSON.stringify({status:'unavailable',reason:['connection_timeout','connection_lost','connection_required','connection_busy','runtime_unavailable','unsupported_server'].includes(error.code)?error.code:'connection_required',at:new Date().toISOString()}));
 process.exitCode=1;
}finally{await client?.close();}
