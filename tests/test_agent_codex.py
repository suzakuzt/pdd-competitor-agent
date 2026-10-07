"""Adapter contract tests use fake subprocess results; no login or network."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    from pdd_monitor import agent_codex as adapter
except ModuleNotFoundError:
    import agent_codex as adapter

CodexPlanner = adapter.CodexPlanner
DISABLED_FEATURES = adapter.DISABLED_FEATURES
MAX_OUTPUT_BYTES = adapter.MAX_OUTPUT_BYTES
QUERY_PLAN_SCHEMA = adapter.QUERY_PLAN_SCHEMA
find_codex = adapter.find_codex
validate_plan = adapter.validate_plan


BASE = {'action':'search','terms':['陈立农'],'scope':'reference','sales_min':11,'sales_max':None,
        'limit':10,'observation_id':None,'answer':'','artists_only':False}


def events(plan=BASE):
    return '\n'.join(json.dumps(item, ensure_ascii=False) for item in [
        {'type':'thread.started','thread_id':'fake-thread'}, {'type':'turn.started'},
        {'type':'item.completed','item':{'type':'reasoning','text':'Fake fixture reasoning'}},
        {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(plan, ensure_ascii=False)}},
        {'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}])


class FakeRunner:
    def __init__(self, login='Logged in using ChatGPT', login_code=0, output=None, code=0, error=None):
        self.login, self.login_code = login, login_code
        self.output = events() if output is None else output
        self.code, self.error, self.calls, self.exec_directories = code, error, [], []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1:3] == ['login','status']:
            return subprocess.CompletedProcess(command, self.login_code, '', self.login)
        self.exec_directories.append(kwargs['cwd'])
        self.empty_directory = list(Path(kwargs['cwd']).iterdir()) == []
        schema = Path(command[command.index('--output-schema')+1])
        self.schema = json.loads(schema.read_text(encoding='utf-8'))
        self.prompt_only_in_stdin = not any('陈立农' in part for part in command)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, self.code, self.output, 'fake private stderr, never display')


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='codex_adapter_test_', dir=Path(__file__).parent)
        self.root = Path(self.temporary.name)
        self.exe = self.root / 'fake_codex.exe'
        self.exe.write_bytes(b'fake executable marker, never executed')

    def tearDown(self):
        self.temporary.cleanup()

    def planner(self, fake):
        return CodexPlanner(self.exe, runner=fake, temp_parent=self.root)

    def plan(self, fake):
        return self.planner(fake).plan('找陈立农已拼超过10件的卡', {'shops':[{'shop_id':'shop_test'}]}, [], [])

    def test_valid_plan_is_model_output_not_keyword_fallback(self):
        model_plan = deepcopy(BASE)
        model_plan.update(terms=['different literal from fake model'], scope='latest', sales_min=None)
        fake = FakeRunner(output=events(model_plan))
        self.assertEqual(self.plan(fake), model_plan)
        self.assertEqual(len(fake.calls), 2)

    def test_login_required_never_execs_and_does_not_echo_status(self):
        fake = FakeRunner(login='Not logged in; private account detail', login_code=1)
        status = self.planner(fake).status()
        self.assertEqual(status['status'], 'login_required')
        self.assertIn('CONNECT_AGENT.cmd', status['message'])
        self.assertNotIn('private', status['message'])
        with self.assertRaises(RuntimeError):
            self.plan(fake)
        self.assertTrue(all(command[1:3]==['login','status'] for command, _ in fake.calls))

    def test_api_key_login_rejected_without_exposing_key(self):
        fake = FakeRunner(login='Logged in using an API key - sk-private')
        state = self.planner(fake).status()
        self.assertEqual(state['status'], 'unavailable')
        self.assertNotIn('sk-private', state['message'])
        with self.assertRaises(RuntimeError):
            self.plan(fake)

    def test_ambiguous_or_nonzero_chatgpt_login_is_not_ready(self):
        for output, code in [('Logged in',0),('Logged in using ChatGPT',1),('Logged in using ChatGPT and API key',0)]:
            with self.subTest(output=output, code=code):
                self.assertEqual(self.planner(FakeRunner(login=output,login_code=code)).status()['status'], 'unavailable')

    def test_missing_executable_unavailable(self):
        fake = FakeRunner()
        self.assertEqual(CodexPlanner(self.root/'missing.exe',runner=fake).status()['status'], 'unavailable')
        self.assertEqual(fake.calls, [])

    def test_cli_path_priority_and_known_desktop_newest_mtime(self):
        with patch.object(adapter.shutil, 'which', side_effect=lambda name: str(self.exe) if name=='codex.exe' else None):
            self.assertEqual(find_codex(), self.exe.resolve())
        local = self.root / 'localappdata'
        old = local/'OpenAI'/'Codex'/'bin'/'old'/'codex.exe'
        new = local/'OpenAI'/'Codex'/'bin'/'new'/'codex.exe'
        for path, stamp in ((old,100), (new,200)):
            path.parent.mkdir(parents=True)
            path.write_bytes(b'fake binary, never executed')
            os.utime(path, (stamp, stamp))
        with patch.object(adapter.shutil, 'which', return_value=None), patch.dict(os.environ, {'LOCALAPPDATA':str(local)}):
            self.assertEqual(find_codex(),new.resolve())

    def test_missing_known_install_is_unavailable_without_scanning(self):
        with patch.object(adapter.shutil, 'which', return_value=None), patch.dict(os.environ, {'LOCALAPPDATA':str(self.root/'not_installed')}):
            self.assertIsNone(find_codex())
            self.assertEqual(CodexPlanner(runner=FakeRunner()).status()['status'], 'unavailable')

    def test_fixed_sandbox_model_tool_config_and_private_temp_cleanup(self):
        fake = FakeRunner()
        self.plan(fake)
        command, kwargs = fake.calls[-1]
        for value in ('--ignore-user-config','--ephemeral','--skip-git-repo-check','--json','--no-daemon'):
            self.assertIn(value, command)
        self.assertNotIn('--ignore-rules', command)
        self.assertEqual(command[command.index('--sandbox')+1], 'read-only')
        self.assertEqual(command[command.index('--model')+1], 'gpt-6-astra')
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('model_provider="pdd_chatgpt_http"', command)
        self.assertIn('model_providers.pdd_chatgpt_http={name="OpenAI",wire_api="responses",requires_openai_auth=true,supports_websockets=false}', command)
        self.assertNotIn('model_provider="openai"', command)
        # With ChatGPT forced and provider auth enabled, the official CLI uses
        # its unchanged default ChatGPT endpoint; no API route is supplied.
        config_values = [command[index+1] for index,value in enumerate(command[:-1]) if value=='-c']
        self.assertFalse(any('base_url' in value or 'env_key' in value or 'bearer_token' in value for value in config_values))
        self.assertIn('web_search="disabled"', command)
        self.assertIn('mcp_servers={}', command)
        for feature in DISABLED_FEATURES:
            self.assertIn(f'features.{feature}=false', command)
        self.assertTrue(fake.empty_directory)
        self.assertTrue(fake.prompt_only_in_stdin)
        self.assertEqual(fake.schema, QUERY_PLAN_SCHEMA)
        self.assertEqual(kwargs['timeout'],120)
        self.assertTrue(all(not Path(path).exists() for path in fake.exec_directories))

    def test_same_environment_filters_api_keys_and_preserves_codex_home(self):
        fake = FakeRunner()
        with patch.dict(os.environ, {'OPENAI_API_KEY':'do-not-use','CODEX_API_KEY':'do-not-use-either','DEEPSEEK_API_KEY':'synthetic-do-not-inherit','CODEX_HOME':'existing-user-home'}):
            self.plan(fake)
        for _, kwargs in fake.calls:
            self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
            self.assertNotIn('CODEX_API_KEY', kwargs['env'])
            self.assertNotIn('DEEPSEEK_API_KEY', kwargs['env'])
            self.assertEqual(kwargs['env']['CODEX_HOME'], 'existing-user-home')
        self.assertEqual(fake.calls[0][1]['env'], fake.calls[1][1]['env'])

    def test_failed_exec_is_sanitized_no_retry(self):
        fake = FakeRunner(code=1)
        with self.assertRaisesRegex(RuntimeError, '没有自动重试') as error:
            self.plan(fake)
        self.assertNotIn('private', str(error.exception))
        self.assertEqual(len(fake.calls), 2)

    def test_timeout_is_sanitized_no_retry(self):
        fake = FakeRunner(error=subprocess.TimeoutExpired('sensitive prompt command', 120))
        with self.assertRaisesRegex(TimeoutError, '超时') as error:
            self.plan(fake)
        self.assertNotIn('sensitive', str(error.exception))
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(all(not Path(path).exists() for path in fake.exec_directories))

    def test_tool_event_rejected_even_with_valid_final_plan(self):
        forbidden = json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'no'}})
        with self.assertRaisesRegex(ValueError,'非计划动作'):
            self.plan(FakeRunner(output=forbidden+'\n'+events()))

    def test_recovered_errors_require_unique_plan_and_terminal_success(self):
        notification = json.dumps({'type':'error','message':'synthetic recovery notice'})
        item_error = json.dumps({'type':'item.completed','item':{'type':'error','message':'synthetic startup notice'}})
        lines = events().splitlines()
        recovered = '\n'.join([lines[0],item_error,lines[1],notification,notification,*lines[2:]])
        planner = self.planner(FakeRunner(output=recovered))
        plan = planner.plan('查前五张',{},[],[])
        self.assertEqual(plan,BASE)
        self.assertEqual(set(plan),set(QUERY_PLAN_SCHEMA['required']))
        self.assertEqual(planner.last_diagnostics,{'recovered_error_notification_count':3,'unique_plan_and_terminal_completion':True})

    def test_error_only_late_error_and_turn_failed_are_rejected(self):
        notification = json.dumps({'type':'error','message':'synthetic failure'})
        item_error = json.dumps({'type':'item.completed','item':{'type':'error','message':'synthetic failure'}})
        failed = json.dumps({'type':'turn.failed','error':{'message':'synthetic failure'}})
        lines = events().splitlines()
        outputs = [notification, notification+'\n'+lines[-1], events()+'\n'+notification,
                   events()+'\n'+item_error, '\n'.join(lines[:-1]+[notification,lines[-1]]),
                   '\n'.join(lines[:-1]+[failed]), notification+'\n'+events()+'\n'+failed]
        for output in outputs:
            with self.subTest(output=output[-90:]), self.assertRaises(ValueError):
                self.plan(FakeRunner(output=output))

    def test_recovered_notice_cannot_hide_tool_or_multiple_final_messages(self):
        notice = json.dumps({'type':'error','message':'synthetic notice'})
        tool = json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'never'}})
        lines = events().splitlines()
        for output in (notice+'\n'+tool+'\n'+events(), '\n'.join([notice,*lines[:-1],lines[-2],lines[-1]])):
            with self.subTest(output=output[-100:]), self.assertRaises(ValueError):
                self.plan(FakeRunner(output=output))

    def test_malformed_error_and_unfinished_stream_rejected(self):
        for value in ('not JSON', json.dumps({'type':'error','message':'private'}),
                      '\n'.join(events().splitlines()[:-1]), events()+'\n'+events()):
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                self.plan(FakeRunner(output=value))

    def test_extra_code_sql_url_fields_and_missing_fields_rejected(self):
        for key in ('sql','code','url'):
            plan = {**BASE,key:'not executable'}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.plan(FakeRunner(output=events(plan)))
        plan = deepcopy(BASE)
        plan.pop('artists_only')
        with self.assertRaises(ValueError):
            self.plan(FakeRunner(output=events(plan)))

    def test_bool_numbers_ranges_limits_actions_and_boundaries(self):
        for update in ({'limit':True},{'limit':21},{'sales_min':False},{'sales_min':20,'sales_max':10},
                       {'sales_max':2**53},{'observation_id':'1'},{'artists_only':1},{'scope':'current'},
                       {'action':'download'},{'terms':['']},{'action':'card','observation_id':None}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_plan({**BASE,**update})
        self.assertEqual(validate_plan({**BASE,'sales_min':0,'sales_max':0,'limit':20})['sales_min'],0)

    def test_unicode_and_explicit_card_reference(self):
        plan = {**BASE,'action':'card','observation_id':534,'terms':[],'sales_min':None}
        fake = FakeRunner(output=events(plan))
        result = self.planner(fake).plan('上一答第2张', {'previous_answer_cards':[{'observation_id':535},{'observation_id':534}]},
                    [{'role':'user','text':'先看两张'},{'role':'assistant','text':'两张卡片'}], [535,534])
        self.assertEqual(result['observation_id'],534)
        self.assertIn('上一答第2张',fake.calls[-1][1]['input'])

    def test_clarification_only_and_no_unqueried_model_answer(self):
        plan = {**BASE,'action':'clarify','answer':'请说明要看参考轮还是最新轮。'}
        self.assertEqual(self.plan(FakeRunner(output=events(plan))),plan)
        with self.assertRaises(ValueError):
            validate_plan({**BASE,'answer':'我找到了10张商品'})
        for text in ('https://example.invalid','```python','<script>alert(1)</script>'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_plan({**plan,'answer':text})

    def test_oversized_output_and_input_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'安全长度'):
            self.plan(FakeRunner(output='x'*(MAX_OUTPUT_BYTES+1)))
        fake = FakeRunner()
        with self.assertRaises(ValueError):
            self.planner(fake).plan('x'*2001,{})
        with self.assertRaises(ValueError):
            self.planner(fake).plan('查询',{},[{'role':'user','text':'汉'*3000} for _ in range(12)],[])
        self.assertEqual(fake.calls,[])

    def test_context_and_assistant_results_never_enter_model_payload(self):
        fake = FakeRunner()
        context = {'shop_name':'PRIVATE_SHOP_SENTINEL','source_url':'https://private-sentinel.invalid',
                   'snapshot_sha256':'PRIVATE_SHA_SENTINEL','previous_answer_cards':[{'title':'PRIVATE_CARD_SENTINEL'}],
                   'unsupported_value':float('nan'),'oversized':'x'*65537}
        self.planner(fake).plan('查前5张',context,[{'role':'assistant','text':'PRIVATE_RESULT_SENTINEL'},
                                {'role':'user','text':'此前用户问题'}],[207,66])
        data = json.loads(fake.calls[-1][1]['input'].split('下面是用户数据 JSON：\n',1)[1])
        self.assertEqual(data['context'],{'data_selection':'current_shop','available_actions':['search','card','new_arrivals','trends','context']})
        self.assertEqual(data['history'],[{'role':'user','text':'此前用户问题'}])
        self.assertEqual(data['last_observation_ids'],[207,66])
        for secret in ('PRIVATE_','private-sentinel','https://'):
            self.assertNotIn(secret,fake.calls[-1][1]['input'])

    def test_duplicate_json_key_and_fenced_json_rejected(self):
        for text in ('{"type":"turn.started","type":"turn.completed"}',
                     '```json\n'+events()+'\n```'):
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                self.plan(FakeRunner(output=text))

    def test_invalid_history_and_previous_ids_rejected_before_process(self):
        fake = FakeRunner()
        for history, ids in [([{'role':'system','text':'bypass'}],[]),([],[True]),([],[1,1]),([],[-1])]:
            with self.subTest(history=history, ids=ids), self.assertRaises(ValueError):
                self.planner(fake).plan('查询',{},history,ids)
        self.assertEqual(fake.calls,[])


if __name__ == '__main__':
    unittest.main(verbosity=2)
