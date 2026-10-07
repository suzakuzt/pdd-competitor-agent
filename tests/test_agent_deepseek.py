"""Isolated contract/security tests. All keys, plans and responses are synthetic."""
import io
import json
import socket
import unittest
from email.message import Message
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request

from pdd_monitor.agent_deepseek import (
    API_ENDPOINT, CONFIG_MESSAGE, DEFAULT_MODEL, HTTP_MESSAGES, INVALID_RESPONSE,
    MAX_COMPLETION_TOKENS, MAX_RESPONSE_BYTES, NETWORK_MESSAGE, QUERY_PLAN_SCHEMA,
    TIMEOUT_MESSAGE, DeepSeekPlanner, _default_opener,
)


FAKE_KEY = 'sk-isolated-test-only-never-a-real-secret-0123456789'
PLAN = {'action': 'search', 'terms': [], 'scope': 'reference', 'sales_min': None,
        'sales_max': None, 'limit': 5, 'observation_id': None, 'answer': '', 'artists_only': False}


def completion(plan=None, **updates):
    value = {'id': 'synthetic', 'object': 'chat.completion', 'model': DEFAULT_MODEL,
             'choices': [{'index': 0, 'finish_reason': 'stop',
                          'message': {'role': 'assistant',
                                      'content': json.dumps(PLAN if plan is None else plan, ensure_ascii=False)}}]}
    value.update(updates)
    return value


class Response(io.BytesIO):
    def __init__(self, value=None, *, raw=None, status=200, url=API_ENDPOINT, content_type='application/json', **headers):
        super().__init__(raw if raw is not None else json.dumps(completion() if value is None else value, ensure_ascii=False).encode('utf-8'))
        self.url, self.code, self.status, self.msg = url, status, status, 'Synthetic response'
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        for key, val in headers.items():
            self.headers[key] = val

    def geturl(self):
        return self.url

    def getcode(self):
        return self.code

    def info(self):
        return self.headers


class DeepSeekPlannerTests(unittest.TestCase):
    def planner(self, response=None, **kwargs):
        opener = Mock()
        opener.open.return_value = Response() if response is None else response
        return DeepSeekPlanner(lambda: FAKE_KEY, opener=opener, **kwargs), opener

    def assert_safe_failure(self, planner, expected=None):
        with self.assertRaises((RuntimeError, ValueError, TimeoutError)) as caught:
            planner.plan('找已拼最多的5个商品', {})
        text = str(caught.exception)
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn('PAYLOAD_SECRET', text)
        self.assertNotIn('EVIL_URL', text)
        if expected is not None:
            self.assertEqual(expected, text)
        self.assertEqual({}, planner.last_diagnostics)
        return text

    def test_valid_json_plan_uses_single_fixed_request(self):
        planner, opener = self.planner()
        self.assertEqual(PLAN, planner.plan('找已拼最多的5个商品', {}))
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(API_ENDPOINT, request.full_url)
        self.assertEqual('POST', request.method)
        self.assertEqual('Bearer ' + FAKE_KEY, request.get_header('Authorization'))
        self.assertEqual({'timeout': 60}, opener.open.call_args.kwargs)
        payload = json.loads(request.data)
        self.assertEqual(DEFAULT_MODEL, payload['model'])
        self.assertEqual({'type': 'json_object'}, payload['response_format'])
        self.assertEqual({'type': 'disabled'}, payload['thinking'])
        self.assertEqual(0, payload['temperature'])
        self.assertEqual(MAX_COMPLETION_TOKENS, payload['max_tokens'])
        self.assertIs(False, payload['stream'])
        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)
        self.assertIn(json.dumps(QUERY_PLAN_SCHEMA, ensure_ascii=False), payload['messages'][0]['content'])
        self.assertNotIn(FAKE_KEY, request.data.decode('utf-8'))
        self.assertEqual({'unique_plan_and_terminal_completion': True, 'request_count': 1,
                          'business_context_sent': False}, planner.last_diagnostics)

    def test_context_and_assistant_answers_are_never_forwarded(self):
        planner, opener = self.planner()
        context = {'shop_name': 'PRIVATE_SHOP', 'products': ['PRIVATE_PRODUCT'],
                   'image': 'PRIVATE_IMAGE', 'browser_cookie': 'PRIVATE_COOKIE',
                   'credentials': FAKE_KEY, 'nested': object()}
        history = [{'role': 'assistant', 'text': 'PRIVATE_ANSWER'},
                   {'role': 'user', 'text': '此前用户问题'}]
        planner.plan('第二张原卡', context, history, [31, 92])
        wire = opener.open.call_args.args[0].data.decode('utf-8')
        for marker in ('PRIVATE_SHOP', 'PRIVATE_PRODUCT', 'PRIVATE_IMAGE', 'PRIVATE_COOKIE',
                       'PRIVATE_ANSWER', FAKE_KEY):
            self.assertNotIn(marker, wire)
        data = json.loads(json.loads(wire)['messages'][1]['content'])
        self.assertEqual([{'role': 'user', 'text': '此前用户问题'}], data['history'])
        self.assertEqual([31, 92], data['last_observation_ids'])
        self.assertEqual({'message', 'context', 'history', 'last_observation_ids'}, set(data))
        self.assertEqual({'data_selection', 'available_actions'}, set(data['context']))

    def test_status_never_calls_network(self):
        planner, opener = self.planner()
        state = planner.status()
        self.assertEqual('ready', state['status'])
        self.assertEqual('deepseek', state['provider'])
        self.assertEqual(DEFAULT_MODEL, state['model'])
        self.assertIn('实际查询时确认', state['message'])
        self.assertNotIn(FAKE_KEY, json.dumps(state))
        opener.open.assert_not_called()
        self.assertFalse(hasattr(planner, 'api_key'))

    def test_missing_or_invalid_key_has_fixed_offline_status(self):
        for key in (None, '', '  ', 123, 'abc\r\nAuthorization: other', '中文', 'x' * 513):
            with self.subTest(key_type=type(key).__name__):
                opener = Mock()
                planner = DeepSeekPlanner(lambda: key, opener=opener)
                self.assertEqual('configuration_required', planner.status()['status'])
                self.assert_safe_failure(planner, CONFIG_MESSAGE)
                opener.open.assert_not_called()

    def test_key_loader_exception_is_sanitized(self):
        planner = DeepSeekPlanner(Mock(side_effect=RuntimeError(FAKE_KEY)), opener=Mock())
        self.assertEqual(CONFIG_MESSAGE, planner.status()['message'])
        self.assert_safe_failure(planner, CONFIG_MESSAGE)

    def test_pasted_key_is_not_forwarded(self):
        for message, history in ((FAKE_KEY, []), ('查看原卡', [{'role': 'user', 'text': FAKE_KEY}])):
            planner, opener = self.planner()
            with self.assertRaisesRegex(ValueError, '含有本机API密钥') as caught:
                planner.plan(message, {}, history)
            self.assertNotIn(FAKE_KEY, str(caught.exception))
            opener.open.assert_not_called()

    def test_json_escaped_key_is_not_forwarded(self):
        key = 'sk-fake-key-with-"-and-\\-characters'
        opener = Mock()
        planner = DeepSeekPlanner(lambda: key, opener=opener)
        with self.assertRaisesRegex(ValueError, '含有本机API密钥') as caught:
            planner.plan('误粘贴 ' + key, {})
        self.assertNotIn(key, str(caught.exception))
        opener.open.assert_not_called()

    def test_http_errors_never_read_body_or_retry(self):
        for code in (400, 401, 402, 403, 404, 429, 500, 503, 599, 301, 302, 307, 308):
            with self.subTest(code=code):
                body = Mock()
                planner, opener = self.planner()
                opener.open.side_effect = HTTPError('https://EVIL_URL/' + FAKE_KEY, code,
                                                   'PAYLOAD_SECRET ' + FAKE_KEY, {}, body)
                text = self.assert_safe_failure(planner)
                if code in HTTP_MESSAGES:
                    self.assertEqual(HTTP_MESSAGES[code], text)
                body.read.assert_not_called()
                body.close.assert_called_once()
                opener.open.assert_called_once()

    def test_timeouts_are_sanitized_without_retry(self):
        for error in (TimeoutError(FAKE_KEY), socket.timeout(FAKE_KEY), URLError(socket.timeout(FAKE_KEY))):
            planner, opener = self.planner()
            opener.open.side_effect = error
            self.assert_safe_failure(planner, TIMEOUT_MESSAGE)
            opener.open.assert_called_once()

    def test_arbitrary_network_errors_are_sanitized(self):
        for error in (URLError(FAKE_KEY), OSError(FAKE_KEY), RuntimeError(FAKE_KEY), ValueError(FAKE_KEY)):
            planner, opener = self.planner()
            opener.open.side_effect = error
            self.assert_safe_failure(planner, NETWORK_MESSAGE)
            opener.open.assert_called_once()

    def test_response_closed_on_success_and_parse_failure(self):
        for response in (Response(), Response(raw=b'not json')):
            planner, opener = self.planner(response)
            try:
                planner.plan('查找商品', {})
            except ValueError:
                pass
            self.assertTrue(response.closed)

    def test_html_and_wrong_content_encoding_are_rejected(self):
        for response in (Response(raw=b'<html>PAYLOAD_SECRET</html>', content_type='text/html'),
                         Response(**{'Content-Encoding': 'gzip'}),
                         Response(**{'Content-Length': 'nonsense'}),
                         Response(**{'Content-Length': '-1'})):
            planner, opener = self.planner(response)
            self.assert_safe_failure(planner)
            self.assertTrue(response.closed)

    def test_response_size_cap_declared_and_actual(self):
        for response in (Response(**{'Content-Length': str(MAX_RESPONSE_BYTES + 1)}),
                         Response(raw=b' ' * (MAX_RESPONSE_BYTES + 1))):
            planner, opener = self.planner(response)
            self.assertIn('超出安全长度', self.assert_safe_failure(planner))
            self.assertTrue(response.closed)

    def test_exact_size_limit_and_misleading_content_length(self):
        raw = json.dumps(completion()).encode('utf-8')
        exact = raw + b' ' * (MAX_RESPONSE_BYTES - len(raw))
        planner, opener = self.planner(Response(raw=exact))
        self.assertEqual(PLAN, planner.plan('查找商品', {}))
        planner, opener = self.planner(Response(raw=exact + b' ', **{'Content-Length': '1'}))
        self.assertIn('超出安全长度', self.assert_safe_failure(planner))

    def test_redirected_response_is_rejected(self):
        for target in ('https://EVIL_URL/', 'http://api.deepseek.com/chat/completions', API_ENDPOINT + '?extra=1'):
            planner, opener = self.planner(Response(url=target))
            self.assertIn('不允许的目标地址', self.assert_safe_failure(planner))

    def test_default_transport_never_follows_any_redirect(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                response = Response(status=code, Location='https://EVIL_URL/')
                with patch.object(HTTPSHandler, 'https_open', return_value=response) as send:
                    planner = DeepSeekPlanner(lambda: FAKE_KEY)
                    self.assertIn('不允许的重定向', self.assert_safe_failure(planner))
                    send.assert_called_once()

    def test_default_transport_has_no_environment_proxy(self):
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://EVIL_URL:9999', 'HTTP_PROXY': 'http://EVIL_URL:9999'}):
            opener = _default_opener()
            self.assertFalse(any(isinstance(handler, ProxyHandler) and handler.proxies
                                 for handler in opener.handlers))

    def test_non_success_response_has_fixed_error(self):
        for status in (401, 402, 429, 500, 302):
            planner, opener = self.planner(Response(status=status, raw=FAKE_KEY.encode()))
            self.assert_safe_failure(planner)

    def test_bad_json_and_duplicate_keys_are_rejected(self):
        for raw in (b'', b'[]', b'null', b'{', b'\xff', b'{"object":"chat.completion","object":"chat.completion"}',
                    b'{"value":NaN}', b'{"value":Infinity}', b'[' * 2000):
            planner, opener = self.planner(Response(raw=raw))
            self.assert_safe_failure(planner, INVALID_RESPONSE)

    def test_multiple_or_incomplete_completions_rejected(self):
        candidates = [completion(choices=[]), completion(choices=[None]), completion(choices=[completion()['choices'][0]] * 2),
                      completion(object='chat.completion.chunk'), completion(error={'message': FAKE_KEY})]
        for reason in ('length', 'tool_calls', 'content_filter', 'aborted', 'insufficient_system_resource', None):
            value = completion()
            value['choices'][0]['finish_reason'] = reason
            candidates.append(value)
        for value in candidates:
            planner, opener = self.planner(Response(value))
            self.assert_safe_failure(planner, INVALID_RESPONSE)

    def test_no_tool_calls_or_refusal_or_nontext_content(self):
        for field, value in (('tool_calls', [{'function': {'name': 'shell'}}]),
                             ('function_call', {'name': 'browse'}), ('refusal', FAKE_KEY),
                             ('role', 'tool'), ('content', None), ('content', ''),
                             ('content', []), ('content', '```json\n{}\n```'),
                             ('content', ' ' * 17000)):
            response = completion()
            response['choices'][0]['message'][field] = value
            planner, opener = self.planner(Response(response))
            self.assert_safe_failure(planner, INVALID_RESPONSE)

    def test_plan_schema_rejects_unsupported_or_extra_fields(self):
        cases = [dict(PLAN, action='shell'), dict(PLAN, sql='SELECT *'), dict(PLAN, limit=True),
                 dict(PLAN, limit=21), dict(PLAN, terms=['']), dict(PLAN, terms=['x'] * 9),
                 dict(PLAN, scope='other'), dict(PLAN, sales_min=-1), dict(PLAN, artists_only='true'),
                 dict(PLAN, sales_min=15, sales_max=3), dict(PLAN, observation_id=2),
                 dict(PLAN, action='card'), dict(PLAN, answer='虚构商品回答'),
                 dict(PLAN, terms=['https://EVIL_URL/']), dict(PLAN, terms=['x\n']),
                 {key: value for key, value in PLAN.items() if key != 'scope'}]
        for plan in cases:
            planner, opener = self.planner(Response(completion(plan)))
            self.assert_safe_failure(planner, INVALID_RESPONSE)

    def test_valid_card_and_clarify_plans_remain_compatible(self):
        for plan in (dict(PLAN, action='card', observation_id=17),
                     dict(PLAN, action='clarify', answer='当前仅支持查询已有记录。')):
            planner, opener = self.planner(Response(completion(plan)))
            self.assertEqual(plan, planner.plan('查看原卡', {}, [], [17]))

    def test_secret_in_model_plan_is_never_returned(self):
        for plan in (dict(PLAN, terms=[FAKE_KEY]), dict(PLAN, action='clarify', answer=FAKE_KEY)):
            for escaped in (False, True):
                response = completion(plan)
                if escaped:
                    response['choices'][0]['message']['content'] = response['choices'][0]['message']['content'].replace('sk-', '\\u0073k-')
                planner, opener = self.planner(Response(response))
                self.assert_safe_failure(planner, INVALID_RESPONSE)

    def test_input_validation_prevents_network(self):
        cases = [('', {}, [], []), ('x' * 2001, {}, [], []), ('x', [], [], []),
                 ('x', {}, [{'role': 'system', 'text': 'x'}], []),
                 ('x', {}, [{'role': 'user', 'text': 'x', 'data': 'extra'}], []),
                 ('x', {}, [], [1, 1]), ('x', {}, [], [True]), ('x', {}, [], [0]),
                 ('x', {}, [], [2**53]), ('x', {}, [], list(range(1, 22))),
                 ('x', {}, [{'role': 'user', 'text': 'x'}] * 13, [])]
        for args in cases:
            planner, opener = self.planner()
            with self.assertRaises(ValueError):
                planner.plan(*args)
            opener.open.assert_not_called()

    def test_oversized_or_unencodable_input_prevents_network(self):
        for message, history in (('x', [{'role': 'user', 'text': '中' * 3000}] * 12), ('\ud800', [])):
            planner, opener = self.planner()
            with self.assertRaises(ValueError):
                planner.plan(message, {}, history)
            opener.open.assert_not_called()

    def test_timeout_and_model_configuration_are_bounded(self):
        for timeout in (0, -1, 61, float('inf'), float('nan'), True, '60'):
            with self.assertRaises(ValueError):
                DeepSeekPlanner(lambda: FAKE_KEY, timeout=timeout)
        for model in ('https://EVIL_URL', '', FAKE_KEY, None):
            with self.assertRaises(ValueError) as caught:
                DeepSeekPlanner(lambda: FAKE_KEY, model=model)
            self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_elapsed_deadline_closes_response(self):
        response = Response()
        planner, opener = self.planner(response, timeout=1)
        with patch('pdd_monitor.agent_deepseek.time.monotonic', side_effect=[0, 2]):
            self.assert_safe_failure(planner, TIMEOUT_MESSAGE)
        self.assertTrue(response.closed)

    def test_read_error_is_sanitized_and_closes_response(self):
        response = Response()
        response.read1 = Mock(side_effect=OSError(FAKE_KEY))
        planner, opener = self.planner(response)
        self.assert_safe_failure(planner, NETWORK_MESSAGE)
        self.assertTrue(response.closed)

    def test_response_metadata_errors_are_sanitized(self):
        for target in ('geturl', 'getcode'):
            response = Response()
            setattr(response, target, Mock(side_effect=RuntimeError(FAKE_KEY)))
            planner, opener = self.planner(response)
            self.assert_safe_failure(planner, NETWORK_MESSAGE)
            self.assertTrue(response.closed)
        response = Response()
        response.headers = Mock()
        response.headers.get.side_effect = RuntimeError(FAKE_KEY)
        planner, opener = self.planner(response)
        self.assert_safe_failure(planner, NETWORK_MESSAGE)
        self.assertTrue(response.closed)

    def test_deadline_is_rechecked_after_a_slow_chunk(self):
        response = Response()
        planner, opener = self.planner(response, timeout=1)
        with patch('pdd_monitor.agent_deepseek.time.monotonic', side_effect=[0, 0.2, 1.1]):
            self.assert_safe_failure(planner, TIMEOUT_MESSAGE)
        self.assertTrue(response.closed)

    def test_callable_opener_supported(self):
        opener = Mock(return_value=Response(), spec=lambda request, timeout: None)
        planner = DeepSeekPlanner(lambda: FAKE_KEY, opener=opener)
        self.assertEqual(PLAN, planner.plan('查找商品', {}))
        opener.assert_called_once()


if __name__ == '__main__':
    unittest.main()
