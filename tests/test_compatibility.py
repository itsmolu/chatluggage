"""Cross-version merges using schema-only captures from installed Codex builds."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from demo import INCOMING_ID, LOCAL_ID, PROJECT_ID
from history_migration import HISTORY, STATE, MigrationError, fingerprint
from migration_workflow import install_native, plan_native, recover_native
from platform_paths import temporary_directory
from tests.test_native import Interrupted, fail_after_state, live_files
from tools.verify_installed_schema import seed
from windows_export import export_windows


FIXTURES = Path(__file__).resolve().parent / 'fixtures'
IDLE = lambda _: None


def zstd_raw_frame(data):
    """Encode a valid Zstandard single-segment frame with uncompressed blocks."""
    frame = bytearray(b'\x28\xb5\x2f\xfd\xa0' + len(data).to_bytes(4, 'little'))
    for start in range(0, max(1, len(data)), 128 * 1024):
        block = data[start:start + 128 * 1024]
        last = start + len(block) == len(data)
        frame.extend(((len(block) << 3) | int(last)).to_bytes(3, 'little'))
        frame.extend(block)
    return bytes(frame)


def create_schema_profile(root, versions, windows, *, tid=None):
    root.mkdir()
    schema = json.loads((FIXTURES / f'schema-{versions}.json').read_text('utf-8'))
    for name, metadata in schema.items():
        with closing(sqlite3.connect(root / name)) as db, db:
            for _, _, sql in metadata['schema']:
                db.execute(sql)
            db.executemany('INSERT INTO _sqlx_migrations(version,description,success,checksum,execution_time) VALUES (?,?,?,?,0)',
                           ((v, desc, success, bytes.fromhex(checksum)) for v, desc, success, checksum in metadata['migrations']))
    tid = tid or (INCOMING_ID if windows else LOCAL_ID)
    seed(root, PROJECT_ID if windows else None, 'Fictional Windows message' if windows else 'Fictional Mac message', tid=tid)
    rollout = next((root / 'sessions').rglob(f'*-{tid}.jsonl'))
    if windows:
        with closing(sqlite3.connect(root / STATE)) as db, db:
            db.execute('UPDATE threads SET rollout_path=? WHERE id=?',
                       ('D:\\Fictional\\Codex\\' + str(rollout.relative_to(root)).replace('/', '\\'), tid))
    (root / 'auth.json').write_text('UNTOUCHED_SYNTHETIC_CREDENTIAL', 'utf-8')
    return rollout


def schema_metadata(root):
    result = {}
    for name in (STATE, HISTORY):
        with closing(sqlite3.connect(root / name)) as db:
            result[name] = (db.execute('SELECT * FROM _sqlx_migrations ORDER BY version').fetchall(),
                            db.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY type,name").fetchall())
    return result


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(temporary_directory(Path(__file__).resolve().parent.parent, '.compat-'))
        self.source, self.target = self.root / 'win', self.root / 'mac'
        self.package = self.root / 'history.zip'
        self.options = {'idle': IDLE, 'scratch': self.root}

    def prepare(self, source='62-8', target='64-9'):
        self.rollout = create_schema_profile(self.source, source, True)
        create_schema_profile(self.target, target, False)

    def plan(self, **kwargs):
        return plan_native(self.package, self.target, **self.options, **kwargs)

    def apply(self, plan, **kwargs):
        return install_native(self.package, self.target, plan['plan_id'], **self.options, **kwargs)

    def test_real_schema_matrix_preserves_history_target_metadata_and_repeat(self):
        for source, target in (('62-8', '62-8'), ('62-8', '64-9'), ('64-9', '64-9')):
            with self.subTest(source=source, target=target):
                self.source = self.root / ('win-' + source + '-' + target)
                self.target = self.root / ('mac-' + source + '-' + target)
                self.package = self.root / (source + '-' + target + '.zip')
                self.prepare(source, target)
                if source == '64-9':
                    with closing(sqlite3.connect(self.source / STATE)) as db, db:
                        db.execute('INSERT INTO session_model_ids VALUES (?, ?, ?, 0, ?)',
                                   (INCOMING_ID, 'synthetic', 'external-id', '{"test":true}'))
                original = live_files(self.source)
                target_before, metadata = live_files(self.target), schema_metadata(self.target)
                export = export_windows(self.source, self.package)
                self.assertEqual(export['source_migration_versions'][STATE], int(source.split('-')[0]))
                plan = self.plan()
                self.assertEqual(live_files(self.target), target_before)
                self.assertEqual(plan['compatibility'][STATE]['source_migration_version'], int(source.split('-')[0]))
                self.assertEqual(plan['compatibility'][HISTORY]['migration_version'], int(target.split('-')[1]))
                self.apply(plan)
                self.assertEqual(live_files(self.source), original)
                self.assertEqual(schema_metadata(self.target), metadata)
                with closing(sqlite3.connect(self.target / HISTORY)) as db:
                    items = db.execute('SELECT item_json FROM thread_items WHERE thread_id=? ORDER BY rollout_ordinal', (INCOMING_ID,)).fetchall()
                    self.assertEqual(len(items), 2)
                    self.assertIn('Fictional Windows message', items[0][0])
                with closing(sqlite3.connect(self.target / STATE)) as db:
                    file, pid = db.execute('SELECT rollout_path,project_id FROM threads WHERE id=?', (INCOMING_ID,)).fetchone()
                    self.assertEqual(pid, PROJECT_ID)
                    self.assertEqual(fingerprint(Path(file)), fingerprint(self.rollout))
                    if source == '64-9':
                        self.assertEqual(db.execute('SELECT external_key,initial_state FROM session_model_ids WHERE session_id=?',
                                                    (INCOMING_ID,)).fetchone(), ('external-id', '{"test":true}'))
                repeat = self.plan()
                self.assertEqual((repeat['files_to_change'], repeat['directories_to_create']), (0, 0))
                self.assertFalse(self.apply(repeat)['target_changed'])

    def test_unfamiliar_version_extra_index_and_defaulted_field_are_compatible(self):
        self.prepare('64-9', '64-9')
        with closing(sqlite3.connect(self.target / STATE)) as db, db:
            db.execute('ALTER TABLE threads ADD COLUMN future_label TEXT NOT NULL DEFAULT \'new\'')
            db.execute('CREATE INDEX future_lookup ON threads(title)')
            db.execute("INSERT INTO _sqlx_migrations(version,description,success,checksum,execution_time) VALUES (65,'future',1,?,0)", (bytes(48),))
        export_windows(self.source, self.package)
        plan = self.plan()
        self.assertEqual(plan['compatibility'][STATE]['target_only_columns'], {'threads': ['future_label']})
        before = schema_metadata(self.target)
        self.apply(plan)
        self.assertEqual(schema_metadata(self.target), before)
        with closing(sqlite3.connect(self.target / STATE)) as db:
            self.assertEqual(db.execute('SELECT future_label FROM threads WHERE id=?', (INCOMING_ID,)).fetchone(), ('new',))

    @unittest.skipUnless(sys.platform == 'win32', 'the public export command runs on Windows')
    def test_public_windows_command_exports_real_64_9_compressed_source(self):
        self.prepare('64-9', '64-9')
        compressed = Path(str(self.rollout) + '.zst')
        compressed.write_bytes(zstd_raw_frame(self.rollout.read_bytes()))
        self.rollout.unlink()
        before = live_files(self.source)
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'cli.py'),
                                 'export', str(self.package), '--json'], capture_output=True,
                                encoding='utf-8', env={**os.environ, 'CODEX_HOME': str(self.source)}, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['source_migration_versions'], {STATE: 64, HISTORY: 9})
        self.assertEqual(live_files(self.source), before)
        self.apply(self.plan())

    def test_source_only_values_and_missing_required_destination_field_block_writes(self):
        self.prepare()
        with closing(sqlite3.connect(self.source / STATE)) as db, db:
            db.execute('ALTER TABLE threads ADD COLUMN future_detail TEXT')
            db.execute("UPDATE threads SET future_detail='preserve this value'")
        export_windows(self.source, self.package)
        before = live_files(self.target)
        with self.assertRaisesRegex(MigrationError, 'future_detail'):
            self.plan()
        self.assertEqual(live_files(self.target), before)
        self.package.unlink()
        with closing(sqlite3.connect(self.source / STATE)) as db, db:
            db.execute('UPDATE threads SET future_detail=NULL')
        export_windows(self.source, self.package)
        self.apply(self.plan())

    def test_missing_required_destination_field_stops_before_inserting(self):
        from schema_compat import insert_rows
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute("ATTACH DATABASE ':memory:' AS incoming")
            db.execute('CREATE TABLE incoming.threads(id TEXT PRIMARY KEY)')
            db.execute("INSERT INTO incoming.threads VALUES ('selected')")
            db.execute('CREATE TABLE main.threads(id TEXT PRIMARY KEY, required_future TEXT NOT NULL)')
            with self.assertRaisesRegex(MigrationError, 'required_future'):
                insert_rows(db, 'threads', '1')
            self.assertEqual(db.execute('SELECT count(*) FROM main.threads').fetchone()[0], 0)

    def test_new_model_ids_cannot_be_silently_lost_to_an_older_target(self):
        self.prepare('64-9', '62-8')
        with closing(sqlite3.connect(self.source / STATE)) as db, db:
            db.execute("INSERT INTO session_model_ids VALUES (?,'synthetic','key',0,'state')", (INCOMING_ID,))
        export_windows(self.source, self.package)
        before = live_files(self.target)
        with self.assertRaisesRegex(MigrationError, 'session_model_ids'):
            self.plan()
        self.assertEqual(live_files(self.target), before)

    def test_partial_import_only_copies_selected_session_model_ids(self):
        self.prepare('64-9', '64-9')
        other = '00000000-0000-4000-8000-000000000003'
        seed(self.source, None, 'Other fictional chat', tid=other)
        other_rollout = next((self.source / 'sessions').rglob(f'*-{other}.jsonl'))
        with closing(sqlite3.connect(self.source / STATE)) as db, db:
            db.execute('UPDATE threads SET rollout_path=?,cwd=? WHERE id=?',
                       ('D:\\Fictional\\Codex\\' + other_rollout.relative_to(self.source).as_posix().replace('/', '\\'),
                        'D:\\Fictional\\Other', other))
            db.executemany("INSERT INTO session_model_ids VALUES (?,'synthetic',?,0,'{}')",
                           ((INCOMING_ID, 'wanted'), (other, 'unselected'), ('absent', 'orphan')))
        export_windows(self.source, self.package)
        before = live_files(self.target)
        choose = lambda rows, _: str(next(n for n, row in enumerate(rows, 1) if row[1] == INCOMING_ID))
        plan = self.plan(selection=choose)
        self.assertEqual(live_files(self.target), before)
        self.apply(plan, selection=plan['selection'])
        with closing(sqlite3.connect(self.target / STATE)) as db:
            self.assertEqual(db.execute('SELECT session_id,external_key FROM session_model_ids').fetchall(), [(INCOMING_ID, 'wanted')])
            self.assertEqual({tid for tid, in db.execute('SELECT id FROM threads')}, {LOCAL_ID, INCOMING_ID})
        with closing(sqlite3.connect(self.target / HISTORY)) as db:
            self.assertFalse(db.execute('SELECT 1 FROM thread_items WHERE thread_id=?', (other,)).fetchone())
        self.assertFalse((self.target / other_rollout.relative_to(self.source)).exists())
        self.assertEqual(self.plan(selection=plan['selection'])['files_to_change'], 0)

    def test_copy_matches_column_names_when_order_changes(self):
        from schema_compat import insert_rows
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute("ATTACH DATABASE ':memory:' AS incoming")
            db.execute('CREATE TABLE incoming.records(id TEXT PRIMARY KEY, value TEXT)')
            db.execute("INSERT INTO incoming.records VALUES ('id', 'body')")
            db.execute('CREATE TABLE main.records(value TEXT, id TEXT PRIMARY KEY)')
            insert_rows(db, 'records', '1')
            self.assertEqual(db.execute('SELECT id,value FROM main.records').fetchone(), ('id', 'body'))

    def test_worker_import_carries_shared_root_ids_and_checks_conflicts(self):
        self.prepare('64-9', '64-9')
        child = '00000000-0000-4000-8000-000000000003'
        sibling = '00000000-0000-4000-8000-000000000004'
        for tid in (child, sibling):
            seed(self.source, None, 'Child fictional chat', tid=tid)
            file = next((self.source / 'sessions').rglob(f'*-{tid}.jsonl'))
            with closing(sqlite3.connect(self.source / STATE)) as db, db:
                db.execute('UPDATE threads SET rollout_path=?,cwd=?,attached_thread_parent_id=? WHERE id=?',
                           ('D:\\Codex\\' + file.relative_to(self.source).as_posix().replace('/', '\\'),
                            'D:\\Fictional\\Project', None if tid == sibling else INCOMING_ID, tid))
                db.execute("INSERT INTO thread_spawn_edges VALUES (?,?,'completed')", (INCOMING_ID, tid))
        with closing(sqlite3.connect(self.source / STATE)) as db, db:
            db.execute("INSERT INTO session_model_ids VALUES (?,'test','shared-key',0,'{}')", (INCOMING_ID,))
        export_windows(self.source, self.package)
        choose = lambda rows, _: str(next(n for n, row in enumerate(rows, 1) if row[1] == child))
        # A root namespace already used on the target must agree with the source.
        with closing(sqlite3.connect(self.target / STATE)) as db, db:
            db.execute("INSERT INTO session_model_ids VALUES (?,'test','shared-key',0,'different')", (INCOMING_ID,))
        before = live_files(self.target)
        with self.assertRaisesRegex(MigrationError, '共享会话标识'):
            self.plan(selection=choose)
        self.assertEqual(live_files(self.target), before)
        with closing(sqlite3.connect(self.target / STATE)) as db, db:
            db.execute('DELETE FROM session_model_ids')
        plan = self.plan(selection=choose)
        self.apply(plan, selection=plan['selection'])
        choose_sibling = lambda rows, _: str(next(n for n, row in enumerate(rows, 1) if row[1] == sibling))
        another = self.plan(selection=choose_sibling)
        self.apply(another, selection=another['selection'])
        with closing(sqlite3.connect(self.target / STATE)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM session_model_ids').fetchone(), (1,))
            self.assertEqual({row[0] for row in db.execute('SELECT id FROM threads')}, {LOCAL_ID, child, sibling})
        self.assertEqual(self.plan(selection=plan['selection'])['files_to_change'], 0)

    def test_missing_root_for_shared_ids_is_refused_during_export(self):
        self.prepare('64-9', '64-9')
        absent = '00000000-0000-4000-8000-000000000099'
        with closing(sqlite3.connect(self.source / STATE)) as db, db:
            db.execute('UPDATE threads SET attached_thread_parent_id=?', (absent,))
            db.execute("INSERT INTO session_model_ids VALUES (?,'test','shared-key',0,'{}')", (absent,))
        before = live_files(self.source)
        with self.assertRaisesRegex(MigrationError, '共享标识对应的主会话'):
            export_windows(self.source, self.package)
        self.assertEqual(live_files(self.source), before)
        self.assertFalse(self.package.exists())

    def test_ignored_insert_cannot_be_reported_as_success(self):
        from schema_compat import insert_rows
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute("ATTACH DATABASE ':memory:' AS incoming")
            db.execute('CREATE TABLE incoming.records(id TEXT PRIMARY KEY)')
            db.execute("INSERT INTO incoming.records VALUES ('row')")
            db.execute('CREATE TABLE main.records(id TEXT PRIMARY KEY)')
            db.execute('CREATE TRIGGER ignore_insert BEFORE INSERT ON records BEGIN SELECT RAISE(IGNORE); END')
            with self.assertRaisesRegex(MigrationError, '实际写入数量'):
                insert_rows(db, 'records', '1')

    def test_unknown_relationship_or_trigger_is_identified(self):
        self.prepare()
        with closing(sqlite3.connect(self.source / STATE)) as db, db:
            db.execute('CREATE TABLE new_chat_links(thread_id TEXT, payload TEXT)')
        before = live_files(self.source)
        with self.assertRaisesRegex(MigrationError, 'new_chat_links'):
            export_windows(self.source, self.package)
        self.assertEqual(live_files(self.source), before)
        with closing(sqlite3.connect(self.source / STATE)) as db, db:
            db.execute('DROP TABLE new_chat_links')
        export_windows(self.source, self.package)
        with closing(sqlite3.connect(self.target / STATE)) as db, db:
            db.execute('CREATE TRIGGER future_change AFTER INSERT ON threads BEGIN UPDATE threads SET title=\'changed\' WHERE id=NEW.id; END')
        before = live_files(self.target)
        with self.assertRaisesRegex(MigrationError, '触发器'):
            self.plan()
        self.assertEqual(live_files(self.target), before)

    def test_cross_version_interruption_restores_original_then_retries(self):
        self.prepare()
        export_windows(self.source, self.package)
        before = live_files(self.target)
        plan = self.plan()
        with self.assertRaises(Interrupted):
            self.apply(plan, inject=fail_after_state(Interrupted))
        recover_native(self.target, idle=IDLE)
        self.assertEqual(live_files(self.target), before)
        self.apply(self.plan())
        self.assertEqual(self.plan()['files_to_change'], 0)
