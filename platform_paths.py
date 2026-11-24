"""Use extended Windows paths only at filesystem boundaries, never in portable metadata."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


def plain_path(path):
    value = os.fspath(path)
    if os.name == 'nt':
        if value.upper().startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
    return Path(value)


def io_path(path):
    path = Path(path)
    if os.name != 'nt':
        return path
    value = os.path.abspath(path)
    if value.startswith('\\\\?\\'):
        return Path(value)
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


@contextmanager
def temporary_directory(parent, prefix):
    # Cleanup also needs the extended path: session side files can exceed MAX_PATH.
    with tempfile.TemporaryDirectory(dir=io_path(parent), prefix=prefix) as value:
        yield plain_path(value)
