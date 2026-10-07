"""Read-only, per-card reference for exact positive front-end sales displays."""
from __future__ import annotations

from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

import base64
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path

from .reporting import esc, link
from .store import _connect_readonly


LABELS = ('已拼', '已抢', '总售', '已售', '售出')
GROUPS = {
    'yipin_gt10': '已拼 >10 件 · 主清单达标',
    'yipin_eq10': '已拼 =10 件 · 临界观察',
    'yipin_1to9': '已拼 1–9 件 · 低于主门槛',
    'other_positive': '其他标签或单位 · 独立参考',
}
LIMITATIONS = [
    '前台精确正数是购买展示信号，不是已核实的成交订单、独立买家或独立 SKU 数。',
    '主清单门槛仍为单卡精确已拼/已抢 >10 件；已抢与已拼件按同一指标，原标签保留；总售、已售、售出和不同单位不混算。',
    '每张卡片、每轮观察独立保留；同标题、同图片不合并，跨轮展示数不累计。',
    '展示价不是已核实的同规格到手价；规格、券、数量和运费条件可能不同。',
    '上新顺序、首次观察和图片日期不能证明实际上架日期；本报告不确认跨轮增长。',
    '仅展开本地已保存且通过 SHA-256 校验的图片；原图网址需手动打开，失败不重试。',
]


def _selection(row):
    if row['sales_precision'] == 'missing' or not row['sales_raw']:
        return 'missing'
    if row['sales_precision'] != 'exact_display' or row['sales_value'] is None:
        return 'non_exact_or_unparsed'
    if row['sales_value'] == 0:
        return 'zero'
    if row['sales_label'] not in LABELS or row['sales_value'] < 0:
        return 'other_excluded'
    return 'positive'


def _group(row):
    if row['sales_label'] in SALES_METRIC_LABELS and row['sales_unit'] == '件':
        if row['sales_value'] > 10:
            return 'yipin_gt10'
        return 'yipin_eq10' if row['sales_value'] == 10 else 'yipin_1to9'
    return 'other_positive'


def _beijing(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone(dt.timedelta(hours=8))).isoformat()


def _display_time(value):
    if not value:
        return '未知'
    try:
        moment = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            return '时区未说明，待核验'
        moment = moment.astimezone(dt.timezone(dt.timedelta(hours=8)))
        text = moment.strftime('%Y-%m-%d %H:%M:%S')
        if moment.microsecond:
            text += '.' + f'{moment.microsecond:06d}'.rstrip('0')
        return text
    except (ValueError, TypeError):
        return '时间格式待核验'


def _read_reference(data_dir, run_id):
    connection = _connect_readonly(data_dir)
    assets = {}
    try:
        connection.execute('BEGIN')
        sql = ('SELECT r.run_id,r.observed_from,r.observed_to,r.observed_from_epoch,'
               'r.observed_to_epoch,r.source_url,r.sort_order,r.status,'
               'r.end_boundary_observed,r.snapshot_sha256,s.shop_name '
               'FROM runs r JOIN shops s ON s.shop_id=r.shop_id ')
        parameters = ()
        if run_id is not None:
            sql += 'WHERE r.run_id=? '
            parameters = (run_id,)
        sql += 'ORDER BY r.observed_to_epoch DESC,r.run_id DESC'
        runs = [dict(row) for row in connection.execute(sql, parameters)]
        if not runs:
            raise ValueError('No matching snapshot run')
        for run in runs:
            rows = [dict(row) for row in connection.execute(
                'SELECT o.observation_id,o.run_id,o.view_order,o.title,o.sales_raw,'
                'o.sales_label,o.sales_value,o.sales_unit,o.sales_precision,o.price_raw,'
                'o.goods_id,o.goods_url,o.identity_status,o.identity_evidence,'
                'o.image_url,o.observed_at,o.observed_at_epoch,o.last_dom_read_at,o.row_json,'
                't.status AS image_status,t.asset_sha256,t.previous_attempt_blocked,'
                't.previous_attempt_reason,t.action_required '
                'FROM observations o LEFT JOIN image_tasks t '
                'ON t.observation_id=o.observation_id WHERE o.run_id=? '
                'ORDER BY o.view_order,o.observation_id', (run['run_id'],))]
            counts = Counter(_selection(row) for row in rows)
            run['counts'] = {name: counts[name] for name in
                             ('positive', 'missing', 'non_exact_or_unparsed', 'zero', 'other_excluded')}
            run['counts'].update(total=len(rows), excluded=len(rows) - counts['positive'])
            run['rows'] = [row for row in rows if _selection(row) == 'positive']
            label_counts = Counter((row['sales_label'], row['sales_unit']) for row in run['rows'])
            run['label_unit_counts'] = [
                {'sales_label': label, 'sales_unit': unit, 'card_count': count}
                for (label, unit), count in sorted(label_counts.items(), key=lambda item: (
                    LABELS.index(item[0][0]), item[0][1] or ''))]
            run['group_counts'] = dict(Counter(_group(row) for row in run['rows']))
            run['observed_from_beijing'] = _beijing(run['observed_from_epoch'])
            run['observed_to_beijing'] = _beijing(run['observed_to_epoch'])
            run['end_boundary_observed'] = bool(run['end_boundary_observed'])
            for row in run['rows']:
                raw = json.loads(row.pop('row_json'))
                row['observed_at_precision'] = raw.get('observedAtPrecision') or (
                    'legacy_unspecified' if row['observed_at'] else 'unknown')
                row['reference_group'] = _group(row)
                row['eligible_gt10'] = row['reference_group'] == 'yipin_gt10'
                row['automatic_image_retry_allowed'] = False
                sha = row['asset_sha256']
                row['image_content_status'] = 'not_saved'
                if sha:
                    if sha not in assets:
                        asset = connection.execute(
                            'SELECT mime,data FROM images.assets WHERE sha256=?', (sha,)).fetchone()
                        if (asset and asset['mime'] in ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
                                and hashlib.sha256(bytes(asset['data'])).hexdigest() == sha):
                            assets[sha] = ('data:' + asset['mime'] + ';base64,' +
                                           base64.b64encode(bytes(asset['data'])).decode('ascii'))
                        else:
                            assets[sha] = None
                    row['image_content_status'] = 'verified_local' if assets[sha] else 'unavailable_or_invalid'
    finally:
        connection.close()
    return runs, {sha: data for sha, data in assets.items() if data}


def _table(run):
    content = ['<div class="tablewrap"><table><thead><tr><th>列表位置</th>'
               '<th>完整商品标题</th><th>销量原文</th><th>展示价</th>'
               '<th>商品链接</th><th>主图</th>'
               '</tr></thead><tbody>']
    label_keys = {(item['sales_label'], item['sales_unit']): str(index)
                  for index, item in enumerate(run['label_unit_counts'])}
    for row in run['rows']:
        key = label_keys[(row['sales_label'], row['sales_unit'])]
        if row['image_content_status'] == 'verified_local':
            photo = (f'<details class="photo"><summary>展开已保存主图</summary>'
                     f'<img data-key="{esc(row["asset_sha256"])}" alt="{esc(row["title"])}" loading="lazy"></details>')
        elif row['image_content_status'] == 'unavailable_or_invalid':
            photo = '<b>内容缺失或校验失败，待复核</b>'
        elif row['previous_attempt_blocked'] or row['image_status'] == 'blocked_previous_attempt':
            photo = '<b>既有失败 / 限制待复核；不自动重试</b>'
        else:
            photo = '<span class="muted">主图待补</span>'
        identity = {
            'unique_goods_id': '商品 ID 本轮唯一',
            'unknown_no_goods_id': '商品 ID 待补',
            'unknown_invalid_goods_id': '商品 ID 无效，待核验',
            'conflict_goods_url': '商品 ID 与链接不一致，待核验',
            'conflict_duplicate_goods_id': '本轮商品 ID 重复，待核验',
        }.get(row['identity_status'], '商品身份待核验')
        precision = {
            'card_read': '逐卡读取时刻', 'batch_read': '批次读取时刻',
            'run_window': '仅精确到整轮观察窗口',
            'legacy_unspecified': '旧来源未说明精度', 'unknown': '未知',
        }.get(row['observed_at_precision'], '来源精度待核验')
        evidence = (
            '<details class="evidence"><summary>来源详情</summary><small>'
            f'观察记录 ID：{esc(row["observation_id"])}<br>'
            f'商品 ID：{esc(row["goods_id"])}<br>'
            f'观察时刻（北京时间）：{esc(_display_time(row["observed_at"]))}<br>'
            f'时间精度：{esc(precision)}<br>'
            f'末次页面读取（北京时间）：{esc(_display_time(row["last_dom_read_at"]))}<br>'
            f'解析口径：{esc(row["sales_label"])} · {esc(row["sales_value"])} · {esc(row["sales_unit"])}<br>'
            '销量精度：前台精确整数<br>'
            f'身份原始状态：{esc(row["identity_status"])}<br>'
            f'图片原始状态：{esc(row["image_status"])}<br>'
            f'图片 SHA-256：{esc(row["asset_sha256"])}<br>'
            f'图片历史说明：{esc(row["previous_attempt_reason"])}'
            '</small></details>')
        content.append(
            f'<tr class="card" data-label="{key}" data-group="{esc(row["reference_group"])}">'
            f'<td>#{esc(row["view_order"])}{evidence}</td>'
            f'<td class="title">{esc(row["title"])}</td>'
            f'<td><strong>{esc(row["sales_raw"])}</strong>'
            f'<span class="badge">{esc(GROUPS[row["reference_group"]])}</span></td>'
            f'<td>{esc(row["price_raw"])}</td>'
            f'<td>{link(row["goods_url"], "打开商品")}<small>{esc(identity)}</small></td>'
            f'<td>{photo}<small>{link(row["image_url"], "手动查看原图网址")}</small></td></tr>')
    if not run['rows']:
        content.append('<tr><td colspan="6">本轮没有符合条件的精确正数展示卡片。</td></tr>')
    content.append('</tbody></table></div>')
    return ''.join(content)


def _html(runs, assets, json_name):
    sections = []
    for index, run in enumerate(runs):
        counts = run['counts']
        label_options = ''.join(
            f'<option value="{key}">{esc(item["sales_label"])} · {esc(item["sales_unit"])} '
            f'（{item["card_count"]} 卡）</option>' for key, item in enumerate(run['label_unit_counts']))
        group_options = ''.join(f'<option value="{key}">{esc(value)}</option>' for key, value in GROUPS.items())
        status = {'complete': '完整', 'partial': '部分'}.get(run['status'], run['status'])
        partial_note = ('' if run['status'] == 'complete' and run['end_boundary_observed'] else
                        '<p class="notice">本轮覆盖不完整；未出现的卡片不能据此判断已下架，不能据此声称全店排名。</p>')
        sections.append(
            f'<section class="run" id="run-{index}"><h2>{esc(run["shop_name"])} · {esc(status)}轮次</h2>'
            f'<p>观察窗口（北京时间）：{esc(_display_time(run["observed_from_beijing"]))} — '
            f'{esc(_display_time(run["observed_to_beijing"]))}</p>'
            f'<details class="evidence"><summary>轮次来源</summary><p class="muted">'
            f'轮次 ID：{esc(run["run_id"])}<br>来源 SHA-256：{esc(run["snapshot_sha256"])}<br>'
            f'原排序：{esc(run["sort_order"])} · 已观察结束边界：{"是" if run["end_boundary_observed"] else "否"} · '
            f'{link(run["source_url"], "已记录来源网址")}</p></details>{partial_note}'
            f'<p>原始卡片 {counts["total"]}；精确正数 {counts["positive"]}。未进入下表：'
            f'零值 {counts["zero"]}，模糊 / 未解析 {counts["non_exact_or_unparsed"]}，'
            f'缺失 {counts["missing"]}，其他不符合口径 {counts["other_excluded"]}。</p>'
            f'<div class="filters"><label>标签与单位 <select class="label-filter">'
            f'<option value="">全部独立口径</option>{label_options}</select></label>'
            f'<label>门槛分类 <select class="group-filter"><option value="">全部精确正数</option>'
            f'{group_options}</select></label><span class="shown">显示 {counts["positive"]} 张卡片</span></div>'
            f'{_table(run)}</section>')
    run_options = ''.join(f'<option value="run-{i}">{esc(_display_time(run["observed_to_beijing"]))} · '
                          f'{esc(run["shop_name"])}</option>' for i, run in enumerate(runs))
    asset_json = json.dumps(assets, ensure_ascii=True).replace('<', '\\u003c')
    notes = ''.join(f'<li>{esc(note)}</li>' for note in LIMITATIONS)
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>销售展示逐卡参考</title>
<style>body{margin:0;background:#f4f6f8;color:#183046;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1580px;margin:30px auto;padding:0 24px}h1{margin-bottom:6px}h2{margin-top:0}section{background:white;border:1px solid #dfe6eb;border-radius:12px;padding:22px;margin:24px 0}.muted,small{color:#5c6f7b}small{display:block;font-size:12px;overflow-wrap:anywhere}.notice{padding:14px;background:#fff3d4}.filters{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:18px 0}input,select{font:inherit;padding:7px;border:1px solid #bccad4;border-radius:5px}input{width:min(470px,85vw)}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:11px;text-align:left;vertical-align:top;border-bottom:1px solid #e0e7eb}th{background:#edf2f6;white-space:nowrap}td.title{min-width:270px;white-space:normal;overflow-wrap:anywhere}td{min-width:105px}.badge{display:block;color:#176244;font-size:12px;margin-top:8px}a{color:#18669b}summary{cursor:pointer;color:#18669b}img{display:block;max-width:240px;max-height:320px;object-fit:contain;margin-top:10px}@media(max-width:1100px){main{margin:18px auto;padding:0 14px}section{padding:16px}.tablewrap{overflow:visible}table,tbody{display:block}thead{display:none}tr.card{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px 16px;padding:18px 0;border-top:1px solid #dce5eb}td{display:block;min-width:0;padding:0;border:0;overflow-wrap:anywhere}td.title{grid-column:1/-1;grid-row:2;min-width:0;font-weight:600}td:first-child{grid-column:1/-1;display:flex;gap:18px;flex-wrap:wrap;color:#5c6f7b}td:nth-child(3)::before{content:'销量原文';display:block;color:#5c6f7b;font-size:12px}td:nth-child(4)::before{content:'展示价';display:block;color:#5c6f7b;font-size:12px}td:nth-child(6){grid-column:1/-1}img{max-width:min(100%,240px)}.filters{gap:10px}.filters label{max-width:100%}input,select{max-width:100%;box-sizing:border-box}}[hidden]{display:none!important}</style></head><body><main>
<h1>销售展示逐卡参考</h1><p>默认显示各轮全部精确正数，最新轮在前；各轮内部保持原列表顺序。</p>
<div class="notice"><ul>''' + notes + '''</ul></div>
<div class="filters"><label>搜索标题 / ID / 原文 <input id="search" type="search" placeholder="输入关键词查找，完整标题均保留"></label>
<label>轮次 <select id="run-filter"><option value="">全部轮次（逐轮独立）</option>''' + run_options + '''</select></label>
<a href="''' + esc(json_name) + '''">下载逐条 JSON</a></div>''' + ''.join(sections) + '''
<p class="muted">所有展示时刻均为北京时间。来源详情保留逐卡、批次或整轮窗口等时间精度；旧来源未说明或缺失时间保持未知，未用整轮结束时间补造。</p>
</main><script>
const imageAssets=''' + asset_json + ''';
function filterRows(){
const query=document.getElementById('search').value.trim().toLocaleLowerCase();
const selectedRun=document.getElementById('run-filter').value;
document.querySelectorAll('section.run').forEach(section=>{
section.hidden=Boolean(selectedRun&&section.id!==selectedRun);
const label=section.querySelector('.label-filter').value, group=section.querySelector('.group-filter').value;
let shown=0;section.querySelectorAll('tr.card').forEach(row=>{
row.hidden=Boolean((label&&row.dataset.label!==label)||(group&&row.dataset.group!==group)||(query&&!row.textContent.toLocaleLowerCase().includes(query)));
if(!row.hidden)shown++;});section.querySelector('.shown').textContent='显示 '+shown+' 张卡片';});}
document.addEventListener('input',filterRows);document.addEventListener('change',filterRows);
document.addEventListener('toggle',event=>{if(event.target.open){event.target.querySelectorAll('img[data-key]').forEach(img=>{if(!img.getAttribute('src')){const asset=imageAssets[img.dataset.key];if(asset)img.src=asset;}});}},true);
</script></body></html>'''


def create_sales_reference(data_dir, output, run_id=None):
    """Write HTML and matching JSON from existing databases; never mutate them."""
    output = Path(output)
    if output.suffix.lower() not in ('.html', '.htm'):
        raise ValueError('Sales reference output must use an .html or .htm extension')
    runs, assets = _read_reference(data_dir, run_id)
    json_output = output.with_suffix('.json')
    payload = {'report_type': 'exact_positive_sales_reference',
               'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
               'scope': 'one_run' if run_id is not None else 'all_runs_separately',
               'run_count': len(runs), 'limitations': LIMITATIONS, 'runs': runs}
    html = _html(runs, assets, json_output.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    for path, content in ((json_output, json.dumps(payload, ensure_ascii=False, indent=2) + '\n'),
                          (output, html)):
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
    return {'status': 'saved', 'path': str(output), 'json_path': str(json_output),
            'run_count': len(runs), 'run_ids': [run['run_id'] for run in runs],
            'positive_observation_count': sum(len(run['rows']) for run in runs),
            'embedded_unique_image_count': len(assets),
            'scope': payload['scope'], 'database_writes_performed': False,
            'network_requests_performed': False}
