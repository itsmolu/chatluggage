"""Check interpreter selection without touching a Codex profile."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest

from cli import VERSION
from platform_paths import temporary_directory


ROOT = Path(__file__).resolve().parents[1]
OLD_SQLITE = """
import runpy
import sqlite3
import sys
sqlite3.sqlite_version = '3.40.1'
sqlite3.sqlite_version_info = (3, 40, 1)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""


def bash_executable():
    if sys.platform == 'win32':
        git = shutil.which('git')
        if git:
            candidate = Path(git).parent.parent / 'bin/bash.exe'
            if candidate.is_file():
                return str(candidate)
        return None  # Do not auto-start WSL to run tests.
    return '/bin/bash' if Path('/bin/bash').is_file() else None


class PythonProbeTests(unittest.TestCase):
    def test_old_python_error_shows_actual_version_and_executable(self):
        code = 'import sys,runpy; sys.version_info=(3,9,6); runpy.run_path(sys.argv[1],run_name="__main__")'
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'cli.py')],
                                capture_output=True, encoding='utf-8',
                                env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'CODEX_MIGRATION_PROBED': '1'}, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn('3.9.6', result.stderr)
        self.assertIn(sys.executable, result.stderr)
        self.assertIn('sqlite3', result.stderr)

    def test_probe_rejects_old_sqlite_and_allows_file_only_recovery(self):
        command = [sys.executable, '-B', '-c', OLD_SQLITE, str(ROOT / 'cli.py'), '--probe-python']
        blocked = subprocess.run(command + ['export'], capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(blocked.returncode, 1, blocked.stderr)
        self.assertIn('SQLite 3.40.1', blocked.stdout)
        self.assertIn('SQLite 3.41.0', blocked.stdout)
        recovery = subprocess.run(command + ['recover'], capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(recovery.returncode, 0, recovery.stderr)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows interpreter discovery')
    def test_windows_cli_retries_with_supported_sqlite(self):
        root = self.enterContext(temporary_directory(ROOT, '.python-entry-'))
        code = """
import os
from pathlib import Path
import shutil
import sys
python = Path(sys.executable)
Path.home = lambda: Path(sys.argv[1])
shutil.which = lambda name: None
os.get_exec_path = lambda env=None: [str(python.parent)]
sys.executable = str(Path(sys.argv.pop(1)) / 'unsupported-python.exe')
""" + OLD_SQLITE
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        env.pop('CODEX_MIGRATION_PROBED', None)
        result = subprocess.run([sys.executable, '-B', '-c', code, str(root), str(ROOT / 'cli.py'), '--version'],
                                env=env, capture_output=True, encoding='utf-8', timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), VERSION)
        self.assertIn(sys.executable, result.stderr)
        self.assertIn('SQLite ', result.stderr)


class PythonEntryTests(unittest.TestCase):
    def setUp(self):
        self.bash = bash_executable()
        if not self.bash:
            self.skipTest('Bash is unavailable; no shell will be installed')
        self.root = self.enterContext(temporary_directory(ROOT, '.python-entry-'))
        self.home = self.root / 'home with spaces'
        self.home.mkdir()
        self.env = {**os.environ, 'HOME': self.home.as_posix(), 'PYTHONIOENCODING': 'utf-8'}
        self.env.pop('CODEX_MIGRATION_PYTHON', None)

    def wrapper(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/bash\n' + content + '\n', encoding='utf-8', newline='\n')
        path.chmod(0o755)
        return path

    def run_launcher(self, *args):
        return subprocess.run([self.bash, '--noprofile', '--norc', str(ROOT / 'mac.sh'), *args],
                              cwd=self.root, env=self.env, capture_output=True, encoding='utf-8', timeout=20)

    def test_existing_runtime_is_used_instead_of_path_python(self):
        runtime = self.home / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
        self.wrapper(runtime, 'exec ' + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"')
        old = self.root / 'old path/python3'
        self.wrapper(old, 'echo wrong-python-was-used >&2\nexit 9')
        self.env['PATH'] = old.parent.as_posix() + os.pathsep + self.env['PATH']
        result = self.run_launcher('--version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), VERSION)
        self.assertNotIn('wrong-python-was-used', result.stderr)
        self.assertIn('SQLite ', result.stderr)

    def test_broken_runtime_falls_through_to_version_manager(self):
        old = self.home / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
        self.wrapper(old, 'echo "Python 3.9.6"; exit 1')
        usable = self.home / 'venv/bin/python3'
        self.wrapper(usable, 'exec ' + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"')
        self.env['VIRTUAL_ENV'] = (self.home / 'venv').as_posix()
        result = self.run_launcher('--version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), VERSION)
        self.assertIn('/venv/bin/python3', result.stderr)

    def test_old_mac_cli_forwards_original_arguments_to_launcher(self):
        code = """import json, os, runpy, sys
script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.version_info = (3, 9, 6)
sys.platform = 'darwin'
def capture(program, args):
    print(json.dumps(args))
    raise SystemExit(0)
os.execv = capture
runpy.run_path(script, run_name='__main__')
"""
        import json
        args = ['plan', 'file with spaces.zip', '--pick', '1,3-5']
        env = dict(self.env)
        env.pop('CODEX_MIGRATION_PROBED', None)
        result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'cli.py'), *args],
                                env=env, capture_output=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['/bin/bash', str(ROOT / 'mac.sh'), *args])

    def test_invalid_explicit_interpreter_stops_with_its_reason(self):
        old = self.wrapper(self.root / 'old-python', 'echo "Python 3.9.6; need 3.11"\nexit 1')
        self.env['CODEX_MIGRATION_PYTHON'] = old.as_posix()
        result = self.run_launcher('--version')
        self.assertEqual(result.returncode, 1)
        self.assertIn('3.9.6', result.stderr)
        self.assertNotIn(VERSION, result.stdout)

    def test_old_sqlite_candidate_falls_through_to_supported_python(self):
        python = shlex.quote(Path(sys.executable).as_posix())
        old = self.home / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
        self.wrapper(old, 'shift\nexec ' + python + ' -B -c ' + shlex.quote(OLD_SQLITE) + ' "$@"')
        env = self.root / 'supported env'
        good = self.wrapper(env / 'bin/python3', 'exec ' + python + ' "$@"')
        self.env['VIRTUAL_ENV'] = env.as_posix()
        result = self.run_launcher('--version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), VERSION)
        self.assertIn(good.as_posix(), result.stderr)
        self.assertNotIn(old.as_posix(), result.stderr)

    def test_explicit_python_handles_an_entry_path_outside_the_working_directory(self):
        self.env['CODEX_MIGRATION_PYTHON'] = Path(sys.executable).as_posix()
        syntax = subprocess.run([self.bash, '-n', str(ROOT / 'mac.sh')], capture_output=True, timeout=10)
        self.assertEqual(syntax.returncode, 0)
        result = self.run_launcher('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('plan', result.stdout)
        self.assertIn('apply', result.stdout)


if __name__ == '__main__':
    unittest.main()
