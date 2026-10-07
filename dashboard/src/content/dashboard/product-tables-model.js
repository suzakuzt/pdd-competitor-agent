export const TABLE_LANES = ['new_watch', 'coverage_review', 'hot', 'catalog'];
export const LANE_LABELS = {catalog: '上新全量', new_watch: '新增记录关注', coverage_review: '疑似旧存量 · 覆盖扩展', hot: '热销参考'};
export const PAGE_SIZE = 10;
const list = value => Array.isArray(value) ? value : [];
const key = value => String(value ?? '');
const distinct = values => [...new Set(list(values).filter(value => value !== null && value !== undefined).map(key))];
const epoch = value => Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;

export function beijingTime(value) {
  if (epoch(value) === null) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(value));
}

export function safeProductLink(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}

export function exactSingleCardSales(row) {
  return ['已拼', '已抢'].includes(row?.sales_label) && row.sales_unit === '件' && row.sales_precision === 'exact_display' && Number.isFinite(row.sales_value) && Number.isInteger(row.sales_value) && row.sales_value >= 0 ? row.sales_value : null;
}

export function tableShopScope(queries, shopId) {
  const shop = list(queries.product_table_shops?.rows).find(row => row.shop_id === shopId) || null;
  const runs = list(queries.runs?.rows).filter(row => row.shop_id === shopId);
  const runIds = new Set(runs.map(row => key(row.run_id)));
  const observations = list(queries.observations?.rows).filter(row => runIds.has(key(row.run_id)) && (!row.shop_id || row.shop_id === shopId));
  const items = list(queries.product_table_items?.rows).filter(row => row.shop_id === shopId && TABLE_LANES.includes(row.lane));
  const arrivals = list(queries.new_arrival_items?.rows).filter(row => runIds.has(key(row.run_id)) && (!row.shop_id || row.shop_id === shopId));
  return {shopId, shop, runs, observations, items, arrivals};
}

export function sourceCardsForItems(items, observations) {
  const ids = new Set(items.flatMap(row => distinct(row.source_observation_ids?.length ? row.source_observation_ids : [row.observation_id])));
  return observations.filter(row => ids.has(key(row.observation_id)));
}

export function relatedCardsForItems(items, observations) {
  const ids = new Set(items.flatMap(row => distinct(row.related_observation_ids)));
  const sourceIds = new Set(items.flatMap(row => distinct(row.source_observation_ids?.length ? row.source_observation_ids : [row.observation_id])));
  return observations.filter(row => ids.has(key(row.observation_id)) && !sourceIds.has(key(row.observation_id)));
}

function compareItems(a, b, lane) {
  if (lane === 'hot' || lane === 'catalog') {
    const delta = (exactSingleCardSales(b) ?? -1) - (exactSingleCardSales(a) ?? -1);
    if (delta) return delta;
    return (a.view_order ?? Infinity) - (b.view_order ?? Infinity) || key(a.id).localeCompare(key(b.id));
  }
  const delta = (epoch(b.first_seen_at) ?? -Infinity) - (epoch(a.first_seen_at) ?? -Infinity);
  if (Number.isFinite(delta) && delta) return delta;
  return (a.view_order ?? Infinity) - (b.view_order ?? Infinity) || key(a.id).localeCompare(key(b.id));
}

function searchable(row) {
  return [row.title, row.normalized_title, row.goods_id, row.observation_id, row.id, row.view_order == null ? '' : `#${row.view_order}`, row.original?.cardText, row.original?.rawText].filter(value => value != null).join(' ').normalize('NFKC').toLocaleLowerCase();
}

export function sameDisplayCandidate(a, b) {
  if (!a.normalized_title?.trim() || a.normalized_title !== b.normalized_title || !a.shop_id || a.shop_id !== b.shop_id || !a.run_id || a.run_id !== b.run_id || a.lane !== b.lane) return false;
  if (a.asset_sha256 && b.asset_sha256 && a.image_content_status === 'verified_local' && b.image_content_status === 'verified_local' && a.asset_sha256 !== b.asset_sha256) return false;
  const sameUrl = Boolean(a.image_url && b.image_url && a.image_url === b.image_url);
  const sameVerifiedAsset = Boolean(a.asset_sha256 && a.asset_sha256 === b.asset_sha256 && a.image_content_status === 'verified_local' && b.image_content_status === 'verified_local');
  return sameUrl || sameVerifiedAsset;
}

// Folding is presentation only; title alone is insufficient. Original evidence remains intact.
export function buildTableView(scope, lane, search = '') {
  const catalogRun = scope.shop?.reference_run_id || scope.shop?.latest_run_id;
  const validCatalogRun = scope.runs.some(row => row.run_id === catalogRun);
  const items = lane === 'catalog' ? (validCatalogRun ? scope.observations.filter(row => row.run_id === catalogRun).map(row => ({...row,
    id: `catalog:${scope.shopId}:${row.observation_id}`, shop_id: scope.shopId, lane: 'catalog', source_observation_ids: [row.observation_id], source_run_ids: [row.run_id],
    first_seen_at: null, last_seen_at: row.observed_at, last_seen_precision: row.observed_at_precision, age_label: '上新列表商品', newness_label: '上新列表商品', reason: '完整参考轮内商品；不按首次发现或今日采集筛选。'
  })) : []) : scope.items.filter(row => row.lane === lane);
  const groups = [];
  const noVerifiedImageConflict = members => new Set(members.filter(row => row.image_content_status === 'verified_local' && row.asset_sha256).map(row => row.asset_sha256)).size <= 1;
  items.forEach((row, index) => {
    const matching = groups.filter(group => noVerifiedImageConflict([...group.members, row]) && group.members.some(member => sameDisplayCandidate(row, member)));
    if (!matching.length) groups.push({id: `card:${key(row.id)}:${index}`, members: [row]});
    else {
      matching[0].members.push(row);
      matching.slice(1).forEach(group => { if (noVerifiedImageConflict([...matching[0].members, ...group.members])) { matching[0].members.push(...group.members); groups.splice(groups.indexOf(group), 1); } });
    }
  });
  const tokens = String(search).normalize('NFKC').toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const result = [];
  for (const {id: groupKey, members} of groups) {
    const originals = sourceCardsForItems(members, scope.observations);
    const related = relatedCardsForItems(members, scope.observations);
    const haystack = [...members, ...originals, ...related].map(searchable).join(' ');
    if (!tokens.every(token => haystack.includes(token))) continue;
    const ordered = [...members].sort((a, b) => compareItems(a, b, lane));
    const sales = members.map(exactSingleCardSales).filter(value => value !== null);
    const highestSales = sales.length ? Math.max(...sales) : null;
    const representative = [...ordered].sort((a, b) => (exactSingleCardSales(b) ?? -1) - (exactSingleCardSales(a) ?? -1))[0];
    const firstDates = members.map(row => row.first_seen_at).filter(value => epoch(value) !== null).sort((a, b) => epoch(a) - epoch(b));
    const lastDates = members.map(row => row.last_seen_at).filter(value => epoch(value) !== null).sort((a, b) => epoch(b) - epoch(a));
    result.push({id: `${scope.shopId}:${lane}:${groupKey}`, lane, members: ordered, originals, related, representative,
      highestSales, firstSeen: firstDates[0] || null, lastSeen: lastDates[0] || null,
      firstEvidence: members.find(row => row.first_seen_at === firstDates[0]) || representative,
      lastEvidence: members.find(row => row.last_seen_at === lastDates[0]) || representative,
      candidateCount: members.length, originalCount: originals.length,
      sameTitleCount: representative.normalized_title ? items.filter(row => row.normalized_title === representative.normalized_title).length : 1,
      groupBasis: members.length > 1 ? '同店、同轮、同 normalized_title，且原图网址相同或已核验图片 SHA-256 相同；仅折叠展示，不确认同 SKU，不累计销量。' : '独立展示项；仅同标题或缺少图片依据时不折叠。'});
  }
  result.sort((a, b) => (lane === 'hot' || lane === 'catalog') ? (b.highestSales ?? -1) - (a.highestSales ?? -1) || compareItems(a.members[0], b.members[0], lane) : compareItems(a.members[0], b.members[0], lane));
  const matchedItems = result.flatMap(group => group.members);
  const originalRows = sourceCardsForItems(matchedItems, scope.observations);
  const relatedRows = relatedCardsForItems(matchedItems, scope.observations);
  const evidenceIds = new Set([...originalRows, ...relatedRows].map(row => key(row.observation_id)));
  const evidenceRows = scope.observations.filter(row => evidenceIds.has(key(row.observation_id)));
  const arrivalIds = new Set(matchedItems.flatMap(row => distinct(row.arrival_item_ids)));
  const arrivals = list(scope.arrivals).filter(row => arrivalIds.has(key(row.arrival_item_id ?? row.item_id ?? row.id)));
  const runIds = new Set(distinct([...matchedItems.flatMap(row => row.source_run_ids || [row.run_id]), ...evidenceRows.map(row => row.run_id), ...((lane === 'hot' || lane === 'catalog') ? [catalogRun] : [...list(scope.shop?.baseline_run_ids), scope.shop?.latest_run_id])]));
  const sourceRuns = scope.runs.filter(row => runIds.has(key(row.run_id)));
  return {shopId: scope.shopId, shop: scope.shop, lane, search: String(search), groups: result, items: matchedItems, originals: originalRows, related: relatedRows, evidenceRows, arrivals, sourceRuns, unfilteredItemCount: items.length};
}

export function pageGroups(view, page = 0, pageSize = PAGE_SIZE) {
  const size = Number.isInteger(pageSize) && pageSize > 0 ? pageSize : PAGE_SIZE;
  const pageCount = Math.max(1, Math.ceil(view.groups.length / size));
  const currentPage = Math.max(0, Math.min(Number.isInteger(page) ? page : 0, pageCount - 1));
  return {page: currentPage, pageCount, pageSize: size, groups: view.groups.slice(currentPage * size, (currentPage + 1) * size)};
}

export function tableSourceBinding(view) {
  if (view.lane === 'catalog') return {queryId: 'observations', queryIds: ['observations','runs','product_table_shops'],
    sourceRows: view.originals, displayRows: view.originals, sourceRowsByQuery: {observations: view.originals, runs: view.sourceRuns, product_table_shops: view.shop ? [view.shop] : []},
    scopeFilters: [{field:'shop_id',label:'当前店铺',value:view.shopId},{field:'run_id',label:'完整参考轮（没有时使用部分轮）',value:view.sourceRuns.map(row=>row.run_id).join('；')},{field:'search',label:'全部页筛选',value:view.search||'全部'}]};
  return {queryId: 'product_table_items', queryIds: ['product_table_items', 'observations', 'new_arrival_items', 'runs', 'product_table_shops'],
    sourceRows: view.items, displayRows: view.items,
    sourceRowsByQuery: {product_table_items: view.items, observations: view.evidenceRows, new_arrival_items: view.arrivals, runs: view.sourceRuns, product_table_shops: view.shop ? [view.shop] : []},
    scopeFilters: [{field: 'shop_id', label: '店铺范围', value: view.shopId}, {field: 'lane', label: '清单', value: LANE_LABELS[view.lane]}, {field: 'search', label: '完整组筛选（含成员与原卡）', value: view.search || '全部'}, {field: 'pagination', label: '来源范围', value: '当前筛选全部页；同标题且同图仅折叠展示'}]};
}

export function productTableSource(queries, component, queryId = component?.queryId) {
  if (!component || !list(component.queryIds || [component.queryId]).includes(queryId) || !queries[queryId]) return null;
  return {query: queries[queryId], rows: component.sourceRowsByQuery?.[queryId] ?? (queryId === component.queryId ? component.sourceRows : []) ?? [], filters: [...(component.scopeFilters || [])]};
}

export function exportProductTable(view, shop, exportedAt = new Date().toISOString()) {
  return {export_type: 'pdd_product_table_reference', exported_at: exportedAt, shop_id: view.shopId, shop: shop || null, lane: view.lane, search: view.search,
    scope: '当前筛选的全部组与全部来源原卡，不限当前页。',
    limitations: ['同店同轮同标题且同图仅折叠展示；同标题不同图不折叠，不确认同 SKU，不相加销量。', '最高单卡销量仅取展示成员的单卡精确销量件数；不跨卡或跨轮累计。', '首次看到不等于真实上架日期；热销参考不代表已核实销量。', '不同清单可以共享原卡，清单数量不可相加。'],
    groups: view.groups.map(group => ({group_id: group.id, grouping_basis: group.groupBasis, highest_single_card_yipin: group.highestSales, member_count: group.candidateCount,
      members: group.members, source_observation_ids: distinct(group.members.flatMap(row => row.source_observation_ids?.length ? row.source_observation_ids : [row.observation_id])), source_observations: group.originals, related_observations: group.related})),
    items: view.items, source_observations: view.originals, related_observations: view.related, source_arrival_items: view.arrivals, source_runs: view.sourceRuns};
}

export function newWatchMessage(shop, count) {
  if (!shop || shop.baseline_state !== 'configured') return '基线尚未配置，暂无法比较新增记录；首次看到不等于真实上架。';
  if (count > 0) return '以下为固定起点后的首次观察记录，仍需核验真实上架时间。';
  return ['complete', 'complete_reference_available'].includes(shop.baseline_coverage_status) ? '当前没有新增记录关注线索，不影响上新全量商品列表。' : '当前没有新增记录关注线索；基线覆盖不足，补扫记录另列供核查。';
}
