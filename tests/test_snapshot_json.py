"""The optional dashboard encoder retains the strict stdlib JSON contract."""
from dataclasses import dataclass
from datetime import datetime
import json
import unittest
from unittest.mock import patch

from pdd_monitor import snapshot_json


def standard(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


class SnapshotJsonTests(unittest.TestCase):
    def test_unicode_nested_values_format_and_newline(self):
        value = {'店铺': '示例店铺😀', 'cards': [{'价格': 4.5, '销量': None, 'yes': True}], 'empty': [], 'tuple': (1, 2)}
        actual = snapshot_json.dumps_bytes(value)
        self.assertEqual(actual, standard(value))
        self.assertIn('示例店铺😀'.encode('utf-8'), actual)
        self.assertTrue(actual.endswith(b'\n'))
        self.assertFalse(actual.endswith(b'\n\n'))

    def test_missing_optional_dependency_uses_original_encoder(self):
        value = {'非ASCII': '数据', 'huge': 10 ** 100}
        with patch.object(snapshot_json, '_orjson', None):
            self.assertEqual(snapshot_json.dumps_bytes(value), standard(value))

    def test_nonfinite_numbers_in_values_or_keys_are_rejected(self):
        for backend in (snapshot_json._orjson, None):
            for number in (float('nan'), float('inf'), -float('inf')):
                for value in (number, {'nested': [number]}, {number: 1}):
                    with self.subTest(backend=backend is not None, value=value), patch.object(snapshot_json, '_orjson', backend):
                        with self.assertRaises(ValueError):
                            snapshot_json.dumps_bytes(value)

    def test_integer_boundaries_preserve_exact_values(self):
        values = [-(1 << 63) - 1, -(1 << 63), (1 << 53) + 1, (1 << 63) - 1,
                  1 << 63, (1 << 64) - 1, 1 << 64, 10 ** 100]
        for value in values:
            with self.subTest(value=value):
                encoded = snapshot_json.dumps_bytes({'integer': value})
                self.assertEqual(json.loads(encoded), {'integer': value})
                self.assertEqual(encoded, standard({'integer': value}))

    def test_unsupported_integer_skips_native_encoder_without_coercion(self):
        class RefuseUse:
            def dumps(self, *args, **kwargs):
                raise AssertionError('outside native integer range')
        with patch.object(snapshot_json, '_orjson', RefuseUse()):
            self.assertEqual(snapshot_json.dumps_bytes({'big': 1 << 128}), standard({'big': 1 << 128}))

    def test_non_string_keys_follow_standard_library_conversions(self):
        value = {1: 'one', None: 'none', False: 'false', 2.5: 'float'}
        self.assertEqual(snapshot_json.dumps_bytes(value), standard(value))

    def test_orjson_extended_types_are_not_silently_accepted(self):
        @dataclass
        class Example:
            value: int
        for value in (datetime(2026, 10, 7), Example(1), b'bytes', {1, 2}, object()):
            with self.subTest(value=type(value)):
                with self.assertRaises(TypeError):
                    snapshot_json.dumps_bytes({'unsupported': value})

    def test_native_type_error_falls_back_without_changing_values(self):
        class Unsupported:
            OPT_INDENT_2 = 1
            OPT_APPEND_NEWLINE = 2
            def dumps(self, *args, **kwargs):
                raise TypeError('unsupported native depth')
        value = {'keep': [1, '值', -0.0]}
        with patch.object(snapshot_json, '_orjson', Unsupported()):
            self.assertEqual(snapshot_json.dumps_bytes(value), standard(value))

    def test_deep_nesting_and_repeated_containers_preserve_json(self):
        value = 1
        for _ in range(260):
            value = [value]
        self.assertEqual(snapshot_json.dumps_bytes(value), standard(value))
        shared = {'x': [1, 2]}
        self.assertEqual(snapshot_json.dumps_bytes([shared, shared]), standard([shared, shared]))

    def test_cycles_are_rejected(self):
        value = []
        value.append(value)
        with self.assertRaises(ValueError):
            snapshot_json.dumps_bytes(value)

    def test_lone_surrogate_is_not_replaced_or_escaped(self):
        with self.assertRaises(UnicodeError):
            snapshot_json.dumps_bytes({'invalid': '\ud800'})

    def test_float_values_are_equal_even_when_exponent_spelling_differs(self):
        value = [1e-6, 1e100, -0.0, 5e-324, 1.7976931348623157e308]
        self.assertEqual(json.loads(snapshot_json.dumps_bytes(value)), json.loads(standard(value)))


if __name__ == '__main__':
    unittest.main()
