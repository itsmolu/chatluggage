"""Prepare disposable Codex profiles for a manual Windows → Mac rehearsal."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys

from accept_mac import (ROOT, MAC_ID, MAC_MESSAGE, MAC_REPLY, PROJECT_ID, WIN_ID, WIN_MESSAGE, WIN_REPLY,
                       check_client, installed_codex)
from verify_installed_schema import initialize, seed
from history_migration import (CORE, RECOVERY_DIR, STATE, WORKSPACES, MigrationError, checked, fingerprint,
                               is_link, json_bytes, read_json, require)
from migration_workflow import PENDING, assert_profile_files_closed
from windows_export import portable_root


def safe_rehearsal_dir(location):
    resolved = location.resolve()
    active = [Path.home() / '.codex']
    if os.environ.get('CODEX_HOME'):
        active.append(Path(os.environ['CODEX_HOME']))
    for folder in active:
        profile = folder.expanduser().resolve()
        require(not resolved.is_relative_to(profile) and not profile.is_relative_to(resolved),
                'Keep this rehearsal away from all live Codex profiles')
    require(resolved == location.absolute(), 'The rehearsal directory must not be a symlink')
    return resolved


def prepare(location, exe, platform):
    require(not location.exists(), 'The rehearsal folder already exists; use a fresh extracted copy to start over')
    location.mkdir(mode=0o700)
    target = location / 'profile'
    initialize(exe, target, location / 'server')
    if platform == 'win32':
        seed(target, PROJECT_ID, WIN_MESSAGE, tid=WIN_ID, reply=WIN_REPLY)
        tid = WIN_ID
    else:
        seed(target, None, MAC_MESSAGE, tid=MAC_ID, reply=MAC_REPLY)
        tid = MAC_ID
    rollouts = list((target / 'sessions').rglob(f'*-{tid}.jsonl'))
    require(len(rollouts) == 1, 'Expected exactly one fictional session')
    metadata = {'fixture': 'full-flow-v1', 'platform': platform, 'fingerprints': {name: fingerprint(checked(target, name)) for name in CORE},
                'rollout': rollouts[0].relative_to(target).as_posix(), 'rollout_sha256': fingerprint(rollouts[0])}
    with (location / 'fictional-profile.json').open('xb') as marker:
        marker.write(json_bytes(metadata))
    return {'status': 'ready', 'synthetic_only': True, 'threads': 1, 'profile': str(target)}


def verify(location, exe):
    target = checked(location, 'profile')
    marker = checked(location, 'fictional-profile.json')
    require(marker.is_file() and target.is_dir(), 'Prepare the fictional Mac profile first')
    metadata = read_json(marker)
    require(metadata.get('fixture') == 'full-flow-v1' and metadata.get('platform') == 'darwin',
            'This directory is not a Mac rehearsal fixture')
    area = checked(target, RECOVERY_DIR)
    require(not checked(area, PENDING).exists(), 'The migration did not finish; run recover on the fictional profile')
    expected = metadata['fingerprints']
    originals = checked(target, metadata['rollout'])
    require(originals.name.endswith(f'-{MAC_ID}.jsonl') and fingerprint(originals) == metadata['rollout_sha256'],
            'The original Mac fictional session file changed')

    # Refuse to ask the server for message bodies until the fixture IDs are confirmed.
    if sys.platform == 'darwin':
        assert_profile_files_closed(target)
    state = checked(target, STATE)
    with closing(sqlite3.connect(state.as_uri() + '?mode=ro', uri=True)) as db:
        ids = {tid for tid, in db.execute('SELECT id FROM threads LIMIT 3')}
    require(ids == {WIN_ID, MAC_ID}, 'Expected both fictional sessions after apply')
    backup = False
    for folder in area.glob('backup-*'):
        if is_link(folder) or not folder.is_dir():
            continue
        if all(fingerprint(checked(folder, name)) == expected[name] for name in CORE):
            backup = True
            break
    require(backup, 'The original databases and index were not found in a verified backup')
    workspace = checked(target, WORKSPACES + '/' + portable_root(PROJECT_ID, 0))
    require(workspace.is_dir(), 'The placeholder project directory is missing')
    check_client(exe, target, checked(location, 'server'), workspace)
    return {'status': 'passed', 'synthetic_only': True, 'total_threads': 2,
            'original_mac_rollout_unchanged': True, 'backup': 'passed', 'local_codex_readback_and_project': 'passed',
            'desktop_sidebar': 'not_run'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify'))
    parser.add_argument('--codex', type=Path, help='optional installed Codex CLI executable')
    args = parser.parse_args()
    try:
        require(sys.platform in ('win32', 'darwin'), 'Run this helper on Windows or Mac')
        require(args.action != 'verify' or sys.platform == 'darwin', 'Run verify on Mac after apply')
        location = safe_rehearsal_dir(ROOT / 'migration-rehearsal')
        exe, _ = installed_codex(args.codex)
        report = prepare(location, exe, sys.platform) if args.action == 'prepare' else verify(location, exe)
        sys.stdout.buffer.write(json_bytes(report))
    except (Exception, KeyboardInterrupt) as err:
        reason = str(err) if isinstance(err, MigrationError) else type(err).__name__
        print(f'Fictional rehearsal stopped: {reason}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
