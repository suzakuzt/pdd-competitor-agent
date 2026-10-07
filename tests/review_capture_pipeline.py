"""Only wrapper-generated SYNTHETIC fixtures may enter disposable stores."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdd_monitor.capture_integrity import verify_capture
from pdd_monitor.store import import_snapshot
from pdd_monitor.validation import validate_store

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixture-root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    marker=json.loads((args.fixture_root/'SYNTHETIC_ONLY.json').read_text(encoding='utf-8'))
    if marker.get('kind')!='SYNTHETIC_TEST_ONLY' or marker.get('production_import_allowed') is not False:
        raise ValueError('Explicit synthetic-only fixture marker required')
    results=[]
    for case in ('complete','partial','recovered_partial'):
        directory=args.fixture_root/case
        session=json.loads((directory/'session.json').read_text(encoding='utf-8'))
        path=directory/session['snapshot']['file']
        proof=verify_capture(path)
        item={'case':case,'passed':False,'integrity':proof}
        if proof['valid']:
            with tempfile.TemporaryDirectory(prefix='pdd_pipeline_SYNTHETIC_') as tmp:
                data=Path(tmp)/'data'
                imported=import_snapshot(data,path)
                valid=validate_store(data,imported['run_id'])
                before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in data.glob('*.sqlite3')}
                duplicate=import_snapshot(data,path)
                after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in data.glob('*.sqlite3')}
                expected='complete' if case=='complete' else 'partial'
                item.update(validation_ok=valid['ok'],duplicate=duplicate['duplicate'],duplicate_both_databases_unchanged=before==after,expected_status=expected)
                item['passed']=valid['ok'] and duplicate['duplicate'] and before==after and proof['verified_status']==expected
        results.append(item)
    report={'status':'passed' if all(r['passed'] for r in results) else 'failed','total':len(results),'passed':sum(bool(r['passed']) for r in results),'results':results,'production_writes':False,'website_capture':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':report['status'],'total':report['total'],'passed':report['passed']}))
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
