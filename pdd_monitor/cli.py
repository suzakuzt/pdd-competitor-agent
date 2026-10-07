"""Local data commands; artist-heat-refresh explicitly reads one public work chart."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import store

PROJECT = Path(__file__).resolve().parents[1]


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def validate(data_dir, run_id=None, output=None):
    from .validation import validate_store
    result = validate_store(data_dir, run_id=run_id)
    if output:
        save_json(output, result)
    return result


def import_and_validate(args, snapshot, image_archive=None, image_queue=None, detail_note=None):
    result = store.import_snapshot(args.data_dir, snapshot, image_archive=image_archive,
                                   image_queue_path=image_queue, detail_note_path=detail_note)
    report = validate(args.data_dir, result['run_id'])
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    audit_path = PROJECT / 'reports' / f'import_validation_{stamp}.json'
    save_json(audit_path, report)
    result['validation'] = report
    result['validation_report'] = str(audit_path)
    return result


def run_inbox(args):
    inbox = Path(args.inbox)
    if not inbox.is_dir():
        raise ValueError(f'Inbox does not exist: {inbox}')
    entries, skipped = [], []
    for path in sorted(inbox.glob('*.json')):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            entries.append((str(path), path, str(exc)))
            continue
        if not isinstance(value, dict) or not isinstance(value.get('rows'), list) or not value.get('observedFrom'):
            skipped.append(path.name)
            continue
        entries.append((value['observedFrom'], path, None))
    results, errors = [], []
    for _, path, error in sorted(entries, key=lambda x: x[0]):
        if error:
            errors.append({'file': str(path), 'error': error})
            continue
        def sidecar(suffix):
            value = path.with_name(path.stem + suffix)
            return value if value.is_file() else None
        try:
            result = import_and_validate(args, path, sidecar('.images.zip'),
                                         sidecar('.image_queue.json'), sidecar('.detail_note.json'))
            results.append({'file': str(path), **result})
            if not result['validation'].get('ok'):
                errors.append({'file': str(path), 'error': 'Post-import validation failed'})
        except (OSError, ValueError, sqlite3.Error) as exc:
            errors.append({'file': str(path), 'error': str(exc)})
    return {'status': 'failed' if errors else ('processed' if results else 'no_input'),
            'source': 'local_snapshot_files_only', 'website_collection_performed': False,
            'results': results, 'errors': errors, 'skipped_non_snapshot_files': skipped}


def parser():
    p = argparse.ArgumentParser(description='拼多多多店竞品 SQLite 数据工作流（本地数据处理）')
    p.add_argument('--data-dir', type=Path, default=PROJECT / 'data')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='创建数据库结构')
    imp = sub.add_parser('import-snapshot', help='导入授权采集的快照并验证')
    imp.add_argument('snapshot', type=Path)
    imp.add_argument('--image-archive', type=Path)
    imp.add_argument('--image-queue', type=Path)
    imp.add_argument('--detail-note', type=Path)
    attach = sub.add_parser('attach-images', help='向既有快照补充已取得的图片，不改原始销量')
    attach.add_argument('run_id')
    attach.add_argument('image_archive', type=Path)
    for name in ('summary', 'candidates', 'validate'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--run-id')
        if name == 'validate':
            cmd.add_argument('--output', type=Path)
    comp = sub.add_parser('compare', help='只比较身份和口径可核对的两轮数据')
    comp.add_argument('older_run_id')
    comp.add_argument('newer_run_id')
    export = sub.add_parser('export', help='导出候选组 JSON')
    export.add_argument('--run-id')
    export.add_argument('--output', required=True, type=Path)
    report = sub.add_parser('report', help='从数据库生成含已有主图的本地 HTML 报告')
    report.add_argument('--run-id')
    report.add_argument('--output', type=Path, default=PROJECT / 'reports' / 'dashboard.html')
    sales = sub.add_parser('sales-reference', help='按轮次导出所有精确正数销量展示的商品参考 HTML 和 JSON')
    sales.add_argument('--run-id')
    sales.add_argument('--output', type=Path, default=PROJECT / 'reports' / 'sales_reference.html')
    dashboard_data = sub.add_parser('dashboard-data', help='只读导出数据面板快照，不采集网站')
    dashboard_data.add_argument('--output', type=Path, default=PROJECT / 'dashboard/src/data.json')
    sub.add_parser('dashboard-build', help='从现有数据库更新并离线构建数据面板')
    sub.add_parser('artist-heat-refresh', help='读取爱奇艺公开作品榜并保存一次独立观察；不采集拼多多')
    profit_preview = sub.add_parser('profit-preview', help='只预览用户试品计划与累计结果，不保存也不采集')
    profit_preview.add_argument('input', type=Path)
    profit_save = sub.add_parser('profit-save', help='保存独立试品版本并重建面板，不自动花费预算')
    profit_save.add_argument('input', type=Path)
    profit_save.add_argument('--expected-revision', required=True, type=int)
    dashboard_open = sub.add_parser('dashboard-open', help='打开已构建的本地数据面板')
    dashboard_open.add_argument('--no-browser', action='store_true')
    dashboard_open.add_argument('--port', type=int, default=8878)
    target = sub.add_parser('competitor-add', help='登记拼多多竞品名称/链接，不采集网站')
    target.add_argument('--name')
    target.add_argument('--url')
    sub.add_parser('competitor-list', help='列出已登记目标与已有真实观察的店铺')
    setting = sub.add_parser('competitor-tracking-set', help='仅保存一店开启/暂停设置，保留历史和固定起点；不启动采集')
    setting.add_argument('--target-id', required=True)
    setting.add_argument('--status', choices=('enabled', 'paused'), required=True)
    setting.add_argument('--expected-revision', type=int, required=True)
    sub.add_parser('tracking-plan', help='只读列出明确开启店铺及下一步；不运行采集或调度')
    capture_plan = sub.add_parser('capture-plan', help='检查本店采集历史、未完成尝试和重采流程；不读网页')
    capture_plan.add_argument('--shop-id', required=True)
    capture_check = sub.add_parser('capture-check', help='独立复核原始批次覆盖、封存快照和卡片数量变化；不写业务库')
    capture_check.add_argument('snapshot', type=Path)
    capture_check.add_argument('--expected-shop-id')
    tracking = sub.add_parser('tracking-start', help='为指定已观察店铺保存一次固定新品关注起点')
    tracking.add_argument('--shop-id', required=True)
    tracking.add_argument('--started-at', help='带时区的开始时间；默认当前UTC，不改变已有起点')
    profile = sub.add_parser('competitor-export', help='导出一店完整标准分析包、历史原卡和已有图片')
    profile.add_argument('--shop-id', required=True)
    profile.add_argument('--output', required=True, type=Path)
    inbox = sub.add_parser('run-inbox', help='处理本地 inbox 快照；不会抓取网站')
    inbox.add_argument('--inbox', type=Path, default=PROJECT / 'inbox')
    image = sub.add_parser('export-image', help='从图片库导出已核对的原始内容')
    image.add_argument('sha256')
    image.add_argument('--output', required=True, type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ('profit-preview', 'profit-save'):
            from .profit_service import validate_trial_reference
            from .profit_lab import evaluate_trial, save_trial
            trial = json.loads(args.input.read_text(encoding='utf-8'))
            root = args.data_dir.resolve().parent
            validate_trial_reference(root, trial)
            analysis = evaluate_trial(trial)
            if args.command == 'profit-preview':
                result = {'status':'ok', 'analysis':analysis, 'saved':False}
            else:
                receipt = save_trial(root, trial, args.expected_revision)
                from .dashboard_runtime import build_dashboard
                try:
                    build = build_dashboard(root, args.data_dir)
                    if not isinstance(build, dict) or build.get('status') != 'built':
                        raise ValueError('面板未确认构建成功')
                except Exception:
                    emit({**receipt, 'status':'saved_but_build_failed', 'dashboard_built':False,
                          'message':'试品版本已保存，面板构建失败；请重新构建，不要按旧版本重复保存。'})
                    return 2
                result = {**receipt, 'dashboard_built':build.get('status')=='built', 'analysis':analysis}
        elif args.command == 'artist-heat-refresh':
            from .artist_heat import fetch_iqiyi_snapshot
            result = fetch_iqiyi_snapshot(args.data_dir.resolve().parent)
        elif args.command == 'init':
            store.initialize(args.data_dir)
            result = {'status': 'initialized', 'data_dir': str(args.data_dir)}
        elif args.command == 'import-snapshot':
            result = import_and_validate(args, args.snapshot, args.image_archive, args.image_queue, args.detail_note)
            emit(result)
            return 0 if result['validation'].get('ok') else 2
        elif args.command == 'summary':
            result = store.get_summary(args.data_dir, args.run_id)
        elif args.command == 'attach-images':
            result = store.attach_images(args.data_dir, args.run_id, args.image_archive)
            result['validation'] = validate(args.data_dir, args.run_id)
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            audit_path = PROJECT / 'reports' / f'image_validation_{stamp}.json'
            save_json(audit_path, result['validation'])
            result['validation_report'] = str(audit_path)
            emit(result)
            return 0 if result['validation'].get('ok') else 2
        elif args.command == 'candidates':
            result = store.get_candidates(args.data_dir, args.run_id)
        elif args.command == 'validate':
            result = validate(args.data_dir, args.run_id, args.output)
            emit(result)
            return 0 if result.get('ok') else 2
        elif args.command == 'compare':
            result = store.compare_runs(args.data_dir, args.older_run_id, args.newer_run_id)
        elif args.command == 'export':
            result = {'summary': store.get_summary(args.data_dir, args.run_id),
                      'groups': store.get_candidates(args.data_dir, args.run_id)}
            save_json(args.output, result)
            result = {'status': 'saved', 'path': str(args.output), 'group_count': len(result['groups'])}
        elif args.command == 'report':
            from .reporting import create_report
            result = create_report(args.data_dir, args.output, args.run_id)
        elif args.command == 'sales-reference':
            from .sales_reference import create_sales_reference
            result = create_sales_reference(args.data_dir, args.output, args.run_id)
        elif args.command == 'dashboard-data':
            from .dashboard_data import build_dashboard_snapshot
            snapshot = build_dashboard_snapshot(args.data_dir, args.output)
            result = {'status': 'saved', 'path': str(args.output), 'default_run_id': snapshot['metadata']['default_run_id'], 'query_row_counts': {key: len(query['rows']) for key, query in snapshot['queries'].items()}}
        elif args.command in ('capture-plan', 'capture-check'):
            from .capture_control import plan, check
            result = plan(args.data_dir.resolve().parent, args.shop_id) if args.command == 'capture-plan' else check(args.data_dir.resolve().parent, args.snapshot, args.expected_shop_id)
            emit(result)
            return 2 if result.get('valid') is False or result.get('coverage_drop_requires_review') else 0
        elif args.command == 'competitor-add':
            from .competitor_registry import register_target
            result = register_target(args.data_dir.resolve().parent, args.name, args.url)
        elif args.command in ('competitor-list', 'competitor-tracking-set', 'tracking-plan'):
            from .competitor_registry import build_targets, set_target_tracking, list_followed_targets
            with store._connect_readonly(args.data_dir) as connection:
                runs = [dict(row) for row in connection.execute('SELECT r.*,s.shop_name,(SELECT COUNT(*) FROM observations o WHERE o.run_id=r.run_id) AS card_count FROM runs r JOIN shops s ON s.shop_id=r.shop_id')]
            root = args.data_dir.resolve().parent
            if args.command == 'competitor-tracking-set':
                result = set_target_tracking(root, runs, args.target_id, args.status, args.expected_revision)
                result.update(dashboard_built=False, message='跟踪设置已保存；面板需另行构建，自动采集尚未接通。')
            elif args.command == 'tracking-plan':
                from .collection_dashboard import build_collection_queries
                collection, _ = build_collection_queries(root)
                result = list_followed_targets(root, runs, collection['collection_attempts']['rows'])
            else:
                targets, fingerprints = build_targets(root, runs)
                result = {'targets': targets, 'registry_files_sha256': fingerprints, 'website_collection_performed': False}
        elif args.command in ('tracking-start', 'competitor-export'):
            from .dashboard_data import build_dashboard_snapshot
            with tempfile.TemporaryDirectory(prefix='pdd_competitor_readonly_') as temporary:
                snapshot = build_dashboard_snapshot(args.data_dir, Path(temporary) / 'data.json')
            if args.command == 'tracking-start':
                from .portfolio_tracking import start_shop_tracking
                result = start_shop_tracking(args.data_dir.resolve().parent, snapshot['queries']['runs']['rows'],
                    snapshot['queries']['observations']['rows'], args.shop_id,
                    args.started_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
            else:
                from .competitor_export import export_competitor
                root = args.data_dir.resolve().parent
                if any(args.output.resolve().is_relative_to(root / folder) for folder in ('data','sources','state')):
                    raise ValueError('Analysis export must be outside data, sources and state')
                result = export_competitor(snapshot, args.shop_id, args.output)
        elif args.command == 'dashboard-build':
            from .dashboard_runtime import build_dashboard
            result = build_dashboard(PROJECT, args.data_dir)
        elif args.command == 'dashboard-open':
            from .dashboard_runtime import start_dashboard
            result = start_dashboard(PROJECT, args.port, open_browser=not args.no_browser)
        elif args.command == 'run-inbox':
            result = run_inbox(args)
            emit(result)
            return 2 if result['errors'] else 0
        elif args.command == 'export-image':
            if len(args.sha256) != 64 or any(c not in '0123456789abcdef' for c in args.sha256):
                raise ValueError('Expected a lowercase SHA-256 digest')
            conn = store.connect(args.data_dir)
            try:
                row = conn.execute('SELECT data, mime FROM images.assets WHERE sha256=?', (args.sha256,)).fetchone()
                if not row:
                    raise ValueError('Image not found')
                blob = bytes(row['data'])
                if hashlib.sha256(blob).hexdigest() != args.sha256:
                    raise ValueError('Stored image checksum mismatch')
            finally:
                conn.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if args.output.exists():
                raise ValueError('Output already exists; choose a new file')
            args.output.write_bytes(blob)
            result = {'status': 'saved', 'path': str(args.output), 'mime': row['mime'], 'bytes': len(blob)}
        else:
            raise ValueError('Unknown command')
        emit(result)
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        emit({'status': 'error', 'type': type(exc).__name__, 'message': str(exc)})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
