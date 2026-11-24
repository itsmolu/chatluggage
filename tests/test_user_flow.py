"""User-facing command paths and cases missed by short single-session fixtures."""

from contextlib import redirect_stdout
import argparse
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import cli
from history_migration import STATE, HISTORY, INDEX, UI, fingerprint
from migration_workflow import install_native, plan_native
from platform_paths import io_path, plain_path, temporary_directory
from tests.test_native import create_profile, connect, live_files


class UserFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(temporary_directory(Path(__file__).parent, '.user-'))
        self.source = self.temp / 'Windows 资料'
        self.rollout = create_profile(self.source, True)
        self.output = self.temp / '迁移包.zip'
        self.env = {**os.environ, 'CODEX_HOME': str(self.source)}

    def run_export(self, *extra, output=None, cwd=None):
        return subprocess.run([sys.executable, '-B', str(Path(cli.__file__).resolve()), 'export',
                               str(output or self.output), *extra], cwd=cwd or self.temp,
                              env=self.env, capture_output=True, encoding='utf-8', timeout=30)

    @unittest.skipUnless(sys.platform == 'win32', 'shipping Windows export command')
    def test_default_source_long_filename_transfer_map_merge_and_repeat(self):
        # A compressed side file carries two UUIDs. This is what failed at 294 chars for a real profile.
        long_source = self.temp / ('源资料_' + 's' * 70)
        io_path(self.source).rename(io_path(long_source))
        self.source = long_source
        self.env['CODEX_HOME'] = str(self.source)
        rel = self.rollout.relative_to(self.temp / 'Windows 资料')
        self.rollout = self.source / rel
        side = self.rollout.with_name(self.rollout.stem + '_11111111-1111-1111-1111-111111111111.jsonl.zst')
        io_path(side).write_bytes(b'fictional compressed side file')
        self.assertGreater(len(str(side)), 260)
        download = self.temp / ('下载_' + 'x' * 35) / 'codex-history-migration'
        io_path(download).mkdir(parents=True)
        output = download / 'Codex-Windows-History.zip'
        before = {rel: fingerprint(self.source / rel) for rel in (str(rel), str(side.relative_to(self.source)), STATE, HISTORY, INDEX, UI)}
        result = self.run_export(output=output, cwd=download)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('导出成功', result.stdout)
        self.assertIn(str(output), result.stdout)
        self.assertNotIn('PRIVATE_SENTINEL', result.stdout + result.stderr)
        self.assertFalse(list(download.glob('codex-export-*')), 'long-path temporary files must be cleaned')
        self.assertEqual(before, {rel: fingerprint(self.source / rel) for rel in before})
        # Transferring the ZIP does not depend on U disk / QQ / WeChat.
        transferred = self.temp / 'Mac Downloads'
        transferred.mkdir()
        package = transferred / output.name
        shutil.copyfile(output, package)
        self.assertEqual(fingerprint(package), fingerprint(output))
        with ZipFile(package) as zipped:
            self.assertEqual(zipped.read(side.relative_to(self.source).as_posix()), io_path(side).read_bytes())
            self.assertFalse(any('auth' in name or name.startswith('\\\\?\\') for name in zipped.namelist()))
        # Core only on Windows: the Mac process guard and sidebar are not simulated as passing.
        target = self.temp / 'Mac profile copy'
        create_profile(target, False)
        target_before = live_files(target)
        preview = plan_native(package, target, idle=lambda _: None, scratch=self.temp)
        code = next(iter(preview['project_roots']))
        workspace = self.temp / '项目文件'
        (workspace / 'src').mkdir(parents=True)
        mapping = {code: str(workspace)}
        plan = plan_native(package, target, mapping, idle=lambda _: None, scratch=self.temp)
        self.assertEqual(live_files(target), target_before)
        installed = install_native(package, target, plan['plan_id'], mapping, idle=lambda _: None, scratch=self.temp)
        self.assertEqual(installed['total_threads'], 2)
        self.assertTrue(Path(installed['backup']).is_dir())
        repeat = plan_native(package, target, mapping, idle=lambda _: None, scratch=self.temp)
        self.assertEqual(repeat['files_to_change'], 0)
        with connect(target / STATE) as db:
            cwd = db.execute('SELECT cwd FROM threads WHERE project_id IS NOT NULL').fetchone()[0]
            self.assertEqual(Path(cwd), workspace / 'src')

    @unittest.skipUnless(sys.platform == 'win32', 'shipping Windows export command')
    def test_missing_source_names_path(self):
        missing = self.temp / '不存在'
        self.env['CODEX_HOME'] = str(missing)
        result = self.run_export()
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(missing), result.stderr)
        self.assertIn('--source', result.stderr)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows extended paths')
    def test_deep_output_directory_with_missing_optional_source_files(self):
        for name in (UI, INDEX):
            (self.source / name).unlink()
        before = live_files(self.source)
        download = self.temp.joinpath(*(f'folder-{i}-' + 'd' * 44 for i in range(6)))
        io_path(download).mkdir(parents=True)
        output = download / 'Codex-Windows-History.zip'
        self.assertGreater(len(str(output)), 323)
        result = self.run_export('--json', output=output)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(Path(report['bundle']), output)
        self.assertEqual(report['sha256'], fingerprint(output))
        self.assertEqual(live_files(self.source), before)
        with ZipFile(io_path(output)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn(UI, archive.namelist())
            self.assertEqual(archive.read(INDEX), b'')
        self.assertEqual([p.name for p in io_path(download).iterdir()], [output.name])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows extended paths')
    def test_lowercase_extended_unc_keeps_server_and_share(self):
        path = plain_path(r'\\?\unc\FictionalServer\FictionalShare\export.zip')
        self.assertEqual(path, Path(r'\\FictionalServer\FictionalShare\export.zip'))
        self.assertTrue(path.is_absolute())

    @unittest.skipUnless(sys.platform == 'win32', 'shipping Windows export command')
    def test_existing_output_is_not_overwritten(self):
        self.output.write_bytes(b'existing user file')
        result = self.run_export()
        self.assertEqual(result.returncode, 1)
        self.assertIn('已存在', result.stderr)
        self.assertEqual(self.output.read_bytes(), b'existing user file')

    @unittest.skipUnless(sys.platform == 'win32', 'shipping Windows export command')
    def test_missing_history_database_is_explained(self):
        (self.source / HISTORY).unlink()
        result = self.run_export()
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.source / HISTORY), result.stderr)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(sys.platform == 'win32', 'shipping Windows export command')
    def test_missing_session_file_is_not_silently_skipped(self):
        self.rollout.unlink()
        result = self.run_export()
        self.assertEqual(result.returncode, 1)
        self.assertIn(self.rollout.name, result.stderr)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(sys.platform == 'win32', 'shipping Windows export command')
    def test_optional_index_and_desktop_state_do_not_break_export(self):
        (self.source / INDEX).unlink()
        (self.source / UI).unlink()
        result = self.run_export('--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['threads'], 1)

    def test_file_error_identifies_both_paths_without_chat_content(self):
        error = FileNotFoundError(2, 'missing', 'C:/source/session.jsonl', None, 'D:/copy/session.jsonl')
        rendered = cli.explain_failure(error)
        self.assertIn('session.jsonl', rendered)
        self.assertIn('FileNotFoundError', rendered)

    def test_generated_mac_command_preserves_quoted_paths_and_mapping(self):
        opts = argparse.Namespace(package=Path("/tmp/user's folder/a.zip"), target=Path('/tmp/Mac data'),
                                  mapping=Path('/tmp/project map.json'))
        args = shlex.split(cli.mac_command('apply', opts, 'a' * 64, '1,3-5'))
        self.assertEqual(args[args.index('--pick') + 1], '1,3-5')
        self.assertEqual(args[args.index('--approve') + 1], 'a' * 64)
        self.assertEqual(args[args.index('--target') + 1], str(opts.target.resolve()))
        self.assertEqual(args[args.index('--map') + 1], str(opts.mapping.resolve()))

    def test_mac_default_honors_custom_codex_home(self):
        with patch.dict(os.environ, {'CODEX_HOME': str(self.source)}):
            parser = argparse.ArgumentParser()
            cli.native_options(parser, bundle=False)
            self.assertEqual(parser.parse_args([]).target, self.source)

    def test_human_plan_prints_a_complete_command_and_explains_empty_projects(self):
        opts = argparse.Namespace(action='plan', json_output=False, package=self.output,
                                  target=self.temp / 'Mac', mapping=None)
        report = {'added_threads': 1, 'local_threads': 1, 'kept_duplicate_threads': 0,
                  'projects': {'Project-123': {'name': 'Example', 'path': '/tmp/empty', 'mapping': 'placeholder'}},
                  'projects_without_roots': [], 'fallback_project_sessions': 0, 'files_to_change': 5,
                  'directories_to_create': 1, 'plan_id': 'a' * 64}
        output = io.StringIO()
        with redirect_stdout(output):
            cli.show_report(report, opts)
        self.assertIn('空的占位目录', output.getvalue())
        args = shlex.split(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(args[args.index('--approve') + 1], report['plan_id'])
        self.assertEqual(args[args.index('--target') + 1], str(opts.target.resolve()))

    @unittest.skipUnless(sys.platform == 'win32', 'checks OS boundary without simulating macOS')
    def test_mac_apply_on_windows_is_rejected_before_any_write(self):
        before = live_files(self.source)
        result = subprocess.run([sys.executable, '-B', str(Path(cli.__file__).resolve()),
                                 'apply', str(self.output), '--approve', 'a' * 64,
                                 '--target', str(self.source)], capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn('macOS', result.stderr)
        self.assertEqual(live_files(self.source), before)


if __name__ == '__main__':
    unittest.main()
