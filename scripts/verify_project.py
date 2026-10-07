"""Portable offline verification with isolated code and temporary test data.

Usage: python scripts/verify_project.py --node <node executable> [--output NEW_DIR]
No package installation, real browser, business-store import or project writes.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

BASELINE_FILES=('pdd_phase1_snapshot_20261004.json','pdd_phase1_available_images_20261004.zip','pdd_phase1_image_queue_20261004.json','pdd_phase1_detail_note_20261004.json')

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--fixture-project',type=Path,help='Read-only historical fixture project; defaults to --project')
    parser.add_argument('--node',default=os.environ.get('PDD_NODE') or shutil.which('node'))
    parser.add_argument('--output',type=Path,help='New output directory; never overwritten')
    parser.add_argument('--temp-root',type=Path,help='Existing short temporary parent; defaults to the system temporary directory')
    parser.add_argument('--timeout',type=int,default=300)
    args=parser.parse_args()
    project=args.project.resolve(strict=True)
    fixture_project=(args.fixture_project or project).resolve(strict=True)
    if not args.node:parser.error('Node is required: pass --node or set PDD_NODE')
    node=Path(args.node).resolve(strict=True)
    if args.timeout<=0:parser.error('--timeout must be positive')
    output=args.output.resolve() if args.output else Path(tempfile.gettempdir()).resolve()/('pdd_verify_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    for root in (project,fixture_project):
        for name in ('data','sources','state','tests','scripts','pdd_monitor','dashboard','backups'):
            if output.is_relative_to(root/name):parser.error('Output must be outside protected project directories')
    if args.output:output.mkdir(parents=True,exist_ok=False)
    else:output.mkdir(exist_ok=False)
    stage=output/'code_copy';stage.mkdir()
    temp_parent=(args.temp_root or Path(tempfile.gettempdir())).resolve(strict=True)
    for root in (project,fixture_project):
        for name in ('data','sources','state','tests','scripts','pdd_monitor','dashboard','backups'):
            if temp_parent.is_relative_to(root/name):parser.error('Temporary parent must be outside protected project directories')
    # Each allowlisted suite already allocates unique TemporaryDirectory/mkdtemp
    # fixtures. An extra parent layer can push atomic backup-copy names over
    # Windows MAX_PATH, so retain the short system parent rather than nesting it.
    temporary=temp_parent
    if os.name=='nt' and len(str(temporary))>45:
        parser.error('Windows temporary path is too long for nested backup tests; pass --temp-root with a shorter temporary parent')
    manifest=[]
    def copy(source,relative):
        target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        manifest.append({'source':str(source),'relative':str(relative),'sha256':digest(source),'copy_sha256':digest(target)})
    for directory,patterns in [('pdd_monitor',('*.py',)),('scripts',('*.py','*.mjs')),('tests',('*.py','*.mjs'))]:
        for pattern in patterns:
            for source in sorted((project/directory).glob(pattern)):copy(source,source.relative_to(project))
    for source in sorted((project/'tests/frontend').glob('*.mjs')):copy(source,source.relative_to(project))
    copy(project/'schema.sql',Path('schema.sql'))
    copy(project/'AGENTS.md',Path('AGENTS.md'))
    copy(project/'dashboard/package.json',Path('dashboard/package.json'))
    for source in sorted((project/'dashboard/src/content').rglob('*')):
        if source.is_file() and source.suffix in ('.js','.jsx','.css'):copy(source,source.relative_to(project))
    for source in sorted((project/'dashboard/tests').glob('*.test.mjs')):copy(source,source.relative_to(project))
    for name in BASELINE_FILES:copy(fixture_project/'sources'/name,Path('sources')/name)
    (output/'code_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    historical=fixture_project/'sources/shop_tracking_B_v5_20261004'
    protected=[p for p in (fixture_project/'data/monitor.sqlite3',fixture_project/'data/images.sqlite3') if p.is_file()]
    protected += [historical/'snapshot.json',*sorted((historical/'batches').glob('batch_*.json'))]
    protected += sorted((fixture_project/'sources/multishop_B_20261004/batches_v2').glob('batch_*.json'))[:9]
    before={str(p):digest(p) for p in protected}
    env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',TMP=str(temporary),TEMP=str(temporary),TMPDIR=str(temporary),PDD_TEST_PROJECT=str(stage),PDD_PACKAGE_TEST_PROJECT=str(stage),PDD_PACKAGE_TEST_TMPDIR=str(temporary))
    env.pop('PYTHONPATH',None)
    python=str(Path(sys.executable).resolve())
    fixtures=output/'SYNTHETIC_INTEGRATION'
    suites=[
      ('python',[python,'-B','-m','unittest','discover','-s','tests','-v'],None),
      ('driver_v4',[str(node),'scripts/review_live_capture_v4.mjs','--project-root',str(fixture_project),'--output',str(output/'driver_v4')],output/'driver_v4/driver_review.json'),
      ('driver_v5',[str(node),'tests/review_rendered_capture.mjs','--output',str(output/'driver_v5.json')],output/'driver_v5.json'),
      ('session',[str(node),'tests/review_capture_session.mjs','--fixture-root',str(historical),'--retain-integration',str(fixtures)],stage/'tests/capture_session_acceptance.json'),
      ('cua_io_deadline',[str(node),'--test','tests/test_cua_capture_io.mjs'],None),
      ('host_capture_runner',[str(node),'--test','tests/test_host_capture_runner.mjs'],None),
      ('cua_module_globals',[str(node),'--experimental-vm-modules','tests/test_cua_missing_globals.mjs'],stage/'cua_missing_globals_acceptance.json'),
      ('capture_pipeline',[python,'-B','tests/review_capture_pipeline.py','--fixture-root',str(fixtures),'--output',str(output/'capture_pipeline.json')],output/'capture_pipeline.json'),
      ('image_package',[python,'-B','scripts/test_package_mixed_page_assets.py','-v'],None),
      ('frontend_models',[str(node),'tests/review_frontend_models.mjs','--output',str(output/'frontend_models.json')],output/'frontend_models.json'),
    ]
    results=[]
    for name,command,receipt in suites:
        start=time.monotonic();log=output/(name+'.log');error=None
        try:
            with log.open('x',encoding='utf-8') as handle:
                completed=subprocess.run(command,cwd=stage,env=env,stdout=handle,stderr=subprocess.STDOUT,timeout=args.timeout)
                code=completed.returncode
        except subprocess.TimeoutExpired:
            code=124;error='suite_timeout'
        except OSError as exc:
            code=127;error=str(exc)
        text=log.read_text(encoding='utf-8')
        match=re.search(r'Ran (\d+) tests?',text)
        result={'suite':name,'exit_code':code,'duration_seconds':round(time.monotonic()-start,3),'log':str(log),'command':command,'test_count':int(match[1]) if match else None,'error':error}
        if name=='driver_v4':
            candidates=list((output/'driver_v4').glob('*.json'));receipt=candidates[0] if len(candidates)==1 else receipt
        if receipt and receipt.is_file():
            data=json.loads(receipt.read_text(encoding='utf-8'))
            result.update(receipt=str(receipt),test_count=data.get('total',data.get('test_count',data.get('tests',len(data.get('checks',[]))))) or result['test_count'],passed=data.get('passed'),receipt_status=data.get('status',data.get('ok')))
        results.append(result)
        print(json.dumps({'suite':name,'exit_code':code,'test_count':result['test_count'],'duration_seconds':result['duration_seconds']}),flush=True)
    after={str(p):digest(p) for p in protected}
    unchanged=before==after
    report={'status':'passed' if unchanged and all(r['exit_code']==0 for r in results) else 'failed','checked_at':datetime.now(timezone.utc).isoformat(),'project':str(project),'fixture_project':str(fixture_project),'output':str(output),'temporary_root':str(temporary),'python':sys.version,'node':subprocess.check_output([str(node),'--version'],text=True).strip(),'results':results,'protected_unchanged':unchanged,'protected_before':before,'protected_after':after,'production_writes_performed':False,'website_capture_performed':False,'limitations':['Offline regression and historical replay only; no new website, browser UI, scheduled trigger, real growth or profitability validation.','Historical fixtures are required; missing fixture is a failure, never silently skipped.']}
    (output/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'receipt':str(output/'verification.json'),'protected_unchanged':unchanged}),flush=True)
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
