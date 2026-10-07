"""Complete source-backed standard competitor package, one observed shop."""
from pathlib import Path
from copy import deepcopy
from .competitor_registry import write_new_json


def export_competitor(snapshot, shop_id, output):
    query = lambda key: snapshot['queries'][key]['rows']
    profiles = [row for row in query('competitor_shops') if row['shop_id'] == shop_id]
    if len(profiles) != 1:
        raise ValueError('Choose one observed shop_id from competitor-list')
    runs = [row for row in query('runs') if row['shop_id'] == shop_id]
    run_ids = {row['run_id'] for row in runs}
    observations = [row for row in query('observations') if row['run_id'] in run_ids]
    asset_ids = {row.get('asset_sha256') for row in observations}
    summaries = [row for row in query('comparison_summaries') if row['target_run_id'] in run_ids]
    comparisons = {row['comparison_id'] for row in summaries}
    selected = {'runs': runs, 'observations': observations,
        'image_assets': [row for row in query('image_assets') if row['sha256'] in asset_ids],
        'candidate_groups': [row for row in query('candidate_groups') if row['run_id'] in run_ids],
        'comparison_summaries': summaries,
        'comparison_items': [row for row in query('comparison_items') if row['comparison_id'] in comparisons],
        'run_history': [row for row in query('run_history') if row['run_id'] in run_ids],
        'new_arrival_summary': [row for row in query('new_arrival_summary') if row['shop_id'] == shop_id],
        'new_arrival_items': [row for row in query('new_arrival_items') if row['run_id'] in run_ids]}
    for key in ('competitor_shops','competitor_products','competitor_dimensions','competitor_strategy'):
        selected[key] = [row for row in query(key) if row['shop_id'] == shop_id]
    for key in ('competitor_targets', 'artist_watchlist', 'artist_research_summary', 'profit_opportunities', 'profit_opportunity_summary', 'profit_trials', 'profit_trial_summary'):
        if key in snapshot['queries']:
            selected[key] = [row for row in query(key) if row['shop_id'] == shop_id]
    public_queries = {'artist_source_channels', 'artist_heat_people', 'artist_heat_history', 'artist_heat_summary', 'artist_heat_sources'}
    trial_queries = {'profit_trials', 'profit_trial_summary'}
    own_target_ids = {row['target_id'] for row in selected.get('competitor_targets', [])}
    def own_tracking_file(name):
        path = Path(str(name).replace('\\', '/'))
        if path.parent.parent.name == 'competitors':
            if path.parent.name == 'targets':
                return path.stem in own_target_ids
            if path.parent.name == 'tracking':
                return path.stem == shop_id
        return True
    for key in sorted(public_queries):
        if key in snapshot['queries']:
            selected[key] = query(key)
    def source_for(key):
        source=deepcopy(snapshot['queries'][key]['source'])
        if key == 'competitor_targets':
            source.pop('observationWindows', None)
            source['files'] = [name for name in source.get('files', []) if own_tracking_file(name)]
            source['registry_files_sha256'] = {name: sha for name, sha in
                snapshot.get('metadata', {}).get('competitor_registry_sha256', {}).items()
                if own_tracking_file(name)}
            for definition in source.get('metricDefinitions', []):
                for lineage in definition.get('sourceLineage', []):
                    if isinstance(lineage.get('tables'), list):
                        lineage['tables'] = [name for name in lineage['tables'] if own_tracking_file(name)]
            source['filters'] = [*source.get('filters', []), f'本标准包限定 shop_id={shop_id}；仅本店登记、跟踪设置与流程状态。共享采集日志是派生来源，仅绑定本店shop_id或run_id的证据参与本店状态。']
            return source
        if key in trial_queries:
            source.pop('observationWindows', None)
            trial_ids = {row['trial_id'] for row in selected.get('profit_trials', [])}
            source['files'] = {name: digest for name, digest in source.get('files', {}).items()
                               if Path(name.replace('\\', '/')).parent.name in trial_ids}
            source['filters'] = [*source.get('filters', []), f'本标准包限定 shop_id={shop_id}；保留本店用户试品队列及版本来源，不按拼多多采集轮次过滤。']
            return source
        if key in public_queries:
            source.pop('observationWindows', None)
            source['filters'] = ['完整保留快照中的已审阅公共目录与作品榜记录；不按店铺或观察轮次过滤，不代表本店专属来源。']
            return source
        if 'observationWindows' in source:
            source['observationWindows']=[window for window in source['observationWindows'] if window['run_id'] in run_ids]
        source['filters']=[*source.get('filters',[]), f'本标准包额外限定 shop_id={shop_id}；仅导出本店轮次与引用原卡。上方SQL为基础只读查询，派生和导出筛选另列。']
        return source
    value = {'schema_version':1,'model_version':'pdd_competitor_v1','shop_id':shop_id,
        'generated_at':snapshot['generatedAt'],'timezone':'Asia/Shanghai',
        'scope':'all stored historical observations plus one explicitly selected reference run; not all currently listed products unless the source scope establishes it',
        'source_database_sha256':snapshot['metadata']['source_database_sha256'],
        'queries':{key:{'rows':rows,'source':source_for(key),
                        'export_filter':({'scope':'public_directory','shop_independent':True}
                                         if key in public_queries else
                                         {'shop_id':shop_id,'scope':'user_trial_queues'} if key in trial_queries else
                                         {'shop_id':shop_id,'scope':'competitor_tracking_state'} if key == 'competitor_targets' else
                                         {'shop_id':shop_id,'run_ids':sorted(run_ids)})}
                   for key,rows in selected.items()},
        'limitations':['展示数不是已核实成交；策略只给证据支持的候选解释和验证动作。',
                       '不同店铺/轮次不能累加为唯一商品；部分采集不能声称全量。']}
    output = Path(output).resolve()
    if output.suffix.lower() != '.json':
        raise ValueError('Standard competitor export must be JSON')
    write_new_json(output, value)
    return {'status':'saved','path':str(output),'shop_id':shop_id,'historical_observation_count':len(observations),
            'reference_product_count':len(selected['competitor_products']),'website_collection_performed':False}
