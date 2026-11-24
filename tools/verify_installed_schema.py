"""Start an isolated local Codex server and seed databases with fictional history."""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_migration import HISTORY, HISTORY_TABLES, STATE, THREAD_TABLES, fingerprint, json_bytes, require, write_index
from schema_compat import inspect_database, tables
from migration_workflow import install_native, plan_native
from windows_export import export_windows


@contextmanager
def app_server(executable, profile, scratch):
    """Use an empty fake home and file credential storage; request no model turns."""
    scratch.mkdir(exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix='fake-home-', dir=scratch))
    tmp = home / 'tmp'
    tmp.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k.upper() in ('SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT')}
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'):
        env[key] = str(home)
    deny = 'http://127.0.0.1:1'
    env.update(CODEX_HOME=str(profile), TMP=str(tmp), TEMP=str(tmp), TMPDIR=str(tmp), OPENAI_BASE_URL=deny,
               HTTP_PROXY=deny, HTTPS_PROXY=deny, ALL_PROXY=deny, NO_PROXY='')
    command = [executable, 'app-server', '--listen', 'stdio://', '-c', 'cli_auth_credentials_store="file"',
               '-c', 'mcp_oauth_credentials_store="file"']
    with subprocess.Popen(command, cwd=scratch, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, encoding='utf-8') as proc:
        replies = queue.Queue()

        def read():
            try:
                for line in proc.stdout:
                    message = json.loads(line)
                    if 'id' in message:
                        replies.put(message)
            finally:
                replies.put(None)

        def send(data):
            proc.stdin.write(json.dumps(data) + '\n')
            proc.stdin.flush()

        request_id = 0

        def call(method, params):
            nonlocal request_id
            request_id += 1
            send({'id': request_id, 'method': method, 'params': params})
            reply = replies.get(timeout=10)
            require(reply and reply['id'] == request_id and 'result' in reply,
                    f'Isolated Codex request failed: {method}: {reply.get("error") if reply else "no response"}')
            return reply['result']

        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        try:
            call('initialize', {'clientInfo': {'name': 'migration_fixture_schema', 'version': '1'},
                                'capabilities': {'experimentalApi': True, 'requestAttestation': False}})
            send({'method': 'initialized'})
            yield call
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            worker.join(timeout=2)


def initialize(executable, profile, scratch):
    """Create both native databases without authenticating or starting a model turn."""
    profile.mkdir()
    with app_server(executable, profile, scratch) as call:
        thread = call('thread/start', {'cwd': str(scratch), 'ephemeral': False, 'historyMode': 'paginated',
                                      'threadSource': 'user', 'approvalPolicy': 'never', 'sandbox': 'read-only'})['thread']
        tid = thread['id']
        call('thread/inject_items', {'threadId': tid, 'items': [{'type': 'message', 'role': 'user',
             'content': [{'type': 'input_text', 'text': 'Fictional offline schema initialization'}]}]})
    for name in (STATE, HISTORY):
        require((profile / name).is_file(), f'Isolated Codex did not initialize {name}')
        with closing(sqlite3.connect(profile / name)) as db, db:
            inspect_database(db, name)
            if name == STATE:
                for table in THREAD_TABLES:
                    db.execute(f'DELETE FROM {table} WHERE thread_id=?', (tid,))
                if 'session_model_ids' in tables(db):
                    db.execute('DELETE FROM session_model_ids WHERE session_id=?', (tid,))
                db.execute('DELETE FROM threads WHERE id=?', (tid,))
            else:
                for table in reversed(HISTORY_TABLES):
                    db.execute(f'DELETE FROM {table} WHERE thread_id=?', (tid,))
    # Remove only the initialization rollout, so it cannot be rediscovered by the isolated server.
    for name in ('sessions', 'archived_sessions'):
        for rollout in (profile / name).rglob(f'*-{tid}.jsonl*'):
            rollout.unlink()


def seed(profile, project, message, *, tid=None, reply=None):
    tid, turn = tid or str(uuid.uuid4()), str(uuid.uuid4())
    user_id, reply_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    roll = profile / now.strftime(f'sessions/%Y/%m/%d/rollout-%Y-%m-%dT%H-%M-%S-{tid}.jsonl')
    roll.parent.mkdir(parents=True, exist_ok=True)
    workspace = profile.parent / 'project'
    workspace.mkdir(exist_ok=True)
    path = r'D:\Fictional\Project' if project else str(workspace)
    reply = reply or f'Fictional reply to: {message}'
    clock = int(now.timestamp())
    stamp = now.isoformat()
    timing = {'turn_id': turn, 'root_turn_id': turn, 'started_at': clock}
    user_item = {'type': 'userMessage', 'id': user_id, 'clientId': None,
                 'content': [{'type': 'text', 'text': message, 'text_elements': []}]}
    reply_item = {'type': 'agentMessage', 'id': reply_id, 'text': reply, 'phase': None}
    events = [
        ('session_meta', {'id': tid, 'session_id': tid, 'timestamp': stamp, 'cwd': path,
                          'originator': 'codex_app', 'cli_version': '0.169.0', 'source': 'vscode',
                          'model_provider': 'openai', 'history_mode': 'paginated', 'thread_source': 'user'}),
        ('event_msg', {'type': 'task_started', **timing}),
        ('event_msg', {'type': 'user_message', 'message': message, 'images': [], 'local_images': [], 'text_elements': []}),
        ('event_msg', {'type': 'agent_message', 'message': reply}),
        ('event_msg', {'type': 'task_complete', **timing, 'last_agent_message': reply, 'completed_at': clock, 'duration_ms': 0}),
    ]
    lines = [(json.dumps({'timestamp': stamp, 'ordinal': ordinal, 'type': kind, 'payload': event}, ensure_ascii=False) + '\n').encode()
             for ordinal, (kind, event) in enumerate(events)]
    raw = b''.join(lines)
    roll.write_bytes(raw)
    with closing(sqlite3.connect(profile / STATE)) as db, db:
        if project:
            db.execute('INSERT INTO projects(id,name,metadata,position,created_at_ms,updated_at_ms) VALUES (?,?,?,0,?,?)',
                       (project, 'Fictional project', '{}', clock * 1000, clock * 1000))
            db.execute('INSERT INTO project_roots (project_id,position,path) VALUES (?,0,?)', (project, path))
        db.execute('INSERT INTO threads(id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,sandbox_policy,approval_mode,'
                   'thread_source,history_mode,originator,project_id,has_user_event) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)',
                   (tid, str(roll), clock, clock, 'vscode', 'openai', path, message, '{"type":"read-only"}',
                    'never', 'user', 'paginated', 'codex_app', project))
    with closing(sqlite3.connect(profile / HISTORY)) as db, db:
        db.execute('INSERT INTO thread_turns(thread_id,turn_id,rollout_ordinal,status,started_at,completed_at,duration_ms,'
                   'first_user_item_id,final_agent_item_id,rollout_byte_offset,rollout_end_ordinal,rollout_end_byte_offset,root_turn_id) '
                   "VALUES (?,?,1,'completed',?,?,0,?,?,?,4,?,?)",
                   (tid, turn, clock, clock, user_id, reply_id, len(lines[0]), len(raw), turn))
        db.execute("INSERT INTO thread_items(thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json,item_type,updated_at_ordinal) "
                   "VALUES (?,?,?,2,?,?,'userMessage',2)", (tid, turn, user_id, clock * 1000, json.dumps(user_item)))
        db.execute("INSERT INTO thread_items(thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json,item_type,updated_at_ordinal) "
                   "VALUES (?,?,?,3,?,?,'agentMessage',3)", (tid, turn, reply_id, clock * 1000, json.dumps(reply_item)))
        db.execute('INSERT INTO thread_history_projection_state(thread_id,next_rollout_byte_offset,next_rollout_ordinal) VALUES (?,?,?)',
                   (tid, len(raw), len(lines)))
    write_index(profile / 'session_index.jsonl', {tid: {'id': tid, 'thread_name': message, 'updated_at': now.isoformat()}})
    return tid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default=shutil.which('codex.exe'))
    parser.add_argument('--scratch', type=Path, default=Path.cwd(), help='existing development directory for temporary files')
    args = parser.parse_args()
    require(sys.platform == 'win32' and args.codex, 'Run on Windows with an installed Codex CLI')
    with tempfile.TemporaryDirectory(prefix='.test-native-', dir=args.scratch.resolve(strict=True)) as temp:
        root = Path(temp)
        source, target, bundle = root / 'win', root / 'mac-copy', root / 'fixture.zip'
        for item in (source, target):
            initialize(str(Path(args.codex).resolve(strict=True)), item, root / (item.name + '-scratch'))
        project = str(uuid.uuid4())
        migrated = seed(source, project, 'Fictional Windows message')
        surviving = seed(target, None, 'Fictional Mac message')
        before = {p.relative_to(source): fingerprint(p) for p in source.rglob('*') if p.is_file()}
        idle = lambda _: None  # The isolated app-server processes have already exited.
        export = export_windows(source, bundle)
        plan = plan_native(bundle, target, idle=idle, scratch=root)
        installed = install_native(bundle, target, plan['plan_id'], idle=idle, scratch=root)
        retry = plan_native(bundle, target, idle=idle, scratch=root)
        with closing(sqlite3.connect(target / STATE)) as db:
            require(dict(db.execute('SELECT id,project_id FROM threads')) == {surviving: None, migrated: project}, 'Thread or project mismatch')
        with closing(sqlite3.connect(target / HISTORY)) as db:
            row = db.execute("SELECT item_json FROM thread_items WHERE thread_id=? AND item_type='userMessage'", (migrated,)).fetchone()
            require(json.loads(row[0])['content'][0]['text'] == 'Fictional Windows message', 'Body mismatch')
        require(before == {p.relative_to(source): fingerprint(p) for p in source.rglob('*') if p.is_file()}, 'Source changed')
        require(retry['files_to_change'] == 0, 'A second import would change data')
        print(json_bytes({'native_state_schema': True, 'native_history_schema': True, 'exported': export['threads'],
                          'planned_additions': plan['added_threads'], 'total_threads': installed['total_threads'],
                          'repeat_writes': retry['files_to_change'], 'source_unchanged': True}).decode(), end='')


if __name__ == '__main__':
    main()
