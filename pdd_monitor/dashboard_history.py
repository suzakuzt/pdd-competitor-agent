"""Retain independent static history files across the canonical dist replacement."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile


class HistoryRetentionError(ValueError):
    """History could not be moved safely; retained evidence must not be removed."""


def _regular(path, *, directory):
    info = path.lstat()
    if (path.is_symlink()
            or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise HistoryRetentionError(f'History retention requires an ordinary unlinked path: {path}')
    return info


def _directory_chain(path):
    for ancestor in reversed((path, *path.parents)):
        if os.path.lexists(ancestor):
            _regular(ancestor, directory=True)


def _identity(path):
    info = _regular(path, directory=True)
    return info.st_dev, info.st_ino


def _ordinary_tree(path):
    _regular(path, directory=True)
    for folder, directories, files in os.walk(path, followlinks=False):
        for name in directories:
            _regular(Path(folder) / name, directory=True)
        for name in files:
            _regular(Path(folder) / name, directory=False)


def _inside(app, path):
    if not path.is_absolute() or path == app or not path.resolve().is_relative_to(app):
        raise HistoryRetentionError(f'History retention path escapes the dashboard app: {path}')


@contextmanager
def retain_static_history(app):
    """Move old history out of dist, then restore it without merging or copying.

    Failed restoration preserves its holding directory for explicit recovery.
    Only an empty holding directory is removed after a successful restoration.
    """
    app = Path(app).expanduser()
    if not app.is_absolute():
        raise HistoryRetentionError('History retention requires an absolute dashboard app path')
    app = Path(os.path.abspath(app))
    _directory_chain(app)
    app_identity = _identity(app)
    dist, history = app / 'dist', app / 'dist/history-exports'
    _directory_chain(history)
    _inside(app, history)
    if not os.path.lexists(history):
        yield
        return
    _ordinary_tree(history)
    history_identity = _identity(history)
    holding = Path(tempfile.mkdtemp(prefix='.history-exports-retained-', dir=app))
    _directory_chain(holding)
    _inside(app, holding)
    holding_identity = _identity(holding)
    retained = holding / 'history-exports'
    _inside(app, retained)
    try:
        history.rename(retained)
    except BaseException:
        # No recursive cleanup: an unexpected occupant is evidence, not trash.
        if not any(holding.iterdir()):
            holding.rmdir()
        raise
    try:
        yield
    finally:
        try:
            _directory_chain(app)
            if _identity(app) != app_identity:
                raise HistoryRetentionError('Dashboard app directory changed during its build')
            _directory_chain(retained)
            _inside(app, retained)
            if _identity(holding) != holding_identity or _identity(retained) != history_identity:
                raise HistoryRetentionError('Retained history directory was replaced during its build')
            _ordinary_tree(retained)
            _directory_chain(dist)
            _inside(app, history)
            if os.path.lexists(history):
                raise HistoryRetentionError('New dist already contains history-exports; neither copy was overwritten')
            if not os.path.lexists(dist):
                dist.mkdir()
            _regular(dist, directory=True)
            retained.rename(history)
        except Exception as error:
            raise HistoryRetentionError(f'History restoration stopped; original files retained at {retained}: {error}') from error
        # rmdir cannot delete unrecognized files left in the holding directory.
        if not any(holding.iterdir()):
            holding.rmdir()
