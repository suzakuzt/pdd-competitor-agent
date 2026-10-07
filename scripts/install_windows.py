"""Install the verified delivery into an empty Windows directory.

Python 3.10+, standard library only. Existing directories with content are never
overwritten. Running against the extracted source directory only validates it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile


PROJECT = "PDDCompetitorAgent"
DEFAULT_TARGET = r"C:\Projects\PDDCompetitorAgent"
IGNORED_PARTS = {"__pycache__", ".git", ".pytest_cache"}
REQUIRED_FILES = {
    "pdd_monitor/__main__.py",
    "data/monitor.sqlite3",
    "data/images.sqlite3",
}


class InstallError(Exception):
    def __init__(self, message: str, exit_code: int = 4) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InstallError(f"Duplicate manifest key: {key}")
        result[key] = value
    return result


def read_manifest(root: Path) -> dict[str, str]:
    path = root / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise InstallError(f"Missing regular manifest file: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"Cannot read manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("project") != PROJECT:
        raise InstallError(f"manifest.json must identify project {PROJECT}")
    entries = manifest.get("files")
    if not isinstance(entries, dict) or not entries:
        raise InstallError("manifest.json must contain a nonempty 'files' SHA-256 mapping.")
    result: dict[str, str] = {}
    windows_names: set[str] = set()
    for name, digest in entries.items():
        if not isinstance(name, str) or not name:
            raise InstallError("Invalid manifest path.")
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or relative.as_posix() != name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or any(char in name for char in "\\:\x00")
            or name == "manifest.json"
            or any(part in IGNORED_PARTS for part in relative.parts)
            or relative.suffix in {".pyc", ".pyo"}
        ):
            raise InstallError(f"Unsafe or excluded manifest path: {name}")
        folded = name.casefold()
        if folded in windows_names:
            raise InstallError(f"Windows filename collision in manifest: {name}")
        windows_names.add(folded)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise InstallError(f"Invalid SHA-256 value for: {name}")
        result[name] = digest.lower()
    missing = REQUIRED_FILES - result.keys()
    if missing:
        raise InstallError("Required files absent from manifest: " + ", ".join(sorted(missing)))
    return result


def package_files(root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise InstallError(f"Symbolic links are not supported in the package: {relative}")
        if path.is_file() and relative.as_posix() != "manifest.json":
            names.add(relative.as_posix())
    return names


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_package(root: Path) -> dict[str, str]:
    entries = read_manifest(root)
    actual = package_files(root)
    missing = entries.keys() - actual
    extra = actual - entries.keys()
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)[:8]))
        if extra:
            details.append("not in manifest: " + ", ".join(sorted(extra)[:8]))
        raise InstallError("Package file inventory differs from manifest (" + "; ".join(details) + ").")
    for name, expected in entries.items():
        if sha256_file(root.joinpath(*PurePosixPath(name).parts)) != expected:
            raise InstallError(f"SHA-256 mismatch: {name}")
    print(f"Verified {len(entries)} files against manifest.json.")
    return entries


def windows_target(raw: str) -> Path:
    if os.name != "nt":
        raise InstallError(
            "This installer requires Windows. No D: directory was created. "
            "Extract the delivery on Windows and run INSTALL_WINDOWS.cmd there.",
            2,
        )
    proposed = PureWindowsPath(raw)
    if not proposed.is_absolute() or re.fullmatch(r"[A-Za-z]:", proposed.drive) is None:
        raise InstallError("Target must be an absolute local Windows path, such as " + DEFAULT_TARGET, 2)
    drive = Path(proposed.anchor)
    if not drive.is_dir():
        raise InstallError(f"Target drive is unavailable: {drive}. No software or drive will be installed.", 2)
    target = Path(raw).resolve()
    if target == drive.resolve():
        raise InstallError("A drive root cannot be the project target. Choose a project subdirectory.", 2)
    return target


def refuse_nonempty_target(target: Path) -> None:
    if not target.exists():
        return
    if not target.is_dir():
        raise InstallError(f"Target exists and is not a directory: {target}", 3)
    if any(target.iterdir()):
        existing_databases = [
            str(target / "data" / name)
            for name in ("monitor.sqlite3", "images.sqlite3")
            if (target / "data" / name).exists()
        ]
        detail = " Existing database files are protected." if existing_databases else " Existing files are protected."
        raise InstallError(
            f"Refusing to overwrite the nonempty target: {target}.{detail} "
            "Choose an empty directory with --target, or keep using the existing project.",
            3,
        )


def validate_database(root: Path) -> None:
    command = [sys.executable, "-m", "pdd_monitor", "--data-dir", str(root / "data"), "validate"]
    print("Running database acceptance checks in: " + str(root), flush=True)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run(command, cwd=root, env=environment, check=False, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"Database validation could not finish: {exc}. The copied files were kept.", 5) from exc
    if completed.returncode != 0:
        raise InstallError(
            f"Database validation failed with exit code {completed.returncode}. "
            "The copied files were kept for inspection. Do not treat this installation as accepted.",
            5,
        )


def install(source: Path, target: Path) -> None:
    entries = verify_package(source)
    if target == source:
        print("Target is the extracted project directory; validating in place without copying.")
        validate_database(target)
        return
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise InstallError("Source and target must not be nested inside one another. Choose a separate empty directory.", 3)
    refuse_nonempty_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = Path(tempfile.mkdtemp(prefix=f".{target.name}.install-", dir=target.parent))
    try:
        for name in entries:
            relative = Path(*PurePosixPath(name).parts)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
        shutil.copy2(source / "manifest.json", staging / "manifest.json")
        verify_package(staging)
        # Recheck immediately before the move; an existing populated folder is
        # never replaced, even if it appeared while the copy was in progress.
        refuse_nonempty_target(target)
        if target.exists():
            target.rmdir()  # Only an empty target can reach this line.
        staging.rename(target)
        staging = None
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
    validate_database(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET, help="Empty Windows destination (default: %(default)s)")
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 10):
            raise InstallError("Python 3.10 or newer is required. Install Python yourself, then run this command again.", 2)
        target = windows_target(args.target)
        source = Path(__file__).resolve().parent.parent
        install(source, target)
        print("Installation accepted: " + str(target))
        print("Next: open a terminal in that directory and run python -m pdd_monitor --data-dir data summary")
        return 0
    except InstallError as exc:
        print(f"ERROR [{exc.exit_code}]: {exc}", file=sys.stderr)
        return exc.exit_code
    except (OSError, shutil.Error) as exc:
        print(f"ERROR [6]: File operation failed: {exc}. Existing project data was not overwritten.", file=sys.stderr)
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
