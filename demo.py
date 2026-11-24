"""Fixtures and fault injection. Never use these routines on a real profile."""

from contextlib import closing
import json
import os
from pathlib import Path
from platform_paths import io_path, temporary_directory
import shutil
import sqlite3
import tempfile

from history_migration import (CORE, HISTORY, HISTORY_TABLES, INDEX, MAGIC, MARKER, SIDECARS, STATE,
                               THREAD_TABLES, UI, allowed_member, build_proposal, checked, export_fixture,
                               fingerprint, json_bytes, profile, read_index, read_json, rehearse, require,
                               validate_db, write_index)


LOCAL_ID = '00000000-0000-4000-8000-000000000001'
INCOMING_ID = '00000000-0000-4000-8000-000000000002'
PROJECT_ID = '00000000-0000-4000-8000-000000000101'
MESSAGE = '这是虚构的 Windows 测试聊天。'
PENDING = 'pending.json'
MIGRATIONS = 'CREATE TABLE _sqlx_migrations(version INTEGER PRIMARY KEY, success INTEGER, checksum BLOB);'
STATE_SCHEMA = '''
    CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL);
    CREATE TABLE project_roots(project_id TEXT REFERENCES projects, position INTEGER, path TEXT NOT NULL,
                               PRIMARY KEY(project_id,position));
    CREATE TABLE thread_sections(id TEXT PRIMARY KEY, name TEXT, appearance TEXT);
    CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT, rollout_path TEXT NOT NULL, cwd TEXT NOT NULL,
                         project_id TEXT REFERENCES projects, thread_section_id TEXT REFERENCES thread_sections);
    CREATE TABLE project_idempotency_keys(key TEXT PRIMARY KEY, project_id TEXT REFERENCES projects);
    CREATE TABLE thread_spawn_edges(parent_thread_id TEXT REFERENCES threads, child_thread_id TEXT REFERENCES threads,
                                    PRIMARY KEY(parent_thread_id,child_thread_id));
'''


class InjectedFailure(RuntimeError):
    pass


class SimulatedInterruption(BaseException):
    pass


def create_fixture(root, *, windows):
    root.mkdir()
    tid = INCOMING_ID if windows else LOCAL_ID
    title = 'Windows 虚构会话' if windows else 'Mac 虚构原会话'
    workspace = 'D:\\Example\\TestProject' if windows else str(root / 'local-workspace')
    cwd = workspace + '\\src' if windows else workspace
    if not windows:
        Path(cwd).mkdir()
    rollout = root / f'sessions/2026/01/01/rollout-2026-01-01T00-00-00-{tid}.jsonl'
    rollout.parent.mkdir(parents=True)
    message = MESSAGE if windows else '这是虚构的 Mac 原聊天。'
    with closing(sqlite3.connect(root / STATE)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(MIGRATIONS + STATE_SCHEMA)
        for table in THREAD_TABLES:
            db.execute(f'CREATE TABLE {table}(thread_id TEXT PRIMARY KEY REFERENCES threads, payload TEXT)')
        db.execute('INSERT INTO _sqlx_migrations VALUES (62,1,?)', (b'synthetic-state-schema',))
        if windows:
            db.execute('INSERT INTO projects VALUES (?,?,0)', (PROJECT_ID, '测试项目'))
            db.execute('INSERT INTO project_roots VALUES (?,0,?)', (PROJECT_ID, workspace))
            db.execute('INSERT INTO project_idempotency_keys VALUES (?,?)', ('synthetic-alias', PROJECT_ID))
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,NULL)',
                   (tid, title, str(rollout), cwd, PROJECT_ID if windows else None))
    with closing(sqlite3.connect(root / HISTORY)) as db, db:
        db.executescript(MIGRATIONS)
        for table in HISTORY_TABLES:
            db.execute(f'CREATE TABLE {table}(thread_id TEXT, ordinal INTEGER, body TEXT, PRIMARY KEY(thread_id,ordinal))')
            db.execute(f'INSERT INTO {table} VALUES (?,1,?)', (tid, message if table == 'thread_items' else '{}'))
        db.execute('INSERT INTO _sqlx_migrations VALUES (8,1,?)', (b'synthetic-history-schema',))
    event = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': message}}
    rollout.write_text(json.dumps(event, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    write_index(root / INDEX, {tid: {'id': tid, 'thread_name': title}})
    ui = {'local-projects': {PROJECT_ID: {'name': '测试项目', 'rootPaths': [workspace]}} if windows else {},
          'thread-project-assignments': {tid: {'projectKind': 'local', 'projectId': PROJECT_ID}} if windows else {},
          'projectless-thread-ids': [] if windows else [tid], 'project-order': [PROJECT_ID] if windows else []}
    (root / UI).write_bytes(json_bytes(ui))
    (root / MARKER).write_text(MAGIC + '\n', encoding='ascii')


def tree(root):
    """Compare every fixture file and directory, including empty workspace roots."""
    return {p.relative_to(root).as_posix(): None if p.is_dir() else fingerprint(p) for p in root.rglob('*')}


def write_atomic(path, data):
    path = io_path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.migration-', delete=False) as stream:
        staged = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            staged.unlink(missing_ok=True)
            raise
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def sidecars_absent(target):
    require(all(fingerprint(checked(target, db + suffix)) is None
                for db in (STATE, HISTORY) for suffix in SIDECARS), 'Exercise requires closed standalone fixture databases')


def recover(target, transaction):
    target = profile(target)
    sidecars_absent(target)
    path = checked(transaction, PENDING)
    record = read_json(path)
    require(record['target'] == str(target), 'Recovery belongs to another profile')
    backup = checked(transaction, 'backup')
    for name, pair in record['files'].items():
        require(allowed_member(name), 'Unexpected recovery file')
        observed = fingerprint(checked(target, name))
        require(observed in (pair['old'], pair['new']), 'Recovery stopped: a target file changed after installation began')
        require(pair['old'] is None or fingerprint(checked(backup, name)) == pair['old'], 'Recovery backup failed verification')
    for name, pair in record['files'].items():
        destination = checked(target, name)
        if pair['old'] is None:
            io_path(destination).unlink(missing_ok=True)
        elif fingerprint(destination) != pair['old']:
            write_atomic(destination, io_path(checked(backup, name)).read_bytes())
        require(fingerprint(destination) == pair['old'], 'Recovery did not restore the original file')
    for name in sorted(record['dirs'], key=lambda name: name.count('/'), reverse=True):
        directory = checked(target, name)
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    path.unlink()


def install_exercise(target, proposal, transaction, inject=None):
    target = profile(target)
    sidecars_absent(target)
    _, proposed, observed, additions, directories = proposal
    require(not (transaction / PENDING).exists(), 'Recover the incomplete installation before retrying')
    require(all(fingerprint(checked(target, name)) == digest for name, digest in observed.items()),
            'The fixture changed since the plan was built')
    # Install session resources before indexes, then replace the databases.
    order = [*additions, INDEX, UI, STATE, HISTORY]
    files = {name: {'old': observed[name], 'new': fingerprint(checked(proposed, name))} for name in order}
    files = {name: pair for name, pair in files.items() if pair['old'] != pair['new']}
    backup = transaction / 'backup'
    backup.mkdir()
    for name, pair in files.items():
        require(pair['new'] is not None, 'Proposed file missing')
        if pair['old'] is not None:
            original = checked(backup, name)
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(io_path(checked(target, name)), io_path(original))
            require(fingerprint(original) == pair['old'], 'Backup failed verification')
    folders = set(directories) | {Path(name).parent.as_posix() for name in files}
    folders.discard('.')
    folders |= {p.as_posix() for folder in folders for p in Path(folder).parents if p != Path('.')}
    new_folders = sorted((name for name in folders if not checked(target, name).is_dir()),
                         key=lambda name: (name.count('/'), name))
    write_atomic(transaction / PENDING, json_bytes({'target': str(target), 'files': files, 'dirs': new_folders}))
    try:
        for name in new_folders:
            checked(target, name).mkdir()
        for count, (name, pair) in enumerate(files.items(), start=1):
            destination = checked(target, name)
            require(fingerprint(destination) == pair['old'], 'The target changed during installation')
            write_atomic(destination, io_path(checked(proposed, name)).read_bytes())
            if inject is not None:
                inject(count, name)
        require(all(fingerprint(checked(target, name)) == pair['new'] for name, pair in files.items()),
                'Installed file failed verification')
    except Exception:
        recover(target, transaction)
        raise
    (transaction / PENDING).unlink()
    return len(files)


def verify_installed(target, original):
    index = read_index(target / INDEX)
    require(set(index) == {LOCAL_ID, INCOMING_ID}, 'Installed session index differs from the demonstration plan')
    with closing(sqlite3.connect((target / STATE).as_uri() + '?mode=ro', uri=True)) as db:
        validate_db(db)
        rows = {row[0]: row[1:] for row in db.execute('SELECT id,title,rollout_path,cwd,project_id FROM threads')}
        require(set(rows) == {LOCAL_ID, INCOMING_ID} and rows[LOCAL_ID][0] == 'Mac 虚构原会话',
                'Existing session was not preserved')
        _, rollout, cwd, project = rows[INCOMING_ID]
        require(project == PROJECT_ID and Path(cwd).is_dir() and io_path(rollout).is_file(),
                'Imported session file or project association is missing')
        require(MESSAGE.encode() in io_path(rollout).read_bytes(), 'The imported synthetic rollout is unreadable')
        stored_root, = db.execute('SELECT path FROM project_roots WHERE project_id=?', (project,)).fetchone()
        require(Path(cwd).parent == Path(stored_root), 'Session is under the wrong project root')
    with closing(sqlite3.connect((target / HISTORY).as_uri() + '?mode=ro', uri=True)) as db:
        validate_db(db)
        value = db.execute('SELECT body FROM thread_items WHERE thread_id=?', (INCOMING_ID,)).fetchone()
        require(value == (MESSAGE,), 'Imported conversation body did not match the fixture')
    ui = read_json(target / UI)
    require(ui['local-projects'][PROJECT_ID]['rootPaths'] == [stored_root] and
            ui['thread-project-assignments'][INCOMING_ID]['projectId'] == PROJECT_ID,
            'Application project metadata did not match the imported database')
    require(all(fingerprint(checked(target, n)) == value for n, value in original.items()
                if n.startswith('sessions/') and value), 'Existing session bytes changed')


def run_demo(output):
    output = output.absolute()
    require(output.resolve() == output and all(not output.is_relative_to(path.resolve()) for path in
            [Path.home() / '.codex', *([Path(os.environ['CODEX_HOME'])] if os.environ.get('CODEX_HOME') else [])]),
            'The demo output must be outside the active Codex profile')
    output.mkdir()
    # Keep fixture rollouts and proposals on short paths even if the ZIP was
    # unpacked deep inside Downloads on Windows.
    source, target = output / 'windows-fixture', output / 'mac-fixture'
    create_fixture(source, windows=True)
    create_fixture(target, windows=False)
    before_source, before = tree(source), tree(target)
    package = output / 'synthetic-migration.zip'
    exported = export_fixture(source, package)
    plan = rehearse(package, target)
    require(tree(source) == before_source and tree(target) == before, 'Planning changed an input fixture')
    require(plan['added_threads'] == plan['added_turns'] == 1, 'Unexpected demonstration plan')
    with temporary_directory(output, 'demo-') as scratch:
        stage = Path(scratch)
        proposal = build_proposal(package, target, stage)

        def inject_after_state(error):
            def change(count, name):
                if name == STATE:
                    require(count >= 2 and tree(target) != before, 'No partial install was performed')
                    raise error('Injected after replacing the state database')
            return change

        expected = ((InjectedFailure, 'rollback-on-error'), (SimulatedInterruption, 'recover-on-restart'))
        for error, label in expected:
            transaction = stage / label
            transaction.mkdir()
            try:
                install_exercise(target, proposal, transaction, inject_after_state(error))
            except error:
                if error is SimulatedInterruption:
                    require((transaction / PENDING).is_file() and tree(target) != before,
                            'The interruption did not leave pending recovery data')
                    recover(target, transaction)
                require(not (transaction / PENDING).exists() and tree(target) == before,
                        'Recovery did not restore the entire target tree')
            else:
                raise RuntimeError('Fault injection did not run')

        transaction = stage / 'success'
        transaction.mkdir()
        count = install_exercise(target, proposal, transaction)
        verify_installed(target, before)
        after = tree(target)
        retry_stage = stage / 'retry'
        retry_stage.mkdir()
        retry_proposal = build_proposal(package, target, retry_stage)
        retry_plan = retry_proposal[0]
        retry_transaction = stage / 'retry-transaction'
        retry_transaction.mkdir()
        retried_files = install_exercise(target, retry_proposal, retry_transaction)
        require(retry_plan['added_threads'] == retry_plan['added_turns'] == 0 and
                retry_plan['kept_duplicate_threads'] == 1 and retried_files == 0 and tree(target) == after,
                'Retry was not idempotent')
        require(tree(source) == before_source, 'The Windows fixture changed')

    report = {'fixture': 'synthetic only', 'windows_fixture_unchanged': True,
              'rehearsal_left_target_unchanged': True, 'zip': exported, 'rehearsal': plan,
              'fault_exercise': {'injected_after': STATE, 'rollback_restored_original_tree': True,
                                 'restart_recovery_restored_original_tree': True, 'installed_files_on_retry': count},
              'post_install': {'local_thread_preserved': True, 'incoming_body_read': True,
                               'project_association_verified': True, 'repeat_install_changed_files': retried_files,
                               'repeat_install_plan': retry_plan,
                               'macos_app_ui_tested': False}}
    (output / 'rehearsal-report.json').write_bytes(json_bytes(report))
    return report
