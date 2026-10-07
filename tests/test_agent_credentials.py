"""Synthetic-only tests. Every test isolates env and uses temporary directories."""
import ctypes
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import warnings
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "pdd_monitor" / "agent_credentials.py"
SPEC = importlib.util.spec_from_file_location("isolated_agent_credentials", MODULE_PATH)
credentials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credentials)

SYNTHETIC_KEY = "synthetic-only-not-a-real-api-key-20261005"


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pdd_credential_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.other = self.root / "other-project"
        self.project.mkdir()
        self.other.mkdir()
        self.local = self.root / "local"
        # clear=True prevents these tests from reading any real API key.
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def assert_safe_error(self, operation, expected):
        with self.assertRaises(credentials.CredentialError) as captured:
            operation()
        self.assertEqual(str(captured.exception), expected)
        self.assertNotIn(SYNTHETIC_KEY, str(captured.exception))

    def test_environment_wins_without_dpapi_or_file_access(self):
        os.environ["DEEPSEEK_API_KEY"] = "  " + SYNTHETIC_KEY + "  "
        with patch.object(credentials, "credential_path", side_effect=AssertionError("must not read file")):
            self.assertEqual(credentials.get_api_key(self.project), SYNTHETIC_KEY)
        self.assertFalse(self.local.exists())

    def test_invalid_environment_does_not_fall_back(self):
        for value in ("", " ", "bad\nkey", "bad key", "密钥", "x" * 513):
            with self.subTest(value_kind=type(value).__name__):
                os.environ["DEEPSEEK_API_KEY"] = value
                with patch.object(credentials, "credential_path", side_effect=AssertionError("must not read file")):
                    self.assert_safe_error(lambda: credentials.get_api_key(self.project), credentials.ERROR_KEY)

    def test_credential_path_canonical_namespace_and_no_creation(self):
        alias = self.project / ".." / self.project.name
        path = credentials.credential_path(self.project)
        canonical = os.path.normcase(os.path.normpath(str(self.project.resolve())))
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        self.assertEqual(path, self.local / "PDDCompetitorAgent" / "credentials" / expected_hash / "deepseek.dpapi")
        self.assertEqual(credentials.credential_path(alias), path)
        self.assertNotEqual(credentials.credential_path(self.other), path)
        self.assertNotIn("shop_", str(path))
        self.assertFalse(self.local.exists())

    @unittest.skipUnless(os.name == "nt", "Windows canonical case semantics")
    def test_windows_path_case_is_same_namespace(self):
        self.assertEqual(credentials.credential_path(str(self.project).upper()), credentials.credential_path(self.project))

    def test_localappdata_missing_or_relative_fails_closed(self):
        for value in (None, "", "relative-folder"):
            if value is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = value
            self.assert_safe_error(lambda: credentials.credential_path(self.project), credentials.ERROR_PATH)

    def test_missing_credential_returns_none_and_creates_nothing(self):
        self.assertIsNone(credentials.get_api_key(self.project))
        self.assertFalse(self.local.exists())

    def test_no_config_defaults_to_codex_without_creating_state(self):
        self.assertEqual(credentials.get_provider_config(self.project), {"version": 1, "provider": "codex", "model": None})
        self.assertFalse((self.project / "state").exists())

    def test_provider_config_exact_safe_schema_and_project_isolation(self):
        expected = {"version": 1, "provider": "deepseek", "model": "deepseek-flash"}
        self.assertEqual(credentials.set_provider_config(self.project, "deepseek", None), expected)
        self.assertEqual(credentials.get_provider_config(self.project), expected)
        path = self.project / "state" / "agent_runtime.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected)
        self.assertEqual(set(json.loads(path.read_text(encoding="utf-8"))), {"version", "provider", "model"})
        self.assertNotIn(SYNTHETIC_KEY, path.read_text(encoding="utf-8"))
        self.assertEqual(credentials.get_provider_config(self.other)["provider"], "codex")
        self.assertEqual(credentials.set_provider_config(self.project, "deepseek", "deepseek-v4-pro")["model"], "deepseek-v4-pro")
        self.assertEqual(credentials.set_provider_config(self.project, "codex", None)["provider"], "codex")

    def test_invalid_provider_models_do_not_replace_good_config(self):
        credentials.set_provider_config(self.project, "codex", None)
        path = self.project / "state" / "agent_runtime.json"
        before = path.read_bytes()
        for provider, model in (("unknown", None), ("deepseek", "deepseek-chat"), ("deepseek", ""),
                                ("deepseek", "deepseek-reasoner"), ("codex", "bad\nmodel"), ("codex", {"key": SYNTHETIC_KEY})):
            self.assert_safe_error(lambda: credentials.set_provider_config(self.project, provider, model), credentials.ERROR_CONFIG)
            self.assertEqual(path.read_bytes(), before)

    def test_malformed_config_never_defaults_or_accepts_key_fields(self):
        path = self.project / "state" / "agent_runtime.json"
        path.parent.mkdir()
        for value in ("not-json", "[]", "{}", "null", "x" * 4097,
                      json.dumps({"version": True, "provider": "codex", "model": None}),
                      json.dumps({"version": 1, "provider": "deepseek", "model": "deepseek-flash", "api_key": SYNTHETIC_KEY}),
                      json.dumps({"version": 2, "provider": "codex", "model": None})):
            path.write_text(value, encoding="utf-8")
            self.assert_safe_error(lambda: credentials.get_provider_config(self.project), credentials.ERROR_CONFIG)

    def test_duplicate_json_fields_do_not_silently_select_codex(self):
        path = self.project / "state" / "agent_runtime.json"
        path.parent.mkdir()
        path.write_text('{"version":1,"provider":"deepseek","provider":"codex","model":null}', encoding="utf-8")
        self.assert_safe_error(lambda: credentials.get_provider_config(self.project), credentials.ERROR_CONFIG)
        with self.assertRaises(ValueError):
            credentials._strict_json('{"key":"synthetic-first","key":"synthetic-second"}')

    def test_inaccessible_config_and_credential_never_mean_missing(self):
        class Inaccessible:
            def stat(self):
                raise PermissionError(SYNTHETIC_KEY)
        with patch.object(credentials, "_config_path", return_value=Inaccessible()):
            self.assert_safe_error(lambda: credentials.get_provider_config(self.project), credentials.ERROR_CONFIG)
        with patch.object(credentials, "credential_path", return_value=Inaccessible()):
            self.assert_safe_error(lambda: credentials.get_api_key(self.project), credentials.ERROR_READ)

    def test_config_write_failure_preserves_previous_config(self):
        credentials.set_provider_config(self.project, "codex", None)
        path = self.project / "state" / "agent_runtime.json"
        previous = path.read_bytes()
        with patch.object(credentials.os, "replace", side_effect=OSError(SYNTHETIC_KEY)):
            self.assert_safe_error(lambda: credentials.set_provider_config(self.project, "deepseek", "deepseek-flash"), credentials.ERROR_CONFIG_SAVE)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual([item.name for item in path.parent.iterdir()], ["agent_runtime.json"])

    def test_oversized_or_empty_encrypted_file_fails_without_dpapi(self):
        path = credentials.credential_path(self.project)
        path.parent.mkdir(parents=True)
        for value in (b"", b"x" * 65537):
            path.write_bytes(value)
            with patch.object(credentials, "_dpapi", side_effect=AssertionError("must not decrypt invalid file size")):
                self.assert_safe_error(lambda: credentials.get_api_key(self.project), credentials.ERROR_READ)

    def test_corrupt_or_wrong_project_payload_fails_with_fixed_message(self):
        path = credentials.credential_path(self.project)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic-encrypted-placeholder")
        for payload in (b"not-json", b"[]", b"\xff", json.dumps({"version": 1, "project": "other", "key": SYNTHETIC_KEY}).encode(),
                        json.dumps({"version": 1, "project": credentials._project_digest(self.project), "key": "bad key"}).encode()):
            with patch.object(credentials, "_dpapi", return_value=payload):
                self.assert_safe_error(lambda: credentials.get_api_key(self.project), credentials.ERROR_READ)
        with patch.object(credentials, "_dpapi", side_effect=ValueError(SYNTHETIC_KEY)):
            self.assert_safe_error(lambda: credentials.get_api_key(self.project), credentials.ERROR_READ)

    def test_store_error_does_not_leak_key_or_replace_ciphertext(self):
        path = credentials.credential_path(self.project)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"previous-encrypted-value")
        with patch.object(credentials, "_dpapi", side_effect=ValueError(SYNTHETIC_KEY)):
            self.assert_safe_error(lambda: credentials.store_api_key(self.project, SYNTHETIC_KEY), credentials.ERROR_STORE)
        self.assertEqual(path.read_bytes(), b"previous-encrypted-value")

    def test_atomic_replace_failure_preserves_old_file_and_cleans_temp(self):
        path = credentials.credential_path(self.project)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"old-ciphertext")
        with patch.object(credentials, "_dpapi", return_value=b"new-ciphertext"), patch.object(credentials.os, "replace", side_effect=OSError(SYNTHETIC_KEY)):
            self.assert_safe_error(lambda: credentials.store_api_key(self.project, SYNTHETIC_KEY), credentials.ERROR_STORE)
        self.assertEqual(path.read_bytes(), b"old-ciphertext")
        self.assertEqual([p.name for p in path.parent.iterdir()], ["deepseek.dpapi"])

    def test_invalid_store_key_never_calls_encryption(self):
        with patch.object(credentials, "_dpapi", side_effect=AssertionError("must not encrypt invalid key")):
            for value in (None, 123, b"key", "", "bad key", "x" * 513):
                self.assert_safe_error(lambda: credentials.store_api_key(self.project, value), credentials.ERROR_KEY)

    def test_blob_layout_is_native_pointer_safe(self):
        self.assertEqual(ctypes.sizeof(credentials._DATA_BLOB._fields_[0][1]), 4)
        self.assertEqual(ctypes.sizeof(credentials._DATA_BLOB._fields_[1][1]), ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(ctypes.sizeof(credentials._DATA_BLOB), 16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 8)

    @unittest.skipUnless(os.name == "nt", "real synthetic Windows CurrentUser DPAPI")
    def test_real_synthetic_dpapi_roundtrip_current_user_and_no_plaintext(self):
        path = credentials.store_api_key(self.project, SYNTHETIC_KEY)
        ciphertext = path.read_bytes()
        self.assertNotIn(SYNTHETIC_KEY.encode(), ciphertext)
        self.assertEqual(credentials.get_api_key(self.project), SYNTHETIC_KEY)
        self.assertFalse((self.project / "state").exists())
        crypt32, kernel32 = credentials._windows_api()
        self.assertIs(kernel32.LocalFree.restype, ctypes.c_void_p)
        self.assertEqual(len(crypt32.CryptProtectData.argtypes), 7)
        self.assertEqual(len(crypt32.CryptUnprotectData.argtypes), 7)
        # Same user, different canonical project must still fail closed.
        other_path = credentials.credential_path(self.other)
        other_path.parent.mkdir(parents=True)
        shutil.copyfile(path, other_path)
        self.assert_safe_error(lambda: credentials.get_api_key(self.other), credentials.ERROR_READ)

    @unittest.skipUnless(os.name == "nt", "real synthetic Windows CurrentUser DPAPI")
    def test_real_dpapi_corruption_and_atomic_replacement(self):
        path = credentials.store_api_key(self.project, SYNTHETIC_KEY)
        first = path.read_bytes()
        credentials.store_api_key(self.project, "synthetic-only-replacement-key")
        self.assertNotEqual(path.read_bytes(), first)
        self.assertEqual(credentials.get_api_key(self.project), "synthetic-only-replacement-key")
        corrupted = bytearray(path.read_bytes())
        corrupted[len(corrupted) // 2] ^= 1
        path.write_bytes(corrupted)
        self.assert_safe_error(lambda: credentials.get_api_key(self.project), credentials.ERROR_READ)

    def test_cli_stores_before_config_and_never_prints_key(self):
        events = []
        with patch.object(credentials.getpass, "getpass", return_value=SYNTHETIC_KEY), \
                patch.object(credentials, "store_api_key", side_effect=lambda project, key: events.append(("store", key))), \
                patch.object(credentials, "set_provider_config", side_effect=lambda project, provider, model: events.append(("config", provider, model))):
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                status = credentials.main(["--project", str(self.project), "--model", "deepseek-v4-pro"])
        self.assertEqual(status, 0)
        self.assertEqual(events, [("store", SYNTHETIC_KEY), ("config", "deepseek", "deepseek-v4-pro")])
        self.assertEqual(output.getvalue().strip(), credentials.SUCCESS_CLI)
        self.assertNotIn(SYNTHETIC_KEY, output.getvalue())

    def test_cli_rejects_argv_key_without_echoing_it(self):
        for args in (["--key", SYNTHETIC_KEY], ["--model", SYNTHETIC_KEY], ["--key-file", SYNTHETIC_KEY]):
            output = io.StringIO()
            with patch.object(credentials.getpass, "getpass", side_effect=AssertionError("must not ask on invalid args")), redirect_stdout(output), redirect_stderr(output):
                status = credentials.main(["--project", str(self.project), *args])
            self.assertEqual(status, 1)
            self.assertEqual(output.getvalue().strip(), credentials.ERROR_CLI_ARGS)
            self.assertNotIn(SYNTHETIC_KEY, output.getvalue())

    def test_cli_getpass_warning_is_error_before_plaintext_fallback(self):
        def unavailable(prompt):
            warnings.warn("synthetic hidden input unavailable", credentials.getpass.GetPassWarning)
            raise AssertionError("plaintext fallback must never execute")
        output = io.StringIO()
        with patch.object(credentials.getpass, "getpass", side_effect=unavailable), \
                patch.object(credentials, "store_api_key", side_effect=AssertionError("must not store")), redirect_stdout(output), redirect_stderr(output):
            status = credentials.main(["--project", str(self.project)])
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue().strip(), credentials.ERROR_HIDDEN_INPUT)

    def test_cli_cancel_and_unexpected_failure_are_fixed_no_tracebacks(self):
        for error, expected, status in ((KeyboardInterrupt(), credentials.ERROR_CANCELLED, 130),
                                        (EOFError(), credentials.ERROR_CANCELLED, 130),
                                        (RuntimeError(SYNTHETIC_KEY), credentials.ERROR_CLI, 1)):
            output = io.StringIO()
            with patch.object(credentials.getpass, "getpass", side_effect=error), redirect_stdout(output), redirect_stderr(output):
                actual = credentials.main(["--project", str(self.project)])
            self.assertEqual(actual, status)
            self.assertEqual(output.getvalue().strip(), expected)
            self.assertNotIn(SYNTHETIC_KEY, output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())

    def test_cli_store_failure_does_not_switch_provider(self):
        output = io.StringIO()
        with patch.object(credentials.getpass, "getpass", return_value=SYNTHETIC_KEY), \
                patch.object(credentials, "store_api_key", side_effect=credentials.CredentialError(credentials.ERROR_STORE)), \
                patch.object(credentials, "set_provider_config") as setting, redirect_stdout(output), redirect_stderr(output):
            status = credentials.main(["--project", str(self.project)])
        setting.assert_not_called()
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue().strip(), credentials.ERROR_STORE)

    @unittest.skipUnless(os.name == "nt", "real synthetic Windows CurrentUser CLI")
    def test_cli_real_synthetic_roundtrip_only_ciphertext_and_safe_config(self):
        with patch.object(credentials.getpass, "getpass", return_value=SYNTHETIC_KEY), redirect_stdout(io.StringIO()):
            status = credentials.main(["--project", str(self.project)])
        self.assertEqual(status, 0)
        self.assertEqual(credentials.get_api_key(self.project), SYNTHETIC_KEY)
        self.assertEqual(credentials.get_provider_config(self.project), {"version": 1, "provider": "deepseek", "model": "deepseek-flash"})
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(SYNTHETIC_KEY.encode(), path.read_bytes())


if __name__ == "__main__":
    unittest.main()
