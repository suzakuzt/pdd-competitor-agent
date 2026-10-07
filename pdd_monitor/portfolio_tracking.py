"""Per-shop fixed arrival anchors, retaining the original legacy configuration."""
import hashlib
import json
import re
from pathlib import Path
from .new_arrivals import load_tracking_config, make_tracking_config, build_new_arrivals


def _read_configs(project):
    folder = Path(project) / 'state' / 'new_arrivals'
    legacy = folder / 'tracking_config.json'
    paths = ([legacy] if legacy.exists() else []) + sorted((folder / 'shops').glob('*.json'))
    configs, fingerprints = {}, {}
    for path in paths:
        if not path.is_file():
            raise ValueError('Tracking configuration must be a regular file')
        raw = path.read_bytes()
        config = load_tracking_config(path)
        if path.read_bytes() != raw:
            raise ValueError('Tracking configuration changed while reading')
        shop = config['shop_id']
        if path != legacy and path.name != shop + '.json':
            raise ValueError('Tracking filename and shop identity differ')
        if shop in configs:
            raise ValueError('More than one fixed tracking origin for a shop')
        configs[shop] = (config, path)
        fingerprints[str(path)] = hashlib.sha256(raw).hexdigest()
    return configs, fingerprints


def build_portfolio_arrivals(project, runs, observations):
    configs, fingerprints = _read_configs(project)
    shops = sorted({run['shop_id'] for run in runs})
    if set(configs) - set(shops):
        raise ValueError('Configured tracking shop is missing from stored observations')
    result = {'new_arrival_items': [], 'new_arrival_summary': []}
    for shop in shops:
        if shop in configs:
            part = build_new_arrivals(runs, observations, configs[shop][0])
            result['new_arrival_items'].extend(dict(row, shop_id=shop) for row in part['new_arrival_items'])
            result['new_arrival_summary'].extend(part['new_arrival_summary'])
        else:
            result['new_arrival_summary'].append({'shop_id': shop, 'timezone': 'Asia/Shanghai',
                'state': 'not_configured', 'tracking_id': None, 'started_at': None,
                'baseline_run_ids': [], 'baseline_observation_count': 0, 'post_baseline_run_count': 0,
                'post_baseline_observation_count': 0, 'first_observed_id_candidate_count': 0,
                'new_card_clue_count': 0, 'item_count': 0})
    legacy = Path(project) / 'state/new_arrivals/tracking_config.json'
    metadata = {'configured': bool(configs), 'config_path': str(legacy),
        'config_sha256': fingerprints.get(str(legacy)), 'per_shop': [
            {'shop_id': shop, 'tracking_id': config['tracking_id'], 'config_path': str(path),
             'config_sha256': fingerprints[str(path)]} for shop, (config, path) in configs.items()],
        'config_files_sha256': fingerprints}
    for filename, digest in fingerprints.items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != digest:
            raise ValueError('Tracking configuration changed during derivation')
    return result, metadata


def start_shop_tracking(project, runs, observations, shop_id, started_at):
    if not re.fullmatch(r'shop_[0-9a-f]{24}', shop_id):
        raise ValueError('Use an observed shop_id from competitor-list')
    configs, _ = _read_configs(project)
    if shop_id in configs:
        config, path = configs[shop_id]
        # Existing origin is immutable, even if new rounds have since arrived.
        build_new_arrivals(runs, observations, config)
        return {'status': 'unchanged', 'config_path': str(path), 'config': config}
    config = make_tracking_config(runs, observations, shop_id=shop_id, started_at=started_at)
    path = Path(project) / 'state/new_arrivals/shops' / (shop_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    from .competitor_registry import write_new_json
    write_new_json(path, config)
    return {'status': 'created', 'config_path': str(path), 'config': config}
