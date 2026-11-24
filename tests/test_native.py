"""Exercise the shipping pipeline with synthetic profiles and two SQLx series."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
from platform_paths import temporary_directory
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from demo import INCOMING_ID, LOCAL_ID, PROJECT_ID
from history_migration import CORE, HISTORY, HISTORY_TABLES, INDEX, RECOVERY_DIR, STATE, THREAD_TABLES, UI
from history_migration import MigrationError, fingerprint, json_bytes, write_index
from migration_workflow import PENDING, install_native, plan_native, recover_native
from windows_export import export_windows, portable_root


BASE_VERSIONS = {STATE: 62, HISTORY: 8}
ALIAS = 'synthetic-windows-project-alias'
MESSAGE = 'Made up Windows body'
ROOT_CODE = portable_root(PROJECT_ID, 0)
IDLE = lambda _: None


class Interrupted(BaseException):
    pass


def connect(path):
    return closing(sqlite3.connect(path))


def create_db(path, windows):
    with connect(path) as db, db:
        db.execute('CREATE TABLE _sqlx_migrations(version INTEGER PRIMARY KEY, success INTEGER, description TEXT, checksum BLOB)')
        db.executemany('INSERT INTO _sqlx_migrations VALUES (?,1,?,?)',
                       ((i, f'Synthetic migration {i}', hashlib.sha384(f'{windows}/{i}'.encode()).digest())
                        for i in range(1, BASE_VERSIONS[path.name] + 1)))


def create_profile(root, windows):
    root.mkdir()
    tid = INCOMING_ID if windows else LOCAL_ID
    rel = f'sessions/2026/01/01/rollout-2026-01-01T00-00-00-{tid}.jsonl'
    file = root / rel
    file.parent.mkdir(parents=True)
    body = MESSAGE if windows else 'Made up Mac body'
    file.write_text(json.dumps({'synthetic': body}) + '\n', encoding='utf-8')
    source_path = r'D:\Synthetic\Code' if windows else str(root / 'workspace')
    cwd = source_path + r'\src' if windows else source_path
    rollout_path = (r'D:\Synthetic\Codex' + '\\' + rel.replace('/', '\\')) if windows else str(file)
    profile_id = PROJECT_ID if windows else None
    for name in (STATE, HISTORY):
        create_db(root / name, windows)
    with connect(root / STATE) as db, db:
        db.executescript('''
            CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT, position INTEGER,
                                  metadata TEXT, created_at_ms INTEGER, updated_at_ms INTEGER);
            CREATE TABLE project_roots(project_id TEXT REFERENCES projects(id), position INTEGER, path TEXT,
                                       PRIMARY KEY(project_id, position));
            CREATE TABLE project_idempotency_keys(key TEXT PRIMARY KEY, project_id TEXT, created_at_ms INTEGER);
            CREATE TABLE thread_sections(id TEXT PRIMARY KEY, name TEXT, appearance TEXT);
            CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT, rollout_path TEXT, cwd TEXT,
                project_id TEXT REFERENCES projects(id), thread_section_id TEXT REFERENCES thread_sections(id),
                attached_thread_parent_id TEXT, sandbox_policy TEXT, approval_mode TEXT, git_origin_url TEXT);
            CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT,PRIMARY KEY(parent_thread_id,child_thread_id));
            CREATE TABLE remote_control_enrollments(id TEXT PRIMARY KEY);
        ''')
        for table in THREAD_TABLES:
            db.execute(f'CREATE TABLE {table}(thread_id TEXT PRIMARY KEY REFERENCES threads(id), body TEXT)')
        if windows:
            db.execute('INSERT INTO projects VALUES (?,?,0,?,100,100)', (PROJECT_ID, 'Test Project', 'PRIVATE_SENTINEL'))
            db.execute('INSERT INTO project_roots VALUES (?,0,?)', (PROJECT_ID, source_path))
            db.execute('INSERT INTO remote_control_enrollments VALUES (?)', ('PRIVATE_SENTINEL',))
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,NULL,NULL,?,?,?)',
                   (tid, body, rollout_path, cwd, profile_id, 'test-policy', 'test-approval', 'PRIVATE_SENTINEL'))
        db.execute('INSERT INTO guardian_review_feedback VALUES (?,?)', (tid, 'PRIVATE_SENTINEL'))
    with connect(root / HISTORY) as db, db:
        for table in HISTORY_TABLES:
            db.execute(f'CREATE TABLE {table}(thread_id TEXT, ordinal INTEGER, body TEXT, PRIMARY KEY(thread_id,ordinal))')
            db.execute(f'INSERT INTO {table} VALUES (?,1,?)', (tid, body))
    write_index(root / INDEX, {tid: {'id': tid, 'thread_name': body}})
    project = {'id': ALIAS, 'name': 'Test Project', 'rootPaths': [source_path], 'createdAt': 100}
    ui = {'local-projects': {ALIAS: project}, 'project-order': [ALIAS],
          'app-server-project-id-by-legacy-project-id-by-host': {'PRIVATE_HOST': {ALIAS: PROJECT_ID}},
          'thread-project-assignments': {tid: {'projectKind': 'local', 'projectId': ALIAS}},
          'private-desktop-setting': {'onboarding': 'PRIVATE_SENTINEL'}} if windows else {'mac-pref': 42}
    (root / UI).write_bytes(json_bytes(ui))
    (root / 'auth.json').write_text('PRIVATE_SENTINEL', encoding='utf-8')
    (root / 'config.toml').write_text('unchanged = true', encoding='utf-8')
    return file


def live_files(root):
    return {name: None if p.is_dir() else fingerprint(p) for p in root.rglob('*')
            if not (name := p.relative_to(root).as_posix()).startswith(RECOVERY_DIR)}


def fail_after_state(error):
    def fail(name):
        if name == STATE:
            raise error('synthetic interruption after main state database replacement')
    return fail


class NativeTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(temporary_directory(Path(__file__).parent, '.test-'))
        self.root = Path(tmp).resolve()
        self.source, self.target, self.package = self.root / 'source', self.root / 'target', self.root / 'native.zip'
        self.rollout = create_profile(self.source, True)
        create_profile(self.target, False)

    def plan(self, mapping=None):
        return plan_native(self.package, self.target, mapping, idle=IDLE, scratch=self.root)

    def apply(self, plan_id, mapping=None, inject=None):
        return install_native(self.package, self.target, plan_id, mapping, idle=IDLE, inject=inject, scratch=self.root)

    def test_export_is_selective_and_keeps_source_files_byte_identical(self):
        assets = self.source / 'attachments'
        assets.mkdir()
        (assets / 'example.txt').write_text('synthetic attachment', encoding='utf-8')
        (assets / 'auth.json').write_text('PRIVATE_SENTINEL', encoding='utf-8')
        original = live_files(self.source)
        export = export_windows(self.source, self.package, include_assets=True)
        self.assertEqual((export['threads'], export['turns'], export['asset_files']), (1, 1, 1))
        self.assertEqual(live_files(self.source), original)
        with ZipFile(self.package) as archive:
            self.assertEqual(set(archive.namelist()), {*CORE, 'manifest.json', 'attachments/example.txt',
                                                       self.rollout.relative_to(self.source).as_posix()})
            for name in archive.namelist():
                self.assertNotIn(b'PRIVATE_SENTINEL', archive.read(name))
                self.assertNotIn(b'PRIVATE_HOST', archive.read(name))

    def test_hard_stop_during_zip_write_leaves_final_name_available(self):
        before = live_files(self.source)
        code = """
import os
from pathlib import Path
import sys
from zipfile import ZipFile
from windows_export import export_windows

write = ZipFile.write
def stop_mid_zip(archive, *args, **kwargs):
    write(archive, *args, **kwargs)
    archive.fp.flush()
    os._exit(85)

ZipFile.write = stop_mid_zip
export_windows(Path(sys.argv[1]), Path(sys.argv[2]))
"""
        project = Path(__file__).resolve().parents[1]
        interrupted = subprocess.run([sys.executable, '-B', '-c', code, str(self.source), str(self.package)],
                                     cwd=project, capture_output=True, timeout=25)
        self.assertEqual(interrupted.returncode, 85, interrupted.stderr)
        self.assertFalse(self.package.exists())
        self.assertEqual(live_files(self.source), before)
        result = export_windows(self.source, self.package)
        self.assertEqual(fingerprint(self.package), result['sha256'])
        with ZipFile(self.package) as archive:
            self.assertIsNone(archive.testzip())

    def test_publishing_zip_does_not_replace_a_late_destination(self):
        test_zip = ZipFile.testzip
        before = live_files(self.source)

        def claim_after_verification(archive):
            result = test_zip(archive)
            with self.package.open('xb') as claimed:
                claimed.write(b'another export')
            return result

        with patch.object(ZipFile, 'testzip', claim_after_verification):
            with self.assertRaises(FileExistsError):
                export_windows(self.source, self.package)
        self.assertEqual(self.package.read_bytes(), b'another export')
        self.assertEqual(live_files(self.source), before)
        self.assertFalse(list(self.root.glob('.codex-export-*.partial')))

    @unittest.skipUnless(sys.platform == 'win32', 'uses Windows file sharing')
    def test_windows_cli_blocks_an_active_database_writer_and_retries(self):
        cli = Path(__file__).resolve().parents[1] / 'cli.py'
        command = [sys.executable, '-B', str(cli), 'export', str(self.package), '--source', str(self.source), '--json']
        before = live_files(self.source)
        with connect(self.source / STATE) as writer:
            try:
                writer.execute('BEGIN IMMEDIATE')
                writer.execute("UPDATE threads SET title = 'uncommitted change'")
                blocked = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=30)
                self.assertEqual(blocked.returncode, 1)
                self.assertIn(f'Windows source file is in use: {STATE}', blocked.stderr)
                self.assertFalse(self.package.exists())
            finally:
                writer.rollback()
        success = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)['threads'], 1)
        self.assertEqual(live_files(self.source), before)

    def test_single_thread_applies_with_real_mapping_preserving_target_sqlx(self):
        export_windows(self.source, self.package)
        work = self.root / 'mac-workspace'
        (work / 'src').mkdir(parents=True)
        mapping = {ROOT_CODE: str(work)}
        before = live_files(self.target)
        with connect(self.target / STATE) as db:
            checksums = db.execute('SELECT checksum FROM _sqlx_migrations ORDER BY version').fetchall()
        plan = self.plan(mapping)
        self.assertEqual(live_files(self.target), before)
        self.assertEqual((plan['added_threads'], plan['added_turns'], plan['total_threads']), (1, 1, 2))
        self.assertFalse(plan['compatibility'][STATE]['source_checksums_match'])
        report = self.apply(plan['plan_id'], mapping)
        self.assertTrue(report['target_changed'])
        backup = Path(report['backup'])
        self.assertEqual(fingerprint(backup / STATE), before[STATE])
        with connect(self.target / STATE) as db:
            path, project, cwd = db.execute('SELECT rollout_path,project_id,cwd FROM threads WHERE id=?', (INCOMING_ID,)).fetchone()
            self.assertEqual((project, cwd), (PROJECT_ID, str(work / 'src')))
            self.assertEqual(Path(path).read_bytes(), self.rollout.read_bytes())
            self.assertEqual(db.execute('SELECT checksum FROM _sqlx_migrations ORDER BY version').fetchall(), checksums)
            self.assertEqual(db.execute('SELECT id FROM threads ORDER BY id').fetchall(), [(LOCAL_ID,), (INCOMING_ID,)])
        with connect(self.target / HISTORY) as db:
            self.assertEqual(db.execute('SELECT body FROM thread_items WHERE thread_id=?', (INCOMING_ID,)).fetchone(), (MESSAGE,))
        ui = json.loads((self.target / UI).read_bytes())
        self.assertEqual(ui['local-projects'][ALIAS]['rootPaths'], [str(work)])
        self.assertEqual(ui['mac-pref'], 42)
        self.assertEqual(fingerprint(self.target / 'auth.json'), before['auth.json'])
        self.assertEqual(fingerprint(self.target / 'config.toml'), before['config.toml'])
        self.assertEqual(list((work / 'src').iterdir()), [])
        installed = live_files(self.target)
        retry = self.plan()
        self.assertEqual((retry['kept_duplicate_threads'], retry['files_to_change']), (1, 0))
        self.assertFalse(self.apply(retry['plan_id'])['target_changed'])
        self.assertEqual(live_files(self.target), installed)

    def test_stale_approval_leaves_profile_untouched(self):
        export_windows(self.source, self.package)
        approved = self.plan()['plan_id']
        (self.target / INDEX).write_bytes((self.target / INDEX).read_bytes() + b'\n')
        before = live_files(self.target)
        with self.assertRaisesRegex(MigrationError, 'no longer matches'):
            self.apply(approved)
        self.assertEqual(live_files(self.target), before)
        self.assertFalse((self.target / RECOVERY_DIR).exists())

    def test_newer_sqlx_version_and_unrelated_table_allow_planning(self):
        with connect(self.source / STATE) as db, db:
            db.execute("INSERT INTO _sqlx_migrations VALUES (64,1,'synthetic update',?)", (bytes(48),))
        with connect(self.target / STATE) as db, db:
            db.execute('CREATE TABLE newer_feature(x INTEGER)')
        before = live_files(self.source)
        export_windows(self.source, self.package)
        plan = self.plan()
        self.assertEqual(plan['compatibility'][STATE]['source_migration_version'], 64)
        self.assertEqual(live_files(self.source), before)

    def test_recovers_after_state_replacement_from_durable_journal(self):
        export_windows(self.source, self.package)
        before = live_files(self.target)
        plan = self.plan()
        with self.assertRaises(Interrupted):
            self.apply(plan['plan_id'], inject=fail_after_state(Interrupted))
        self.assertNotEqual(live_files(self.target), before)
        with self.assertRaisesRegex(MigrationError, 'run recover'):
            self.plan()
        recovery = recover_native(self.target, idle=IDLE)
        self.assertTrue(recovery['restored_originals'])
        self.assertFalse((self.target / RECOVERY_DIR / PENDING).exists())
        self.assertEqual(live_files(self.target), before)
        self.assertEqual(self.plan()['plan_id'], plan['plan_id'])

    def test_archived_compressed_session_keeps_its_file_bytes_and_canonical_path(self):
        archived = self.source / 'archived_sessions'
        archived.mkdir()
        compressed = archived / (self.rollout.name + '.zst')
        # Only the shipping copy behavior is under test; Codex decompresses the original bytes.
        compressed.write_bytes(b'\x28\xb5\x2f\xfd\x20\x09\x49\x00\x00test data')
        self.rollout.unlink()
        with connect(self.source / STATE) as db, db:
            db.execute('UPDATE threads SET rollout_path=?', (r'D:\Synthetic\Codex\archived_sessions' + '\\' + self.rollout.name,))
        export_windows(self.source, self.package)
        plan = self.plan()
        self.apply(plan['plan_id'])
        with connect(self.target / STATE) as db:
            path, = db.execute('SELECT rollout_path FROM threads WHERE id=?', (INCOMING_ID,)).fetchone()
        self.assertEqual(Path(path), self.target / 'archived_sessions' / self.rollout.name)
        self.assertEqual(Path(path + '.zst').read_bytes(), compressed.read_bytes())

    def test_restores_wal_on_install_error(self):
        export_windows(self.source, self.package)
        with connect(self.target / STATE) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
            checkpointed = (self.target / STATE).read_bytes()
            db.execute("UPDATE threads SET title='Committed only to the WAL'")
            db.commit()
            wal = (self.target / (STATE + '-wal')).read_bytes()
            shm = (self.target / (STATE + '-shm')).read_bytes()
        (self.target / STATE).write_bytes(checkpointed)
        (self.target / (STATE + '-wal')).write_bytes(wal)
        (self.target / (STATE + '-shm')).write_bytes(shm)
        before = live_files(self.target)
        plan = self.plan()
        with self.assertRaisesRegex(MigrationError, 'backup was restored'):
            self.apply(plan['plan_id'], inject=fail_after_state(OSError))
        self.assertEqual(live_files(self.target), before)
        completed = self.apply(plan['plan_id'])
        self.assertTrue(completed['target_changed'])
        self.assertFalse((self.target / (STATE + '-wal')).exists())
        with connect(self.target / STATE) as db:
            self.assertEqual(db.execute('SELECT title FROM threads WHERE id=?', (LOCAL_ID,)).fetchone(),
                             ('Committed only to the WAL',))

    def test_intervening_sidecar_prevents_recovery(self):
        export_windows(self.source, self.package)
        plan = self.plan()
        with self.assertRaises(Interrupted):
            self.apply(plan['plan_id'], inject=fail_after_state(Interrupted))
        (self.target / (HISTORY + '-wal')).write_bytes(b'synthetic intervening change')
        before = live_files(self.target)
        with self.assertRaisesRegex(MigrationError, 'should be unchanged'):
            recover_native(self.target, idle=IDLE)
        self.assertEqual(live_files(self.target), before)
        self.assertTrue((self.target / RECOVERY_DIR / PENDING).is_file())


if __name__ == '__main__':
    unittest.main()
