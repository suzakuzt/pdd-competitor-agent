"""Explicit project-local opt-in for the verified incremental backup pipeline."""
import json
from pathlib import Path


def incremental_backup_enabled(project):
    path = Path(project) / 'state/backup_policy.json'
    if not path.exists():
        return False
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('schema') != 'pdd-backup-policy-v1'
            or value.get('mode') not in ('full', 'incremental')):
        raise ValueError('Invalid project backup policy; no backup mode selected')
    return value['mode'] == 'incremental'
