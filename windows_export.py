"""Build a portable bundle from a closed Windows Codex profile."""

from contextlib import closing, contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import zipfile

from history_migration import (ASSET_DIRS, CORE, DB_SIDECARS, HISTORY, HISTORY_TABLES, INDEX, MANIFEST, MAX_BYTES,
                               MAX_ENTRIES, MAX_JSON_BYTES, NATIVE, ROLLOUT, STATE, THREAD_TABLES, UI, UUID, MigrationError,
                               allowed_member, assert_unchanged, checked, database, fingerprint, is_link,
                               json_bytes, native_profile, parts, read_index, read_json, require, signature,
                               snapshots, validate_db, verify_paths, win_path, write_index)
from platform_paths import io_path, plain_path, temporary_directory
from schema_compat import OPTIONAL_TABLES, create_session_scope


ADMIN_COLUMNS = {'sandbox_policy': '{"type":"read-only"}', 'approval_mode': 'never', 'git_origin_url': None}
KEEP_STATE = {'_sqlx_migrations', 'threads', 'projects', 'project_roots',
              'project_idempotency_keys', 'thread_sections', 'thread_spawn_edges',
              *(t for t in THREAD_TABLES if t != 'guardian_review_feedback')}
KEEP_HISTORY = {'_sqlx_migrations', *HISTORY_TABLES}


def directory_files(root, bucket):
    root_dir = checked(root, bucket)
    if not io_path(root_dir).exists():
        return
    require(io_path(root_dir).is_dir(), f'{bucket} is not a directory')

    def walk_error(exc):
        raise exc

    for current, dirs, files in os.walk(io_path(root_dir), onerror=walk_error):
        here = plain_path(current)
        for entry in sorted(dirs + files):
            item = here / entry
            require(not is_link(item), f'{bucket} contains a link; move it away before exporting')
            if entry in files:
                yield item.relative_to(root).as_posix()


def source_inventory(root, include_assets):
    rollouts, assets = {}, []
    observed_count = 0
    for bucket in ('sessions', 'archived_sessions', *(ASSET_DIRS if include_assets else ())):
        for name in directory_files(root, bucket):
            observed_count += 1
            require(observed_count <= MAX_ENTRIES * 4, 'The Windows profile inventory exceeds the supported size')
            match = ROLLOUT.fullmatch(Path(name).name)
            if bucket in ('sessions', 'archived_sessions'):
                if match:
                    rollouts.setdefault(match[1], []).append(name)
            elif allowed_member(name, native=True):
                assets.append(name)
    return {tid: sorted(paths) for tid, paths in rollouts.items()}, sorted(assets)


def project_ui(ui, projects, codes, threads, aliases, times):
    """Copy known project UI fields without importing Windows host state."""
    legacy = {pid: [] for pid in projects}
    for alias, pid in aliases.items():
        legacy[pid].append(alias)
    existing = ui.get('local-projects', {})
    local, keys, order = {}, {}, []
    for pid, name in projects.items():
        matches = [key for key in (pid, *legacy[pid]) if key in existing]
        key = matches[0] if matches else pid
        old = existing.get(key, {})
        item = {key: old[key] if isinstance(old.get(key), (int, str)) else fallback
                for key, fallback in zip(('createdAt', 'updatedAt'), times[pid], strict=True)}
        item.update({'id': key, 'name': name, 'rootPaths': codes.get(pid, [])})
        local[key], keys[pid] = item, key
        order.append(key)

    preferred = [key for key in ui.get('project-order', []) if key in local]
    return {'local-projects': local, 'project-order': list(dict.fromkeys(preferred + order)),
            'thread-project-assignments': {tid: {'projectKind': 'local', 'projectId': keys[pid]}
                                           for tid, pid in threads.items() if pid is not None},
            'projectless-thread-ids': [tid for tid, pid in threads.items() if pid is None]}


def table_names(db):
    return {name for name, in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def clear_unrelated_rows(db, keep):
    available = table_names(db)
    require(keep <= available, 'Database is missing expected history tables')
    keep = keep | (OPTIONAL_TABLES.keys() & available)
    for name in sorted(available - keep):
        quoted = name.replace('"', '""')
        db.execute(f'DELETE FROM "{quoted}"')


def read_aliases(db, ui, projects):
    aliases = {key: pid for key, pid in db.execute('SELECT key,project_id FROM project_idempotency_keys') if pid in projects}
    for host in ui.get('app-server-project-id-by-legacy-project-id-by-host', {}).values():
        for key, pid in host.items():
            if pid not in projects:
                continue
            require(key not in aliases or aliases[key] == pid, 'Windows project aliases disagree')
            aliases[key] = pid
    return aliases


def portable_root(pid, pos):
    return 'Project-' + hashlib.sha256(f'{pid}/{pos}'.encode()).hexdigest()[:20]


def choose_rollout(options, recorded):
    canonical = [name for name in options if ROLLOUT.fullmatch(Path(name).name)[2] is None]
    require(canonical, f'数据库引用的会话文件未找到：{recorded}。请确认 sessions 和 archived_sessions 都在源目录中。')
    ref = win_path(recorded)
    def normalized(pieces):
        return (*[p.casefold() for p in pieces[:-1]], pieces[-1].casefold().removesuffix('.zst'))

    paths = [name for name in canonical if normalized(name.split('/')) ==
             normalized(ref.parts[-len(name.split('/')):])]
    if not paths:
        paths = [name for name in canonical if Path(name).name.casefold().removesuffix('.zst') ==
                 ref.name.casefold().removesuffix('.zst')]
    # A canonical JSONL path can legitimately have only a compressed sibling on disk.
    require(len(paths or canonical) == 1, 'Several session files match one Windows history row')
    return (paths or canonical)[0]


def prepare_state(path, ui, inventory):
    plan, picked = {}, set()
    with closing(database(path)) as db:
        signature(db, STATE)
        with db:
            clear_unrelated_rows(db, KEEP_STATE)
            rows = db.execute('SELECT id,name,created_at_ms,updated_at_ms FROM projects ORDER BY position,id').fetchall()
            projects = {pid: name for pid, name, _, _ in rows}
            times = {pid: (created, updated) for pid, _, created, updated in rows}
            aliases = read_aliases(db, ui, projects)
            assigned = ui.get('thread-project-assignments', {})
            root_codes, by_project, by_parent = set(), {}, {}
            for pid, pos, old in db.execute('SELECT project_id,position,path FROM project_roots ORDER BY project_id,position').fetchall():
                code = portable_root(pid, pos)
                require(code not in root_codes, 'Project directory identifiers collide')
                root_codes.add(code)
                by_project.setdefault(pid, []).append(code)
                by_parent.setdefault(pid, []).append((code, win_path(old)))
                db.execute('UPDATE project_roots SET path=? WHERE project_id=? AND position=?', (code, pid, pos))
            rows = db.execute('SELECT id,rollout_path,cwd,project_id FROM threads ORDER BY id').fetchall()
            require(rows, 'No local session rows to export')
            for tid, recorded, cwd, pid in rows:
                require(re.fullmatch(UUID, tid), 'Unexpected Windows session ID')
                rollout = choose_rollout(inventory.get(tid, []), recorded)
                require(allowed_member(rollout, native=True), 'Invalid Windows session path')
                picked.update([rollout, *(p for p in inventory[tid] if ROLLOUT.fullmatch(Path(p).name)[2])])
                entry = assigned.get(tid, {})
                alias = entry.get('projectId')
                assigned_to = aliases.get(alias, alias) if entry.get('projectKind') == 'local' and isinstance(alias, str) else None
                if assigned_to in projects:
                    pid = assigned_to
                require(pid is None or pid in projects, 'A Windows session points to a missing project')
                source_cwd = win_path(cwd)
                matches = [(code, root) for code, root in by_parent.get(pid, ()) if source_cwd.is_relative_to(root)]
                if matches:
                    code, root = max(matches, key=lambda match: len(match[1].parts))
                    workspace = '/'.join((code, *source_cwd.relative_to(root).parts))
                else:
                    workspace = ('Unmapped' if pid is not None else 'Projectless') + '/' + tid
                parts(workspace)
                plan[tid] = {'rollout': rollout, 'workspace': workspace, 'project_id': pid}
                db.execute('UPDATE threads SET rollout_path=?,cwd=?,project_id=?,sandbox_policy=?,approval_mode=?,git_origin_url=? '
                           'WHERE id=?', (rollout.removesuffix('.zst'), workspace, pid, *ADMIN_COLUMNS.values(), tid))
            # Raw project metadata can contain host-specific configuration. Keep only portable project records.
            db.execute("UPDATE projects SET metadata='{}'")
            db.execute('DELETE FROM project_idempotency_keys WHERE project_id NOT IN (SELECT id FROM projects)')
            db.executemany('INSERT INTO project_idempotency_keys(key,project_id,created_at_ms) '
                           'SELECT ?,id,created_at_ms FROM projects WHERE id=? '
                           'ON CONFLICT(key) DO NOTHING', sorted(aliases.items()))
            if 'session_model_ids' in table_names(db):
                create_session_scope(db, 'main', 'SELECT id FROM threads')
                missing_root = db.execute('SELECT 1 FROM session_model_ids WHERE session_id IN (SELECT id FROM model_id_scope) '
                                          'AND session_id NOT IN (SELECT id FROM threads) LIMIT 1').fetchone()
                require(not missing_root, '源资料缺少共享标识对应的主会话记录，无法完整导出子会话。')
            db.execute('UPDATE threads SET attached_thread_parent_id=NULL WHERE attached_thread_parent_id NOT IN (SELECT id FROM threads)')
            db.execute('DELETE FROM thread_spawn_edges WHERE parent_thread_id NOT IN (SELECT id FROM threads) '
                       'OR child_thread_id NOT IN (SELECT id FROM threads)')
            for table in THREAD_TABLES:
                db.execute(f'DELETE FROM {table} WHERE thread_id NOT IN (SELECT id FROM threads)')
            if 'session_model_ids' in table_names(db):
                db.execute('DELETE FROM session_model_ids WHERE session_id NOT IN (SELECT id FROM model_id_scope)')
            validate_db(db)
        db.execute('VACUUM')
    memberships = {tid: row['project_id'] for tid, row in plan.items()}
    return plan, sorted(picked), project_ui(ui, projects, by_project, memberships, aliases, times), len(projects)


def prepare_history(path, ids):
    with closing(database(path)) as db:
        signature(db, HISTORY)
        with db:
            clear_unrelated_rows(db, KEEP_HISTORY)
            db.execute('CREATE TEMP TABLE selected (id TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO selected VALUES (?)', ((tid,) for tid in ids))
            for table in reversed(HISTORY_TABLES):
                db.execute(f'DELETE FROM {table} WHERE thread_id NOT IN (SELECT id FROM selected)')
            validate_db(db)
        db.execute('VACUUM')
        return db.execute('SELECT count(*) FROM thread_turns').fetchone()[0]


def private_zip(destination, inputs, manifest):
    fd, name = tempfile.mkstemp(dir=io_path(destination.parent), prefix='.codex-export-', suffix='.partial')
    partial = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out:
            with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                archive.writestr(MANIFEST, json_bytes(manifest))
                for name, file in inputs.items():
                    compression = zipfile.ZIP_STORED if name.endswith(('.zst', '.png', '.jpg', '.jpeg', '.webp')) else zipfile.ZIP_DEFLATED
                    archive.write(io_path(file), name, compress_type=compression)
            out.flush()
            os.fsync(out.fileno())
        with zipfile.ZipFile(partial) as archive:
            require(archive.testzip() is None, 'Could not verify new ZIP')
        digest = fingerprint(partial)
        # Keep the destination exclusive even if it appeared after the initial preflight.
        if os.name == 'nt':
            partial.rename(io_path(destination))
        else:
            os.link(partial, destination)
        return digest
    finally:
        partial.unlink(missing_ok=True)


@contextmanager
def hold_windows_source_files(source):
    """Allow reads of the actual profile files, but prevent writes and replacements during capture."""
    if sys.platform != 'win32':
        yield
        return

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    open_file.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value
    generic_read, share_read, open_existing = 0x80000000, 1, 3
    handles = []
    try:
        for name in (*CORE, *DB_SIDECARS):
            path = checked(source, name)
            try:
                mode = io_path(path).lstat().st_mode
            except FileNotFoundError:
                continue
            require(stat.S_ISREG(mode), f'Expected a regular source file: {name}')
            # OPEN_EXISTING and GENERIC_READ leave the source data untouched.
            # No share-write or share-delete: active writers conflict, but snapshot reads can proceed.
            handle = open_file(str(io_path(path)), generic_read, share_read, None, open_existing, 0, None)
            if handle == invalid_handle:
                error = ctypes.get_last_error()
                if error in (2, 3):
                    raise MigrationError(f'Windows source changed while preparing export: {name}. Retry after closing Codex')
                if error in (32, 33):
                    raise MigrationError(f'Windows source file is in use: {name}\n文件正在使用：{source / name}。请退出使用这份资料的程序后重试。')
                raise MigrationError(f'Could not secure the Windows source file for reading: {name} (Windows error {error})')
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            close(handle)


def export_windows(source, output, *, include_assets=False, progress=None):
    """Never open the live database with SQLite or write inside the source profile."""
    progress = progress or (lambda _: None)
    progress('检查源目录和输出位置')
    source = native_profile(source)
    output = plain_path(io_path(output.expanduser()).resolve())
    require(output.suffix.lower() == '.zip' and not output.is_relative_to(source), 'Choose a new .zip outside the source profile')
    require(not io_path(output).exists(), f'输出文件已存在：{output}。为避免覆盖，请换一个 ZIP 文件名后重试。')
    require(io_path(output.parent).is_dir(), f'输出文件夹不存在：{output.parent}。请先创建文件夹。')
    progress('检查文件占用并复制数据库')
    with hold_windows_source_files(source), temporary_directory(output.parent, 'codex-export-') as stage:
        observed = snapshots(source, stage, native=True)
        progress('核对会话文件和项目关联')
        rollouts, assets = source_inventory(source, include_assets)
        plan, selected, ui, projects = prepare_state(stage / STATE, read_json(stage / UI), rollouts)
        turns = prepare_history(stage / HISTORY, plan)
        versions = {}
        for name in (STATE, HISTORY):
            with closing(database(stage / name, readonly=True)) as db:
                versions[name] = db.execute('SELECT max(version) FROM _sqlx_migrations WHERE success=1').fetchone()[0]
        io_path(stage / UI).write_bytes(json_bytes(ui))
        write_index(stage / INDEX, {tid: row for tid, row in read_index(stage / INDEX).items() if tid in plan})
        inputs = {name: checked(stage, name) for name in CORE}
        payload_names = selected + assets
        require(len(inputs) + len(payload_names) + 1 <= MAX_ENTRIES, 'Export exceeds the supported file count')
        verify_paths([MANIFEST, *inputs, *payload_names])
        size = 0
        meta = {}
        for name, file in inputs.items():
            meta[name] = {'sha256': fingerprint(file), 'bytes': io_path(file).stat().st_size}
            size += meta[name]['bytes']
        progress(f'复制 {len(payload_names)} 个会话及附件文件')
        for name in payload_names:
            src = checked(source, name)
            dest = checked(stage, name)
            digest = fingerprint(src)
            require(digest, 'A source file disappeared during export')
            length = io_path(src).stat().st_size
            size += length
            require(size <= MAX_BYTES, 'Export exceeds the supported size')
            io_path(dest.parent).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(io_path(src), io_path(dest))
            require(fingerprint(dest) == digest and io_path(dest).stat().st_size == length, f'复制过程中源文件有变化：{src}。请退出 Codex 后重试。')
            observed[name] = digest
            meta[name] = {'sha256': digest, 'bytes': length}
            inputs[name] = dest
        manifest = {'format': 1, 'scope': 'full', 'kind': NATIVE, 'with_assets': include_assets,
                    'files': meta, 'threads': plan}
        require(len(json_bytes(manifest)) <= MAX_JSON_BYTES and size + len(json_bytes(manifest)) <= MAX_BYTES,
                'Export exceeds the package limit')
        require(all(meta[n]['bytes'] <= MAX_JSON_BYTES for n in (UI, INDEX)), 'Project metadata or index exceeds the supported size')
        progress('复核源文件并生成 ZIP')
        assert_unchanged(source, observed, 'Windows source files changed; quit Codex and export again')
        require(source_inventory(source, include_assets) == (rollouts, assets), 'Windows session inventory changed during export')
        bundle_hash = private_zip(output, inputs, manifest)
    return {'mode': 'export', 'bundle': str(output), 'sha256': bundle_hash, 'threads': len(plan),
            'source_migration_versions': versions,
            'turns': turns, 'projects': projects, 'session_files': len(selected), 'asset_files': len(assets),
            'unindexed_session_files': sum(len(paths) for paths in rollouts.values()) - len(selected),
            'project_sessions_using_fallback': sum(row['workspace'].startswith('Unmapped/') for row in plan.values())}
