"""Check captured Codex schemas and read migrated fictional history with the installed CLI."""

import argparse
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo import INCOMING_ID, LOCAL_ID, PROJECT_ID
from history_migration import HISTORY, STATE, json_bytes, require
from migration_workflow import install_native, plan_native
from platform_paths import temporary_directory
from tests.test_compatibility import create_schema_profile, schema_metadata, zstd_raw_frame
from tests.test_native import live_files
from verify_installed_schema import app_server
from windows_export import export_windows


def verify(codex, scratch):
    results = []
    idle = lambda _: None  # Every profile below is created here, away from live data.
    with temporary_directory(scratch, 'compat-check-') as root:
        for source_version, target_version, compressed in (('62-8', '62-8', False), ('62-8', '64-9', False),
                                                           ('64-9', '64-9', False), ('64-9', '64-9', True)):
            case = root / (source_version + '-to-' + target_version + ('-zstd' if compressed else ''))
            case.mkdir()
            source, target, package = case / 'windows', case / 'mac-copy', case / 'history.zip'
            original_rollout = create_schema_profile(source, source_version, True)
            if compressed:
                compressed_rollout = Path(str(original_rollout) + '.zst')
                compressed_rollout.write_bytes(zstd_raw_frame(original_rollout.read_bytes()))
                original_rollout.unlink()
                original_rollout = compressed_rollout
            create_schema_profile(target, target_version, False)
            if source_version == '64-9':
                with closing(sqlite3.connect(source / STATE)) as db, db:
                    db.execute("INSERT INTO session_model_ids VALUES (?,'synthetic','external-key',0,'{}')", (INCOMING_ID,))
            source_before, target_before, metadata = live_files(source), live_files(target), schema_metadata(target)
            export_windows(source, package)
            plan = plan_native(package, target, idle=idle, scratch=root)
            require(live_files(target) == target_before, 'The preview changed the target')
            install_native(package, target, plan['plan_id'], idle=idle, scratch=root)
            require(schema_metadata(target) == metadata, 'The merge changed target schema or SQLx records')
            with closing(sqlite3.connect(target / STATE)) as db:
                rollout, cwd, pid = db.execute('SELECT rollout_path,cwd,project_id FROM threads WHERE id=?', (INCOMING_ID,)).fetchone()
                payload = Path(rollout + '.zst') if compressed else Path(rollout)
                require(pid == PROJECT_ID and payload.read_bytes() == original_rollout.read_bytes(), 'Project or rollout mismatch')
                if source_version == '64-9':
                    require(db.execute('SELECT external_key FROM session_model_ids WHERE session_id=?', (INCOMING_ID,)).fetchone()
                            == ('external-key',), 'Model identifier association was lost')
            with closing(sqlite3.connect(target / HISTORY)) as db:
                require(db.execute('SELECT count(*) FROM thread_items WHERE thread_id=?', (INCOMING_ID,)).fetchone() == (2,),
                        'Expected a user item and an agent item')
            repeat = plan_native(package, target, idle=idle, scratch=root)
            require(repeat['files_to_change'] == repeat['directories_to_create'] == 0, 'Repeated import has pending writes')
            no_op = install_native(package, target, repeat['plan_id'], idle=idle, scratch=root)
            require(not no_op['target_changed'], 'Repeated apply changed the profile')
            readback = 'not_run'  # Reading a 62/8 target with the new CLI would upgrade it.
            if target_version == '64-9':
                with app_server(codex, target, case / 'client') as call:
                    listed = call('thread/list', {'limit': 10, 'currentAccountOnly': False, 'modelProviders': []})['data']
                    require({t['id'] for t in listed} == {INCOMING_ID, LOCAL_ID}, 'Codex did not list both fictional chats')
                    for tid, expected in ((INCOMING_ID, 'Fictional Windows message'), (LOCAL_ID, 'Fictional Mac message')):
                        turns = call('thread/turns/list', {'threadId': tid, 'limit': 5, 'itemsView': 'full'})['data']
                        require(len(turns) == 1 and turns[0]['status'] == 'completed', 'Codex turn readback failed')
                        items = turns[0]['items']
                        require(len(items) == 2 and items[0]['content'][0]['text'] == expected
                                and items[1]['text'] == f'Fictional reply to: {expected}', 'Codex item readback failed')
                    imported = call('thread/read', {'threadId': INCOMING_ID, 'includeTurns': False})['thread']
                    resumed = call('thread/resume', {'threadId': INCOMING_ID, 'excludeTurns': True, 'cwd': cwd})
                    require(imported['projectId'] == PROJECT_ID and Path(resumed['cwd']) == Path(cwd),
                            f"Codex project or resume directory readback failed: cwd={resumed.get('cwd')} expected={cwd}")
                readback = 'passed'
            require(live_files(source) == source_before, 'Windows source changed')
            results.append({'source': source_version, 'target': target_version, 'compressed': compressed, 'merge': 'passed',
                            'read_only_plan': 'passed', 'target_metadata_preserved': True,
                            'resume_with_mapped_cwd': readback, 'repeat_writes': 0, 'source_unchanged': True, 'codex_readback': readback})
    return {'status': 'passed', 'synthetic_only': True,
            'codex_cli': subprocess.check_output([codex, '--version'], text=True).strip(), 'cases': results,
            'application_process_guard': 'not_run', 'desktop_sidebar': 'not_run'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default=shutil.which('codex.exe') or shutil.which('codex'))
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    require(args.codex, 'Provide --codex with an installed Codex CLI')
    sys.stdout.buffer.write(json_bytes(verify(str(Path(args.codex).resolve(strict=True)), args.scratch.resolve(strict=True))))


if __name__ == '__main__':
    main()
