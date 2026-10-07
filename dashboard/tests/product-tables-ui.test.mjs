import * as verifiedImages from '../src/content/dashboard/verified-image.js';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import os from 'node:os';
import {test} from 'node:test';
import {fileURLToPath, pathToFileURL} from 'node:url';
const content = process.env.PDD_PRODUCT_TABLE_CONTENT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src/content/dashboard');
const plugin = process.env.DATA_PLUGIN_ROOT || path.join(process.env.CODEX_HOME || path.join(os.homedir(), '.codex'), 'plugins/cache/openai-curated-remote/data-analytics/1.0.11');
const {loadPrebuiltCompiler} = await import(pathToFileURL(path.join(plugin, 'scripts/data-app-runtime.mjs')));
const compiler = await loadPrebuiltCompiler();
const model = await import(pathToFileURL(path.join(content, 'product-tables-model.js')));
const guard = await import(pathToFileURL(path.join(plugin, 'templates/data-app/base/src/source-provenance.js')));
const source = fs.readFileSync(path.join(content, 'ProductTables.jsx'), 'utf8');
const css = fs.readFileSync(path.join(content, 'product-tables.css'), 'utf8');
const query = rows => ({rows, source: {title: 'Synthetic test', tables: ['synthetic']}, methods: []});
const queries = {product_table_shops: query([{shop_id: 'A', shop_name: '店A', baseline_state: 'configured', baseline_coverage_status: 'complete_reference_available', baseline_run_ids: ['a1'], reference_run_id: 'a1', latest_run_id: 'a1', hot_count: 1}, {shop_id: 'B', shop_name: '店B', baseline_state: 'no_baseline'}]), product_table_items: query([{id: 'i1', shop_id: 'A', lane: 'hot', observation_id: 'o1', source_observation_ids: ['o1'], title: '合成商品标题', normalized_title: '合成商品标题', image_url: 'https://example.invalid/remote.jpg', run_id: 'a1', sales_label: '已拼', sales_unit: '件', sales_precision: 'exact_display', sales_value: 21, first_seen_at: '2026-10-04T00:00:00Z', last_seen_at: '2026-10-05T00:00:00Z'}]), observations: query([{observation_id: 'o1', run_id: 'a1', title: '合成原卡', sales_raw: '已拼21件'}]), new_arrival_items: query([]), runs: query([{run_id: 'a1', shop_id: 'A'}]), image_assets: query([])};
const flattenText = node => typeof node === 'string' || typeof node === 'number' ? String(node) : Array.isArray(node) ? node.map(flattenText).join('') : flattenText(node?.props?.children || '');
function harness() {
  const instances = new Map(); let active = null;
  const react = {Fragment: 'Fragment', useMemo: fn => fn(), useEffect: () => {}, useState(initial) { const index = active.cursor++; if (!(index in active.states)) active.states[index] = typeof initial === 'function' ? initial() : initial; const state = active; return [state.states[index], update => state.states[index] = typeof update === 'function' ? update(state.states[index]) : update]; }};
  const shared = new Proxy({useDataApp: () => ({queries, visible: () => true, filters: {run_id: 'b-foreign'}})}, {get: (object, key) => object[key] ?? key});
  const jsx = (type, props) => ({type, props});
  const context = {exports: {}, module: {exports: {}}, require: id => id.endsWith('verified-image.js') ? verifiedImages : id === 'react' ? {__esModule: true, default: react, ...react} : id === 'react/jsx-runtime' ? {jsx, jsxs: jsx, Fragment: 'Fragment'} : id === '../../data-app-public.jsx' ? shared : id.endsWith('.css') ? {} : model, console, Map, Set, Date, Math, JSON, Number, String, Boolean, Object, Array, URL, Intl};
  context.module.exports = context.exports; vm.runInNewContext(compiler.transform(source, {commonjs: true}), context, {filename: 'ProductTables.jsx'});
  function expand(node, id, nodes) {
    if (Array.isArray(node)) return node.forEach((child, i) => expand(child, `${id}:${i}`, nodes));
    if (!node || typeof node !== 'object') return;
    if (typeof node.type === 'function') {
      const stateKey = `${id}:${node.type.name}:${node.props?.key || ''}`, previous = active;
      if (!instances.has(stateKey)) instances.set(stateKey, {cursor: 0, states: []}); active = instances.get(stateKey); active.cursor = 0;
      const rendered = node.type(node.props); active = previous; expand(rendered, `${id}:result`, nodes); return;
    }
    nodes.push(node); expand(node.props?.children, `${id}:children`, nodes); expand(node.props?.headerControls, `${id}:controls`, nodes); expand(node.props?.filters, `${id}:filters`, nodes);
  }
  return {render(props = {}) { const nodes = []; expand({type: context.module.exports.ProductTables, props: {shopId: 'A', ...props}}, 'root', nodes); return nodes; }};
}
test('canonical compiler parses JSX model and scoped CSS', () => {
  compiler.parseJavaScript(compiler.transform(source, {commonjs: false})); compiler.parseJavaScript(fs.readFileSync(path.join(content, 'product-tables-model.js'), 'utf8')); compiler.parseCss(css);
});
test('all rendered evidence wrappers pass canonical source provenance guard', () => {
  const nodes = harness().render(), wrappers = nodes.filter(node => node.type === 'DataComponent'); assert.equal(wrappers.length, 6);
  const catalog = wrappers.find(node => node.props.id === 'pt-catalog');
  assert.equal(catalog.props.queryId, 'observations');
  assert.equal(catalog.props.sourceRows[0], queries.observations.rows[0]);
  for (const node of wrappers) guard.reviewedNarrativeQueries(node.props, queries);
});
test('read-only UI has public source wrappers, no tracking writes or automatic external image fetch', () => {
  const nodes = harness().render(); assert.equal(nodes.filter(node => node.type === 'img').length, 0);
  assert.doesNotMatch(source, /fetch\s*\(|__pdd_competitor_tracking|localStorage|sessionStorage|<main/);
  assert.match(source, /from '..\/..\/data-app-public.jsx'/);
});
test('shop actions use stable shop ID even with no baseline', () => {
  let selected = null; const nodes = harness().render({onSelectShop: id => selected = id}); nodes.find(node => node.type === 'button' && flattenText(node) === '查看本店').props.onClick(); assert.equal(selected, 'B');
  const b = harness().render({shopId: 'B'}); assert.match(b.map(flattenText).join(''), /基线尚未配置/); assert.doesNotMatch(b.map(flattenText).join(''), /0新品|0 新品/);
});
test('source panel exposes all explicit sources and closes immediately for another shop', () => {
  const h = harness(); const hot = h.render().find(node => node.type === 'DataComponent' && node.props.id === 'pt-hot'); hot.props.onOpen('source', hot.props);
  const sidebar = h.render().find(node => node.type === 'SourceSidebar'); assert.equal(sidebar.props.getSource('observations').rows.length, 1); assert.equal(sidebar.props.getSource('observations').query, queries.observations);
  assert.equal(h.render({shopId: 'B'}).filter(node => node.type === 'SourceSidebar').length, 0);
});
test('search changes complete-scope source rows and clears stale source without depending on effect', () => {
  const h = harness(), first = h.render(), hot = first.find(node => node.type === 'DataComponent' && node.props.id === 'pt-hot'); hot.props.onOpen('source', hot.props);
  first.find(node => node.type === 'input' && node.props.type === 'search').props.onChange({target: {value: 'unmatched'}});
  const nodes = h.render(); assert.equal(nodes.find(node => node.type === 'DataComponent' && node.props.id === 'pt-hot').props.sourceRows.length, 0); assert.equal(nodes.filter(node => node.type === 'SourceSidebar').length, 0);
});
test('details expose full title, original card and date evidence', () => {
  const h = harness(), nodes = h.render(); nodes.find(node => node.type === 'button' && node.props.className === 'pt-title').props.onClick();
  const dialog = h.render().find(node => node.type === 'Dialog' && node.props.title === '商品原卡与分组依据'); assert.equal(dialog.props.open, true); assert.match(flattenText(dialog), /合成商品标题|原卡/);
});
test('layout keeps sales before date columns and mobile hides dates only within authored tables', () => {
  const nodes = harness().render(), table = nodes.find(node => node.type === 'table' && node.props.className === 'pt-product-table'); const headers = table.props.children[0].props.children.props.children;
  assert.match(flattenText(headers[1]), /最高单卡销量/); assert.match(css, /max-width:800px/); assert.match(css, /\.product-tables \.pt-product-table \.pt-date-col\{display:none\}/); assert.doesNotMatch(css, /(?:^|\})\s*(?:body|:root|html)\b/);
});
