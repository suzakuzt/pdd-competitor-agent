import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {test} from 'node:test';
const content = process.env.PDD_PRODUCT_TABLE_CONTENT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src/content/dashboard');
const m = await import(pathToFileURL(path.join(content, 'product-tables-model.js')));
export const query = rows => ({rows, source: {title: 'Synthetic reviewed test', tables: ['synthetic']}, methods: []});
export function fixture() {
  const item = (id, extra = {}) => ({id, shop_id: 'A', lane: 'hot', run_id: 'a1', observation_id: id, source_observation_ids: [id], title: '同标题商品', normalized_title: '同标题商品', image_url: `https://example.invalid/${id}.jpg`, sales_value: 11, sales_label: '已拼', sales_unit: '件', sales_precision: 'exact_display', first_seen_at: '2026-10-04T01:00:00Z', last_seen_at: '2026-10-05T02:00:00Z', view_order: Number(id.replace(/\D/g, '')) || 1, price_raw: '¥6.5', newness_label: '首次看到非上架', ...extra});
  const items = [item('a1', {sales_value: 21}), item('a2', {title: '同标题商品隐藏成员', image_url: 'https://example.invalid/a1.jpg', sales_value: 15, goods_id: '90022'}), item('a3'), item('a4', {normalized_title: '', title: '空标题规范化'}), item('a5', {normalized_title: '', title: '另一独立卡'}), item('a6', {image_url: '', asset_sha256: null}), item('a7', {image_url: '', asset_sha256: null}), item('b1', {shop_id: 'B', run_id: 'b1', sales_value: 999}), item('new1', {lane: 'new_watch', run_id: 'a2', source_observation_ids: ['old1', 'new1'], arrival_item_ids: ['arr1'], merge_basis: 'same_unique_goods_id'}), item('cov1', {lane: 'coverage_review', related_observation_ids: ['weak1'], arrival_item_ids: ['arr2']})];
  const observations = items.map(row => ({...row, observed_at: row.last_seen_at, original: {cardText: `原文 ${row.id}`}}));
  observations.push({observation_id: 'old1', run_id: 'a1', title: '历史名称可搜索', sales_value: 3, sales_label: '已拼', sales_unit: '件', sales_precision: 'exact_display'}, {observation_id: 'weak1', run_id: 'a2', title: '弱关联可搜索', sales_value: 88});
  return {product_table_shops: query([{shop_id: 'A', shop_name: '同名店', baseline_state: 'configured', baseline_coverage_status: 'complete_reference_available', baseline_run_ids: ['a1'], latest_run_id: 'a2', reference_run_id: 'a1', hot_count: 7, last_read_at: null}, {shop_id: 'B', shop_name: '同名店', baseline_state: 'no_baseline'}]), product_table_items: query(items), observations: query(observations), runs: query([{shop_id: 'A', run_id: 'a1'}, {shop_id: 'A', run_id: 'a2'}, {shop_id: 'B', run_id: 'b1'}]), new_arrival_items: query([{shop_id: 'A', run_id: 'a2', arrival_item_id: 'arr1'}, {shop_id: 'A', run_id: 'a1', arrival_item_id: 'arr2'}, {shop_id: 'B', run_id: 'b1', arrival_item_id: 'arrB'}]), image_assets: query([])};
}

test('same-named shops and unknown shop never inherit another shop', () => {
  const q = fixture(), scope = m.tableShopScope(q, 'A');
  assert.ok(scope.items.every(row => row.shop_id === 'A'));
  assert.ok(scope.observations.every(row => row.run_id !== 'b1'));
  assert.equal(m.tableShopScope(q, 'unknown').items.length, 0);
});
test('only same-title same-image same-round folds; empty identities and images survive', () => {
  const view = m.buildTableView(m.tableShopScope(fixture(), 'A'), 'hot');
  assert.equal(view.items.length, 7); assert.equal(view.groups.length, 6); assert.equal(view.originals.length, 7);
  assert.deepEqual(view.groups.find(group => group.candidateCount === 2).members.map(row => row.id), ['a1', 'a2']);
  assert.equal(view.groups.filter(group => group.representative.image_url === '').length, 2);
  assert.equal(view.groups.filter(group => !group.representative.normalized_title).length, 2);
});
test('exact title and nonempty shop/run are mandatory; title-only is insufficient', () => {
  const a = fixture().product_table_items.rows[0];
  for (const change of [{shop_id: 'B'}, {run_id: 'other'}, {lane: 'new_watch'}, {normalized_title: '同标题商品 '}, {normalized_title: ' '}, {image_url: 'different'}, {run_id: null}]) assert.equal(m.sameDisplayCandidate(a, {...a, ...change}), false);
  assert.equal(m.sameDisplayCandidate({...a, run_id: null}, {...a, run_id: null}), false);
});
test('verified SHA supports image equality, conflicting verified content rejects URL equality', () => {
  const a = {...fixture().product_table_items.rows[0], asset_sha256: 'shaA', image_content_status: 'verified_local'};
  assert.equal(m.sameDisplayCandidate(a, {...a, image_url: 'different'}), true);
  assert.equal(m.sameDisplayCandidate(a, {...a, asset_sha256: 'shaB'}), false);
  assert.equal(m.sameDisplayCandidate(a, {...a, image_url: 'different', image_content_status: 'unverified'}), false);
});
test('unverified bridge cannot put two conflicting verified assets in one folded group', () => {
  const q = fixture(), a = q.product_table_items.rows[0];
  q.product_table_items.rows = ['shaA', null, 'shaB'].map((sha, i) => ({...a, id: `bridge${i}`, observation_id: `bridge${i}`, source_observation_ids: [`bridge${i}`], asset_sha256: sha, image_content_status: sha ? 'verified_local' : null}));
  const view = m.buildTableView(m.tableShopScope(q, 'A'), 'hot');
  assert.equal(view.groups.length, 2);
  assert.ok(view.groups.every(group => new Set(group.members.filter(row => row.image_content_status === 'verified_local').map(row => row.asset_sha256)).size <= 1));
});
test('maximum single-card number is not a sum and labels/unknown values are preserved', () => {
  const view = m.buildTableView(m.tableShopScope(fixture(), 'A'), 'hot');
  assert.equal(view.groups[0].highestSales, 21); assert.notEqual(view.groups[0].highestSales, 36);
  for (const sales of [{sales_value: null}, {sales_precision: 'non_exact_or_unparsed'}, {sales_label: '已售'}, {sales_unit: '单'}, {sales_value: 1.5}, {sales_value: -1}]) assert.equal(m.exactSingleCardSales({...view.items[0], ...sales}), null);
  assert.equal(m.exactSingleCardSales({...view.items[0], sales_value: 0}), 0);
});
test('hot sort is numeric descending and ties use original position, not first-seen timestamp', () => {
  const q = fixture(); q.product_table_items.rows = [q.product_table_items.rows[0], q.product_table_items.rows[2]].map((row, i) => ({...row, sales_value: 25, view_order: i ? 2 : 20, first_seen_at: i ? '2020-01-01T00:00:00Z' : '2026-10-05T00:00:00Z'}));
  assert.deepEqual(m.buildTableView(m.tableShopScope(q, 'A'), 'hot').items.map(row => row.view_order), [2, 20]);
});
test('member-only search retains the complete group and hidden member source cards', () => {
  const view = m.buildTableView(m.tableShopScope(fixture(), 'A'), 'hot', '90022');
  assert.equal(view.groups.length, 1); assert.equal(view.items.length, 2); assert.equal(view.originals.length, 2);
  assert.equal(m.buildTableView(m.tableShopScope(fixture(), 'A'), 'hot', 'no match').items.length, 0);
});
test('strong ID source history is searchable; weak related evidence is separate from anchor count', () => {
  const scope = m.tableShopScope(fixture(), 'A');
  const strong = m.buildTableView(scope, 'new_watch', '历史名称'); assert.equal(strong.originals.length, 2);
  const weak = m.buildTableView(scope, 'coverage_review', '弱关联'); assert.equal(weak.originals.length, 1); assert.equal(weak.related.length, 1); assert.equal(weak.evidenceRows.length, 2);
  assert.equal(weak.groups[0].originalCount, 1);
});
test('related observations exclude source anchors and keep earlier as well as later evidence', () => {
  const q = fixture(), item = q.product_table_items.rows.find(row => row.id === 'cov1');
  item.related_observation_ids = ['cov1', 'weak1'];
  const view = m.buildTableView(m.tableShopScope(q, 'A'), 'coverage_review');
  assert.deepEqual(view.related.map(row => row.observation_id), ['weak1']); assert.equal(view.evidenceRows.length, 2);
  assert.equal(m.exportProductTable(view, view.shop).related_observations.length, 1);
});
test('pagination retains full filtered sources, export, run and arrival provenance', () => {
  const q = fixture(), original = q.product_table_items.rows[0];
  q.product_table_items.rows = Array.from({length: 23}, (_, i) => ({...original, id: `p${i}`, observation_id: `p${i}`, source_observation_ids: [`p${i}`], image_url: `img${i}`}));
  q.observations.rows = q.product_table_items.rows.map(row => ({...row}));
  const view = m.buildTableView(m.tableShopScope(q, 'A'), 'hot'), last = m.pageGroups(view, 999);
  assert.equal(last.page, 2); assert.equal(last.groups.length, 3); assert.equal(m.pageGroups(view).groups.length, 10);
  assert.equal(m.tableSourceBinding(view).sourceRows.length, 23); assert.equal(m.exportProductTable(view, view.shop).source_observations.length, 23);
  assert.equal(view.sourceRuns.length, 1);
});
test('empty new table still includes current shop and baseline run evidence', () => {
  const q = fixture(); q.product_table_items.rows = q.product_table_items.rows.filter(row => row.lane !== 'new_watch');
  const binding = m.tableSourceBinding(m.buildTableView(m.tableShopScope(q, 'A'), 'new_watch'));
  assert.equal(binding.sourceRows.length, 0); assert.equal(binding.sourceRowsByQuery.product_table_shops[0].shop_id, 'A'); assert.equal(binding.sourceRowsByQuery.runs.length, 2);
});
test('source panel uses exact reviewed query object and never the global product-pool run', () => {
  const q = fixture(), binding = m.tableSourceBinding(m.buildTableView(m.tableShopScope(q, 'A'), 'new_watch'));
  const source = m.productTableSource(q, binding, 'observations'); assert.equal(source.query, q.observations); assert.equal(source.rows.length, 2);
  assert.ok(!source.rows.some(row => row.run_id === 'b1')); assert.equal(m.productTableSource(q, binding, 'unrequested'), null);
  assert.equal(binding.sourceRowsByQuery.new_arrival_items[0].arrival_item_id, 'arr1');
});
test('JSON export preserves each original sales value, grouping evidence and separate related cards', () => {
  const scope = m.tableShopScope(fixture(), 'A'), view = m.buildTableView(scope, 'hot', '90022');
  const exported = JSON.parse(JSON.stringify(m.exportProductTable(view, scope.shop, '2026-10-05T00:00:00Z')));
  assert.deepEqual(exported.groups[0].source_observations.map(row => row.sales_value), [21, 15]);
  assert.match(exported.groups[0].grouping_basis, /同店、同轮/);
  const weak = m.exportProductTable(m.buildTableView(scope, 'coverage_review'), scope.shop); assert.equal(weak.source_observations.length, 1); assert.equal(weak.related_observations.length, 1);
});
test('no baseline and insufficient baseline are distinct; no claim of zero new products', () => {
  assert.match(m.newWatchMessage({baseline_state: 'no_baseline'}, 0), /尚未配置/);
  assert.match(m.newWatchMessage({baseline_state: 'configured', baseline_coverage_status: 'partial_only'}, 0), /覆盖不足/);
  assert.doesNotMatch(m.newWatchMessage({baseline_state: 'configured', baseline_coverage_status: 'complete_reference_available'}, 0), /不足|0新品|0 新品/);
});
test('Beijing timestamps and safe external links preserve unknowns and reject executable schemes', () => {
  assert.match(m.beijingTime('2026-10-05T01:02:00Z'), /09:02/); assert.equal(m.beijingTime(null), '时间未知');
  assert.equal(m.safeProductLink('javascript:alert(1)'), null); assert.equal(m.safeProductLink('data:text/html,x'), null); assert.equal(m.safeProductLink('https://example.invalid/a'), 'https://example.invalid/a');
});
test('derive model never mutates reviewed input rows', () => {
  const q = fixture(), before = JSON.stringify(q); const view = m.buildTableView(m.tableShopScope(q, 'A'), 'hot'); m.pageGroups(view, 1); m.exportProductTable(view, view.shop); assert.equal(JSON.stringify(q), before);
});
