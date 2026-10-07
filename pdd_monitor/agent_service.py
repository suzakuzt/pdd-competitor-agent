"""Bounded local chat search: real model plan, deterministic reviewed evidence.

This service cannot write business records, run SQL/shell from a prompt, browse,
or publish. Model unavailability is explicit, never a canned successful answer.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
import json
import secrets
import threading
import time

from .agent_query import AgentQuery
from .agent_provider import configured_planner
from .agent_local import local_plan
from .agent_codex import validate_plan


def beijing(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def allowed_agent_request(headers, port, *, write=False):
    host = headers.get('Host', '')
    if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
        return False
    origin = 'http://' + host
    if headers.get('Origin') and headers['Origin'] != origin:
        return False
    if headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
        return False
    if headers.get('Referer'):
        ref = urlsplit(headers['Referer'])
        if ref.scheme + '://' + ref.netloc != origin:
            return False
    if write:
        if headers.get('Origin') != origin:
            return False
        if headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return False
        try:
            size = int(headers.get('Content-Length', '-1'))
        except (TypeError, ValueError):
            return False
        return 0 < size <= 32768
    return True


class AgentService:
    def __init__(self, project, planner=None, query_factory=None, local_planner=local_plan):
        self.project = Path(project)
        self.planner = planner if planner is not None else configured_planner(self.project)
        self.query_factory = query_factory
        self.local_planner = local_planner
        self.lock = threading.Lock()
        self.query_lock = threading.Lock()
        self.cached_query = None
        self.cached_signature = None
        self.jobs = {}
        self.busy = False

    def query(self):
        if self.query_factory:
            return self.query_factory()
        path = self.project / 'dashboard/src/data.json'
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        with self.query_lock:
            if signature != self.cached_signature:
                self.cached_query = AgentQuery.from_path(path)
                self.cached_signature = signature
            return self.cached_query

    def status(self):
        result = self.planner.status()
        return {**result, 'service': 'pdd-agent-search', 'capabilities': ['search', 'card', 'new_arrivals', 'trends', 'context'],
                'local_search_available': self.local_planner is not None,
                'website_collection_performed': False}

    def _payload(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'shop_id','message','history','last_observation_ids'}:
            raise ValueError('聊天请求字段不正确')
        shop_id, message = payload.get('shop_id'), payload.get('message')
        if not isinstance(shop_id, str) or not isinstance(message, str) or not message.strip() or len(message) > 2000:
            raise ValueError('请选择店铺并输入不超过2000字的问题')
        history = payload.get('history', [])
        if type(history) is not list or len(history)>12:
            raise ValueError('对话上下文最多12条')
        for entry in history:
            if not isinstance(entry, dict) or set(entry) != {'role','text'} or entry['role'] not in ('user','assistant') or not isinstance(entry['text'], str) or len(entry['text'])>3000:
                raise ValueError('对话上下文格式不正确')
        ids = payload.get('last_observation_ids', [])
        if type(ids) is not list or len(ids)>20 or any(type(value) is not int for value in ids) or len(set(ids))!=len(ids):
            raise ValueError('上一答原卡引用格式不正确')
        query = self.query()
        query.context(shop_id)  # Validate locally; do not send business context to the model.
        for oid in ids:
            query.card(shop_id, oid)  # Validate every previous reference against this shop locally.
        context = {'data_selection': 'current_shop', 'available_actions': ['search', 'card', 'new_arrivals', 'trends', 'context']}
        user_history = [deepcopy(entry) for entry in history if entry['role'] == 'user']
        return query, shop_id, message.strip(), user_history, list(ids), context

    def start(self, payload):
        data = self._payload(payload)
        # Known whole-message queries use existing records without credentials,
        # network calls or an additional review/collection workflow.
        plan = self.local_planner(data[2], data[4]) if self.local_planner else None
        if plan is None:
            state = self.status()
            if state.get('status') != 'ready':
                return 503, state
        with self.lock:
            if self.busy:
                return 409, {'status':'busy','message':'Agent正在处理一个问题，请等本次完成后再发送。'}
            now = time.monotonic()
            self.jobs = {key: value for key,value in self.jobs.items() if now-value['_created']<1800}
            while len(self.jobs)>=30:
                self.jobs.pop(next(iter(self.jobs)))
            request_id = secrets.token_hex(24)
            self.jobs[request_id] = {'status':'running','message':'正在检索当前店铺商品…','_created':now}
            self.busy = True
        threading.Thread(target=self._work, args=(request_id,data,plan), daemon=True).start()
        return 202, {'status':'running','request_id':request_id}

    def result(self, request_id):
        with self.lock:
            value = self.jobs.get(request_id)
            if not value:
                return 404, {'status':'failed','message':'查询记录已过期，请重新发送。'}
            return 200, deepcopy({k:v for k,v in value.items() if not k.startswith('_')})

    def _work(self, request_id, data, local_query_plan=None):
        query, shop_id, message, history, ids, context = data
        try:
            plan = local_query_plan if local_query_plan is not None else self.planner.plan(message, context, history, ids)
            result = execute_plan(query, shop_id, plan)
            result['execution_mode'] = 'local' if local_query_plan is not None else 'model'
            result['model_requests'] = 0 if local_query_plan is not None else 1
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            # Adapter messages are sanitized and contain neither stderr nor auth data.
            result = {'status':'failed','message':'Agent未完成本次查询。'+str(exc)[:220], 'observation_ids':[]}
        except Exception:
            result = {'status':'failed','message':'Agent查询遇到错误，本次没有返回数据。可以保留问题后重试或使用原有筛选。','observation_ids':[]}
        with self.lock:
            result['_created'] = self.jobs[request_id]['_created']
            self.jobs[request_id] = result
            self.busy = False


def execute_plan(query, shop_id, plan):
    """Revalidate every model field. No model-generated paths, SQL or URLs."""
    plan = validate_plan(plan)
    fields = {'action','terms','scope','sales_min','sales_max','limit','observation_id','answer','artists_only'}
    if type(plan) is not dict or set(plan) != fields:
        raise ValueError('模型未返回有效的查询条件，请把问题说得更具体。')
    action = plan['action']
    if type(action) is not str or action not in ('search','card','new_arrivals','trends','context','clarify'):
        raise ValueError('本入口仅查询已有记录，暂不执行采集、保存或上架。')
    if type(plan['answer']) is not str or len(plan['answer'])>800:
        raise ValueError('模型说明过长或格式不正确')
    # Search validates common typed parameters even for non-search actions.
    criteria = {key:plan[key] for key in ('terms','scope','sales_min','sales_max','limit','artists_only')}
    checked = query.search(shop_id, **criteria)
    oid = plan['observation_id']
    if oid is not None and type(oid) is not int:
        raise ValueError('商品卡片编号必须为整数')
    if action == 'search':
        evidence = checked
        rows = evidence['rows']
        shop = query.context(shop_id)['shops'][0]
        all_new_list = all(run.get('sort_order') == '上新' for run in evidence['runs']) and bool(evidence['runs'])
        scope_name = {'reference':'完整上新列表' if all_new_list else '完整商品参考列表','latest':'最新采集记录','all':'全部历史记录'}[plan['scope']]
        if plan['scope']=='reference' and any(run.get('status')!='complete' or not run.get('end_boundary_observed') for run in evidence['runs']):
            scope_name = '上新部分记录（暂无完整轮）' if all_new_list else '商品部分记录（暂无完整轮）'
        answer = f"{shop['shop_name']}：从{scope_name} {evidence['population_count']} 张商品原卡中，匹配 {evidence['total']} 张，按已拼/已抢销量从多到少展示前 {len(rows)} 张。"
        if plan['terms']:
            answer += '\n匹配关键词：'+'、'.join(plan['terms'])+'。'
        if plan['artists_only']:
            answer += '\n范围：已记录艺人关联的商品。'
        if plan['sales_min'] is not None or plan['sales_max'] is not None:
            lower = plan['sales_min'] if plan['sales_min'] is not None else '不限'
            upper = plan['sales_max'] if plan['sales_max'] is not None else '不限'
            answer += f'\n销量筛选范围（已拼/已抢）：{lower}—{upper} 件。'
        if not rows:
            answer += '\n当前筛选没有匹配商品，可调整关键词或销量范围。'
    elif action == 'card':
        evidence = query.card(shop_id, oid)
        rows = [evidence['card']]
        row = rows[0]
        answer = f"已定位原卡 #{row['view_order']}：{row['title']}。\n{row.get('sales_raw') or '销量未显示'}；{row.get('price_raw') or '价格未显示'}。\n下方给出原图与已记录参考入口。"
    elif action == 'new_arrivals':
        evidence = query.new_arrivals(shop_id, limit=plan['limit'])
        rows = [row['anchor_card'] for row in evidence['rows']]
        answer = f"新增记录（固定起点后的首次观察线索）共 {evidence['total']} 条，本次展示 {len(rows)} 条。\n{evidence['interpretation']}。"
    elif action == 'trends':
        evidence = query.trends(shop_id, limit=plan['limit'])
        rows = [row['anchor_card'] for row in evidence['rows']]
        shop = query.context(shop_id)['shops'][0]
        answer = f"{shop['shop_name']}：有图增长观察清单 {evidence['total']} 张，本次展示 {len(rows)} 张。按变化线索排序。"
        if rows:
            for index, signal in enumerate(evidence['rows'], 1):
                rate = signal.get('rate24')
                timing = (f"，折算24h +{rate:.2f}件" if rate is not None else
                          '，观察间隔不在6—48h范围，未折算24h' if signal.get('rate_status') == 'irregular_interval' else
                          '，读取间隔精度不足，未折算24h')
                identity = '展示线索，身份待核对' if signal['basis'] == 'provisional_title_image' else '同店商品ID配对'
                answer += (f"\n{index}. {signal['anchor_card']['title']}｜{signal['label']}｜本次展示 +{signal['latest_delta']:g}件"
                           f"{timing}｜{identity}。")
            answer += '\n单日变化不等于持续趋势；这是观察清单，尚无已验证的预测概率。'
        else:
            answer += '\n' + {
                'not_generated':'本轮趋势数据尚未生成，需更新数舱数据。',
                'incomplete_reference':'尚无完整参考轮，等待完整采集。',
                'no_comparable_growth':'当前没有可比的正增长线索；继续积累完整每日记录。',
                'no_pictured_growth':'已有正增长线索，但图片尚未通过本地完整性核验。',
            }.get(evidence['state'],'当前没有可展示的增长观察卡。')
        if evidence['missing_image_count']:
            answer += f"\n另有 {evidence['missing_image_count']} 张增长观察卡待补图，原始记录仍保留。"
    elif action == 'context':
        evidence = query.context(shop_id)
        rows = []
        shop = evidence['shops'][0]
        answer = f"{shop['shop_name']}：已有 {shop['historical_run_count']} 轮、{shop['historical_observation_count']} 条独立观察。\n参考轮 {shop['reference']['card_count']} 卡；最新轮 {shop['latest']['card_count']} 卡。\n最新观察截止 {beijing(shop['latest']['observed_to'])}（北京时间）。本入口查询已有记录，没有启动网站采集。"
    else:
        evidence = query.context(shop_id)
        rows = []
        answer = '需要补充或调整问题：'+(plan['answer'] or '请提供商品关键词、销量条件或原卡编号。')+'\n此条尚未返回商品查询结果。'
    refs = []
    seen = set()
    for row in rows:
        for artist in row.get('artist_sources', []):
            for source in artist.get('sources', []):
                url = source.get('url')
                if url and url not in seen:
                    seen.add(url)
                    refs.append({'label':f"{artist.get('artist_name','艺人')} · {source.get('label','公开入口')}", 'url':url})
    run_ids = sorted({row['run_id'] for row in rows})
    if action in ('search','trends'):
        run_ids = [run['run_id'] for run in evidence['runs']]
        if evidence['runs']:
            cutoff = max(r['observed_to'] for r in evidence['runs'])
            answer += f'\n采集截止：{beijing(cutoff)}（北京时间）。'
    query_result = {key:evidence[key] for key in ('total','population_count','has_more','next_offset','sort_basis') if key in evidence}
    if action == 'trends':
        query_result.update(state=evidence['state'], missing_image_count=evidence['missing_image_count'],
            growth_candidate_count=evidence['growth_candidate_count'], summary=evidence['summary'],
            trend_rows=[{key:value for key,value in row.items() if key != 'anchor_card'} for row in evidence['rows']])
    return {'status':'completed','answer':answer,'observation_ids':[row['observation_id'] for row in rows],
            'references':refs[:15], 'scope':{'shop_id':shop_id,'run_ids':run_ids},
            'tool_calls':[{'tool':action,'criteria':criteria,'observation_id':oid}],
            'snapshot':evidence['snapshot'], 'limitations':evidence.get('limitations',[]),
            'query_result':query_result,
            'website_collection_performed':False,'business_data_written':False,
            'answered_at':datetime.now(timezone.utc).isoformat()}
