// Reuse ordinary Google Chrome and its browser-managed login storage.
// Never add an ephemeral profile, debugging port or emulation.
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';

const execute = promisify(execFile);
export function normalChromeProcess(row) {
 const command = String(row.CommandLine || '');
 return path.win32.basename(String(row.ExecutablePath || '')).toLowerCase() === 'chrome.exe'
  && !/(?:^|\s)--(?:type|user-data-dir|headless|remote-debugging-port|remote-debugging-pipe|incognito|guest)(?:[=\s]|$)/i.test(command);
}

export function ordinaryChromeArguments(url) {
 if (!url) return [];
 const parsed = new URL(url);
 if (parsed.protocol !== 'https:' || parsed.hostname !== 'mobile.yangkeduo.com'
     || parsed.port || parsed.username || parsed.password
     || !/^\/(mall_page|goods|goods1)\.html$/.test(parsed.pathname)) {
  throw new Error('Only the requested public PDD page may be opened');
 }
 return [parsed.href];
}

async function runningChrome() {
 const ps = path.join(process.env.SystemRoot || 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
 const {stdout} = await execute(ps, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
  "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); @(Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" | Select-Object ExecutablePath,CommandLine) | ConvertTo-Json -Compress"],
  {windowsHide:true, timeout:10000, maxBuffer:1024*1024});
 if (!stdout.trim()) return [];
 const rows = JSON.parse(stdout.replace(/^\uFEFF/, ''));
 return Array.isArray(rows) ? rows : [rows];
}

export async function ensureNormalChrome(project, url, dependencies={}) {
 // Validate even if Chrome already runs; do not use URL/arguments as shell code.
 const args = ordinaryChromeArguments(url);
 const processes = await (dependencies.processes || runningChrome)();
 const existing = processes.find(normalChromeProcess);
 if (existing) return {launched:false, executable:existing.ExecutablePath};
 // An existing custom-profile Chrome must not capture a normal launch implicitly.
 if (processes.some(row => !/(?:^|\s)--type(?:[=\s]|$)/i.test(row.CommandLine || ''))) {
  throw new Error('请先自行打开你平时使用的 Chrome 普通窗口（非无痕或访客窗口），再点开始采集。');
 }
 const candidates = [
  path.join(process.env.LOCALAPPDATA || '', 'Google/Chrome/Application/chrome.exe'),
  path.join(process.env.PROGRAMFILES || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe'),
  path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Google/Chrome/Application/chrome.exe')
 ];
 let executable;
 for (const candidate of candidates) {
  try { if ((await (dependencies.stat || fs.stat)(candidate)).isFile()) { executable=candidate; break; } } catch {}
 }
 if (!executable) throw new Error('未找到 Google Chrome，请先打开你平时使用的 Chrome。');
 const child = (dependencies.spawn || spawn)(executable, args, {detached:true,windowsHide:false,stdio:'ignore'});
 await new Promise((resolve,reject) => {child.once('spawn',resolve);child.once('error',reject);});
 child.unref();
 return {launched:true,executable};
}
