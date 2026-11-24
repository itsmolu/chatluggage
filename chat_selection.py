"""Stable chat numbers and a paged, local-time-as-recorded terminal picker."""

from collections import defaultdict
from itertools import groupby
from pathlib import Path
import re

from history_migration import MigrationError, ROLLOUT, require


def catalog(db, manifest):
    rows = []
    for tid, title, project in db.execute(
            'SELECT t.id,t.title,p.name FROM incoming.threads t LEFT JOIN incoming.projects p ON t.project_id=p.id'):
        rollout = manifest[tid]['rollout']
        match = ROLLOUT.fullmatch(Path(rollout).name)
        require(match and match[1] == tid and not match[2], '会话清单与文件名不一致。')
        rows.append((rollout.rsplit('/', 1)[-1][8:27], tid, title or '未命名聊天', project or '无项目'))
    return sorted(rows, key=lambda row: (row[0], row[1]), reverse=True)


def numbers(value, limit):
    selected = set()
    for part in value.replace('，', ',').split(','):
        match = re.fullmatch(r'([0-9]{1,6})(?:-([0-9]{1,6}))?', part.strip())
        require(match, '请输入编号或范围，例如 1,3-5。')
        first, last = int(match[1]), int(match[2] or match[1])
        require(1 <= first <= last <= limit, f'编号范围是 1 到 {limit}。')
        selected.update(range(first, last + 1))
    return selected


def ranges(values):
    parts = []
    for _, group in groupby(enumerate(sorted(values)), lambda pair: pair[1] - pair[0]):
        run = [n for _, n in group]
        parts.append(str(run[0]) if len(run) == 1 else f'{run[0]}-{run[-1]}')
    return ','.join(parts)


def label(value, limit=70):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))[:limit]


def answer(prompt):
    try:
        value = input(prompt).strip().lower()
    except EOFError:
        raise MigrationError('输入已结束，预览已取消。') from None
    if value in ('q', '0'):
        raise MigrationError('已取消选择。')
    return value


def prompt_selection(rows, local_ids):
    months = defaultdict(list)
    for n, row in enumerate(rows, 1):
        months[row[0][:7]].append(n)
    print(f'迁移包共有 {len(rows)} 条聊天，按会话文件记录的开始月份排列（最近在前）。')
    for i, (month, indices) in enumerate(months.items(), 1):
        existing = sum(rows[n - 1][1] in local_ids for n in indices)
        print(f'  {i}. {month}　{len(indices)} 条，其中 Mac 已有 {existing} 条')
    while True:
        mode = answer('选择导入范围　[1] 全选　[2] 自选月份或聊天　[0] 取消：')
        if mode == '1':
            return 'all'
        if mode == '2':
            break
        print('请输入 1、2 或 0。')
    groups = list(months.values())
    while True:
        value = answer('输入上面的月份编号（例如 1 或 1,3-5），a 浏览所有月份，0 取消：')
        try:
            chosen = range(1, len(groups) + 1) if value == 'a' else sorted(numbers(value, len(groups)))
            visible = [n for m in chosen for n in groups[m - 1]]
            break
        except MigrationError as error:
            print(error)
    picked, page, size = set(), 0, 15
    visible_set = set(visible)
    pages = (len(visible) + size - 1) // size
    while True:
        print(f'\n聊天列表　第 {page + 1}/{pages} 页　已选 {len(picked)} 条')
        for n in visible[page * size:(page + 1) * size]:
            stamp, tid, title, project = rows[n - 1]
            flag = 'Mac 已有' if tid in local_ids else '待导入'
            print(f'  {"✓" if n in picked else " "} {n}. {stamp[:10]}　{label(project, 32)}　{label(title)}　[{flag}]')
        cmd = answer('输入聊天编号（如 2,4-6）切换勾选，n/p 翻页，a 选定这些月份全部聊天，回车确认，0 取消：')
        if cmd == 'a':
            return ranges(visible)
        if cmd in ('n', 'p'):
            page = min(pages - 1, max(0, page + (1 if cmd == 'n' else -1)))
        elif not cmd and picked:
            return ranges(picked)
        else:
            try:
                indices = numbers(cmd, len(rows))
                require(indices <= visible_set, '请使用所选月份中的聊天编号。')
                picked.symmetric_difference_update(indices)
            except MigrationError as error:
                print(error)
