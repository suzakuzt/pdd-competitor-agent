"""Read-only dashboard provenance adapter for the independent attempt journal."""
from datetime import date, timedelta
import hashlib
from pathlib import Path
from .collection_log import get_status

def build_collection_queries(project):
    state = Path(project) / 'state' / 'collection_attempts'
    journal = state / 'journal.sqlite3'
    before = hashlib.sha256(journal.read_bytes()).hexdigest() if journal.is_file() else None
    status = get_status(state)
    today = date.fromisoformat(status['date'])
    dates = [today]
    enabled_at = status.get('first_enabled_at') or status['scheduler'].get('enabled_at')
    if enabled_at:
        from .collection_log import _parse, TZ
        dates.append(_parse(enabled_at).astimezone(TZ).date())
    for item in status['attempts']:
        if item.get('slot_date'):
            dates.append(date.fromisoformat(item['slot_date']))
    first_day = min(dates)
    slots = []
    cursor = first_day
    while cursor <= today:
        daily = status if cursor == today else get_status(state, cursor.isoformat())
        slots.extend(daily['slots'])
        cursor += timedelta(days=1)
    after = hashlib.sha256(journal.read_bytes()).hexdigest() if journal.is_file() else None
    if before != after:
        raise ValueError('Collection journal changed while exporting; retry after collection commits')
    caveats = ['运行日志记录采集尝试，不负责触发定时器；启用状态需独立调度凭据。',
               'manual/manual_history不算08:00或20:00到点成功；缺轮次、partial、failed、missed不表示无变化。',
               '状态为导出时刻的快照，超过时段无新数据须重新刷新后复核，不能当实时运行状态。'] + status.get('history_limitations', [])
    def query(label, rows, tables, code):
        return {'rows': rows, 'source': {'label':label,'provider':'SQLite','classification':'observed' if tables==['attempts'] else 'derived',
            'tables':tables,'files':[str(journal)],'executedAt':status['generated_at'],'timezone':'Asia/Shanghai',
            'grain':'one collection attempt' if tables==['attempts'] else 'one local date and scheduled slot',
            'sql':'SELECT * FROM attempts ORDER BY recorded_at DESC,attempt_id;\nSELECT * FROM configuration WHERE config_id=1;\nSELECT * FROM scheduler_events ORDER BY event_id; -- v2; v1 uses its current configuration as a limited legacy baseline',
            'caveats':caveats,'metricDefinitions':[{'label':label,'definition':code,'sourceLineage':[{'tables':tables}]}],
            'evidenceFlow':[{'title':'只读日志','detail':'journal.sqlite3 mode=ro/query_only；未创建日志、未触发采集。'},
                            {'title':'派生档位','detail':'collection_log.get_status以Asia/Shanghai每日08:00、20:00和30分钟开始窗口派生计划/错过状态。'}]},
            'methods':[{'language':'python','code':code}]}
    queries = {
        'collection_attempts':query('实际采集尝试', status['attempts'], ['attempts'], 'collection_log.get_status(state_dir)["attempts"]; 原运行时间、状态、run_id与快照SHA逐条保留。'),
        'collection_schedule':query('早晚计划与实际记录', slots, ['attempts','configuration','scheduler_events'], '从历史最早启用日期至本地今天展开08:00/20:00；按配置事件形成的历史启用区间判定槽位；超过30分钟且无attempt才派生missed，停用和重新启用不抹除旧missed；不回填历史成功。'),
    }
    metadata = {key:status[key] for key in ('timezone','slot_times','start_window_minutes','scheduler_state','generated_at','journal_exists','active_lock')}
    metadata.update(status['scheduler'])
    metadata.update({key:status[key] for key in ('first_enabled_at','enabled_intervals','scheduler_history','history_limitations')})
    metadata.update(source_journal_sha256=before, query_only=True, actual_scheduled_successes=sum(a['trigger_kind']=='scheduled' and a['status']=='complete' for a in status['attempts']))
    return queries, metadata
