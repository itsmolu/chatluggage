"""Read-only planning and recoverable offline installation of native bundles."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
from stat import S_IMODE
import subprocess
import sys
import tempfile
import time
import uuid

from platform_paths import io_path, temporary_directory

from history_migration import (ASSET_DIRS, CORE, DB_SIDECARS, HISTORY, INDEX, MAX_ENTRIES, MAX_JSON_BYTES,
                               RECOVERY_DIR, STATE, UI, WORKSPACES, MigrationError, allowed_member, assert_unchanged, build_proposal,
                               checked, copy_stable, fingerprint, is_link, json_bytes, native_profile,
                               parts, read_json, require, verify_paths)


PENDING = 'pending.json'
TXN = re.compile('[0-9a-f]{32}')
SHA = re.compile('[0-9a-f]{64}')
TOUCHED = (*CORE, *DB_SIDECARS)


def mac_idle(target):
    require(sys.platform == 'darwin', 'Native Mac planning and installation run only on macOS')
    ps = subprocess.run(['/bin/ps', '-ww', '-axo', 'uid=,pid=,comm='], capture_output=True, timeout=10)
    require(ps.returncode == 0, 'Could not verify that the Mac app is closed')
    uid = str(os.getuid())
    for row in os.fsdecode(ps.stdout).splitlines():
        values = row.split(None, 2)
        if len(values) < 3 or values[0] != uid:
            continue
        command = values[2].casefold()
        if (re.search(r'/(chatgpt|codex)\.app/contents/', command)
                or command.rsplit('/', 1)[-1] in ('chatgpt', 'codex', 'codex-app-server', 'codex-desktop', 'codex-cli')):
            raise MigrationError('Quit all ChatGPT/Codex windows with Cmd+Q, then run this in a separate Terminal')
    assert_profile_files_closed(target)


def assert_profile_files_closed(target):
    names = [str(path) for name in TOUCHED if (path := checked(target, name)).is_file()]
    if not names:
        return
    opened = subprocess.run(['/usr/sbin/lsof', '-w', '-F', 'p', *names], capture_output=True, timeout=15)
    require(opened.returncode in (0, 1) and not opened.stderr,
            'Could not verify that the Mac profile files are closed')
    require(opened.returncode == 1 and not opened.stdout,
            'A process has a profile file open; close it before continuing')


def input_bundle(package, target):
    package = package.expanduser().resolve(strict=True)
    require(not package.is_relative_to(target), 'Keep the migration ZIP outside the Codex profile')
    require(fingerprint(package), 'Migration ZIP was not found')
    return package


def recovery_root(target):
    area = checked(target, RECOVERY_DIR)
    require(not area.exists() or area.is_dir(), 'The backup path is not a directory')
    return area


def no_interruption(area):
    require(fingerprint(checked(area, PENDING)) is None, 'An earlier installation is incomplete; run recover first')


def sync_directory(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def synced_copy(source, dest, mode=None):
    with io_path(source).open('rb') as original, io_path(dest).open('xb') as copy:
        if os.name == 'posix':
            os.fchmod(copy.fileno(), 0o600 if mode is None else mode)
        shutil.copyfileobj(original, copy, length=1024 * 1024)
        copy.flush()
        os.fsync(copy.fileno())


def write_record(path, value):
    data = json_bytes(value)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.history-record-', delete=False) as output:
        temp = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            os.replace(temp, path)
            sync_directory(path.parent)
        finally:
            output.close()
            temp.unlink(missing_ok=True)


def sync_remove(path):
    path.unlink()
    sync_directory(path.parent)


@contextmanager
def exclusive(area):
    try:
        area.mkdir(mode=0o700)
        sync_directory(area.parent)
    except FileExistsError:
        require(area.is_dir() and not is_link(area), 'The backup directory is invalid')
    lock = checked(area, 'lock')
    fingerprint(lock)
    with lock.open('a+b') as stream:
        if os.name == 'posix':
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise MigrationError('Another migration or recovery is running') from None
        else:
            import msvcrt
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise MigrationError('Another migration or recovery is running') from None
        yield


def under_write_guard(name):
    return name in DB_SIDECARS or allowed_member(name, native=True)


def candidate_actions(target, proposal):
    report, proposed, observed, additions, dirs = proposal
    result = {}
    # Carrying a WAL into a standalone replacement can overwrite the new state. Remove it offline first.
    # If there is nothing to install, preserve even the existing WAL and index formatting.
    if report['core_content_changed'] or additions or report['added_threads'] or any(
            fingerprint(checked(proposed, n)) != observed[n] for n in (UI, INDEX)):
        for name in (*sorted(additions), INDEX, UI, *DB_SIDECARS, STATE, HISTORY):
            require(under_write_guard(name), 'Proposed write is outside the allowed profile files')
            new = None if name in DB_SIDECARS else fingerprint(checked(proposed, name))
            require(name in DB_SIDECARS or new is not None, 'A proposed file disappeared')
            old = observed[name]
            if old != new:
                result[name] = {'old': old, 'new': new}
    require(len(result) <= MAX_ENTRIES, 'Too many files in one installation')
    folders = set(dirs)
    for name in (*folders, *result):
        folders.update(p.as_posix() for p in Path(name).parents if p != Path('.'))
    absent = []
    for name in sorted(folders, key=lambda n: (n.count('/'), n)):
        candidate = checked(target, name)
        require(not candidate.exists() or candidate.is_dir(), 'A destination file occupies a project or session directory')
        if not candidate.exists():
            absent.append(name)
    return result, absent


def preview_digest(target, bundle_hash, observed, report, actions, absent):
    return hashlib.sha256(json_bytes({'target': str(target), 'bundle_sha256': bundle_hash,
                                    'observed': observed, 'project_roots': report['project_roots'], 'selection': report['selection'],
                                    'mapped_sessions': report['mapped_sessions'],
                                    'writes': actions, 'directories': absent})).hexdigest()


def compute(target, package, stage, mapping, assert_idle, selection):
    assert_idle(target)
    bundle_hash = fingerprint(package)
    proposal = build_proposal(package, target, stage, mapping, native=True, selection=selection)
    require(fingerprint(package) == bundle_hash, 'The migration ZIP changed while preparing the plan')
    report, _, observed, _, _ = proposal
    actions, absent = candidate_actions(target, proposal)
    approval = preview_digest(target, bundle_hash, observed, report, actions, absent)
    assert_idle(target)
    return proposal, actions, absent, approval, bundle_hash


@contextmanager
def prepared(target, package, mapping, idle, scratch, selection):
    candidate = Path(scratch or tempfile.gettempdir()).resolve(strict=True)
    require(not candidate.is_relative_to(target), 'The temporary directory must be outside the target profile')
    with temporary_directory(candidate, 'chm-plan-') as stage:
        yield compute(target, package, stage, mapping, idle, selection)


def plan_native(package, target, mapping=None, *, idle=mac_idle, scratch=None, selection='all'):
    target = native_profile(target)
    package = input_bundle(package, target)
    area = recovery_root(target)
    no_interruption(area)
    with prepared(target, package, mapping, idle, scratch, selection) as (proposal, actions, absent, approval, bundle_hash):
        no_interruption(area)
        assert_unchanged(target, proposal[2], 'The target changed during planning')
        return {'mode': 'plan', 'target_written': False, 'plan_id': approval, 'bundle_sha256': bundle_hash,
                'files_to_change': len(actions), 'directories_to_create': len(absent), **proposal[0]}


def transaction_backup(target, area, observed, actions, backup_id):
    backup = checked(area, 'backup-' + backup_id)
    backup.mkdir(mode=0o700)
    sync_directory(area)
    names = tuple(dict.fromkeys((*TOUCHED, *(name for name, pair in actions.items() if pair['old'] is not None))))
    backed = copy_stable(target, backup, names)
    require(all(backed[name] == observed[name] for name in names), 'The profile changed after planning; backup was not applied')
    for name, expected in backed.items():
        if expected is None:
            continue
        with checked(backup, name).open('rb+') as stream:
            if os.name == 'posix':
                os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
    sync_directory(backup)
    return backup, backed


def temporary_neighbor(target, name, txn):
    rel = Path(name).parent / ('.codex-history-' + txn + '.tmp')
    return checked(target, rel.as_posix())


def replace(target, name, source, txn, mode=None):
    path = checked(target, name)
    temp = temporary_neighbor(target, name, txn)
    require(fingerprint(temp) is None, 'Unexpected transaction scratch file; run recover')
    try:
        synced_copy(source, temp, mode)
        os.replace(io_path(temp), io_path(path))
        sync_directory(path.parent)
    finally:
        if fingerprint(temp) is not None:
            sync_remove(temp)


def rollback(target, area, idle):
    journal = checked(area, PENDING)
    record = read_json(journal)
    txn = record.get('id', '')
    require(record.get('format') == 1 and isinstance(txn, str) and TXN.fullmatch(txn) and
            record.get('target') == str(target), 'Recovery record does not match this profile')
    backup = checked(area, 'backup-' + txn)
    require(backup.is_dir(), 'Recovery backup directory is missing')
    files = record.get('files', {})
    require(isinstance(files, dict) and all(under_write_guard(name) for name in files), 'Invalid recovery paths')
    verify_paths(list(files))
    for name, pair in files.items():
        require(isinstance(pair, dict) and set(pair) == {'old', 'new'} and pair['old'] != pair['new'] and
                all(v is None or isinstance(v, str) and SHA.fullmatch(v) for v in pair.values()) and
                (name not in DB_SIDECARS or pair['new'] is None), 'Invalid recovery fingerprints')
        current = fingerprint(checked(target, name))
        require(current in (pair['old'], pair['new']), 'Recovery stopped: a target file changed after installation began')
        require(pair['old'] is None or fingerprint(checked(backup, name)) == pair['old'], 'Recovery backup failed verification')
        fingerprint(temporary_neighbor(target, name, txn))
    guard = record['unchanged']
    require(isinstance(guard, dict) and set(guard) == set(TOUCHED) - files.keys() and
            all(v is None or isinstance(v, str) and SHA.fullmatch(v) for v in guard.values()), 'Invalid recovery guard')
    assert_unchanged(target, guard, 'Recovery stopped: a database or sidecar that should be unchanged was modified')
    modes = record.get('original_modes', {})
    require(isinstance(modes, dict) and set(modes) == {name for name, pair in files.items() if pair['old'] is not None}
            and all(type(mode) is int and 0 <= mode <= 0o777 for mode in modes.values()), 'Invalid recovery permissions')
    folders = record.get('directories')
    require(isinstance(folders, list) and all(parts(name)[0] in ('sessions', 'archived_sessions', WORKSPACES, *ASSET_DIRS)
                                              for name in folders), 'Invalid recovery directories')
    for name in folders:
        item = checked(target, name)
        require(not item.exists() or item.is_dir(), 'A recovery directory is occupied by a file')
    idle(target)
    assert_unchanged(target, guard, 'A database or sidecar changed before recovery')
    # Restore main files before restoring the WAL/SHM from the same verified backup.
    ordered = [name for name in files if name not in DB_SIDECARS] + [name for name in DB_SIDECARS if name in files]
    for name in ordered:
        path = checked(target, name)
        old = files[name]['old']
        temp = temporary_neighbor(target, name, txn)
        if fingerprint(temp) is not None:
            sync_remove(temp)
        current = fingerprint(path)
        require(current in (old, files[name]['new']), 'Recovery stopped: a profile file changed during recovery')
        if current == old:
            continue
        if old is None:
            sync_remove(path)
        else:
            replace(target, name, checked(backup, name), txn, modes.get(name))
        require(fingerprint(path) == old, 'Could not restore a profile file')
    for name in sorted(folders, key=lambda n: n.count('/'), reverse=True):
        path = checked(target, name)
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            sync_directory(path.parent)
    assert_unchanged(target, {**guard, **{name: pair['old'] for name, pair in files.items()}}, 'Restored profile did not verify')
    sync_remove(journal)
    return backup


def install_native(package, target, approved, mapping=None, *, idle=mac_idle, inject=None, scratch=None, selection='all'):
    target = native_profile(target)
    package = input_bundle(package, target)
    require(isinstance(approved, str) and SHA.fullmatch(approved), 'Give the plan_id from a successful rehearsal as --approve')
    idle(target)
    area = recovery_root(target)
    no_interruption(area)
    # Confirm the rehearsal before creating anything in the profile, including the backup directory.
    with prepared(target, package, mapping, idle, scratch, selection) as (proposal, files, folders, token, bundle_hash):
        require(token == approved, 'The plan no longer matches. Run plan again before applying')
        report, proposed, observed, _, _ = proposal
        if not files and not folders:
            no_interruption(area)
            assert_unchanged(target, observed, 'The target changed during planning')
            return {'mode': 'apply', 'target_changed': False, 'files_changed': 0, **report}
        with exclusive(area):
            no_interruption(area)
            assert_unchanged(target, observed, 'The target changed after planning')
            idle(target)
            txn = uuid.uuid4().hex
            pending = {'format': 1, 'id': txn, 'target': str(target), 'files': files, 'directories': folders,
                       'original_modes': {name: S_IMODE(checked(target, name).stat().st_mode) & 0o777
                                          for name, pair in files.items() if pair['old'] is not None},
                       'unchanged': {name: observed[name] for name in TOUCHED if name not in files}}
            require(len(json_bytes(pending)) <= MAX_JSON_BYTES, 'Recovery record exceeds the supported size')
            backup, backed = transaction_backup(target, area, observed, files, txn)
            write_record(backup / 'backup.json', {'format': 1, 'id': txn, 'target': str(target),
                                                 'originals': backed, 'bundle_sha256': bundle_hash,
                                                 'created_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
            assert_unchanged(target, observed, 'The profile changed before installation')
            idle(target)
            journal = checked(area, PENDING)
            write_record(journal, pending)
            try:
                for name in files:
                    require(fingerprint(temporary_neighbor(target, name, txn)) is None, 'A transaction scratch file already exists')
                for name in folders:
                    folder = checked(target, name)
                    folder.mkdir(mode=0o700)
                    sync_directory(folder.parent)
                for name, pair in files.items():
                    if name in TOUCHED:
                        idle(target)
                    path = checked(target, name)
                    require(fingerprint(path) == pair['old'], 'A destination file changed during installation')
                    if pair['new'] is None:
                        sync_remove(path)
                    else:
                        replace(target, name, checked(proposed, name), txn)
                    if inject is not None:
                        inject(name)
                idle(target)
                assert_unchanged(target, {**observed, **{name: pair['new'] for name, pair in files.items()}},
                                 'Installed files did not match the verified plan')
            except (Exception, KeyboardInterrupt) as exc:
                try:
                    rollback(target, area, idle)
                except (Exception, KeyboardInterrupt):
                    raise MigrationError('Install stopped and could not be rolled back automatically. Keep Codex closed and run recover.') from None
                raise MigrationError('Install stopped. The backup was restored; run plan before trying again.') from exc
            sync_remove(journal)
            return {'mode': 'apply', 'target_changed': True, 'files_changed': len(files), 'backup': str(backup), **report}


def recover_native(target, *, idle=mac_idle):
    target = native_profile(target, recovering=True)
    area = recovery_root(target)
    require(fingerprint(checked(area, PENDING)) is not None, 'No incomplete installation found')
    idle(target)
    with exclusive(area):
        require(fingerprint(checked(area, PENDING)) is not None, 'No incomplete installation found')
        backup = rollback(target, area, idle)
        return {'mode': 'recover', 'restored_originals': True, 'backup': str(backup)}
