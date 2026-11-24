"""Exercise the public picker and offline merge against two months of synthetic data."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from chat_selection import prompt_selection
from demo import INCOMING_ID, LOCAL_ID, PROJECT_ID
from history_migration import HISTORY, HISTORY_TABLES, INDEX, STATE, UI, WORKSPACES, MigrationError, read_index, write_index
from migration_workflow import install_native, plan_native
from platform_paths import temporary_directory
from tests.test_native import IDLE, ROOT_CODE, connect, create_profile, live_files
from windows_export import export_windows

OTHER = '00000000-0000-4000-8000-000000000003'


class SelectionTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(temporary_directory(Path(__file__).resolve().parent, '.select-'))
        self.mac = root / 'mac'
        self.zip = root / 'windows.zip'
        win = root / 'win'
        first_file = create_profile(win, True)
        create_profile(self.mac, False)
        self.newer = f'sessions/2026/02/02/rollout-2026-02-02T00-00-00-{OTHER}.jsonl'
        rollout = win / self.newer
        rollout.parent.mkdir(parents=True)
        rollout.write_bytes(b'fictional February chat\n')
        self.first = first_file.relative_to(win)
        with connect(win / STATE) as db, db:
            db.execute('INSERT INTO threads SELECT ?,?, ?,cwd,NULL,NULL,id,sandbox_policy,approval_mode,NULL '
                       'FROM threads WHERE id=?', (OTHER, 'February chat', 'D:\\Codex\\' + self.newer, INCOMING_ID))
            db.execute('INSERT INTO thread_spawn_edges VALUES (?,?)', (INCOMING_ID, OTHER))
        with connect(win / HISTORY) as db, db:
            for table in HISTORY_TABLES:
                db.execute(f'INSERT INTO {table} VALUES (?,1,?)', (OTHER, 'February history'))
        write_index(win / INDEX, {**read_index(win / INDEX), OTHER: {'id': OTHER, 'thread_name': 'February chat'}})
        (win / 'attachments').mkdir()
        (win / 'attachments/unassigned.txt').write_text('unlinked')
        export_windows(win, self.zip, include_assets=True)
        self.kw = {'idle': IDLE, 'scratch': root}

    def plan(self, selection):
        return plan_native(self.zip, self.mac, selection=selection, **self.kw)

    def apply(self, token, selection):
        return install_native(self.zip, self.mac, token, selection=selection, **self.kw)

    def test_pick_one_month_install_repeat_then_import_remaining(self):
        before = live_files(self.mac)
        with patch('builtins.input', side_effect=['2', '1', 'a']), redirect_stdout(StringIO()) as output:
            plan = self.plan(prompt_selection)
        self.assertIn('2026-02', output.getvalue())
        self.assertIn('February chat', output.getvalue())
        self.assertEqual((plan['selection'], plan['added_threads'], plan['added_turns']), ('1', 1, 1))
        self.assertEqual(plan['projects'], {})
        self.assertEqual(live_files(self.mac), before)
        with self.assertRaisesRegex(MigrationError, 'no longer matches'):
            self.apply(plan['plan_id'], 'all')
        self.assertEqual(live_files(self.mac), before)
        self.apply(plan['plan_id'], '1')
        with connect(self.mac / STATE) as db:
            self.assertEqual(set(db.execute('SELECT id,attached_thread_parent_id FROM threads')), {(LOCAL_ID, None), (OTHER, None)})
            self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone(), (0,))
            self.assertEqual(db.execute('SELECT count(*) FROM thread_spawn_edges').fetchone(), (0,))
        with connect(self.mac / HISTORY) as db:
            for table in HISTORY_TABLES:
                self.assertEqual(set(t for t, in db.execute(f'SELECT thread_id FROM {table}')), {OTHER, LOCAL_ID})
        self.assertEqual(set(read_index(self.mac / INDEX)), {OTHER, LOCAL_ID})
        self.assertNotIn(INCOMING_ID, (self.mac / UI).read_text('utf8'))
        self.assertFalse((self.mac / self.first).exists())
        self.assertTrue((self.mac / self.newer).is_file())
        self.assertFalse((self.mac / 'attachments').exists())
        self.assertEqual(self.plan('1')['files_to_change'], 0)
        remainder = self.plan('all')
        self.assertEqual((remainder['added_threads'], remainder['kept_duplicate_threads']), (1, 1))
        self.assertEqual(self.apply(remainder['plan_id'], 'all')['total_threads'], 3)
        self.assertTrue((self.mac / self.first).is_file())

    def test_known_legacy_root_reused_for_new_chat_and_repeat(self):
        legacy = self.mac / WORKSPACES / 'Project-01/Root-01'
        legacy.mkdir(parents=True)
        with connect(self.mac / STATE) as db, db:
            db.execute("INSERT INTO projects VALUES (?, 'Test Project',0,'{}',100,100)", (PROJECT_ID,))
            db.execute('INSERT INTO project_roots VALUES (?,0,?)', (PROJECT_ID, str(legacy)))
        before = live_files(self.mac)
        plan = self.plan('2')
        self.assertEqual(plan['projects'][ROOT_CODE]['mapping'], 'legacy')
        self.assertEqual(live_files(self.mac), before)
        self.apply(plan['plan_id'], '2')
        self.assertTrue((legacy / 'src').is_dir())
        with connect(self.mac / STATE) as db:
            self.assertEqual(db.execute('SELECT cwd FROM threads WHERE id=?', (INCOMING_ID,)).fetchone(), (str(legacy / 'src'),))
        self.assertIn(str(legacy), next(iter(json.loads((self.mac / UI).read_bytes())['local-projects'].values()))['rootPaths'])
        repeat = self.plan('2')
        self.assertEqual((repeat['files_to_change'], repeat['directories_to_create']), (0, 0))
        with connect(self.mac / STATE) as db, db:
            db.execute('UPDATE project_roots SET path=?', (str(self.mac / 'sessions'),))
        before = live_files(self.mac)
        with self.assertRaises(MigrationError):
            self.plan('all')
        self.assertEqual(live_files(self.mac), before)

    def test_cancel_and_invalid_selection_leave_target_unchanged(self):
        before = live_files(self.mac)
        with patch('builtins.input', side_effect=['2', '1', '0']), redirect_stdout(StringIO()), self.assertRaises(MigrationError):
            self.plan(prompt_selection)
        with self.assertRaises(MigrationError):
            self.plan('3')
        self.assertEqual(live_files(self.mac), before)

    def test_paged_selection_keeps_checked_items_across_pages(self):
        rows = [('2026-01-01', str(i), f'chat {i}', 'project') for i in range(16)]
        with patch('builtins.input', side_effect=['2', 'a', '1,3-5', 'n', '16', 'p', '3', '']), redirect_stdout(StringIO()) as output:
            self.assertEqual(prompt_selection(rows, {'0'}), '1,4-5,16')
        self.assertIn('2/2', output.getvalue())
        self.assertIn('Mac 已有', output.getvalue())
