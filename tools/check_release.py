"""Exercise a freshly extracted download through its public Windows command."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from zipfile import ZipFile

from build_release import FILES, PACKAGE_ROOT, VERSION
from demo import INCOMING_ID, LOCAL_ID, PROJECT_ID
from history_migration import HISTORY, STATE, fingerprint, require
from migration_workflow import install_native, plan_native, recover_native
from platform_paths import temporary_directory
from tests.test_native import IDLE, Interrupted, MESSAGE, ROOT_CODE, connect, create_profile, fail_after_state, live_files


def run(args, cwd, env):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, encoding='utf-8', timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result


def check_transferred_history(package, scratch):
    """Exercise the copied README export using only disposable target files."""
    target = scratch / 'fictional-target'
    workspace = scratch / 'mapped-workspace'
    (workspace / 'src').mkdir(parents=True)
    create_profile(target, False)
    before = live_files(target)
    digest = fingerprint(package)
    mappings = {ROOT_CODE: str(workspace)}
    options = {'idle': IDLE, 'scratch': scratch}
    preview = plan_native(package, target, mappings, **options)
    require(live_files(target) == before and preview['added_threads'] == 1, 'The README export preview did not match')
    try:
        install_native(package, target, preview['plan_id'], mappings, inject=fail_after_state(Interrupted), **options)
    except Interrupted:
        recover_native(target, idle=IDLE)
    else:
        raise RuntimeError('The interruption check did not run')
    require(live_files(target) == before, 'Recovery did not restore the fictional target')
    preview = plan_native(package, target, mappings, **options)
    merged = install_native(package, target, preview['plan_id'], mappings, **options)
    require(merged['target_changed'] and merged['total_threads'] == 2, 'The README export merge did not match')
    with connect(target / STATE) as db:
        require({tid for tid, in db.execute('SELECT id FROM threads')} == {INCOMING_ID, LOCAL_ID}, 'Session IDs did not match')
        require(db.execute('SELECT project_id,cwd FROM threads WHERE id=?', (INCOMING_ID,)).fetchone()
                == (PROJECT_ID, str(workspace / 'src')), 'Project mapping did not match')
    with connect(target / HISTORY) as db:
        require(db.execute('SELECT body FROM thread_items WHERE thread_id=?', (INCOMING_ID,)).fetchone()
                == (MESSAGE,), 'Imported history did not match')
    after = live_files(target)
    repeat = plan_native(package, target, mappings, **options)
    no_op = install_native(package, target, repeat['plan_id'], mappings, **options)
    require(not no_op['target_changed'] and live_files(target) == after and fingerprint(package) == digest,
            'Repeated import changed files')
    return {'readme_export_plan_apply': 'passed', 'readme_export_recovery': 'passed', 'readme_export_repeat_writes': 0}


def check_packaged_tests():
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.'))
    skipped, expected_failures = len(result.skipped), len(result.expectedFailures)
    passed = result.testsRun - skipped - expected_failures
    require(result.wasSuccessful() and passed > 0, 'Packaged tests did not pass')
    return {'packaged_tests': passed, 'packaged_tests_skipped': skipped, 'packaged_tests_expected_failures': expected_failures}


def check(archive, scratch, codex=None):
    if sys.platform != 'win32':
        raise RuntimeError('This runner verifies the Windows download flow; use accept_mac.py on macOS')
    with temporary_directory(scratch, 'download-check-') as tmp:
        base = Path(tmp).resolve()
        download = base / '下载 解压后的项目'
        with ZipFile(archive) as zipped:
            expected = {f'{PACKAGE_ROOT}/{name}' for name in FILES}
            if set(zipped.namelist()) != expected or zipped.testzip() is not None:
                raise ValueError('The source ZIP has unexpected files or failed verification')
            zipped.extractall(download)
        project = download / PACKAGE_ROOT
        env = {k: v for k, v in os.environ.items() if k.upper() not in ('PYTHONPATH', 'PYTHONHOME', 'CODEX_HOME')}
        source = base / 'fictional-source'
        env.update(CODEX_HOME=str(source), PYTHONIOENCODING='utf-8')
        # Ensure the README entry point uses this interpreter even if Windows has another Python on PATH.
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
        setup = (
            "from pathlib import Path; from tests.test_native import create_profile; "
            "from platform_paths import io_path; "
            "import sys; p=Path(sys.argv[1]); r=create_profile(p,True); "
            "io_path(r.with_name(r.stem+'_11111111-1111-1111-1111-111111111111.jsonl.zst')).write_bytes(b'fictional side file')"
        )
        run([sys.executable, '-B', '-c', setup, str(source)], project, env)
        version = run([sys.executable, '-B', 'cli.py', '--version'], project, env).stdout.strip()
        if version != VERSION:
            raise ValueError('Wrong packaged CLI version')
        readme = (project / 'README.md').read_text(encoding='utf-8')
        blocks = re.findall(r'```powershell\n(.*?)\n```', readme, flags=re.S)
        if len(blocks) != 2 or not blocks[0].startswith('cd ') or not blocks[1].startswith('python -B .\\cli.py export '):
            raise ValueError('README Windows commands changed; update the release check')
        # Execute the actual documented command after changing from a different directory.
        # Quoting is literal PowerShell quoting; the scratch path is not evaluated as shell code.
        quoted = "'" + str(project).replace("'", "''") + "'"
        command = f"$ErrorActionPreference='Stop'; cd {quoted}; {blocks[1]}; exit $LASTEXITCODE"
        powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        exported = run([powershell, '-NoProfile', '-NonInteractive', '-Command', command], base, env)
        if '导出成功' not in exported.stdout:
            raise ValueError('The documented command did not produce the documented success message')
        package = project / 'Codex-Windows-History.zip'
        transfer = base / 'Mac Downloads'
        transfer.mkdir()
        copied = transfer / package.name
        shutil.copyfile(package, copied)
        if hashlib.sha256(package.read_bytes()).digest() != hashlib.sha256(copied.read_bytes()).digest():
            raise ValueError('Transfer copy differs')
        # Run the merge and the test suite using the extracted project's code.
        exercise = """
import json, sys
from pathlib import Path
sys.path.insert(0, 'tools')
from check_release import check_packaged_tests, check_transferred_history
print(json.dumps({**check_transferred_history(Path(sys.argv[1]), Path(sys.argv[2])), **check_packaged_tests()}))
"""
        coverage = json.loads(run([sys.executable, '-B', '-c', exercise, str(copied), str(base)], project, env).stdout)
        report = {'version': version, 'readme_windows_command': 'passed', 'archive_integrity': 'passed',
                  'transfer_sha256': 'passed', **coverage,
                  'mac_desktop_and_os_guard': 'not_run', 'installed_codex_schema': 'not_run',
                  'cross_version_readback': 'not_run'}
        if codex:
            native = run([sys.executable, '-B', 'tools/verify_installed_schema.py', '--codex', str(codex),
                          '--scratch', str(base)], project, env)
            details = json.loads(native.stdout)
            if not details['source_unchanged'] or details['total_threads'] != 2 or details['repeat_writes']:
                raise ValueError('Installed Codex structure check failed')
            report['installed_codex_schema'] = 'passed'
            compatible = run([sys.executable, '-B', 'tools/verify_compatibility.py', '--codex', str(codex),
                              '--scratch', str(base)], project, env)
            compatibility = json.loads(compatible.stdout)
            cases = compatibility['cases']
            expected = {('62-8', '62-8', False): ('passed', 'not_run'),
                        ('62-8', '64-9', False): ('passed', 'passed'),
                        ('64-9', '64-9', False): ('passed', 'passed'),
                        ('64-9', '64-9', True): ('passed', 'passed')}
            actual = {(row['source'], row['target'], row['compressed']): (row['merge'], row['codex_readback']) for row in cases}
            if compatibility['status'] != 'passed' or len(cases) != len(expected) or actual != expected:
                raise ValueError('Cross-version readback did not pass')
            report['cross_version_readback'] = 'passed'
            report['cross_version_cases'] = cases
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--scratch', type=Path, required=True, help='existing directory outside live Codex data')
    parser.add_argument('--codex', type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.archive.resolve(strict=True), args.scratch.resolve(strict=True), args.codex),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
