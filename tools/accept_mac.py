"""Check the shipping migration core on temporary profiles using a fictional Windows bundle."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from verify_installed_schema import app_server, initialize, seed
from history_migration import HISTORY, RECOVERY_DIR, STATE, MigrationError, fingerprint, json_bytes, require
from migration_workflow import PENDING, assert_profile_files_closed, install_native, plan_native, recover_native
from windows_export import export_windows, portable_root


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = 'windows-fictional.zip'
WIN_ID = '00000000-0000-4000-8000-000000000912'
MAC_ID = '00000000-0000-4000-8000-000000000911'
PROJECT_ID = '00000000-0000-4000-8000-000000000910'
WIN_MESSAGE = 'Fictional Windows question for migration acceptance'
WIN_REPLY = 'Fictional Windows reply for migration acceptance'
MAC_MESSAGE = 'Fictional Mac question for migration acceptance'
MAC_REPLY = 'Fictional Mac reply for migration acceptance'


class Interrupted(BaseException):
    """Stop after replacing the state DB and leave the pending recovery record in place."""


def installed_codex(override):
    if override:
        return str(override.resolve(strict=True)), None
    if sys.platform == 'win32':
        exe = shutil.which('codex.exe')
        if exe:
            return exe, None
        ps = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(ps), '-NoProfile', '-NonInteractive', '-Command',
                                 '[Console]::OutputEncoding = [Text.Encoding]::UTF8; '
                                 'Get-AppxPackage -Name OpenAI.Codex | Select-Object -ExpandProperty InstallLocation'],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=15)
        if result.returncode == 0:
            for folder in result.stdout.splitlines():
                if folder.strip():
                    candidate = Path(folder.strip()) / 'app/resources/codex.exe'
                    if candidate.is_file():
                        return str(candidate), None
        raise MigrationError('Could not find the installed Codex CLI. Pass its executable with --codex.')
    require(sys.platform == 'darwin', 'On a development host, pass --codex for the installed CLI')
    folders = (Path('/Applications'), Path.home() / 'Applications')
    bundles = [folder / f'{name}.app' for folder in folders for name in ('ChatGPT', 'Codex')]
    for bundle in bundles:
        info = bundle / 'Contents' / 'Info.plist'
        if not info.is_file():
            continue
        with info.open('rb') as stream:
            metadata = plistlib.load(stream)
        if metadata.get('CFBundleIdentifier') != 'com.openai.codex':
            continue
        resources = bundle / 'Contents' / 'Resources'
        candidates = [resources / path for path in ('codex-cli/bin/codex', 'codex', 'bin/codex', 'resources/codex')]
        candidates.extend(sorted(resources.glob('*/bin/codex')))
        for exe in candidates:
            if exe.is_file() and os.access(exe, os.X_OK):
                return str(exe), metadata.get('CFBundleShortVersionString')
    # Also handles a renamed app or separately installed CLI without requiring a bundle path.
    path = shutil.which('codex')
    require(path, 'Could not locate the local Codex CLI; pass its executable with --codex')
    return path, None


def tree(target):
    return {key: None if item.is_dir() else fingerprint(item) for item in target.rglob('*')
            if (key := item.relative_to(target).as_posix()).split('/')[0] != RECOVERY_DIR}


def build_fixture(out, executable, scratch):
    require(sys.platform == 'win32', 'Generate the fictional Windows bundle on the Windows build host')
    with tempfile.TemporaryDirectory(prefix='.test-mac-bundle-', dir=scratch) as path:
        base = Path(path)
        source = base / 'win'
        initialize(executable, source, base / 'server')
        seed(source, PROJECT_ID, WIN_MESSAGE, tid=WIN_ID, reply=WIN_REPLY)
        report = export_windows(source, out)
    require(report['threads'] == report['turns'] == report['projects'] == 1, 'Unexpected fixture inventory')
    return report


def check_client(exe, target, scratch, expected_workspace):
    with app_server(exe, target, scratch) as call:
        ids = {row['id'] for row in call('thread/list', {'limit': 8, 'currentAccountOnly': False,
                                                        'modelProviders': []})['data']}
        require(ids == {MAC_ID, WIN_ID}, 'Isolated Codex did not list exactly the two fictional chats')
        for thread_id, user, reply in ((MAC_ID, MAC_MESSAGE, MAC_REPLY), (WIN_ID, WIN_MESSAGE, WIN_REPLY)):
            turns = call('thread/turns/list', {'threadId': thread_id, 'limit': 5, 'itemsView': 'full'})['data']
            require(len(turns) == 1 and turns[0]['status'] == 'completed', 'Isolated Codex did not read a completed turn')
            items = turns[0]['items']
            require(len(items) == 2 and items[0]['type'] == 'userMessage' and items[1]['type'] == 'agentMessage' and
                    items[0]['content'][0]['text'] == user and items[1]['text'] == reply,
                    'Isolated Codex could not read the expected fictional conversation bodies')
        imported = call('thread/read', {'threadId': WIN_ID, 'includeTurns': False})['thread']
        # thread/read describes the historical rollout's cwd. Resume returns the
        # effective cwd separately, and accepts the mapped workspace explicitly.
        resumed = call('thread/resume', {'threadId': WIN_ID, 'excludeTurns': True,
                                        'cwd': str(expected_workspace)})
        require(imported['projectId'] == PROJECT_ID and Path(resumed['cwd']).resolve() == expected_workspace,
                'The Codex client reported a different project or effective working directory')


def acceptance(exe, root, scheme, fixture_dir, set_stage):
    set_stage('verify_windows_fixture')
    package = fixture_dir / FIXTURE
    expected = (fixture_dir / 'windows-fictional.sha256').read_text(encoding='ascii').strip()
    require(fingerprint(package) == expected, 'The packaged Windows fixture failed SHA-256 verification')
    target = root / 'fixture-mac'
    tmp = root / 'scratch'
    set_stage('initialize_temporary_codex_profile')
    initialize(exe, target, tmp)
    seed(target, None, MAC_MESSAGE, tid=MAC_ID, reply=MAC_REPLY)

    def check_idle(path):
        require(path == target and path.resolve(strict=True) == target, 'The acceptance runner can only use its new temporary fixture')
        if scheme == 'macOS':
            assert_profile_files_closed(path)

    handle_probe = False
    if scheme == 'macOS':
        set_stage('verify_mac_open_file_guard')
        with (target / STATE).open('rb'):
            try:
                check_idle(target)
            except MigrationError as exc:
                if 'has a profile file open' not in str(exc):
                    raise
                handle_probe = True
        require(handle_probe, 'The Mac open-file check did not detect the held fixture file')
    check_idle(target)
    before = tree(target)
    workspace = root / 'mapped-project'
    workspace.mkdir()
    mapping = {portable_root(PROJECT_ID, 0): str(workspace)}

    def plan():
        return plan_native(package, target, mapping, idle=check_idle, scratch=root)

    set_stage('plan_without_writing')
    original_plan = plan()
    require(tree(target) == before and original_plan['added_threads'] == original_plan['added_turns'] == 1,
            'Planning modified the fixture or found unexpected additions')

    def interrupt(name):
        if name == STATE:
            raise Interrupted

    recovered = False
    try:
        set_stage('simulate_install_interruption')
        install_native(package, target, original_plan['plan_id'], mapping, idle=check_idle, inject=interrupt, scratch=root)
    except Interrupted:
        set_stage('recover_interrupted_install')
        require((target / RECOVERY_DIR / PENDING).is_file() and tree(target) != before, 'Interruption did not leave pending writes')
        recovered = recover_native(target, idle=check_idle)['restored_originals']
        require(recovered and tree(target) == before, 'Recovery did not restore all original fixture files')
    require(recovered, 'The interruption test did not run')
    set_stage('install_and_recheck')
    second = plan()
    installed = install_native(package, target, second['plan_id'], mapping, idle=check_idle, scratch=root)
    repeat = plan()
    require(installed['target_changed'] and installed['total_threads'] == 2 and repeat['files_to_change'] == 0
            and repeat['directories_to_create'] == 0 and repeat['added_threads'] == 0, 'The re-run would import duplicate data')
    with closing(sqlite3.connect((target / HISTORY).as_uri() + '?mode=ro', uri=True)) as db:
        require(db.execute('SELECT count(*) FROM thread_items WHERE thread_id=?', (WIN_ID,)).fetchone() == (2,),
                'Missing fictional Windows message items')
    set_stage('read_back_with_local_codex')
    check_client(exe, target, tmp, workspace)
    require(fingerprint(package) == expected, 'The source fixture changed')
    return {'synthetic_only': True, 'added_threads': installed['added_threads'], 'total_threads': installed['total_threads'],
            'backup_and_recovery': 'passed', 'read_only_plan': 'passed', 'repeat_writes': repeat['files_to_change'],
            'open_file_guard': 'passed' if handle_probe else 'not_run', 'application_process_guard': 'not_run',
            'client_readback_and_project': 'passed', 'desktop_sidebar': 'not_run',
            'schemas': {name: {k: v for k, v in info.items() if k in ('migration_version', 'source_migration_version', 'source_checksums_match')}
                        for name, info in original_plan['compatibility'].items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', type=Path, help='optional installed Codex CLI executable')
    parser.add_argument('--build-fixture', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--fixture-dir', type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage = 'preflight'

    def set_stage(name):
        nonlocal stage
        stage = name

    try:
        require(sys.version_info >= (3, 11), 'Python 3.11 or newer is required')
        exe, version = installed_codex(args.codex)
        if args.build_fixture:
            stage = 'build_windows_fixture'
            sys.stdout.buffer.write(json_bytes(build_fixture(args.build_fixture.resolve(), exe, ROOT)))
            return 0
        scheme = 'macOS' if sys.platform == 'darwin' else 'development dry run'
        cli = subprocess.run([exe, '--version'], capture_output=True, encoding='utf-8', errors='replace',
                             check=True, timeout=10).stdout.strip()
        if scheme == 'macOS':
            value = subprocess.check_output(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'], timeout=10)
            require(value.strip(), 'macOS did not return a system temporary directory')
            scratch = Path(os.fsdecode(value.rstrip(b'\r\n'))).resolve(strict=True)
            require(scratch.is_relative_to(Path('/private/var/folders')), 'The temporary directory is outside the macOS system temp area')
        else:
            scratch = Path.cwd()
        with tempfile.TemporaryDirectory(prefix='.test-chm-', dir=scratch) as temp:
            result = acceptance(exe, Path(temp).resolve(strict=True), scheme, args.fixture_dir.resolve(strict=True), set_stage)
        report = {'status': 'passed', 'platform': scheme, 'system_version': platform.mac_ver()[0] or None,
                  'codex_cli': cli, 'codex_app': version, 'python': platform.python_version(),
                  'sqlite': sqlite3.sqlite_version, **result}
    except (Exception, KeyboardInterrupt) as exc:
        safe_reason = (exc.args[0] if isinstance(exc, MigrationError) else
                       f'{type(exc).__name__} (errno {exc.errno})' if isinstance(exc, OSError) and exc.errno else
                       type(exc).__name__)
        print(json.dumps({'status': 'stopped', 'stage': stage, 'reason': safe_reason}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
