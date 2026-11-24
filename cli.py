"""Export local Codex history from Windows and merge it into a Mac profile."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from tempfile import gettempdir
from uuid import uuid4
import zipfile


def probe_python(check_schema=True):
    details = 'Python ' + '.'.join(map(str, sys.version_info[:3]))
    if sys.version_info < (3, 11):
        return details, '需要 Python 3.11 或更新版本（含 sqlite3）。'
    try:
        import sqlite3
    except (ImportError, OSError):
        return details, '无法加载 sqlite3。'
    details += '\nSQLite ' + sqlite3.sqlite_version
    if not check_schema:
        return details, None
    if sqlite3.sqlite_version_info < (3, 41, 0):
        return details, '需要 SQLite 3.41.0 或更新版本，用于读取 Codex 历史索引。'
    try:
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript("""
                PRAGMA trusted_schema=OFF;
                CREATE TABLE probe(payload TEXT);
                CREATE INDEX probe_observed ON probe(json_extract(payload, '$.observed_at_ms'))
                    WHERE json_extract(payload, '$.observed_at_ms') IS NOT NULL;
            """)
    except sqlite3.Error:
        return details, '当前 SQLite 无法在安全模式下使用 JSON 表达式索引。'
    return details, None


def switch_python(details, reason):
    script = Path(__file__).resolve()
    if not os.environ.get('CODEX_MIGRATION_PROBED'):
        if sys.platform == 'darwin':
            os.execv('/bin/bash', ['/bin/bash', str(script.with_name('mac.sh')), *sys.argv[1:]])
        if sys.platform == 'win32':
            candidates = []
            launcher = shutil.which('py')
            if launcher:
                try:
                    result = subprocess.run([launcher, '-0p'], capture_output=True, text=True, errors='replace', timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    result = None
                candidates.extend(re.findall(r'(?im)([A-Z]:\\[^\r\n]*?python(?:3(?:\.\d+)?)?\.exe)\s*$', result.stdout if result else ''))
            candidates.append(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
            if os.environ.get('LOCALAPPDATA'):
                candidates.extend((Path(os.environ['LOCALAPPDATA']) / 'Programs/Python').glob('Python*/python.exe'))
            candidates.extend(Path(p) / 'python.exe' for p in os.get_exec_path() if Path(p).is_absolute()
                              and Path(p).name.lower() != 'windowsapps')
            for exe in dict.fromkeys(Path(p) for p in candidates):
                if exe.is_file() and exe != Path(sys.executable):
                    try:
                        check = subprocess.run([str(exe), '-B', str(script), '--probe-python', *sys.argv[1:2]],
                                               capture_output=True, encoding='utf-8', errors='replace', timeout=10)
                    except (OSError, subprocess.TimeoutExpired):
                        continue
                    if check.returncode == 0:
                        print(f'使用解释器：{exe}\n{check.stdout.rstrip()}', file=sys.stderr, flush=True)
                        raise SystemExit(subprocess.call([str(exe), '-B', str(script), *sys.argv[1:]],
                                             env=dict(os.environ, CODEX_MIGRATION_PROBED='1')))
    raise SystemExit(f'找不到满足要求的 Python。当前解释器：{sys.executable}\n{details}\n{reason}')


if __name__ == '__main__':
    probing = sys.argv[1:2] == ['--probe-python']
    args = sys.argv[2:] if probing else sys.argv[1:]
    # Recovery checks file hashes and restores copies without opening either database.
    details, reason = probe_python(check_schema=args[:1] != ['recover'])
    if probing:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        print(f'{details}\n{reason}' if reason else details)
        raise SystemExit(1 if reason else 0)
    if reason:
        switch_python(details, reason)

import sqlite3

from chat_selection import label, prompt_selection
from demo import run_demo
from history_migration import MigrationError, json_bytes, read_json, rehearse, require
from migration_workflow import install_native, plan_native, recover_native
from windows_export import export_windows
from platform_paths import plain_path


VERSION = '0.1.0-rc.16'


def default_profile():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')


def mac_command(action, opts, token=None, selection='all'):
    args = ['/bin/bash', str(Path(__file__).resolve().with_name('mac.sh')), action]
    if action != 'recover':
        args.append(str(opts.package.expanduser().resolve()))
    args += ['--target', str(opts.target.expanduser().resolve())]
    if action != 'recover' and getattr(opts, 'mapping', None):
        args += ['--map', str(opts.mapping.expanduser().resolve())]
    if token:
        if selection != 'all':
            args += ['--pick', selection]
        args += ['--approve', token]
    return shlex.join(args)


def show_report(report, opts):
    if opts.json_output or opts.action in ('demo', 'rehearse'):
        sys.stdout.buffer.write(json_bytes(report))
        return
    if opts.action == 'export':
        print(f"导出成功：{report['threads']} 条会话，{report['turns']} 个历史轮次，{report['projects']} 个项目。")
        versions = report.get('source_migration_versions')
        if versions:
            print(f"源数据库 SQLx 版本：状态库 {versions['state_5.sqlite']}，历史库 {versions['thread_history_1.sqlite']}。")
        print(f"迁移包：{report['bundle']}")
        print(f"SHA-256：{report['sha256']}")
        print('下一步：把这个 ZIP 和项目程序传到 Mac，再按 README 的 Mac 步骤预览。')
        if report['unindexed_session_files']:
            print(f"说明：另有 {report['unindexed_session_files']} 个未被状态库选中的会话文件未包含在迁移包内。")
    elif opts.action == 'plan':
        print(f"预览完成，尚未修改 Mac 资料。准备新增 {report['added_threads']} 条会话，保留 {report['local_threads']} 条已有会话。")
        for name, info in report.get('compatibility', {}).items():
            print(f"{name}：包内 SQLx {info['source_migration_version']} → Mac SQLx {info['migration_version']}，结构检查通过。")
        print(f"目标资料目录：{opts.target.expanduser().resolve()}")
        print(f"同 ID 会话保留 Mac 版本：{report['kept_duplicate_threads']} 条。")
        if 'selected_threads' in report:
            print(f"本次选中 {report['selected_threads']} / {report['incoming_threads']} 条聊天。")
            if report['selection'] != 'all':
                print('部分导入只复制所选聊天及其关联记录。包内无法归属到聊天的独立附件文件将跳过。')
        types = {'existing': 'Mac 已有项目目录', 'explicit': '你指定的工作目录', 'placeholder': '空的占位目录',
                 'legacy': '沿用旧版占位目录'}
        by_project = {}
        for code, project in report['projects'].items():
            by_project.setdefault((project.get('project_id'), project['name']), []).append((code, project))
        for (_, name), roots in by_project.items():
            print(f'项目 {label(name)}')
            for code, project in roots:
                print(f"  {types[project['mapping']]}　{label(project['path'], 4096)}　({code})")
        if report['projects_without_roots'] or report['fallback_project_sessions']:
            print('部分会话没有匹配的工作目录，将使用空的占位目录。')
        if any(p['mapping'] == 'placeholder' for p in report['projects'].values()) or report['fallback_project_sessions']:
            print('空目录不包含原来的工作文件；关联实际目录请参阅 docs/project-paths.md。')
        if not report['files_to_change'] and not report['directories_to_create']:
            print('无需合并：这次没有要写入的文件。')
        else:
            print('\n确认路径和会话数后，复制下面整条命令到同一个终端执行：')
            print(mac_command('apply', opts, report['plan_id'], report.get('selection', 'all')))
    elif opts.action == 'apply':
        if report['target_changed']:
            print(f"合并完成：新增 {report['added_threads']} 条会话，合计 {report['total_threads']} 条。")
            print(f"备份：{report['backup']}")
            print('现在可以打开平时使用的 Codex，查看会话正文和项目归属。')
        else:
            print('无需合并：已有这些会话，本次没有改动资料。')
    elif opts.action == 'recover':
        print(f"中断恢复完成。备份：{report['backup']}")
        print('重新运行 plan 预览后再决定是否合并。')


def explain_failure(exc):
    if isinstance(exc, MigrationError):
        return str(exc)
    if isinstance(exc, OSError):
        names = [str(plain_path(value)) for value in (exc.filename, getattr(exc, 'filename2', None)) if value]
        paths = '\n涉及路径：' + '\n涉及路径：'.join(names) if names else ''
        code = getattr(exc, 'winerror', None) or exc.errno
        if isinstance(exc, FileNotFoundError):
            message = '找不到所需文件或文件夹。请核对上面的资料目录和下面的路径。'
        elif isinstance(exc, FileExistsError):
            message = '文件或文件夹已存在。请换一个输出名称。'
        elif isinstance(exc, PermissionError):
            message = '文件被占用或没有访问权限。请关闭占用它的程序，并确认目录可访问。'
        else:
            message = '文件操作失败，请检查路径、磁盘空间和访问权限。'
        return f'{message}{paths}\n系统错误：{type(exc).__name__} ({code})'
    if isinstance(exc, zipfile.BadZipFile):
        return 'ZIP 无法读取。请确认选择的是导出的迁移包，并重新传输后重试。'
    if isinstance(exc, sqlite3.Error):
        return f'数据库处理失败：{exc}。请保留原始资料，确认两端结构受本版本支持。'
    # Do not print JSON/message values or local variables in generic diagnostics.
    frames = []
    tb = exc.__traceback__
    while tb:
        filename = Path(tb.tb_frame.f_code.co_filename)
        if filename.parent == Path(__file__).resolve().parent:
            frames.append(f'{filename.name}:{tb.tb_lineno}')
        tb = tb.tb_next
    return f"资料格式或运行环境不符合预期：{type(exc).__name__}。位置：{' → '.join(frames)}。请提供这条提示排查。"


def native_options(parser, *, bundle=True):
    if bundle:
        parser.add_argument('package', type=Path, help='verified Windows export (.zip)')
        parser.add_argument('--map', dest='mapping', type=Path, help='JSON project directory mapping')
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--all', dest='selection', action='store_const', const='all', help='select all chats')
        group.add_argument('--pick', dest='selection', metavar='1,3-5', help='use the chat numbers from the preview')
    parser.add_argument('--target', type=Path, default=default_profile(), help='Mac profile (default: CODEX_HOME or ~/.codex)')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure') and not stream.isatty():
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    commands = parser.add_subparsers(dest='action', required=True)
    exporter = commands.add_parser('export', help='copy local Windows history into a new migration ZIP')
    exporter.add_argument('output', type=Path, help='new .zip path outside the source profile')
    exporter.add_argument('--source', type=Path, default=default_profile(),
                          help='Windows profile (default: CODEX_HOME or %%USERPROFILE%%\\.codex)')
    exporter.add_argument('--include-assets', action='store_true', help='include selected attachments, plans, and images')
    plan = commands.add_parser('plan', help='Mac: rehearse the migration without writing the target')
    native_options(plan)
    apply = commands.add_parser('apply', help='Mac: install a reviewed plan offline with a backup')
    native_options(apply)
    apply.add_argument('--approve', required=True, help='the exact plan_id returned by plan')
    recover = commands.add_parser('recover', help='Mac: restore the files from an incomplete installation')
    native_options(recover, bundle=False)
    demo = commands.add_parser('demo', help='create a demo ZIP and exercise recovery')
    demo.add_argument('output', nargs='?', type=Path, help='a new output directory; defaults to a unique temporary folder')
    fixture = commands.add_parser('rehearse', help='check a demo ZIP without writing the fixture target')
    fixture.add_argument('package', type=Path)
    fixture.add_argument('target', type=Path)
    fixture.add_argument('--map', dest='mapping', type=Path, help='JSON project directory mapping')
    for command in (exporter, plan, apply, recover, demo, fixture):
        command.add_argument('--json', dest='json_output', action='store_true', help='output a machine-readable JSON report')
    opts = parser.parse_args()
    phase = '检查输入参数'

    def progress(value):
        nonlocal phase
        phase = value
        if not opts.json_output:
            print(f'正在{value}…', file=sys.stderr, flush=True)

    try:
        if opts.action == 'export' and not opts.json_output:
            print(f'Windows 资料目录：{opts.source.expanduser().absolute()}', file=sys.stderr, flush=True)
            print(f'输出 ZIP：{opts.output.expanduser().absolute()}', file=sys.stderr, flush=True)
        elif opts.action in ('plan', 'apply', 'recover') and not opts.json_output:
            print(f'Mac 资料目录：{opts.target.expanduser().absolute()}', file=sys.stderr, flush=True)
        mapping = read_json(opts.mapping) if getattr(opts, 'mapping', None) else None
        if opts.action == 'demo':
            output = opts.output or (Path(gettempdir()).resolve() / f'codex-demo-{uuid4().hex[:12]}')
            report = run_demo(output)
            if opts.output is None and not opts.json_output:
                check = report['rehearsal']
                repeats = report['post_install']['repeat_install_changed_files']
                print(f"Demo passed (fictional data): {check['local_threads']} -> {check['total_threads']} chats; "
                      f"interruption recovery passed; repeat writes: {repeats}.")
                print(f"Report: {(output / 'rehearsal-report.json')}")
                return 0
        elif opts.action == 'rehearse':
            report = rehearse(opts.package, opts.target, mapping)
        elif opts.action == 'export':
            require(sys.platform == 'win32', 'Run export on Windows')
            report = export_windows(opts.source, opts.output, include_assets=opts.include_assets, progress=progress)
        else:
            require(sys.platform == 'darwin', 'Run this command in a separate macOS Terminal')
            if opts.action == 'plan':
                progress('预览迁移包并核对 Mac 资料')
                selection = opts.selection
                if selection is None:
                    selection = prompt_selection if sys.stdin.isatty() and not opts.json_output else 'all'
                report = plan_native(opts.package, opts.target, mapping, selection=selection)
            elif opts.action == 'apply':
                progress('重新核对预览并备份、合并资料')
                report = install_native(opts.package, opts.target, opts.approve, mapping, selection=opts.selection or 'all')
            else:
                progress('恢复上次中断的合并')
                report = recover_native(opts.target)
        show_report(report, opts)
    except (MigrationError, OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error,
            zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(f'操作已停止。步骤：{phase}\n{explain_failure(exc)}', file=sys.stderr)
        if opts.action in ('apply', 'recover'):
            print('保持 Codex 退出。若提示有未完成的合并，可运行：\n' + mac_command('recover', opts), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('操作已取消。若是在合并写入时中断，请保持 Codex 退出并运行 recover。', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
