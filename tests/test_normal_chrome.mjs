import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {ensureNormalChrome,normalChromeProcess,ordinaryChromeArguments} from '../scripts/normal_chrome.mjs';
const executable='C:\\Users\\user\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe';
const row={ExecutablePath:executable,CommandLine:`"${executable}" --profile-directory="Profile 2"`};
test('normal Chrome with an existing personal profile is reused without spawning',async()=>{
 const value=await ensureNormalChrome('.', 'https://mobile.yangkeduo.com/mall_page.html?ps=test', {processes:async()=>[row],spawn:()=>assert.fail('no second browser')});
 assert.equal(value.launched,false);assert.equal(value.executable,executable);
});
test('dedicated, private, guest, headless and subprocess windows are not ordinary Chrome',()=>{
 for(const flag of ['--type=renderer','--user-data-dir=C:/custom','--headless','--remote-debugging-port=0','--remote-debugging-pipe','--incognito','--guest']) assert.equal(normalChromeProcess({...row,CommandLine:row.CommandLine+' '+flag}),false);
 assert.equal(normalChromeProcess({...row,ExecutablePath:'C:/Microsoft/Edge/Application/msedge.exe'}),false);
});
test('normal launch passes just the validated user URL, no automation flags',async()=>{
 const url='https://mobile.yangkeduo.com/mall_page.html?ps=example';let called;
 const result=await ensureNormalChrome('.',url,{processes:async()=>[],stat:async()=>({isFile:()=>true}),spawn:(file,args,options)=>{called={file,args,options};const child=new EventEmitter();child.unref=()=>{};queueMicrotask(()=>child.emit('spawn'));return child;}});
 assert.equal(result.launched,true);assert.deepEqual(called.args,[url]);assert.equal(called.options.windowsHide,false);
});
test('existing custom Chrome never causes a fallback launch or copied profile',async()=>{
 await assert.rejects(ensureNormalChrome('.',null,{processes:async()=>[{...row,CommandLine:row.CommandLine+' --user-data-dir=C:/custom'}],spawn:()=>assert.fail('no fallback')}),/自行打开/);
});

test('private or guest-only browser cannot become the saved-login collection session',async()=>{
 for(const flag of ['--incognito','--guest']) {
  await assert.rejects(ensureNormalChrome('.',null,{processes:async()=>[{...row,CommandLine:row.CommandLine+' '+flag}],spawn:()=>assert.fail('no replacement browser')}),/普通窗口/);
 }
});

test('repeated collections keep reusing the same ordinary profile without relaunch',async()=>{
 const dependencies={processes:async()=>[row],spawn:()=>assert.fail('must reuse saved Chrome session')};
 for(let index=0;index<3;index++) {
  const result=await ensureNormalChrome('.', 'https://mobile.yangkeduo.com/mall_page.html?ps=test',dependencies);
  assert.equal(result.launched,false);assert.equal(result.executable,executable);
 }
});
test('launch accepts no switches, other hosts, credentials or non-PDD paths',()=>{
 for(const url of ['--remote-debugging-port=0','https://example.org/','https://me:secret@mobile.yangkeduo.com/mall_page.html','https://mobile.yangkeduo.com:1234/mall_page.html','https://mobile.yangkeduo.com/login.html']) assert.throws(()=>ordinaryChromeArguments(url));
 assert.deepEqual(ordinaryChromeArguments(null),[]);
});
