"""Select the explicitly configured local Agent planner, never silently fallback."""
from .agent_codex import CodexPlanner
from .agent_credentials import get_api_key, get_provider_config
from .agent_deepseek import DeepSeekPlanner


class UnavailablePlanner:
    def status(self):
        return {'status': 'unavailable', 'message': 'Agent 本机配置无法读取，请重新配置后重启本项目服务。'}

    def plan(self, *args, **kwargs):
        raise RuntimeError(self.status()['message'])


def configured_planner(project):
    try:
        config = get_provider_config(project)
    except (OSError, ValueError, RuntimeError):
        return UnavailablePlanner()
    if config['provider'] == 'deepseek':
        return DeepSeekPlanner(lambda: get_api_key(project), model=config['model'])
    return CodexPlanner()
