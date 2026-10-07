// Portable adapter for the unified verifier; children use synthetic data only.
import {spawnSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const tests = process.env.PDD_PRODUCT_TABLE_TEST_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../dashboard/tests');
const result = spawnSync(process.execPath, ['--test', '--test-reporter=tap', path.join(tests, 'product-tables.test.mjs'), path.join(tests, 'product-tables-ui.test.mjs')], {encoding: 'utf8', env: process.env, windowsHide: true, maxBuffer: 4 * 1024 * 1024});
if (result.error) throw result.error;
const checks = [...result.stdout.matchAll(/^(ok|not ok) \d+ - (.+)$/gm)].map(match => ({name: match[2], passed: match[1] === 'ok'}));
export const acceptance = {suite: 'product_tables', total: checks.length, passed: checks.filter(check => check.passed).length, checks};
if (result.status !== 0 || !checks.length || acceptance.passed !== acceptance.total) throw new Error(`Product table checks failed (exit ${result.status}):\n${result.stdout}\n${result.stderr}`);
