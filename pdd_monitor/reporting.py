"""An offline, source-backed report generated from the SQLite databases."""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
from pathlib import Path
from urllib.parse import urlsplit

from .store import _connect_readonly


def esc(value):
    return html.escape('未显示' if value is None else str(value), quote=True)


def link(url, label):
    try:
        safe_scheme = url and urlsplit(url).scheme in ('http', 'https')
    except ValueError:
        safe_scheme = False
    if safe_scheme:
        return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'
    return '<span class="muted">链接待核验</span>'


def create_report(data_dir, output, run_id=None):
    if not (Path(data_dir) / 'monitor.sqlite3').is_file():
        raise ValueError('Database missing; import a snapshot first')
    conn = _connect_readonly(data_dir)
    try:
        conn.execute('PRAGMA query_only=ON')
        if run_id is None:
            run = conn.execute('SELECT * FROM runs ORDER BY observed_to_epoch DESC, run_id DESC LIMIT 1').fetchone()
        else:
            run = conn.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if not run:
            raise ValueError('No matching snapshot run')
        run_id = run['run_id']
        rows = [dict(r) for r in conn.execute('SELECT o.*, t.status AS image_status, t.asset_sha256 '
                'FROM observations o LEFT JOIN image_tasks t ON t.observation_id=o.observation_id '
                'WHERE o.run_id=? ORDER BY o.view_order', (run_id,))]
        groups = [dict(g) for g in conn.execute('SELECT * FROM candidate_groups WHERE run_id=? ORDER BY first_view_order', (run_id,))]
        membership = {}
        for row in conn.execute('SELECT group_id, observation_id FROM group_members WHERE run_id=?', (run_id,)):
            membership.setdefault(row['group_id'], []).append(row['observation_id'])
        assets = {}
        for asset in conn.execute('SELECT DISTINCT a.sha256,a.mime,a.data FROM images.assets a JOIN image_tasks t '
                                  'ON t.asset_sha256=a.sha256 WHERE t.run_id=?', (run_id,)):
            assets[asset['sha256']] = 'data:' + asset['mime'] + ';base64,' + base64.b64encode(bytes(asset['data'])).decode('ascii')
        shop = conn.execute('SELECT shop_name FROM shops WHERE shop_id=?', (run['shop_id'],)).fetchone()['shop_name']
    finally:
        conn.close()
    by_id = {r['observation_id']: r for r in rows}
    eligible = [r for r in rows if r['eligible_gt10']]
    missing = sum(r['sales_raw'] is None or r['sales_raw'] == '' for r in rows)
    saved = sum(bool(r['asset_sha256']) for r in rows)
    zone = dt.timezone(dt.timedelta(hours=8))
    start = dt.datetime.fromtimestamp(run['observed_from_epoch'], zone).strftime('%Y-%m-%d %H:%M:%S')
    end = dt.datetime.fromtimestamp(run['observed_to_epoch'], zone).strftime('%H:%M:%S')

    def table(members):
        content = ['<div class="tablewrap"><table><thead><tr><th>列表位置</th><th>产品信息</th><th>销量原文</th><th>展示价</th><th>商品链接</th><th>已有主图</th></tr></thead><tbody>']
        for r in members:
            badge = '<span class="yes">达标</span>' if r['eligible_gt10'] else '<span class="muted">对照项</span>'
            photo = '<span class="muted">待后续处理</span>'
            if r['asset_sha256'] in assets:
                photo = f'<details class="photo"><summary>查看主图</summary><img data-key="{esc(r["asset_sha256"])}" alt="{esc(r["title"])}" loading="lazy"></details>'
            image_status = '已保存' if r['asset_sha256'] in assets else ('失败待复核' if r['image_status'] == 'blocked_previous_attempt' else '待处理')
            identity_note = {
                'unique_goods_id': '商品ID单轮唯一',
                'unknown_no_goods_id': '待补商品ID',
                'unknown_invalid_goods_id': '商品ID无效，身份未知',
                'conflict_goods_url': '商品ID与链接冲突，待核验',
                'conflict_duplicate_goods_id': '本轮商品ID重复，待核验',
            }.get(r['identity_status'], '身份待核验')
            content.append(f'<tr><td>#{r["view_order"]}<br>{badge}</td><td class="title">{esc(r["title"])}</td><td><strong>{esc(r["sales_raw"])}</strong></td><td>{esc(r["price_raw"])}</td><td>{link(r["goods_url"], r["goods_id"] or "查看商品")}<br><small>{identity_note}</small></td><td>{photo}<small>{image_status}</small></td></tr>')
        content.append('</tbody></table></div>')
        return ''.join(content)

    sections = [f'<h2>当前达标清单 · {len(eligible)} 张卡片</h2>', table(eligible),
                '<h2>同标题候选组</h2><p class="muted">这些组用于逐条对照。尚未核实为同设计或同SKU；销量不相加。</p>']
    for g in groups:
        members = sorted((by_id[i] for i in membership.get(g['group_id'], [])), key=lambda r: r['view_order'])
        sections.append(f'<details class="group"><summary><b>{esc(g["group_code"])}</b>　{esc(g["normalized_title"])}　<span class="muted">{g["member_count"]} 条对照 / {g["trigger_count"]} 条达标</span></summary>{table(members)}</details>')
    sections.append(f'<details class="group"><summary>全部原始卡片 · {len(rows)} 条</summary>{table(rows)}</details>')
    json_assets = json.dumps(assets, ensure_ascii=False).replace('<', '\\u003c')
    status_label = {'complete': '完整', 'partial': '部分'}.get(run['status'], run['status'])
    coverage_note = ('' if run['status'] == 'complete' and run['end_boundary_observed'] else
                     '<p class="notice">本轮覆盖不完整：仅列实际采到的卡片；不能据缺失判断下架，也不能据此给出全店排名。历史完整快照仍独立保留。</p>')
    content = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(shop)} · SQLite 监测结果</title>
<style>body{{font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif;background:#f4f6f9;color:#172c42;margin:0}}main{{max-width:1360px;margin:28px auto;padding:0 22px}}h1{{font-size:27px;margin-bottom:4px}}h2{{margin-top:30px}}.muted,small{{color:#617285}}small{{display:block;font-size:12px}}.meta{{display:flex;gap:14px;flex-wrap:wrap;margin:20px 0}}.metric{{background:white;padding:15px 22px;border-radius:10px;min-width:130px}}.metric b{{display:block;font-size:29px}}.notice{{padding:15px 20px;background:#fff6df;border-left:4px solid #d5a124;border-radius:6px}}.tablewrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{border-bottom:1px solid #e1e7ee;padding:11px 13px;text-align:left;vertical-align:top}}th{{white-space:nowrap;background:#eaf0f6}}td.title{{min-width:240px;max-width:460px}}.yes{{color:#12633d;font-size:12px;font-weight:700}}a{{color:#2368a8;word-break:break-all}}details.group{{background:white;border:1px solid #dde4ec;border-radius:8px;margin:12px 0}}summary{{cursor:pointer;padding:12px}}.photo summary{{padding:0;color:#2368a8;white-space:nowrap}}img{{display:block;width:200px;height:200px;object-fit:contain;margin-top:10px}}footer{{margin:25px 0;color:#617285;font-size:12px}}</style></head><body><main>
<h1>{esc(shop)} · SQLite 监测结果</h1><p class="muted">观察时间：{start}—{end}（北京时间） · 快照状态：{esc(status_label)}</p>
{coverage_note}
<div class="meta"><div class="metric">已采商品卡片<b>{len(rows)}</b></div><div class="metric">单卡已拼 &gt;10<b>{len(eligible)}</b></div><div class="metric">同标题候选组<b>{len(groups)}</b></div><div class="metric">有图片内容的卡片<b>{saved}</b></div></div>
<div class="notice">本轮为观察数据，卡片数不等于独立商品或SKU数量。上新排序不证明上架日期；暂无可靠依据确认所有新品与跨轮增长。未显示销量共 {missing} 张卡片，保留未知。价格是前台展示价，规格及优惠可能不同。图片从本地SQLite读取，缺图单独待处理。</div>
{''.join(sections)}<footer>来源运行ID：{esc(run_id)}<br>原始快照SHA-256：{esc(run['snapshot_sha256'])}<br>此报告由本地数据库生成；打开页面不会下载远程图片。</footer></main>
<script>const imageAssets={json_assets};document.addEventListener('toggle',event=>{{if(event.target.open){{event.target.querySelectorAll('img[data-key]').forEach(img=>{{if(!img.src)img.src=imageAssets[img.dataset.key]||'';}});}}}},true);</script></body></html>'''
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(output)
    return {'status': 'saved', 'path': str(output), 'run_id': run_id, 'card_count': len(rows),
            'eligible_gt10_count': len(eligible), 'candidate_group_count': len(groups), 'embedded_unique_image_count': len(assets)}
