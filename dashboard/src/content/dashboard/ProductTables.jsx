import {verifiedCardImage} from './verified-image.js';
import React, {useEffect, useMemo, useState} from 'react';
import {DataComponent, Dialog, Section, SectionHeader, SourceSidebar, useDataApp} from '../../data-app-public.jsx';
import {LANE_LABELS, beijingTime, safeProductLink, tableShopScope, buildTableView, pageGroups, tableSourceBinding, productTableSource, exportProductTable, newWatchMessage} from './product-tables-model.js';
import './product-tables.css';
import {ZoomableImage} from './ZoomableImage.jsx';
import {SkuPanel} from './CollectionControls.jsx';

function LocalImage({row, assets}) {
  const src = verifiedCardImage(row,assets);
  return src ? <ZoomableImage className="pt-image" src={src} alt={row.title || '商品原卡主图'}/> : <span className="pt-image pt-image-missing">主图<br/>待补</span>;
}

function Timestamp({value, precision, window}) {
  return <span className="pt-time">{beijingTime(value)}{precision && !['card_read'].includes(precision) && <small>{window?.from && window?.to ? `${beijingTime(window.from)}—${beijingTime(window.to)} 观察窗口` : '观察时间，非上架日期'}</small>}</span>;
}

function OriginalCard({row, assets}) {
  const link = safeProductLink(row.goods_url);
  return <article className="pt-original" data-reviewed-rows="true">
    <LocalImage row={row} assets={assets}/><div><strong>{row.title || '标题待补'}</strong><p>{row.sales_raw || '销量未显示'} · {row.price_raw || '价格未显示'}</p><small>原位置 #{row.view_order ?? '未知'} · {beijingTime(row.observed_at || row.last_dom_read_at)}（北京时间）</small><small>原卡 {row.observation_id} · 轮次 {row.run_id}</small>{link && <a href={link} target="_blank" rel="noopener noreferrer">打开已记录商品链接 ↗</a>}<details><summary>原文与销量口径</summary><p>{row.original?.cardText || row.original?.rawText || '无额外原卡文字'}</p><p>{row.sales_label || '标签未知'} · {row.sales_value ?? '数值未知'} · {row.sales_unit || '单位未知'} · {row.sales_precision || '精度未知'}</p><p>商品 ID：{row.goods_id || '待补'}；{row.identity_status || '身份待核验'}</p></details></div>
    <SkuPanel row={row}/>
  </article>;
}

function GroupDetail({group, assets, onClose}) {
  return <Dialog open={Boolean(group)} onClose={onClose} title="商品原卡与分组依据" expanded className="pt-detail-dialog">{group && <div className="product-tables pt-dialog-body" data-reviewed-rows="true">
    <h3>{group.representative.title || '标题待补'}</h3><p>{group.groupBasis}</p><p>{group.candidateCount} 个展示成员 · {group.originalCount} 张来源原卡。单卡销量分别保留，不累计。</p>
    {group.members.map(item => <div key={item.id} className="pt-member"><strong>{item.newness_label || item.age_label || '观察参考'}</strong><p>{item.reason || '依据尚未记录'}</p><small>首次看到：{beijingTime(item.first_seen_at)} · 最新观察：{beijingTime(item.last_seen_at)}（北京时间）</small><small>展示合并依据：{item.merge_basis === 'same_unique_goods_id' ? '跨轮同一可靠且每轮唯一的商品 ID；原卡分别保留' : '独立原卡'}{item.related_observation_ids?.some(id => !(item.source_observation_ids || []).includes(id)) ? '；其他关联观察单独列示，不用于身份合并' : ''}</small></div>)}
    <div className="pt-originals">{group.originals.map(row => <OriginalCard key={`${row.run_id}:${row.observation_id}`} row={row} assets={assets}/>)}</div>{group.related.length > 0 && <details className="pt-related"><summary>其他关联观察（弱线索，不合并身份或数量）· {group.related.length} 张</summary><div className="pt-originals">{group.related.map(row => <OriginalCard key={`${row.run_id}:${row.observation_id}`} row={row} assets={assets}/>)}</div></details>}{!group.originalCount && <p>当前快照未提供关联原卡，请通过来源面板核查。</p>}
  </div>}</Dialog>;
}

function TableBody({view, page, setPage, expandAll, assets, onDetail}) {
  const pagination = pageGroups(view, page);
  const [opened, setOpened] = useState({});
  useEffect(() => { setPage(0); setOpened({}); }, [view.search, view.shopId, view.lane]);
  return <><div className="pt-table-scroll" tabIndex={0} role="region" aria-label={`${LANE_LABELS[view.lane]}表格，可横向滚动`}><table className="pt-product-table"><thead><tr><th scope="col">商品与原卡</th><th scope="col" className="pt-number">最高单卡销量<small>件 · 不合计</small></th><th scope="col">原展示价</th><th scope="col">新旧线索</th><th scope="col" className="pt-date-col">首次看到<small>北京时间 · 非上架日</small></th><th scope="col" className="pt-date-col">最新观察<small>北京时间</small></th><th scope="col">疑似重复</th></tr></thead><tbody>
  {pagination.groups.map(group => { const row = group.representative, showOriginals = expandAll || opened[group.id]; return <React.Fragment key={group.id}>
    <tr><td><div className="pt-product"><LocalImage row={row} assets={assets}/><div><button className="pt-title" onClick={() => onDetail(group)}>{row.title || '标题待补'}</button><small>{group.originalCount} 张原卡 · <button className="pt-text-button" onClick={() => onDetail(group)}>展开标题 / 原卡</button></small></div></div></td>
    <td className="pt-number"><strong className="pt-sales">{group.highestSales === null ? '未知' : group.highestSales.toLocaleString('zh-CN')}</strong><small>{group.highestSales === null ? row.sales_raw || '未显示精确销量件数' : '单卡前台展示'}</small></td><td><span className="pt-price">{row.price_raw || '未显示'}</span><small>{group.highestSales === null ? '该展示成员原价' : '对应最高销量成员'}</small></td>
    <td><span className={`pt-tag pt-tag-${view.lane}`}>{row.newness_label || row.age_label || (view.lane === 'coverage_review' ? '疑似旧存量补扫' : '上架时间未核实')}</span><button className="pt-text-button" onClick={() => onDetail(group)}>查看日期与依据</button></td><td className="pt-date-col"><Timestamp value={group.firstSeen} precision={group.firstEvidence.first_seen_precision} window={group.firstEvidence.first_seen_window}/></td><td className="pt-date-col"><Timestamp value={group.lastSeen} precision={group.lastEvidence.last_seen_precision} window={group.lastEvidence.last_seen_window}/></td>
    <td>{group.candidateCount > 1 ? <><strong>{group.candidateCount} 项折叠</strong><small>同标题且同图</small></> : group.sameTitleCount > 1 ? <><span>{group.sameTitleCount} 个同标题</span><small>图片依据不同，分别保留</small></> : <span className="pt-muted">未折叠</span>}<button className="pt-text-button" aria-expanded={Boolean(showOriginals)} onClick={() => expandAll ? onDetail(group) : setOpened(current => ({...current, [group.id]: !current[group.id]}))}>{showOriginals ? expandAll ? '原卡详情' : '收起原卡' : `展开 ${group.originalCount} 张原卡`}</button></td></tr>
    {showOriginals && <tr className="pt-expanded-row"><td colSpan={7}><p>{group.groupBasis}</p><div className="pt-originals">{group.originals.map(original => <OriginalCard key={`${original.run_id}:${original.observation_id}`} row={original} assets={assets}/>)}</div></td></tr>}
  </React.Fragment>; })}</tbody></table></div>
  <div className="pt-pagination" data-reviewed-rows="true"><span>{view.originals.length} 张来源原卡 / {view.groups.length} 个展示组 · 每页 {pagination.pageSize} 组</span><div><button disabled={!pagination.page} onClick={() => setPage(pagination.page - 1)}>上一页</button><span>{pagination.page + 1} / {pagination.pageCount}</span><button disabled={pagination.page + 1 >= pagination.pageCount} onClick={() => setPage(pagination.page + 1)}>下一页</button></div></div></>;
}

function LaneTable({view, shop, assets, expandAll, onDetail, onExport, onOpenSource}) {
  const [page, setPage] = useState(0);
  return <DataComponent id={`pt-${view.lane}`} {...tableSourceBinding(view)} onOpen={onOpenSource} kind="table" title={LANE_LABELS[view.lane]} variant="plain" description="同店同轮同标题且同图只折叠展示。来源、复制和导出包含筛选命中的完整组及全部原卡，不限当前页，不累计销量。" headerControls={<button className="pt-button" onClick={() => onExport(view)}>导出全部筛选 JSON</button>}>
    <div data-reviewed-rows="true">{view.lane === 'new_watch' && <p className="pt-lane-note">{newWatchMessage(shop, view.groups.length)}</p>}{view.lane === 'catalog' && <p className="pt-lane-note">{shop?.reference_run_id ? '包含本店完整上新列表中的全部已采集商品，较早上新与今日发布均在范围内。' : '当前仅有上新部分记录，尚未采集完整；不能代表全店。'}按销量从多到少排列。采集至 {beijingTime(shop?.reference_observed_to || shop?.latest_observed_to)}。</p>}{view.lane === 'hot' && <p className="pt-lane-note">使用最近一次完整覆盖：{beijingTime(shop?.reference_observed_to)} · 仅单卡精确销量 &gt;10 件，按最高单卡销量降序。</p>}
    {view.groups.length ? <TableBody view={view} page={page} setPage={setPage} expandAll={expandAll} assets={assets} onDetail={onDetail}/> : <p className="pt-empty">{view.search ? '当前搜索没有匹配记录，可清空搜索查看全部。' : view.lane === 'new_watch' ? '暂无新增记录关注线索。' : view.lane === 'hot' ? '当前没有可列出的达标热销参考；需核查完整覆盖与销量口径。' : '当前没有可列出的覆盖扩展记录。'}</p>}</div>
  </DataComponent>;
}

function ShopOverview({shops, shopId, onSelectShop, onOpenSource}) {
  return <DataComponent id="pt-shops" queryId="product_table_shops" kind="table" title="店铺核对概览" variant="plain" sourceRows={shops} displayRows={shops} scopeFilters={[{field: 'shop_id', label: '概览范围', value: '已提供核对记录的全部店铺'}]} onOpen={onOpenSource} description="最新观察和最近完整覆盖分别列示；部分采集不替代完整参考。读取时间来自已记录的页面读取，未知不补造。"><div className="pt-table-scroll" data-reviewed-rows="true"><table className="pt-shop-table"><thead><tr><th>店铺</th><th>热销参考</th><th>最新采集至</th><th>最近完整覆盖</th><th>新增记录依据 / 核对</th><th><span className="pt-sr-only">选择店铺</span></th></tr></thead><tbody>{shops.map(shop => <tr key={shop.shop_id} className={shop.shop_id === shopId ? 'pt-shop-selected' : ''}><th scope="row">{shop.shop_name || shop.shop_id}</th><td><strong>{shop.hot_count ?? '未知'} 张</strong><small>达标原卡</small></td><td>{shop.latest_card_count ?? '未知'} 张原卡 · {shop.latest_is_complete ? '完整' : '部分'}<small>{beijingTime(shop.latest_observed_to)}</small></td><td>{shop.reference_run_id ? <>{shop.reference_card_count ?? '未知'} 张原卡<small>{beijingTime(shop.reference_observed_to)}</small></> : '尚无完整覆盖'}</td><td>{shop.baseline_state === 'configured' ? '已有固定基线' : '基线尚未配置'}<details><summary>查看核对依据</summary><p>已记录页面读取：{beijingTime(shop.last_read_at)}（北京时间；可能来自较早轮次）</p>{(shop.check_results || []).map(check => <p key={check.id}>{check.passed ? '✓' : '待核对'} {check.label}：{check.detail}</p>)}</details></td><td><button className="pt-button" disabled={shop.shop_id === shopId} onClick={() => onSelectShop?.(shop.shop_id)}>{shop.shop_id === shopId ? '当前店铺' : '查看本店'}</button></td></tr>)}</tbody></table></div></DataComponent>;
}

function ExportDialog({document: result, onClose}) {
  const [message, setMessage] = useState('');
  useEffect(() => setMessage(''), [result]);
  const text = result?.text || '';
  const download = () => { try { const url = URL.createObjectURL(new Blob([text], {type: 'application/json;charset=utf-8'})); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `商品参考-${result.lane}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); setMessage('已请求浏览器下载完整 JSON，请查看下载记录。'); } catch { setMessage('下载请求未成功，请重试或复制完整 JSON。'); } };
  const copy = async () => { try { await navigator.clipboard.writeText(text); setMessage('完整 JSON 已复制。'); } catch { setMessage('剪贴板未获许可，请下载完整 JSON；下方仅为预览。'); } };
  return <Dialog open={Boolean(result)} onClose={onClose} title="导出当前筛选的全部结果" expanded className="pt-export-dialog">{result && <div className="product-tables pt-dialog-body" data-reviewed-rows="true"><p>{result.groupCount} 个展示组 · {result.originalCount} 张来源原卡。包含所有页、分组依据、各成员和原始单卡销量。</p><p>预览最多 6000 字符；复制和下载使用完整的 {text.length.toLocaleString('zh-CN')} 字符。</p><textarea aria-label="JSON 预览，最多 6000 字符" value={text.slice(0, 6000)} readOnly/><div className="pt-export-actions"><button className="pt-button" onClick={download}>下载完整 JSON</button><button className="pt-button" onClick={copy}>复制完整 JSON</button></div><p role="status">{message}</p></div>}</Dialog>;
}

export function ProductTables({shopId, onSelectShop, onOpenHistory}) {
  const {queries, visible} = useDataApp();
  const [controls, setControls] = useState({shopId, search: '', expandAll: false});
  const search = controls.shopId === shopId ? controls.search : '', expandAll = controls.shopId === shopId && controls.expandAll;
  const update = patch => setControls(current => ({...(current.shopId === shopId ? current : {shopId, search: '', expandAll: false}), ...patch}));
  const scope = useMemo(() => tableShopScope(queries, shopId), [queries, shopId]);
  const views = useMemo(() => Object.fromEntries(['new_watch', 'coverage_review', 'hot', 'catalog'].map(lane => [lane, buildTableView(scope, lane, search)])), [scope, search]);
  const assets = useMemo(() => new Map((queries.image_assets?.rows || []).map(row => [row.sha256, row])), [queries.image_assets]);
  const [detail, setDetail] = useState(null), [exportDocument, setExportDocument] = useState(null), [sourceRequest, setSourceRequest] = useState(null);
  const token = `${shopId}:${search}`;
  useEffect(() => { setDetail(null); setExportDocument(null); setSourceRequest(null); }, [shopId, search, queries]);
  const openSource = (type, component) => { if (type === 'source') setSourceRequest({token, component}); };
  const activeSource = sourceRequest?.token === token ? sourceRequest.component : null;
  const exportView = view => setExportDocument({token, lane: view.lane, groupCount: view.groups.length, originalCount: view.originals.length, text: JSON.stringify(exportProductTable(view, scope.shop), null, 2)});
  const show = id => visible ? visible(id) : true;
  const laneProps = {shop: scope.shop, assets, expandAll, onDetail: group => setDetail({token, group}), onExport: exportView, onOpenSource: openSource};
  return <article className="product-tables">
    {queries.product_table_shops && show('pt-shops') && <ShopOverview shops={queries.product_table_shops.rows || []} shopId={shopId} onSelectShop={onSelectShop} onOpenSource={openSource}/>}
    {!scope.shop || !queries.product_table_items ? <p className="pt-empty">当前店铺的商品表尚未生成，不能据此判断没有商品或新品。</p> : <>
      <SectionHeader id="pt-current-shop-heading" title="本店商品清单" filters={<div className="pt-controls"><label>搜索标题 / 商品 ID / 原卡<input aria-label="搜索本店商品成员和来源原卡" type="search" value={search} onChange={event => update({search: event.target.value})} placeholder="搜索全组成员与原卡"/></label><label className="pt-toggle"><input type="checkbox" checked={expandAll} onChange={event => update({expandAll: event.target.checked})}/>展开全部原卡</label></div>}/>
      {show('pt-shop-context') && <DataComponent id="pt-shop-context" queryId="product_table_shops" title="当前店铺依据" kind="metric" variant="plain" sourceRows={[scope.shop]} displayRows={[scope.shop]} scopeFilters={[{field: 'shop_id', label: '当前店铺', value: shopId}]} onOpen={openSource}><div className="pt-shop-context" data-reviewed-rows="true"><strong>{scope.shop.shop_name || shopId}</strong><span>{scope.shop.baseline_state === 'configured' ? scope.shop.baseline_coverage_note || '固定观察基线已登记。' : '基线尚未配置，暂无法比较新增记录。'}</span>{onOpenHistory && <button className="pt-text-button" onClick={() => onOpenHistory(shopId)}>查看更新与变化 ↗</button>}</div></DataComponent>}
      {show('pt-hot') && <Section id="pt-hot-heading" title="热销参考" spacing="content"><LaneTable key={`${shopId}:hot`} view={views.hot} {...laneProps}/></Section>}
      {show('pt-catalog') && <Section id="pt-catalog-heading" title="上新全量 · 新品列表" spacing="section"><LaneTable key={`${shopId}:catalog`} view={views.catalog} {...laneProps}/></Section>}
      {show('pt-new_watch') && <details className="pt-coverage" key={`${shopId}:new-records`}><summary>新增记录 · 固定起点后的首次观察</summary><LaneTable key={`${shopId}:new`} view={views.new_watch} {...laneProps}/></details>}
      {show('pt-coverage_review') && <details className="pt-coverage" key={`${shopId}:coverage`}><summary>疑似旧存量 · 覆盖扩展，展开核查日期与依据</summary><LaneTable key={`${shopId}:coverage-table`} view={views.coverage_review} {...laneProps}/></details>}
      <p className="pt-footnote">同店同轮、同标题且同图才折叠；不同图单独保留。首次看到不是已核实上架日期；销量为单卡前台展示数。各表可能引用相同原卡，数量不可相加。</p>
    </>}
    <GroupDetail group={detail?.token === token ? detail.group : null} assets={assets} onClose={() => setDetail(null)}/><ExportDialog document={exportDocument?.token === token ? exportDocument : null} onClose={() => setExportDocument(null)}/>{activeSource && <SourceSidebar key={activeSource.id} component={activeSource} queries={queries} getSource={queryId => productTableSource(queries, activeSource, queryId)} onClose={() => setSourceRequest(null)}/>}
  </article>;
}
