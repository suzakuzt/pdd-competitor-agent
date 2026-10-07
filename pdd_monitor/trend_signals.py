"""Transparent display-change rules over reviewed daily rows; no learned forecast."""
from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import math

from .warehouse_focus import _yipin


MODEL_VERSION = 'display_trend_rules_v2_sales_alias'
RULES = {'recent_delta_days': 3, 'baseline_delta_days': 4, 'minimum_acceleration_rate24': 3,
         'minimum_acceleration_ratio': 2, 'ewma_alpha': .4, 'outcome_window_days': 7,
         'minimum_interval_hours': 6, 'maximum_interval_hours': 48}
TREND_LIMITATIONS = [
    '规则仅分析原卡公开展示变化，不确认真实订单、同SKU、利润或跟品成功率。',
    '1—10件及>10件均关注；明确0、其他标签和缺失或模糊销量不进入关注清单。',
    '少于4个连续有效日期只能给单日增长或观察线索；至少8个连续有效日期才具备近3段与前4段的同尺度比较条件。',
    '增量按两次实际读取间隔换算为每24小时；仅明确card_read/batch_read点时刻允许归一化。精度不明或窗口时刻只保留原差值和时间界限。',
    '缺日、部分轮、身份链断开、时间无效和展示数下降会切断连续段；不将断点两侧强拼为增长。',
    '加速线索须近3段均增长、均速至少3件/24h且至少前4段均速2倍，同时EWMA上升；这是透明观察规则，不是爆火预测。',
    '分段均速用整段展示增量除以实际总时长；EWMA以24小时alpha=0.4按区间时距校正，不把短区间与长区间等权。',
    '仅6至48小时的明确读取间隔进入归一化趋势；极短跨午夜或过长区间只保留展示差值。',
    '未来验证目标为推荐后7天明显起量；当前没有已验证预测模型或校准概率，forecast_eligible恒为false。',
]
LABELS = {
    'excluded_zero': ('零销量不关注', 'excluded', '保留原始记录，出现明确正销量后再进入关注。'),
    'excluded_unknown_sales': ('销量未满足关注口径', 'excluded', '保留原文，等待精确已拼/已抢正数。'),
    'incomplete_snapshot': ('等待完整采集', 'data_gap', '完成本店全量采集后再比较趋势。'),
    'insufficient_history': ('等待每日积累', 'observe', '继续每日采集，先获得相邻两天可比数据。'),
    'history_gap': ('历史暂不可连续比较', 'data_gap', '核对缺日、原卡对应关系和读取时间。'),
    'negative_display_anomaly': ('展示数回落待核对', 'data_gap', '核对前后原卡及平台展示口径，暂不判断增长。'),
    'single_day_growth': ('单日增长线索', 'watch', '继续每日观察，满4个连续有效日期后复核持续性。'),
    'single_day_observation': ('单日观察', 'observe', '继续每日观察，当前资料不足以判断持续增长。'),
    'sustained_growth': ('连续增长线索', 'watch', '核对商品适配和成本，并持续观察增长是否延续。'),
    'uneven_growth': ('增长不连续，继续观察', 'observe', '继续每日观察，避免仅凭一次变化决定跟品。'),
    'stable_display': ('近期展示值持平', 'observe', '保留监测，等待新的展示变化。'),
    'growth_acceleration': ('增长加速线索', 'priority', '优先核对商品规格、经营适配及完整成本，再决定是否小量试品。'),
    'zero_baseline_growth': ('零增量基线后持续增长', 'priority', '核对起量是否延续及实际经营条件，再决定是否小量试品。'),
    'slowing_growth': ('展示增长放缓', 'watch', '降低跟进优先级，继续观察是否恢复。'),
    'recent_spike': ('最近一段跳升，持续性待看', 'watch', '复核本次跳升及下一次数据，暂不当作持续趋势。'),
}
MATCH_BASES = ('confirmed_goods_id', 'provisional_title_image')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point_valid(point):
    return bool(point and point.get('is_complete') is True and point.get('time_valid') is True
                and point.get('point_status') in ('anchor', 'matched') and point.get('observation_id') is not None
                and point.get('sales_label') in SALES_METRIC_LABELS and point.get('sales_unit') == '件'
                and type(point.get('cumulative_yipin')) is int and point['cumulative_yipin'] >= 0)


def _interval(old, new, delta):
    """Keep bounded windows distinct from a known observation-to-observation time."""
    result = dict(interval_hours=None, interval_hours_lower=None, interval_hours_upper=None,
                  rate24=None, rate24_lower=None, rate24_upper=None, rate_status='time_unverified')
    times = [point.get(key) for point in (old, new) for key in ('time_lower_epoch', 'time_upper_epoch')]
    if not all(_finite(value) for value in times):
        return result
    old_lower, old_upper, new_lower, new_upper = times
    if old_upper < old_lower or new_upper < new_lower or new_lower <= old_upper:
        return result
    lower, upper = (new_lower - old_upper) / 3600, (new_upper - old_lower) / 3600
    result.update(interval_hours_lower=lower, interval_hours_upper=upper)
    precise = all(point.get('observed_at_precision') in ('card_read', 'batch_read') for point in (old, new))
    if precise and old_lower == old_upper and new_lower == new_upper:
        if not RULES['minimum_interval_hours'] <= lower <= RULES['maximum_interval_hours']:
            result.update(interval_hours=lower, rate_status='irregular_interval')
            return result
        result.update(interval_hours=lower, rate24=delta * 24 / lower,
                      rate24_lower=delta * 24 / lower, rate24_upper=delta * 24 / lower, rate_status='normalized_exact_interval')
    elif precise:
        result.update(rate_status='window_only', rate24_lower=delta * 24 / upper, rate24_upper=delta * 24 / lower)
    else:
        result['rate_status'] = 'precision_unknown'
    return result


def _edge(old, new):
    result = dict(valid=False, reason='identity_unverified', delta=None, raw_display_change=None,
                  basis=None, interval_hours=None, interval_hours_lower=None, interval_hours_upper=None,
                  rate24=None, rate24_lower=None, rate24_upper=None, rate_status='not_comparable')
    if not (_point_valid(old) and _point_valid(new)):
        result['reason'] = 'missing_or_invalid_point'
        return result
    raw = new['cumulative_yipin'] - old['cumulative_yipin']
    if date.fromisoformat(new['date']) - date.fromisoformat(old['date']) != timedelta(days=1):
        result['reason'] = 'missing_day'
        return result
    if (new.get('break_before') is not False or new.get('baseline_date') != old['date']
            or new.get('baseline_observation_id') != old['observation_id']
            or new.get('delta_match_basis') not in MATCH_BASES):
        return result
    interval = _interval(old, new, raw)
    if interval['interval_hours_lower'] is None:
        result['reason'] = 'time_unverified'
        return result
    if raw < 0:
        if new.get('delta_status') == 'negative_anomaly' and new.get('daily_delta') is None:
            result.update(reason='negative_display_anomaly', basis=new['delta_match_basis'], raw_display_change=raw)
        return result
    if new.get('delta_status') != 'comparable' or type(new.get('daily_delta')) is not int or new['daily_delta'] != raw:
        result['reason'] = 'invalid_delta_evidence'
        return result
    result.update(interval)
    result.update(valid=True, reason=None, delta=raw, raw_display_change=raw, basis=new['delta_match_basis'])
    return result


def _ewma(edges):
    value = edges[0]['rate24']
    for edge in edges[1:]:
        alpha = 1 - (1 - RULES['ewma_alpha']) ** (edge['interval_hours'] / 24)
        value = alpha * edge['rate24'] + (1 - alpha) * value
    return value


def _weighted_rate(edges):
    return sum(edge['delta'] for edge in edges) / sum(edge['interval_hours'] for edge in edges) * 24


def _signal(item, points):
    value = _yipin(item)
    valid_points = [p for p in points if _point_valid(p)]
    ending = points[-1] if points and points[-1]['date'] == item['date'] else None
    edges = [_edge(old, new) for old, new in zip(points, points[1:])]
    tail = [ending] if _point_valid(ending) else []
    tail_edges = []
    if tail:
        for index in range(len(points) - 2, -1, -1):
            edge = edges[index]
            if not edge['valid']:
                break
            tail.insert(0, points[index])
            tail_edges.insert(0, edge)
    normalized = []
    for edge in reversed(tail_edges):
        if edge['rate24'] is None:
            break
        normalized.insert(0, edge)
    latest = edges[-1] if ending is not None and edges else None
    bases = {edge['basis'] for edge in tail_edges}
    if latest and latest['reason'] == 'negative_display_anomaly':
        bases.add(latest['basis'])
    basis = 'provisional_title_image' if 'provisional_title_image' in bases else 'confirmed_goods_id' if bases else 'single_card_only'
    result = {key: item.get(key) for key in ('observation_id', 'run_id', 'shop_id', 'date', 'title', 'view_order',
                                            'track_id', 'asset_sha256', 'image_url', 'image_content_status', 'image_content_kind',
                                            'sales_raw', 'price_raw', 'category')}
    result.update(anchor_observation_id=item['observation_id'], anchor_run_id=item['run_id'], yipin_value=value,
                  model_version=MODEL_VERSION, included_in_watch=value is not None and value > 0,
                  history_days=len(valid_points), recorded_dates=len(points), consecutive_days=len(tail),
                  valid_delta_days=len(tail_edges), all_valid_delta_days=sum(edge['valid'] for edge in edges),
                  normalized_delta_days=len(normalized), avg_recent=None, avg_base=None, ratio=None,
                  recent_delta_days=0, baseline_delta_days=0, avg_unit='display_increment_per_24h',
                  latest_delta=latest['delta'] if latest and latest['valid'] else None,
                  delta=latest['delta'] if latest and latest['valid'] else None,
                  raw_display_change=latest['raw_display_change'] if latest else None,
                  interval_hours=latest['interval_hours'] if latest else None,
                  interval_hours_lower=latest['interval_hours_lower'] if latest else None,
                  interval_hours_upper=latest['interval_hours_upper'] if latest else None,
                  rate24=latest['rate24'] if latest and latest['valid'] else None,
                  rate24_lower=latest['rate24_lower'] if latest and latest['valid'] else None,
                  rate24_upper=latest['rate24_upper'] if latest and latest['valid'] else None,
                  rate_status=latest['rate_status'] if latest else 'no_previous_day',
                  positive_recent_days=sum(edge['delta'] > 0 for edge in tail_edges[-3:]),
                  ewma_recent=None, ewma_base=None, ewma_momentum=None, ewma_alpha=RULES['ewma_alpha'],
                  ewma_method='elapsed_time_adjusted_24h_alpha', rate_average_method='total_delta_over_total_hours',
                  recent_interval_hours=None, baseline_interval_hours=None,
                  basis=basis, identity_confirmed=basis == 'confirmed_goods_id', same_sku_verified=False,
                  forecast_eligible=False, forecast_status='model_not_validated', model_ready=False,
                  outcome_window_days=7, outcome_definition_status='requires_validation_outcomes',
                  evidence_observation_ids=[p['observation_id'] for p in tail],
                  evidence_run_ids=list(dict.fromkeys(p['run_id'] for p in tail)), evidence_dates=[p['date'] for p in tail],
                  readiness_stage='data_gap', alert_candidate=False)
    if len(normalized) >= 3:
        result.update(avg_recent=_weighted_rate(normalized[-3:]), recent_delta_days=3,
                      recent_interval_hours=sum(edge['interval_hours'] for edge in normalized[-3:]))
    if len(normalized) >= 7:
        base, recent = normalized[-7:-3], normalized[-3:]
        base_avg = _weighted_rate(base)
        result.update(avg_base=base_avg, baseline_delta_days=4, baseline_interval_hours=sum(edge['interval_hours'] for edge in base),
                      ratio=result['avg_recent'] / base_avg if base_avg > 0 else None,
                      ewma_base=_ewma(base), ewma_recent=_ewma([*base, *recent]))
        result['ewma_momentum'] = result['ewma_recent'] - result['ewma_base']
    reasons = []
    if not result['included_in_watch']:
        code = 'excluded_zero' if value == 0 else 'excluded_unknown_sales'
    elif item.get('is_complete') is not True:
        code = 'incomplete_snapshot'
    elif latest and latest['reason'] == 'negative_display_anomaly':
        code = 'negative_display_anomaly'
        reasons.append(f"最新相邻展示差值为{latest['raw_display_change']}件，保留原值并停止增长判断。")
    elif not tail:
        code = 'history_gap'
    elif len(tail) == 1:
        code = 'history_gap' if len(points) > 1 else 'insufficient_history'
        result['readiness_stage'] = 'single_snapshot' if len(points) == 1 else 'data_gap'
    elif len(tail) < 4:
        code = 'single_day_growth' if tail_edges[-1]['delta'] > 0 else 'single_day_observation'
        result['readiness_stage'] = 'single_day_only'
        reasons.append(f"仅{len(tail)}个连续有效日期，最新相邻展示增加{tail_edges[-1]['delta']}件。")
    else:
        result['readiness_stage'] = 'baseline_comparison_ready' if len(normalized) >= 7 else 'three_day_observation'
        positive = result['positive_recent_days']
        code = 'sustained_growth' if positive == 3 else 'uneven_growth' if positive else 'stable_display'
        reasons.append(f'最近3个相邻观察区间有{positive}段展示增长。')
        if len(normalized) >= 7:
            recent, baseline = result['avg_recent'], result['avg_base']
            if positive == 3 and recent >= RULES['minimum_acceleration_rate24'] and result['ewma_momentum'] > 0:
                if baseline == 0:
                    code = 'zero_baseline_growth'
                elif result['ratio'] >= RULES['minimum_acceleration_ratio']:
                    code = 'growth_acceleration'
            if code not in ('zero_baseline_growth', 'growth_acceleration'):
                if baseline > 0 and recent <= baseline / 2 and result['ewma_momentum'] < 0:
                    code = 'slowing_growth'
                elif positive < 3 and normalized[-1]['rate24'] >= 10 and (baseline == 0 or normalized[-1]['rate24'] >= 2 * baseline):
                    code = 'recent_spike'
            reasons.append(f'近3段均速{recent:.2f}件/24h，前4段均速{baseline:.2f}件/24h。')
            if baseline == 0:
                reasons.append('基线增量为0，不计算增长倍数。')
        elif result['avg_recent'] is not None:
            reasons.append(f"近3段均速{result['avg_recent']:.2f}件/24h；尚无前4段完整同尺度基线。")
    if result['included_in_watch']:
        if tail_edges and len(normalized) < len(tail_edges):
            reasons.append('部分读取时刻精度不明、为时间窗口或间隔超出6—48小时；该段只保留展示差值，不参与速度或加速比较。')
        if basis == 'provisional_title_image':
            reasons.append('同店唯一同标题同原图的展示线索，商品身份仍待核对。')
        if not reasons:
            reasons.append('继续积累完整且可比的每日数据。')
    label, level, action = LABELS[code]
    result.update(signal_code=code, label=label, level=level, action=action, reasons=reasons,
                  alert_candidate=code in ('growth_acceleration', 'zero_baseline_growth', 'recent_spike', 'negative_display_anomaly'))
    result['signal_id'] = 'trend_' + hashlib.sha256('|'.join(map(str, (item['shop_id'], item['run_id'], item['observation_id'], code, MODEL_VERSION))).encode()).hexdigest()[:24]
    return result


def build_trend_signals(focus_items, display_history, focus_summary=()):
    """Return per-current-card signals and per-shop readiness, without I/O or mutation."""
    keys, current_by_shop = {}, {}
    for item in focus_items:
        key = item['shop_id'], item['run_id'], item['observation_id']
        if key in keys:
            raise ValueError('Duplicate current card in trend inputs')
        date.fromisoformat(item['date'])
        current = item['run_id'], item['date']
        if item['shop_id'] in current_by_shop and current_by_shop[item['shop_id']] != current:
            raise ValueError('Trend focus contains multiple current snapshots for one shop')
        current_by_shop[item['shop_id']] = current
        keys[key] = item
    grouped, seen = defaultdict(list), set()
    for point in display_history:
        key = point['shop_id'], point['anchor_run_id'], point['anchor_observation_id']
        if key not in keys:
            raise ValueError('History anchor outside current trend focus')
        item = keys[key]
        date.fromisoformat(point['date'])
        if point['date'] > item['date'] or point.get('anchor_date') != item['date']:
            raise ValueError('Future or mismatched anchor date in trend history')
        row_key = (*key, point['date'])
        if row_key in seen:
            raise ValueError('Duplicate daily trend evidence')
        seen.add(row_key)
        grouped[key].append(point)
    signals = [_signal(item, sorted(grouped[key], key=lambda p: p['date'])) for key, item in keys.items()]
    # A review queue is an ordered evidence list, never a synthetic success score.
    level_order = {'priority': 0, 'watch': 1, 'observe': 2, 'data_gap': 3, 'excluded': 4}
    def priority(row):
        return (level_order[row['level']],
                -(row['rate24'] if row['rate24'] is not None else row['latest_delta'] or 0),
                -(row['latest_delta'] or 0), row['observation_id'])
    signals.sort(key=lambda row: (str(row['shop_id']), *priority(row)))
    for shop in current_by_shop:
        selected = 0
        for rank, row in enumerate((row for row in signals if row['shop_id'] == shop), 1):
            local = row['image_content_status'] == 'verified_local' and isinstance(row['asset_sha256'], str) and len(row['asset_sha256']) == 64
            choose = (local and row['included_in_watch'] and row['level'] in ('priority', 'watch', 'observe')
                      and (row['latest_delta'] or 0) > 0 and selected < 5)
            selected += int(choose)
            row.update(rank=rank, selected_for_review=choose, review_rank=selected if choose else None)
    metadata = {row['shop_id']: row for row in focus_summary}
    summaries = []
    for shop, (run_id, day) in sorted(current_by_shop.items()):
        cards = [row for row in signals if row['shop_id'] == shop]
        watch = [row for row in cards if row['included_in_watch']]
        source = metadata.get(shop, {})
        if source and (source.get('run_id') != run_id or source.get('date') != day):
            raise ValueError('Trend summary does not match current shop snapshot')
        full_dates = {point['date'] for key in keys if key[0] == shop for point in grouped[key] if point.get('is_complete') is True}
        summaries.append(dict(shop_id=shop, shop_name=source.get('shop_name'), date=day, reference_run_id=run_id,
            model_version=MODEL_VERSION, total_cards=len(cards), watch_card_count=len(watch), excluded_card_count=len(cards)-len(watch),
            full_snapshot_days=len(full_dates), cards_with_2_dates=sum(r['consecutive_days'] >= 2 for r in watch),
            cards_with_4_dates=sum(r['consecutive_days'] >= 4 for r in watch),
            cards_with_8_normalized_dates=sum(r['normalized_delta_days'] >= 7 for r in watch),
            priority_count=sum(r['level'] == 'priority' for r in watch), watch_count=sum(r['level'] == 'watch' for r in watch),
            selected_review_count=sum(r['selected_for_review'] for r in cards),
            data_gap_count=sum(r['level'] == 'data_gap' for r in watch),
            provisional_signal_count=sum(r['basis'] == 'provisional_title_image' for r in watch),
            signal_counts=dict(Counter(r['signal_code'] for r in cards)), model_ready=False, rules_ready=True,
            model_status='forecast_not_validated', forecast_eligible_card_count=0, outcome_window_days=7,
            outcome_definition_status='requires_validation_outcomes'))
    return {'trend_signals': signals, 'trend_signal_summary': summaries}
