"""Verified bundles, SQLite snapshots, and the merge planner."""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import tempfile
import unicodedata
import zipfile
from urllib.parse import quote

from platform_paths import io_path, plain_path, temporary_directory
from schema_compat import MigrationError, compare_databases, copy_session_model_ids, inspect_database, insert_rows, require


STATE = 'state_5.sqlite'
HISTORY = 'thread_history_1.sqlite'
INDEX = 'session_index.jsonl'
UI = '.codex-global-state.json'
CORE = (STATE, HISTORY, INDEX, UI)
MARKER = '.migration-synthetic'
MAGIC = 'codex-history-migration-synthetic-v1'
NATIVE = 'codex-history-migration-v1'
MANIFEST = 'manifest.json'
PROJECT_FIELDS = ('local-projects', 'project-order', 'thread-project-assignments',
                  'sidebar-project-thread-orders', 'projectless-thread-ids',
                  'app-server-project-id-by-legacy-project-id-by-host')
HISTORY_TABLES = ('thread_history_projection_state', 'thread_turns', 'thread_items',
                  'thread_realtime_items')
THREAD_TABLES = ('thread_attachments', 'thread_dynamic_tools', 'thread_read_receipts',
                 'guardian_review_feedback')
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
ROLLOUT = re.compile(rf'rollout-\d{{4}}-\d\d-\d\dT\d\d-\d\d-\d\d-({UUID})(?:_({UUID}))?\.jsonl(?:\.zst)?')
SIDECARS = ('-wal', '-shm', '-journal')
DB_SIDECARS = tuple(name + suffix for name in (STATE, HISTORY) for suffix in SIDECARS)
RECOVERY_DIR = '.codex-history-migration'
WORKSPACES = 'windows-history-workspaces'
NATIVE_PROJECT_FIELDS = ('local-projects', 'project-order', 'thread-project-assignments', 'projectless-thread-ids')
MAX_JSON_BYTES = 32 * 1024**2
MAX_BYTES = 16 * 1024**3
MAX_ENTRIES = 50_000
ASSET_DIRS = ('attachments', 'plans', 'generated_images')
ASSET_SUFFIXES = {'.json', '.txt', '.md', '.png', '.jpg', '.jpeg', '.webp', '.pdf'}
def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def read_json(path):
    source = io_path(path)
    require(source.stat().st_size <= MAX_JSON_BYTES, f'JSON 文件超过大小限制：{path}')
    try:
        return json.loads(source.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise MigrationError(f'JSON 格式不正确：{path}，第 {exc.lineno} 行，第 {exc.colno} 列。') from None


def is_link(path):
    path = io_path(path)
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 1024)


def parts(relative):
    require(isinstance(relative, str), 'A package path is not a string')
    pieces = relative.split('/')
    require(len(pieces) <= 64 and all(p and p not in ('.', '..') and not p.endswith(('.', ' '))
                and len(p.encode('utf-8')) <= 255 and not any(c in '\\:<>"|?*' or ord(c) < 32 for c in p)
                and not re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', p.split('.')[0].rstrip())
                for p in pieces), 'Invalid relative path')
    return pieces


def checked(root, relative):
    path = root
    for piece in parts(relative):
        path /= piece
        try:
            linked = is_link(path)
        except FileNotFoundError:
            continue
        require(not linked, 'Linked files and directories are not supported')
    return path


def fingerprint(path):
    path = io_path(path)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    require(not is_link(path) and stat.S_ISREG(mode), 'Expected a regular file')
    require(path.stat().st_size <= MAX_BYTES, 'File exceeds the size limit')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def profile(root):
    root = root.absolute()
    active = [Path.home() / '.codex']
    if os.environ.get('CODEX_HOME'):
        active.append(Path(os.environ['CODEX_HOME']))
    resolved = root.resolve()
    require(all(not resolved.is_relative_to(path.resolve()) and
                not path.resolve().is_relative_to(resolved) for path in active),
            'Use a synthetic fixture away from the active Codex profile')
    require(root.is_dir() and not is_link(root) and resolved == root, 'Invalid fixture directory')
    mark = checked(root, MARKER)
    require(mark.is_file() and mark.read_text(encoding='ascii').strip() == MAGIC,
            'This prototype accepts only generated synthetic profiles')
    return root


def native_profile(root, *, recovering=False):
    root = plain_path(root.expanduser().absolute())
    require(io_path(root).is_dir(), f'Codex 资料目录不存在：{root}。请用 --source（Windows）或 --target（Mac）指定实际目录。')
    resolved = plain_path(io_path(root).resolve(strict=True))
    require(not is_link(root) and resolved == root, 'Use a real profile directory, without symlinks')
    require(not io_path(root / MARKER).exists(), 'Use the demo commands for synthetic prototype profiles')
    if not recovering:
        for name in (STATE, HISTORY):
            require(io_path(checked(root, name)).is_file(), f'缺少必需的 Codex 数据库：{root / name}。请确认资料目录正确且在这台电脑上使用过 Codex。')
    return root


def database_uri(path, readonly=False):
    mode = '?mode=ro&immutable=1' if readonly else '?mode=rw'
    if os.name == 'nt':
        # Keep the extended path for SQLite and its adjacent journal files.
        return 'file:' + quote(str(io_path(path)), safe='') + mode
    return path.as_uri() + mode


def database(path, readonly=False):
    location = database_uri(path, readonly)
    db = sqlite3.connect(location, uri=True, timeout=3)
    db.execute('PRAGMA trusted_schema=OFF')
    return db


def validate_db(db):
    require(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'SQLite integrity check failed')
    require(not db.execute('PRAGMA foreign_key_check').fetchone(), 'SQLite foreign key check failed')


def signature(db, name=None):
    schema = db.execute("SELECT type,name,tbl_name,replace(coalesce(sql,''),char(13),'') "
                        "FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    columns = 'version,success,hex(checksum)' if name is None else 'version,success,description,hex(checksum)'
    migrations = db.execute(f'SELECT {columns} FROM _sqlx_migrations ORDER BY version').fetchall()
    if name is not None:
        inspect_database(db, name)
    return schema, migrations


def copy_stable(source, dest, filenames):
    before = {n: fingerprint(checked(source, n)) for n in filenames}
    for name, digest in before.items():
        if digest is None:
            continue
        out = checked(dest, name)
        io_path(out.parent).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(io_path(checked(source, name)), io_path(out))
        require(fingerprint(out) == digest, 'Copied file did not match the source')
    require(all(fingerprint(checked(source, n)) == digest for n, digest in before.items()),
            'The source changed during capture; close Codex before retrying')
    return before


def assert_unchanged(root, observed, message):
    require(all(fingerprint(checked(root, n)) == digest for n, digest in observed.items()), message)


def snapshots(source, stage, native=False, observe_shm=False):
    # Carry the WAL, but let SQLite rebuild SHM on the private writable copy.
    files = (*CORE, *(db + s for db in (STATE, HISTORY) for s in ('-wal', '-journal')))
    recorded = copy_stable(source, stage, files)
    if observe_shm:
        for db in (STATE, HISTORY):
            recorded[db + '-shm'] = fingerprint(checked(source, db + '-shm'))
    require(all(recorded[n] for n in ((STATE, HISTORY) if native else CORE)),
            'The profile is missing a required database or index')
    for name in (STATE, HISTORY):
        # Open the writable clone, never the source. If SQLite left a hot rollback
        # journal or WAL, opening the clone lets it recover before consolidation.
        with closing(database(stage / name)) as db:
            require(db.execute('PRAGMA journal_mode=DELETE').fetchone() == ('delete',),
                    'Could not consolidate the SQLite journal on the private copy')
            validate_db(db)
    if native:
        for name, data in ((INDEX, b''), (UI, b'{}\n')):
            if recorded[name] is None:
                io_path(stage / name).write_bytes(data)
    return recorded


def read_index(path):
    path = io_path(path)
    entries = {}
    require(path.stat().st_size <= MAX_JSON_BYTES, 'Session index exceeds the size limit')
    with path.open(encoding='utf-8-sig') as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    raise MigrationError(f'会话索引格式不正确：{plain_path(path)}，第 {number} 行。') from None
                require(isinstance(row, dict) and isinstance(row.get('id'), str), 'Invalid session index row')
                entries[row['id']] = row
    return entries


def write_index(path, entries):
    path = io_path(path)
    with path.open('w', encoding='utf-8', newline='\n') as stream:
        for row in entries.values():
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def win_path(value):
    # The longest matching project root wins, as in the Windows migration builder.
    require(isinstance(value, str) and '\0' not in value, 'Invalid Windows project path')
    if value.upper().startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    path = PureWindowsPath(value)
    require(path.is_absolute() and '..' not in path.parts, 'Invalid Windows project path')
    return path


def find_rollouts(root):
    result = {}
    for bucket in ('sessions', 'archived_sessions'):
        parent = checked(root, bucket)
        if not parent.exists():
            continue
        require(parent.is_dir(), 'Invalid session directory')
        for current, dirs, files in os.walk(parent):
            require(all(not is_link(Path(current) / d) for d in dirs + files), 'Linked session entry')
            for file in sorted(files):
                match = ROLLOUT.fullmatch(file)
                if match and match[2] is None:
                    require(match[1] not in result, 'Multiple rollout files for one fixture session')
                    result[match[1]] = (Path(current) / file).relative_to(root).as_posix()
    return result


def allowed_member(name, native=False):
    segments = parts(name)
    if name in CORE:
        return True
    if len(segments) < 2:
        return False
    if segments[0] in ('sessions', 'archived_sessions'):
        match = ROLLOUT.fullmatch(segments[-1])
        return bool(match and (native or match[2] is None))
    return native and segments[0] in ASSET_DIRS and all(not p.startswith('.') for p in segments[1:]) and \
        segments[-1].casefold() != 'auth.json' and Path(segments[-1]).suffix.lower() in ASSET_SUFFIXES


def export_fixture(source, output):
    source = profile(source)
    require(not output.resolve().is_relative_to(source), 'Place the package outside the source profile')
    require(not output.exists() and not output.is_symlink(), 'Output ZIP already exists')
    with temporary_directory(output.parent, 'fixture-') as tmp:
        stage = Path(tmp)
        snapshots(source, stage)
        rollouts = find_rollouts(source)
        plan = {}
        with closing(sqlite3.connect(stage / STATE)) as db, db:
            roots = {}
            for pid, pos, value in db.execute('SELECT project_id,position,path FROM project_roots ORDER BY project_id,position'):
                code = f'Project-{len(roots) + 1:03d}'
                roots[pid, pos] = code, win_path(value)
            seen = db.execute('SELECT id,project_id,cwd FROM threads ORDER BY id').fetchall()
            require(seen and {tid for tid, _, _ in seen} == rollouts.keys(), 'Fixture rollouts and database disagree')
            for tid, pid, cwd in seen:
                require(re.fullmatch(UUID, tid), 'Invalid synthetic thread ID')
                source_cwd = win_path(cwd)
                candidates = [(code, root) for (owner, _), (code, root) in roots.items()
                              if owner == pid and source_cwd.is_relative_to(root)]
                if pid is not None:
                    require(candidates, 'A project session is outside its project roots')
                if candidates:
                    code, root = max(candidates, key=lambda item: len(item[1].parts))
                    portable = '/'.join((code, *source_cwd.relative_to(root).parts))
                else:
                    portable = f'Projectless/{tid}'
                parts(portable)
                plan[tid] = {'rollout': rollouts[tid], 'workspace': portable}
                db.execute('UPDATE threads SET cwd=?,rollout_path=? WHERE id=?', (portable, rollouts[tid], tid))
            for (pid, pos), (code, _) in roots.items():
                db.execute('UPDATE project_roots SET path=? WHERE project_id=? AND position=?', (code, pid, pos))
            validate_db(db)
        ui = read_json(stage / UI)
        filtered = {key: ui[key] for key in PROJECT_FIELDS if key in ui}
        for pid, item in filtered.get('local-projects', {}).items():
            item['rootPaths'] = [code for (owner, _), (code, _) in roots.items() if owner == pid]
        (stage / UI).write_bytes(json_bytes(filtered))
        write_index(stage / INDEX, {tid: row for tid, row in read_index(stage / INDEX).items() if tid in plan})
        captured = copy_stable(source, stage, rollouts.values())
        require(all(captured.values()), 'A session file was removed during capture')
        files = {}
        for name in (*CORE, *sorted(rollouts.values())):
            item = checked(stage, name)
            files[name] = {'sha256': fingerprint(item), 'bytes': item.stat().st_size}
        manifest = {'format': 1, 'scope': 'full', 'kind': MAGIC, 'files': files, 'threads': plan}
        require(sum(row['bytes'] for row in files.values()) <= MAX_BYTES, 'Fixture is too large')
        package = stage / 'package.zip'
        with zipfile.ZipFile(package, 'x', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST, json_bytes(manifest))
            for name in files:
                archive.write(stage / name, name)
        with zipfile.ZipFile(package) as archive:
            require(archive.testzip() is None, 'ZIP integrity check failed')
        # Exclusive destination: never replace a previous package.
        with output.open('xb') as stream, package.open('rb') as original:
            shutil.copyfileobj(original, stream)
    return {'threads': len(plan), 'sha256': fingerprint(output)}


def portable_key(name):
    return unicodedata.normalize('NFD', name).casefold()


def verify_paths(names):
    files, directories = set(), {}
    for name in names:
        segments = parts(name)
        key = portable_key(name)
        require(key not in files and key not in directories, 'Repeated, case-equivalent or conflicting package members')
        files.add(key)
        for i in range(1, len(segments)):
            folder = '/'.join(segments[:i])
            key = portable_key(folder)
            require(key not in files and directories.setdefault(key, folder) == folder,
                    'Package contains ambiguous or conflicting directories')


def unpack_verified(package, stage, native=False):
    with zipfile.ZipFile(io_path(package)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        require(len(entries) <= MAX_ENTRIES and sum(entry.file_size for entry in entries) <= MAX_BYTES,
                'Package exceeds the size or file count limit')
        verify_paths(names)
        require(MANIFEST in names and archive.getinfo(MANIFEST).file_size <= MAX_JSON_BYTES, 'Missing or oversized manifest')
        require(all(stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                    and entry.filename == entry.orig_filename and not entry.flag_bits & 1
                    and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    and (entry.filename == MANIFEST or allowed_member(entry.filename, native)) for entry in entries),
                'Unexpected package member')
        manifest = json.loads(archive.read(MANIFEST))
        require(isinstance(manifest, dict) and (manifest.get('format'), manifest.get('scope'), manifest.get('kind')) ==
                (1, 'full', NATIVE if native else MAGIC),
                'Unsupported package format or scope')
        files = manifest['files']
        require(isinstance(files, dict) and set(files) == set(names) - {MANIFEST} and set(CORE) <= files.keys(),
                'Package inventory mismatch')
        require(sum(1 for name in files if name.startswith(('sessions/', 'archived_sessions/'))) > 0,
                'Package has no session files')
        for name, record in files.items():
            info = archive.getinfo(name)
            require(isinstance(record, dict) and type(record.get('bytes')) is int and record['bytes'] == info.file_size
                    and (name not in (INDEX, UI) or info.file_size <= MAX_JSON_BYTES), 'Package member failed size verification')
            item = checked(stage, name)
            io_path(item.parent).mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with archive.open(info) as source, io_path(item).open('xb') as out:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    require(size <= info.file_size, 'Package member expands beyond its recorded size')
                    digest.update(chunk)
                    out.write(chunk)
            require(size == info.file_size and digest.hexdigest() == record.get('sha256'), 'Package member failed verification')
    require(isinstance(manifest.get('threads'), dict) and manifest['threads'], 'Invalid thread manifest')
    return (manifest['threads'], files) if native else manifest['threads']


def merge_ui(existing, incoming):
    if isinstance(existing, dict) and isinstance(incoming, dict):
        return existing | {k: merge_ui(existing[k], v) if k in existing else v for k, v in incoming.items()}
    if isinstance(existing, list) and isinstance(incoming, list):
        return existing + [v for i, v in enumerate(incoming) if v not in existing and v not in incoming[:i]]
    return existing


def workspace_for(value, target, mappings):
    code, *suffix = parts(value)
    if code in mappings:
        return Path(mappings[code]).joinpath(*suffix)
    return checked(target, WORKSPACES + '/' + value)


def compatibility(local_path, other_path, name, native):
    with closing(database(local_path, readonly=True)) as local, closing(database(other_path, readonly=True)) as other:
        if native:
            result = compare_databases(local, other, name)
            validate_db(other)
            return result
        ours = signature(local)
        theirs = signature(other)
        require(ours[0] == theirs[0], f'{name}: the two database schemas differ')
        require(ours[1] == theirs[1], 'The two database migration checksums differ')
        validate_db(other)
    return {'migration_version': None, 'schema_matches': True,
            'source_checksums_match': ours[1] == theirs[1], 'target_checksums_preserved': True}


def build_proposal(package, target, stage, mappings=None, *, native=False, selection='all'):
    target = native_profile(target) if native else profile(target)
    imported, proposed = stage / 'imported', stage / 'proposed'
    imported.mkdir()
    proposed.mkdir()
    inventory = unpack_verified(package, imported, native=native)
    plan, package_files = inventory if native else (inventory, {})
    observed = snapshots(target, proposed, native=native, observe_shm=native)
    if native:
        starting = {name: fingerprint(checked(proposed, name)) for name in CORE}
    if mappings is None:
        mappings = {}
    require(isinstance(mappings, dict) and all(isinstance(v, str) for v in mappings.values()),
            'Expected a JSON object mapping project root codes to directories')
    paths, additions, directories, reused = {}, [], set(), set()
    added_roots = 0
    compatibility_report = {name: compatibility(proposed / name, imported / name, name, native)
                            for name in (STATE, HISTORY)}
    with closing(database(proposed / STATE)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('ATTACH DATABASE ? AS incoming', (database_uri(imported / STATE, readonly=True),))
        source_rows = db.execute('SELECT id,rollout_path,cwd,project_id FROM incoming.threads ORDER BY id').fetchall()
        local_ids = {tid for tid, in db.execute('SELECT id FROM main.threads')}
        require(plan.keys() == {row[0] for row in source_rows}, 'Thread manifest does not match the database')
        chosen = set(plan)
        scope = 'all'
        if selection != 'all':
            from chat_selection import catalog, numbers, ranges
            rows = catalog(db, plan)
            value = selection(rows, local_ids) if callable(selection) else selection
            if value != 'all':
                indices = numbers(value, len(rows))
                chosen = {rows[n - 1][1] for n in indices}
                if len(chosen) != len(plan):
                    scope = ranges(indices)
        added = chosen - local_ids
        duplicates = chosen & local_ids
        roots = db.execute('SELECT project_id,position,path FROM incoming.project_roots ORDER BY project_id,position').fetchall()
        owners = {code: pid for pid, _, code in roots}
        codes = owners.keys()
        root_pattern = r'Project-[a-f0-9]{20}' if native else r'Project-\d{3,}'
        require(len(codes) == len(roots) and all(re.fullmatch(root_pattern, code) for code in codes),
                'Invalid project root codes')
        require(mappings.keys() <= codes, 'Unknown project root in mapping')
        db.execute('CREATE TEMP TABLE selected(id TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO selected VALUES (?)', ((tid,) for tid in sorted(chosen)))
        selected_projects = 'SELECT project_id FROM incoming.threads WHERE id IN (SELECT id FROM selected)'
        project_names = dict(db.execute('SELECT id,name FROM incoming.projects' +
                             (f' WHERE id IN ({selected_projects})' if scope != 'all' else '')))
        if scope != 'all':
            roots = [row for row in roots if row[0] in project_names]
            selected_codes = {row[2] for row in roots}
            mappings = {code: value for code, value in mappings.items() if code in selected_codes}
        require(all(Path(v).is_absolute() and Path(v).is_dir() for v in mappings.values()),
                'Project mappings must name an existing absolute directory for a known root')
        mappings = {code: str(Path(value).resolve(strict=True)) for code, value in mappings.items()}
        require(all(not Path(value).is_relative_to(target) for value in mappings.values()),
                'Map project roots to directories outside the Codex profile')
        project_roots, root_projects = {}, {}
        new_projects = len(project_names.keys() - {row[0] for row in db.execute('SELECT id FROM main.projects')})
        managed = set()
        expected_rollouts = set()
        with db:
            if scope != 'all':
                db.execute('PRAGMA defer_foreign_keys=ON')
            for table, predicate in (('thread_sections', 's.name IS NOT m.name OR s.appearance IS NOT m.appearance'),
                                     ('projects', 's.name IS NOT m.name')):
                dependency = selected_projects if table == 'projects' else (
                    'SELECT thread_section_id FROM incoming.threads WHERE id IN (SELECT id FROM selected)')
                restrict = f' AND s.id IN ({dependency})' if scope != 'all' else ''
                require(not db.execute(f'SELECT 1 FROM incoming.{table} s JOIN main.{table} m USING(id) '
                                       f'WHERE ({predicate}){restrict} LIMIT 1').fetchone(), f'Conflicting {table} ID')
                insert_rows(db, table, f'id NOT IN (SELECT id FROM main.{table}){restrict}')
            restrict = f' AND s.project_id IN ({selected_projects})' if scope != 'all' else ''
            conflict = db.execute('SELECT 1 FROM incoming.project_idempotency_keys s '
                                  'JOIN main.project_idempotency_keys m USING(key) '
                                  f'WHERE s.project_id IS NOT m.project_id{restrict} LIMIT 1').fetchone()
            require(not conflict, 'Conflicting project alias')
            insert_rows(db, 'project_idempotency_keys',
                        f'key NOT IN (SELECT key FROM main.project_idempotency_keys){restrict}')
            for pid, pos, code in roots:
                existing = db.execute('SELECT path FROM main.project_roots WHERE project_id=? AND position=?',
                                      (pid, pos)).fetchone()
                dest = workspace_for(code, target, mappings)
                if native and existing and code not in mappings:
                    # Repeated packages can safely reuse a Mac-side mapping already installed.
                    require(Path(existing[0]).is_absolute() and Path(existing[0]).is_dir(), 'An existing project root is missing')
                    prior = Path(existing[0])
                    if prior != dest:
                        if prior.is_relative_to(target) or prior.resolve().is_relative_to(target):
                            relative = prior.relative_to(target).as_posix() if prior.is_relative_to(target) else ''
                            require(re.fullmatch(WORKSPACES + r'/Project-[0-9]{2,}/Root-[0-9]{2,}', relative)
                                    and checked(target, relative) == prior and prior.resolve() == prior,
                                    '已有项目路径位于 Codex 资料目录内，且不符合旧版占位目录格式。')
                            managed.add(code)
                        else:
                            reused.add(code)
                        mappings[code] = str(prior.resolve(strict=True))
                        dest = Path(mappings[code])
                require(existing in (None, (str(dest),)), 'Conflicting project root')
                project_roots[code] = str(dest)
                root_projects[code] = {'project_id': pid, 'name': project_names[pid], 'position': pos,
                                       'mapping': 'legacy' if code in managed else 'existing' if code in reused
                                       else 'explicit' if code in mappings else 'placeholder'}
                if code not in mappings:
                    directories.add(dest.relative_to(target).as_posix())
                if existing is None:
                    insert_rows(db, 'project_roots', 's.project_id=? AND s.position=?', (pid, pos))
                    db.execute('UPDATE main.project_roots SET path=? WHERE project_id=? AND position=?', (str(dest), pid, pos))
                    added_roots += 1
            db.execute('CREATE TEMP TABLE new_threads(id TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO new_threads VALUES (?)', ((tid,) for tid in sorted(added)))
            insert_rows(db, 'threads', 'id IN (SELECT id FROM new_threads)')
            if scope != 'all':
                db.execute('UPDATE main.threads SET attached_thread_parent_id=NULL WHERE id IN (SELECT id FROM new_threads) '
                           'AND attached_thread_parent_id NOT IN (SELECT id FROM main.threads)')
            for tid, indexed, cwd, pid in source_rows:
                rollout = plan[tid]['rollout'] if native else indexed
                match = ROLLOUT.fullmatch(parts(rollout)[-1])
                expected = {'rollout': rollout, 'workspace': cwd}
                if native:
                    expected['project_id'] = pid
                require(allowed_member(rollout, native=native) and match and match[1] == tid and match[2] is None
                        and plan[tid] == expected and (not native or indexed == rollout.removesuffix('.zst')),
                        'Inconsistent session paths')
                expected_rollouts.add(rollout)
                code, *_ = parts(cwd)
                require((pid is None and cwd == f'Projectless/{tid}') or
                        (native and pid is not None and (pid in project_names or tid not in chosen) and cwd == f'Unmapped/{tid}') or
                        (code in owners and owners[code] == pid), 'Session is outside its project')
                if tid not in added:
                    continue
                dest = workspace_for(cwd, target, mappings)
                if code in managed:
                    checked(target, dest.relative_to(target).as_posix())
                    directories.add(dest.relative_to(target).as_posix())
                elif code in mappings:
                    require(dest.is_dir() and dest.resolve().is_relative_to(Path(mappings[code]).resolve()),
                            'Mapped session directory is missing or outside the mapped root')
                else:
                    directories.add(dest.relative_to(target).as_posix())
                canonical = checked(target, indexed)
                if native:
                    competing = indexed if indexed != rollout else indexed + '.zst'
                    digest = fingerprint(checked(target, competing))
                    require(digest is None, 'A different compression variant already exists at the target')
                    require(competing not in package_files, 'The bundle contains conflicting rollout representations')
                    observed[competing] = digest
                stage_payload(rollout, imported, proposed, target, observed, additions)
                db.execute('UPDATE main.threads SET rollout_path=?,cwd=? WHERE id=?', (str(canonical), str(dest), tid))
                paths[tid] = {'project_id': pid, 'workspace': str(dest)}
                if native:
                    paths[tid]['mapping'] = root_projects[code]['mapping'] if code in root_projects else 'fallback'
            for table in THREAD_TABLES:
                insert_rows(db, table, 'thread_id IN (SELECT id FROM new_threads)')
            insert_rows(db, 'thread_spawn_edges', 'child_thread_id IN (SELECT id FROM new_threads) '
                        'AND parent_thread_id IN (SELECT id FROM main.threads)')
            if native:
                copy_session_model_ids(db)
            validate_db(db)
        require(db.execute('SELECT count(*) FROM main.threads').fetchone()[0] == len(local_ids) + len(added),
                'Merged session count did not match the plan')
    if native:
        require(expected_rollouts <= package_files.keys(), 'A canonical session file is missing from the package')
        for name in sorted(set(package_files) - set(CORE) - expected_rollouts):
            if name.startswith(('sessions/', 'archived_sessions/')):
                match = ROLLOUT.fullmatch(Path(name).name)
                require(match[2] and match[1] in plan, 'Unexpected session side file')
                if match[1] not in added:
                    continue
            elif scope != 'all':
                continue  # The export format has no ownership index for loose asset files.
            stage_payload(name, imported, proposed, target, observed, additions)
    with closing(database(proposed / HISTORY)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('ATTACH DATABASE ? AS incoming', (database_uri(imported / HISTORY, readonly=True),))
        before = db.execute('SELECT count(*) FROM main.thread_turns').fetchone()[0]
        with db:
            db.execute('CREATE TEMP TABLE new_threads(id TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO new_threads VALUES (?)', ((tid,) for tid in sorted(added)))
            for table in HISTORY_TABLES:
                expected_rows = db.execute(f'SELECT count(*) FROM incoming.{table} WHERE thread_id IN (SELECT id FROM new_threads)').fetchone()[0]
                prior_rows = db.execute(f'SELECT count(*) FROM main.{table}').fetchone()[0]
                insert_rows(db, table, 'thread_id IN (SELECT id FROM new_threads)')
                require(db.execute(f'SELECT count(*) FROM main.{table}').fetchone()[0] == prior_rows + expected_rows,
                        f'{table}: imported history row count did not match')
            validate_db(db)
        turns = db.execute('SELECT count(*) FROM main.thread_turns').fetchone()[0] - before
    index, incoming_index = read_index(proposed / INDEX), read_index(imported / INDEX)
    prior_index_size = len(index)
    for tid, row in incoming_index.items():
        if tid in added and tid not in index:
            index[tid] = row
    if not native or len(index) != prior_index_size or observed[INDEX] is None:
        write_index(proposed / INDEX, index)
    local_ui, incoming_ui = read_json(proposed / UI), read_json(imported / UI)
    if native:
        with closing(database(imported / STATE, readonly=True)) as db:
            aliases = dict(db.execute('SELECT key,project_id FROM project_idempotency_keys'))
        if scope != 'all':
            keep = {key for key in incoming_ui.get('local-projects', {}) if key in project_names or aliases.get(key) in project_names}
            keep.update(row['projectId'] for tid, row in incoming_ui.get('thread-project-assignments', {}).items() if tid in chosen)
            incoming_ui['local-projects'] = {key: row for key, row in incoming_ui.get('local-projects', {}).items() if key in keep}
        incoming_ui = normalize_ui(incoming_ui, local_ui, root_projects, project_roots, paths, project_names, aliases,
                                   {tid: plan[tid] for tid in chosen})
    for entry in (() if native else incoming_ui.get('local-projects', {}).values()):
        entry['rootPaths'] = [project_roots[root] for root in entry.get('rootPaths', [])]
    if 'thread-project-assignments' in incoming_ui:
        incoming_ui['thread-project-assignments'] = {tid: v for tid, v in incoming_ui['thread-project-assignments'].items()
                                                     if tid in added}
    if 'projectless-thread-ids' in incoming_ui:
        incoming_ui['projectless-thread-ids'] = [tid for tid in incoming_ui['projectless-thread-ids'] if tid in added]
    merged_ui = dict(local_ui)
    for key in (NATIVE_PROJECT_FIELDS if native else PROJECT_FIELDS):
        if key in incoming_ui:
            merged_ui[key] = merge_ui(local_ui[key], incoming_ui[key]) if key in local_ui else incoming_ui[key]
    if not native or merged_ui != local_ui or observed[UI] is None:
        (proposed / UI).write_bytes(json_bytes(merged_ui))
    assert_unchanged(target, observed, 'The target changed during planning')
    report = {'local_threads': len(local_ids), 'incoming_threads': len(plan), 'added_threads': len(added),
              'kept_duplicate_threads': len(duplicates), 'total_threads': len(local_ids) + len(added),
              'added_turns': turns, 'total_index_entries': len(index), 'project_roots': project_roots,
              'mapped_sessions': paths, 'database_integrity': 'ok'}
    if native:
        report.update({'compatibility': compatibility_report,
                       'selection': scope, 'selected_threads': len(chosen),
                       'projects': {code: {**desc, 'path': project_roots[code]} for code, desc in root_projects.items()},
                       'projects_without_roots': [name for pid, name in project_names.items()
                                                  if pid not in {root['project_id'] for root in root_projects.values()}],
                       'new_projects': new_projects, 'new_project_roots': added_roots,
                       'core_content_changed': any(fingerprint(checked(proposed, name)) != starting[name] for name in CORE),
                       'copied_assets': sum(name.split('/')[0] in ASSET_DIRS for name in additions),
                       'fallback_project_sessions': sum(v['project_id'] is not None and v['mapping'] == 'fallback'
                                                        for v in paths.values())})
    return report, proposed, observed, additions, directories


def stage_payload(name, imported, proposed, target, observed, additions):
    wanted = fingerprint(checked(imported, name))
    prior = fingerprint(checked(target, name))
    require(wanted and prior in (None, wanted), 'Missing payload or conflicting file at the target')
    observed[name] = prior
    if prior is None:
        copy_stable(imported, proposed, [name])
        require(fingerprint(checked(proposed, name)) == wanted, 'The verified payload changed while staging')
        additions.append(name)


def normalize_ui(incoming, local, root_info, project_roots, new_threads, project_names, aliases, plan):
    """Rebase allowed UI fields and honor aliases used by the destination host."""
    original = incoming.get('local-projects', {})
    local_projects = local.get('local-projects', {})
    by_root = {}
    for code, info in root_info.items():
        by_root.setdefault(info['project_id'], []).append(code)
    assignments = incoming.get('thread-project-assignments', {})
    windows_keys = {key: key if key in project_names else aliases[key] for key in original
                    if key in project_names or key in aliases}
    for key, row in original.items():
        require(row.get('id') == key, 'Inconsistent desktop project ID')
        for code in row.get('rootPaths', []):
            pid = root_info[code]['project_id']
            require(key not in windows_keys or windows_keys[key] == pid, 'Inconsistent desktop project paths')
            windows_keys[key] = pid
    for tid, row in assignments.items():
        if tid in plan:
            key = row.get('projectId')
            pid = plan[tid]['project_id']
            require(row.get('projectKind') == 'local' and key in original and pid is not None and
                    (key not in windows_keys or windows_keys[key] == pid), 'Conflicting desktop project assignment')
            windows_keys[key] = pid
    local_aliases = {}
    for host_aliases in local.get('app-server-project-id-by-legacy-project-id-by-host', {}).values():
        for alias, pid in host_aliases.items():
            if alias in local_projects:
                local_aliases.setdefault(pid, set()).add(alias)
    new_ui, keys = {}, {}
    for key, pid in windows_keys.items():
        row = original[key]
        require(row.get('name') == project_names[pid] and row.get('rootPaths', []) == by_root.get(pid, []),
                'Desktop project name or paths do not match the state database')
        aliases = local_aliases.get(pid, set())
        matching = key if key in aliases else pid if pid in local_projects else sorted(aliases)[0] if aliases else key
        require(matching not in keys.values(), 'More than one desktop project refers to the same Mac project')
        keys[key] = matching
        if matching in local_projects:
            require(local_projects[matching].get('name') == row['name'] and
                    local_projects[matching].get('rootPaths', []) == [project_roots[code] for code in by_root.get(pid, [])],
                    'Desktop project conflicts with the Mac profile')
        new_ui[matching] = {**{k: row[k] for k in ('name', 'createdAt', 'updatedAt') if k in row},
                            'id': matching, 'rootPaths': [project_roots[code] for code in by_root.get(pid, [])]}
    require(keys.keys() == original.keys(), 'A desktop project has no corresponding database project')
    return {'local-projects': new_ui, 'project-order': [keys[k] for k in incoming.get('project-order', []) if k in keys],
            'thread-project-assignments': {tid: {'projectKind': 'local', 'projectId': keys[value['projectId']]}
                                           for tid, value in assignments.items() if tid in new_threads},
            'projectless-thread-ids': [tid for tid in incoming.get('projectless-thread-ids', [])
                                       if tid in new_threads and new_threads[tid]['project_id'] is None]}


def rehearse(package, target, mappings=None):
    with temporary_directory(Path(tempfile.gettempdir()), 'rehearse-') as tmp:
        report, *_ = build_proposal(package, target, Path(tmp), mappings)
    return {'mode': 'rehearsal', 'target_written': False, **report}
