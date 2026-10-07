"""Each helper must load the explicit project, even beside an imported package."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.package_cached_images import load_store


class ImageStoreLoaderTests(unittest.TestCase):
    def project(self, root, name, marker):
        project = Path(root) / name
        package = project / 'pdd_monitor'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('', encoding='utf-8')
        (package / 'capture_integrity.py').write_text('MARKER = ' + repr(marker), encoding='utf-8')
        (package / 'store.py').write_text(
            'def independent_gate():\n    from .capture_integrity import MARKER\n    return MARKER\n',
            encoding='utf-8')
        return project

    def test_relative_gate_uses_explicit_project_and_ignores_loaded_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.project(tmp, 'chosen', 'chosen gate')
            fake = types.ModuleType('pdd_monitor.store')
            fake.independent_gate = lambda: 'wrong project'
            with patch.dict(sys.modules, {'pdd_monitor.store': fake}):
                actual = load_store(project)
                self.assertEqual(actual.independent_gate(), 'chosen gate')
                self.assertEqual(Path(actual.__file__).resolve(), project / 'pdd_monitor/store.py')

    def test_two_projects_and_reloads_do_not_share_relative_dependency_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.project(tmp, 'first', 'first gate')
            second = self.project(tmp, 'second', 'second gate')
            a, b = load_store(first), load_store(second)
            self.assertNotEqual(a.__package__, b.__package__)
            self.assertEqual((a.independent_gate(), b.independent_gate()), ('first gate', 'second gate'))
            (first / 'pdd_monitor/capture_integrity.py').write_text("MARKER = 'updated first gate'", encoding='utf-8')
            self.assertEqual(load_store(first).independent_gate(), 'updated first gate')

    def test_failed_import_does_not_leave_partial_package_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self.project(tmp, 'broken', 'unused')
            (project / 'pdd_monitor/store.py').write_text("raise RuntimeError('synthetic failure')", encoding='utf-8')
            before = {name for name in sys.modules if name.startswith('_pdd_image_store_')}
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                load_store(project)
            self.assertEqual(before, {name for name in sys.modules if name.startswith('_pdd_image_store_')})


if __name__ == '__main__':
    unittest.main()
