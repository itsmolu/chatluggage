from contextlib import closing
from pathlib import Path
from platform_paths import temporary_directory
import sqlite3
import tempfile
import unittest
from zipfile import ZipFile

from demo import INCOMING_ID, PENDING, SimulatedInterruption, create_fixture, install_exercise, recover, run_demo, tree
from history_migration import HISTORY, INDEX, STATE, MigrationError, build_proposal, export_fixture, rehearse


class MigrationTests(unittest.TestCase):
    def setUp(self):
        # The Windows app sandbox's default temp path can exceed MAX_PATH.
        tmp = self.enterContext(temporary_directory(Path(__file__).parent, '.test-'))
        self.root = Path(tmp).resolve()
        self.source = self.root / 'win'
        self.target = self.root / 'mac'
        self.package = self.root / 'full.zip'
        create_fixture(self.source, windows=True)
        create_fixture(self.target, windows=False)

    def test_demo_restores_then_retries_without_writes(self):
        report = run_demo(self.root / 'demo')
        self.assertTrue(report['fault_exercise']['rollback_restored_original_tree'])
        self.assertTrue(report['fault_exercise']['restart_recovery_restored_original_tree'])
        self.assertEqual(report['rehearsal']['total_threads'], 2)
        self.assertEqual(report['post_install']['repeat_install_changed_files'], 0)

    def test_export_includes_changes_committed_only_to_wal(self):
        with closing(sqlite3.connect(self.source / HISTORY)) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone(), ('wal',))
            conn.execute('PRAGMA wal_autocheckpoint=0')
            conn.execute('INSERT INTO thread_turns VALUES (?,2,?)', (INCOMING_ID, '{}'))
            conn.commit()
            self.assertGreater((self.source / (HISTORY + '-wal')).stat().st_size, 0)
            before = tree(self.source)
            export_fixture(self.source, self.package)
            actual = rehearse(self.package, self.target)
            self.assertEqual(actual['added_turns'], 2)
            self.assertEqual(tree(self.source), before)

    def test_checked_zip_rejects_modified_index(self):
        export_fixture(self.source, self.package)
        tampered = self.root / 'changed.zip'
        with ZipFile(self.package) as old, ZipFile(tampered, 'x') as new:
            for name in old.namelist():
                data = old.read(name)
                new.writestr(name, data + b'\n' if name == INDEX else data)
        before = tree(self.target)
        with self.assertRaisesRegex(MigrationError, 'verification'):
            rehearse(tampered, self.target)
        self.assertEqual(tree(self.target), before)

    def test_export_ignores_unrelated_files(self):
        (self.source / 'auth.json').write_text('{"synthetic":true}', encoding='utf-8')
        (self.source / 'config.toml').write_text('synthetic = true\n', encoding='utf-8')
        export_fixture(self.source, self.package)
        with ZipFile(self.package) as archive:
            self.assertTrue({INDEX, STATE, HISTORY}.issubset(archive.namelist()))
            self.assertNotIn('auth.json', archive.namelist())
            self.assertNotIn('config.toml', archive.namelist())

    def test_incompatible_target_left_untouched(self):
        export_fixture(self.source, self.package)
        with closing(sqlite3.connect(self.target / STATE)) as conn, conn:
            conn.execute('UPDATE _sqlx_migrations SET checksum=?', (b'other-platform-checksum',))
        before = tree(self.target)
        with self.assertRaisesRegex(MigrationError, 'migration checksums differ'):
            rehearse(self.package, self.target)
        self.assertEqual(tree(self.target), before)

    def test_explicit_workspace_mapping(self):
        export_fixture(self.source, self.package)
        workspace = self.root / 'actual-workspace'
        (workspace / 'src').mkdir(parents=True)
        before = tree(self.target)
        plan = rehearse(self.package, self.target, {'Project-001': str(workspace)})
        self.assertEqual(plan['mapped_sessions'][INCOMING_ID]['workspace'], str(workspace / 'src'))
        self.assertEqual(tree(self.target), before)
        self.assertEqual(tree(workspace), {'src': None})

    def test_recovery_refuses_to_overwrite_an_intervening_change(self):
        export_fixture(self.source, self.package)
        stage, txn = self.root / 'stage', self.root / 'txn'
        stage.mkdir()
        txn.mkdir()
        proposal = build_proposal(self.package, self.target, stage)

        def interrupt(_, name):
            if name == STATE:
                raise SimulatedInterruption()

        with self.assertRaises(SimulatedInterruption):
            install_exercise(self.target, proposal, txn, interrupt)
        index = self.target / INDEX
        index.write_bytes(index.read_bytes() + b'\n')
        before = tree(self.target)
        with self.assertRaisesRegex(MigrationError, 'target file changed'):
            recover(self.target, txn)
        self.assertEqual(tree(self.target), before)
        self.assertTrue((txn / PENDING).exists())


if __name__ == '__main__':
    unittest.main()
