"""Local provider settings and project-bound Windows CurrentUser DPAPI storage.

No network calls, secret logging, provider fallback, or plaintext key files.
The environment override is explicit; an invalid override does not fall back.
DPAPI protects at Windows-user scope. It is not a boundary against another
process running as that same user. Moving the project changes its credential
namespace, so credentials must be configured again after a move.
"""
from __future__ import annotations

import ctypes
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import warnings

DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")
DEFAULT_CONFIG = {"version": 1, "provider": "codex", "model": None}
MAX_KEY_BYTES = 512
MAX_CIPHERTEXT_BYTES = 65536
CRYPTPROTECT_UI_FORBIDDEN = 0x1

ERROR_PATH = "无法确定本项目的本机凭据位置。"
ERROR_KEY = "API 密钥格式无效，请重新配置。"
ERROR_READ = "无法读取或解密本项目的本机凭据，请重新配置。"
ERROR_STORE = "无法安全保存本项目的本机凭据。"
ERROR_PLATFORM = "本机加密凭据仅支持 Windows 当前用户。"
ERROR_CONFIG = "Agent 提供方配置无效，请检查提供方和模型设置。"
ERROR_CONFIG_SAVE = "无法保存 Agent 提供方配置。"
ERROR_CLI_ARGS = "参数无效。请使用 --project 指定项目，可选 --model 指定支持的 DeepSeek 模型。"
ERROR_HIDDEN_INPUT = "当前终端无法隐藏输入，已取消；请在支持隐藏输入的本机终端重试。"
ERROR_CANCELLED = "已取消凭据配置。"
ERROR_CLI = "凭据配置未完成，请检查本机加密支持和项目设置后重试。"
SUCCESS_CLI = "API 密钥已加密保存，DeepSeek 设置已更新。请仅重启本项目服务使设置生效。"


class CredentialError(ValueError):
    """Only fixed, non-sensitive public messages are raised by this module."""


class _DATA_BLOB(ctypes.Structure):
    # DWORD remains 32-bit on 64-bit Windows. Pointers use native pointer size.
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _project_root(project) -> Path:
    if not isinstance(project, (str, os.PathLike)) or not str(project).strip():
        raise CredentialError(ERROR_PATH)
    root = Path(project).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise CredentialError(ERROR_PATH)
    return root


def _project_digest(project) -> str:
    canonical = os.path.normcase(os.path.normpath(str(_project_root(project))))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def credential_path(project) -> Path:
    """Return the encrypted file location without creating it or reading a key."""
    try:
        local = os.environ.get("LOCALAPPDATA")
        if not local or not Path(local).is_absolute():
            raise CredentialError(ERROR_PATH)
        base = Path(local).resolve()
        return base / "PDDCompetitorAgent" / "credentials" / _project_digest(project)[:24] / "deepseek.dpapi"
    except Exception:
        raise CredentialError(ERROR_PATH) from None


def _validate_key(key) -> str:
    if type(key) is not str:
        raise CredentialError(ERROR_KEY)
    value = key.strip()
    if not value or len(value) > MAX_KEY_BYTES or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise CredentialError(ERROR_KEY)
    return value


def _windows_api():
    if os.name != "nt":
        raise CredentialError(ERROR_PLATFORM)
    # ctypes uses secure DLL loading defaults on supported Python versions;
    # these are Windows system DLLs, never project-local executable modules.
    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DATA_BLOB)
    crypt32.CryptProtectData.argtypes = [blob_pointer, ctypes.c_wchar_p, blob_pointer,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, blob_pointer]
    crypt32.CryptProtectData.restype = ctypes.c_int32
    crypt32.CryptUnprotectData.argtypes = [blob_pointer, ctypes.POINTER(ctypes.c_wchar_p), blob_pointer,
                                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, blob_pointer]
    crypt32.CryptUnprotectData.restype = ctypes.c_int32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _blob(data: bytes):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi(data: bytes, entropy: bytes, *, decrypt: bool) -> bytes:
    crypt32, kernel32 = _windows_api()
    source, source_buffer = _blob(data)
    extra, extra_buffer = _blob(entropy)
    output = _DATA_BLOB()
    try:
        operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        # No description is requested/returned; only the output BLOB needs LocalFree.
        # LOCAL_MACHINE is intentionally never enabled: default scope is CurrentUser.
        success = operation(ctypes.byref(source), None, ctypes.byref(extra), None, None,
                            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output))
        if not success or not output.pbData or not 0 < output.cbData <= MAX_CIPHERTEXT_BYTES:
            raise CredentialError(ERROR_READ if decrypt else ERROR_STORE)
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            if decrypt and output.cbData:
                ctypes.memset(output.pbData, 0, output.cbData)
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
        # Best-effort removal of mutable native buffers. Python's returned str/
        # bytes cannot be promised securely erased and must never be logged.
        ctypes.memset(source_buffer, 0, len(source_buffer))
        ctypes.memset(extra_buffer, 0, len(extra_buffer))


def _entropy(project) -> bytes:
    return b"PDDCompetitorAgent:deepseek:v1:" + bytes.fromhex(_project_digest(project))


def _strict_json(raw):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique_pairs)


def _atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".agent-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def store_api_key(project, key) -> Path:
    """Atomically replace only this project's encrypted credential; return its path.

    This does not set a provider/model or modify the environment. The caller
    must not include the input key or exceptions from unrelated code in logs.
    """
    value = _validate_key(key)
    try:
        path = credential_path(project)
        payload = json.dumps({"version": 1, "project": _project_digest(project), "key": value},
                             separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        encrypted = _dpapi(payload, _entropy(project), decrypt=False)
        _atomic_write(path, encrypted)
        return path
    except Exception:
        raise CredentialError(ERROR_STORE) from None


def get_api_key(project) -> str | None:
    """Read env override first, otherwise decrypt this project's local credential.

    None means not configured. Malformed/undecryptable credentials raise a safe
    error; they never change provider or silently use another project's key.
    """
    if "DEEPSEEK_API_KEY" in os.environ:
        return _validate_key(os.environ["DEEPSEEK_API_KEY"])
    try:
        path = credential_path(project)
        try:
            information = path.stat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(information.st_mode) or not 0 < information.st_size <= MAX_CIPHERTEXT_BYTES:
            raise CredentialError(ERROR_READ)
        with path.open("rb") as handle:
            encrypted = handle.read(MAX_CIPHERTEXT_BYTES + 1)
        if not 0 < len(encrypted) <= MAX_CIPHERTEXT_BYTES:
            raise CredentialError(ERROR_READ)
        raw = _dpapi(encrypted, _entropy(project), decrypt=True)
        payload = _strict_json(raw.decode("utf-8"))
        if (not isinstance(payload, dict) or set(payload) != {"version", "project", "key"}
                or type(payload["version"]) is not int or payload["version"] != 1
                or payload["project"] != _project_digest(project)):
            raise CredentialError(ERROR_READ)
        return _validate_key(payload["key"])
    except Exception:
        raise CredentialError(ERROR_READ) from None


def _config_path(project) -> Path:
    root = _project_root(project)
    path = root / "state" / "agent_runtime.json"
    # A relocated state symlink/junction must not write outside this project.
    if not path.parent.resolve().is_relative_to(root) or path.is_symlink():
        raise CredentialError(ERROR_CONFIG)
    return path


def _validate_config(config) -> dict:
    if (not isinstance(config, dict) or set(config) != {"version", "provider", "model"}
            or type(config["version"]) is not int or config["version"] != 1):
        raise CredentialError(ERROR_CONFIG)
    provider, model = config["provider"], config["model"]
    if provider not in ("codex", "deepseek"):
        raise CredentialError(ERROR_CONFIG)
    if provider == "deepseek":
        if model not in DEEPSEEK_MODELS:
            raise CredentialError(ERROR_CONFIG)
    elif model is not None and (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", model)):
        raise CredentialError(ERROR_CONFIG)
    return {"version": 1, "provider": provider, "model": model}


def get_provider_config(project) -> dict:
    """Return validated settings; missing file means Codex with no model override."""
    try:
        path = _config_path(project)
        try:
            information = path.stat()
        except FileNotFoundError:
            return dict(DEFAULT_CONFIG)
        if not stat.S_ISREG(information.st_mode) or information.st_size > 4096:
            raise CredentialError(ERROR_CONFIG)
        with path.open("rb") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            raise CredentialError(ERROR_CONFIG)
        return _validate_config(_strict_json(raw.decode("utf-8-sig")))
    except Exception:
        raise CredentialError(ERROR_CONFIG) from None


def set_provider_config(project, provider, model) -> dict:
    """Write only {version, provider, model}; credentials are never serialized."""
    try:
        if provider == "deepseek" and model is None:
            model = DEEPSEEK_MODELS[0]
        config = _validate_config({"version": 1, "provider": provider, "model": model})
        _atomic_write(_config_path(project), (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return dict(config)
    except CredentialError:
        raise CredentialError(ERROR_CONFIG) from None
    except Exception:
        raise CredentialError(ERROR_CONFIG_SAVE) from None


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default message echoes unknown argv, which might contain a
        # mistakenly supplied key. Never interpolate parser input into output.
        raise CredentialError(ERROR_CLI_ARGS)


def main(argv=None) -> int:
    """Interactive setup: key comes only from getpass, never argv or a file."""
    try:
        parser = _SafeParser(description="通过隐藏输入设置本项目的 DeepSeek 加密凭据。")
        parser.add_argument("--project", required=True, help="已存在的项目目录")
        parser.add_argument("--model", choices=DEEPSEEK_MODELS, default=DEEPSEEK_MODELS[0], help="DeepSeek 模型")
        args = parser.parse_args(argv)
        # Resolve the destination before asking for sensitive input. Do not read
        # an existing credential or use DEEPSEEK_API_KEY during interactive setup.
        credential_path(args.project)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("请输入 DeepSeek API 密钥（输入不显示）：")
        try:
            store_api_key(args.project, key)
        finally:
            key = None  # Avoid retaining an unnecessary reference after use.
        set_provider_config(args.project, "deepseek", args.model)
        print(SUCCESS_CLI)
        return 0
    except (KeyboardInterrupt, EOFError):
        print(ERROR_CANCELLED)
        return 130
    except getpass.GetPassWarning:
        print(ERROR_HIDDEN_INPUT)
        return 1
    except CredentialError as error:
        # Public module errors contain only the fixed messages defined above.
        print(str(error))
        return 1
    except Exception:
        print(ERROR_CLI)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
