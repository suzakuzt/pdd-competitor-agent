"""Re-run the local synthetic image-package acceptance and save a small receipt."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import unittest

if __name__ == '__main__':
    scripts=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=scripts.parent,help='Project code read only; defaults to parent of scripts')
    parser.add_argument('--work-dir',type=Path,help='Existing directory for disposable synthetic fixtures; defaults to system temp')
    parser.add_argument('--out',required=True,type=Path,help='New external acceptance receipt path')
    args=parser.parse_args()
    project=args.project.resolve();out=args.out.resolve()
    if out.is_relative_to(project):parser.error('--out must be outside the source project')
    if out.exists():parser.error('--out already exists; do not overwrite earlier acceptance evidence')
    if args.work_dir is not None and not args.work_dir.is_dir():parser.error('--work-dir must already exist')
    os.environ['PDD_PACKAGE_TEST_PROJECT']=str(project)
    if args.work_dir is not None:os.environ['PDD_PACKAGE_TEST_TMPDIR']=str(args.work_dir.resolve())
    import test_package_mixed_page_assets as checks
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(checks.MixedBundleAcceptance)
    names=[]
    for test in suite:names.append(test.id())
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    paths=[scripts/name for name in ('package_mixed_page_assets.py','package_cached_images.py','test_package_mixed_page_assets.py')]
    receipt={'status':'passed' if result.wasSuccessful() else 'failed','checked_at':datetime.now(timezone.utc).isoformat(),
        'tests_run':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors),
        'test_names':names,'failures':[{'test':str(test),'traceback':detail} for test,detail in result.failures+result.errors],
        'code_sha256':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        'source_project':str(project),
        'scope':'Synthetic local temporary databases and original-receipt fixtures; source store implementation was imported read-only. No website, image download or source database write performed.',
        'tested_import':'8 independent raw cards, 4 saved image references, 2 image BLOBs, 4 missing images; all 8 cards remain importable.',
        'limitations':['Requires original pageAssets receipt directoryPath and existing ordinary local image files.',
            'Same URL with differing verified hashes is omitted and marked requires_review, never automatically selected or retried.',
            'The current importer normalizes nonblocked missing image work to pending_image_stage; source_state_json and bundle acceptance preserve conflict/failure review evidence.',
            'This receipt validates the helper; a final sealed real snapshot still needs its own bundle_acceptance.json.']}
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf-8') as handle:handle.write(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(str(out))
    raise SystemExit(0 if result.wasSuccessful() else 1)
