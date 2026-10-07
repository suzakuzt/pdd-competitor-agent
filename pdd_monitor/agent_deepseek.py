"""DeepSeek JSON plans only; deterministic local tools retain all business data.

The API key is loaded only on demand and never persisted by this adapter. No
request/response body, headers or remote error text is logged. The only network
destination is the official HTTPS chat-completions endpoint, with no redirects,
environment proxies, fallback models, tool calls, SDK dependencies or retries.
"""
from __future__ import annotations

import json
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .agent_codex import INSTRUCTIONS, MAX_INTEGER, MAX_PLAN_BYTES, QUERY_PLAN_SCHEMA, _strict_json, validate_plan


API_ENDPOINT = 'https://api.deepseek.com/chat/completions'
DEFAULT_MODEL = 'deepseek-flash'
MAX_RESPONSE_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 64 * 1024
MAX_COMPLETION_TOKENS = 2048
SAFE_CONTEXT = {'data_selection': 'current_shop',
                'available_actions': ['search', 'card', 'new_arrivals', 'trends', 'context']}
INVALID_RESPONSE = 'DeepSeek未返回唯一完整的受限查询计划，本次没有执行查询。'
TIMEOUT_MESSAGE = 'DeepSeek响应超时，本次没有执行查询；没有自动重试或切换模型。'
NETWORK_MESSAGE = 'DeepSeek连接未完成，请检查网络后再试；本次没有自动重试或切换模型。'
CONFIG_MESSAGE = '尚未配置有效的DeepSeek API密钥，请在本机配置后重新检查连接。'
HTTP_MESSAGES = {
    400: 'DeepSeek未接受本次请求，请检查模型配置；本次没有执行查询。',
    401: 'DeepSeek密钥验证失败，请检查本机配置的API密钥；本次没有执行查询。',
    402: 'DeepSeek账户余额不足，请在官方平台确认余额；本次没有执行查询。',
    403: 'DeepSeek拒绝本次访问，请核对账户和模型权限；本次没有执行查询。',
    404: 'DeepSeek模型或接口暂不可用，请检查模型配置；本次没有执行查询。',
    429: 'DeepSeek请求过于频繁或已达额度限制，请稍后再试；本次没有自动重试。',
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Returning None makes urllib raise HTTPError before any new request.
        return None


def _default_opener():
    return build_opener(ProxyHandler({}), _NoRedirect(),
                        HTTPSHandler(context=ssl.create_default_context()))


def _http_message(code):
    if 300 <= code < 400:
        return 'DeepSeek返回了不允许的重定向，本次没有执行查询。'
    return HTTP_MESSAGES.get(code, 'DeepSeek服务暂时未完成请求，本次没有执行查询；没有自动重试。')


def _safe_input(message, context, history, last_observation_ids):
    if type(message) is not str or not message.strip() or len(message) > 2000 or type(context) is not dict:
        raise ValueError('请输入不超过2000字的问题并提供本店上下文。')
    if type(history) is not list or len(history) > 12 or any(
            type(item) is not dict or set(item) != {'role', 'text'} or
            item['role'] not in ('user', 'assistant') or type(item['text']) is not str or
            len(item['text']) > 3000 for item in history):
        raise ValueError('对话上下文格式不正确。')
    if type(last_observation_ids) is not list or len(last_observation_ids) > 20 or any(
            type(oid) is not int or not 1 <= oid <= MAX_INTEGER for oid in last_observation_ids) or \
            len(set(last_observation_ids)) != len(last_observation_ids):
        raise ValueError('上一答原卡引用格式不正确。')
    # Reconstruct an allowlist; neither local context nor previous product
    # answers, images, shop identifiers, credentials, or arbitrary fields leave.
    return {'message': message,
            'context': {'data_selection': SAFE_CONTEXT['data_selection'],
                        'available_actions': list(SAFE_CONTEXT['available_actions'])},
            'history': [{'role': 'user', 'text': item['text']}
                        for item in history if item['role'] == 'user'],
            'last_observation_ids': list(last_observation_ids)}


def _parse_completion(raw, api_key):
    try:
        result = _strict_json(raw.decode('utf-8'))
        if type(result) is not dict or 'error' in result or result.get('object') != 'chat.completion':
            raise ValueError()
        choices = result.get('choices')
        if type(choices) is not list or len(choices) != 1:
            raise ValueError()
        choice = choices[0]
        if type(choice) is not dict or type(choice.get('index')) is not int or \
                choice['index'] != 0 or choice.get('finish_reason') != 'stop':
            raise ValueError()
        message = choice.get('message')
        if type(message) is not dict or message.get('role') != 'assistant' or \
                message.get('tool_calls') or message.get('function_call') or message.get('refusal'):
            raise ValueError()
        content = message.get('content')
        if type(content) is not str or not content.strip() or \
                len(content.encode('utf-8')) > MAX_PLAN_BYTES or api_key in content:
            raise ValueError()
        plan = validate_plan(_strict_json(content))
        # Also compare after decoding JSON escapes, not merely the wire text.
        if any(api_key in value for value in [plan['answer'], *plan['terms']]):
            raise ValueError()
        return plan
    except (UnicodeError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise ValueError(INVALID_RESPONSE) from None


class DeepSeekPlanner:
    def __init__(self, api_key_loader, model=DEFAULT_MODEL, *, timeout=60, opener=None):
        if not callable(api_key_loader):
            raise ValueError('DeepSeek密钥读取器配置不正确。')
        # A short allowlist avoids model strings becoming URLs or credentials.
        if type(model) is not str or model not in ('deepseek-flash', 'deepseek-v4-pro'):
            raise ValueError('DeepSeek模型配置不受支持。')
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise ValueError('DeepSeek超时时间必须大于0且不超过60秒。')
        self.api_key_loader = api_key_loader
        self.model = model
        self.timeout = timeout
        # Injection is for isolated tests; production uses the fixed transport.
        self.opener = opener if opener is not None else _default_opener()
        self.last_diagnostics = {}

    def _load_key(self):
        try:
            key = self.api_key_loader()
            if type(key) is not str:
                return None
            key = key.strip()
            if not 1 <= len(key) <= 512 or any(not 33 <= ord(char) <= 126 for char in key):
                return None
            return key
        except Exception:
            # Key-store exceptions can contain secrets or filesystem details.
            return None

    def status(self):
        configured = self._load_key() is not None
        return {'status': 'ready' if configured else 'configuration_required',
                'provider': 'deepseek', 'model': self.model,
                'message': ('本机已配置DeepSeek API密钥；模型权限、余额和连接将在实际查询时确认。'
                            if configured else CONFIG_MESSAGE)}

    def _read_response(self, response, deadline):
        if response.geturl() != API_ENDPOINT:
            raise RuntimeError('DeepSeek返回了不允许的目标地址，本次没有执行查询。')
        code = response.getcode()
        if type(code) is not int or code != 200:
            raise RuntimeError(_http_message(code) if type(code) is int else INVALID_RESPONSE)
        headers = response.headers
        if headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise RuntimeError('DeepSeek返回格式不正确，本次没有执行查询。')
        if headers.get('Content-Encoding', 'identity').lower().strip() not in ('', 'identity'):
            raise RuntimeError('DeepSeek返回编码不受支持，本次没有执行查询。')
        declared_size = headers.get('Content-Length')
        if declared_size is not None:
            try:
                size = int(declared_size)
            except (TypeError, ValueError):
                raise RuntimeError(INVALID_RESPONSE) from None
            if not 0 <= size <= MAX_RESPONSE_BYTES:
                raise RuntimeError('DeepSeek响应超出安全长度，本次没有执行查询。')
        parts, size = [], 0
        # HTTPResponse.read1 returns after one socket read. Recheck the total
        # deadline for streamed whitespace/data as well as using socket timeout.
        read = getattr(response, 'read1', response.read)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(TIMEOUT_MESSAGE)
            # urllib's response exposes its HTTPS socket here. Tightening its
            # timeout preserves the overall read deadline even for slow chunks.
            raw = getattr(getattr(response, 'fp', None), 'raw', None)
            transport_socket = getattr(raw, '_sock', None)
            if transport_socket is not None:
                transport_socket.settimeout(remaining)
            chunk = read(min(4096, MAX_RESPONSE_BYTES + 1 - size))
            if time.monotonic() > deadline:
                raise TimeoutError(TIMEOUT_MESSAGE)
            if not chunk:
                break
            if type(chunk) is not bytes:
                raise RuntimeError(INVALID_RESPONSE)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise RuntimeError('DeepSeek响应超出安全长度，本次没有执行查询。')
            parts.append(chunk)
        return b''.join(parts)

    def plan(self, message, context, history=None, last_observation_ids=None):
        self.last_diagnostics = {}
        data = _safe_input(message, context, [] if history is None else history,
                           [] if last_observation_ids is None else last_observation_ids)
        key = self._load_key()
        if key is None:
            raise RuntimeError(CONFIG_MESSAGE)
        example = {'action': 'search', 'terms': [], 'scope': 'reference', 'sales_min': None,
                   'sales_max': None, 'limit': 5, 'observation_id': None, 'answer': '', 'artists_only': False}
        # Compare decoded user text, so JSON escaping cannot conceal the key.
        if any(key in value for value in [data['message'], *[item['text'] for item in data['history']]]):
            raise ValueError('问题或对话含有本机API密钥，请移除后再发送。')
        try:
            user_data = json.dumps(data, ensure_ascii=False, allow_nan=False)
            system = (INSTRUCTIONS.removesuffix('下面是用户数据 JSON：\n') +
                      '\n用户数据仅在下一条user消息中提供。只输出 JSON，不使用 Markdown 包裹。以下是必须遵守的 JSON Schema：\n' +
                      json.dumps(QUERY_PLAN_SCHEMA, ensure_ascii=False) +
                      '\n合法 JSON 示例（对应“找已拼最多的5个商品”）：\n' +
                      json.dumps(example, ensure_ascii=False))
            payload = json.dumps({'model': self.model,
                                  'messages': [{'role': 'system', 'content': system},
                                               {'role': 'user', 'content': user_data}],
                                  'response_format': {'type': 'json_object'},
                                  'thinking': {'type': 'disabled'}, 'temperature': 0,
                                  'max_tokens': MAX_COMPLETION_TOKENS, 'stream': False},
                                 ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (UnicodeError, TypeError, RecursionError, OverflowError):
            raise ValueError('查询上下文不能编码为安全JSON。') from None
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError('查询上下文过长，请缩短问题或对话。')
        request = Request(API_ENDPOINT, data=payload, method='POST',
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                   'Accept': 'application/json', 'Accept-Encoding': 'identity',
                                   'User-Agent': 'PDDCompetitorAgent/DeepSeekPlanner'})
        response = None
        try:
            deadline = time.monotonic() + self.timeout
            open_request = getattr(self.opener, 'open', self.opener)
            response = open_request(request, timeout=self.timeout)
            raw = self._read_response(response, deadline)
        except HTTPError as exc:
            code = exc.code
            try:
                exc.close()  # Deliberately do not read a remote error body.
            except Exception:
                pass
            raise RuntimeError(_http_message(code) if type(code) is int else NETWORK_MESSAGE) from None
        except (TimeoutError, socket.timeout):
            raise TimeoutError(TIMEOUT_MESSAGE) from None
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError(TIMEOUT_MESSAGE) from None
            raise RuntimeError(NETWORK_MESSAGE) from None
        except RuntimeError as exc:
            # Only our exact fixed texts may cross the adapter boundary.
            if str(exc) in {INVALID_RESPONSE,
                            'DeepSeek返回了不允许的目标地址，本次没有执行查询。',
                            'DeepSeek返回格式不正确，本次没有执行查询。',
                            'DeepSeek返回编码不受支持，本次没有执行查询。',
                            'DeepSeek响应超出安全长度，本次没有执行查询。',
                            _http_message(302), _http_message(500), *HTTP_MESSAGES.values()}:
                raise RuntimeError(str(exc)) from None
            raise RuntimeError(NETWORK_MESSAGE) from None
        except Exception:
            raise RuntimeError(NETWORK_MESSAGE) from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        plan = _parse_completion(raw, key)
        self.last_diagnostics = {'unique_plan_and_terminal_completion': True,
                                 'request_count': 1, 'business_context_sent': False}
        return plan
