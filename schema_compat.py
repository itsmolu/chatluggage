"""Inspect migration-relevant structures and copy rows by column name."""

import re
from dataclasses import dataclass


class MigrationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise MigrationError(message)


STATE_TABLES = {
    'threads': {'id', 'rollout_path', 'cwd', 'title', 'project_id', 'thread_section_id',
                'attached_thread_parent_id', 'sandbox_policy', 'approval_mode', 'git_origin_url'},
    'projects': {'id', 'name', 'metadata', 'position', 'created_at_ms', 'updated_at_ms'},
    'project_roots': {'project_id', 'position', 'path'},
    'project_idempotency_keys': {'key', 'project_id', 'created_at_ms'},
    'thread_sections': {'id', 'name', 'appearance'},
    'thread_spawn_edges': {'parent_thread_id', 'child_thread_id'},
    'thread_attachments': {'thread_id'},
    'thread_dynamic_tools': {'thread_id'},
    'thread_read_receipts': {'thread_id'},
    'guardian_review_feedback': {'thread_id'},
}
HISTORY_TABLES = {name: {'thread_id'} for name in (
    'thread_history_projection_state', 'thread_turns', 'thread_items', 'thread_realtime_items')}
OPTIONAL_TABLES = {'session_model_ids': {'session_id', 'kind', 'external_key', 'ordinal', 'initial_state'}}
# These are host settings or rebuild progress, excluded from portable exports.
HOST_TABLES = {'backfill_state', 'remote_control_enrollments', 'external_agent_config_imports',
               'rollout_migration_state', 'rollout_migration_skipped_rollouts'}
IDENTITY_COLUMNS = {'thread_id', 'session_id', 'project_id', 'parent_thread_id', 'child_thread_id',
                    'turn_id', 'item_id'}


@dataclass(frozen=True)
class Column:
    type_name: str
    required: bool
    default: str | None
    primary_key: bool
    generated: bool


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def tables(db, schema='main'):
    return {name for name, in db.execute(
        f"SELECT name FROM {schema}.sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def columns(db, table, schema='main'):
    return {row[1]: Column(row[2], bool(row[3]), row[4], bool(row[5]), bool(row[6]))
            for row in db.execute(f'PRAGMA {schema}.table_xinfo({quoted(table)})')}


def inspect_database(db, name):
    required = STATE_TABLES if name == 'state_5.sqlite' else HISTORY_TABLES
    available = tables(db)
    require('_sqlx_migrations' in available, f'{name}: 缺少 SQLx 迁移记录。')
    migrations = db.execute('SELECT version,success,description,hex(checksum) FROM _sqlx_migrations ORDER BY version').fetchall()
    require(migrations and all(type(v) is int and v > 0 and success == 1 and isinstance(desc, str)
                              and re.fullmatch('[A-F0-9]{96}', checksum)
                              for v, success, desc, checksum in migrations), f'{name}: SQLx 迁移记录不完整或存在未成功的更新。')
    expected = dict(required)
    if name == 'state_5.sqlite':
        expected.update({t: fields for t, fields in OPTIONAL_TABLES.items() if t in available})
    for table, fields in expected.items():
        require(table in available, f'{name}: 缺少必需的表 {table}。')
        actual = columns(db, table)
        missing = fields - actual.keys()
        require(not missing, f'{name}: 表 {table} 缺少必需字段 {", ".join(sorted(missing))}。')
        require(all(not column.generated for column in actual.values()), f'{name}: 表 {table} 含待适配的生成字段。')
    for table in sorted(available - expected.keys() - HOST_TABLES - {'_sqlx_migrations'}):
        actual = columns(db, table)
        references = {row[2] for row in db.execute(f'PRAGMA foreign_key_list({quoted(table)})')}
        related = (actual.keys() & IDENTITY_COLUMNS or references & expected.keys()
                   or re.search(r'thread|session|project|conversation|message|turn', table, re.I))
        require(not related, f'{name}: 发现待适配的聊天关联表 {table}。请保留迁移包并使用支持该表的工具版本。')
    return migrations


def triggers(db, schema, relevant):
    return {name: sql.replace('\r\n', '\n').strip() for name, table, sql in db.execute(
        f"SELECT name,tbl_name,sql FROM {schema}.sqlite_schema WHERE type='trigger'") if table in relevant}


def compare_databases(target, source, name):
    local = inspect_database(target, name)
    incoming = inspect_database(source, name)
    local_descriptions = {row[0]: row[2] for row in local}
    require(all(row[2] == local_descriptions[row[0]] for row in incoming if row[0] in local_descriptions),
            f'{name}: 两端已有迁移记录的说明不一致，需要检查对应版本。')
    relevant = (STATE_TABLES.keys() | OPTIONAL_TABLES.keys()) if name == 'state_5.sqlite' else HISTORY_TABLES.keys()
    require(triggers(target, 'main', relevant) == triggers(source, 'main', relevant),
            f'{name}: 聊天表触发器发生变化，需要适配后再合并。')
    schema_sql = "SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    defaults = {}
    for table in sorted(tables(source) & tables(target) & relevant):
        additions = columns(target, table).keys() - columns(source, table).keys()
        if additions:
            defaults[table] = sorted(additions)
    return {'migration_version': local[-1][0], 'source_migration_version': incoming[-1][0],
            'schema_matches': target.execute(schema_sql).fetchall() == source.execute(schema_sql).fetchall(),
            'source_checksums_match': local == incoming, 'target_checksums_preserved': True,
            'structure_compatible': True, 'target_only_columns': defaults}


def affinity(type_name):
    value = type_name.upper()
    if 'INT' in value:
        return 'INTEGER'
    if any(part in value for part in ('CHAR', 'CLOB', 'TEXT')):
        return 'TEXT'
    if not value or 'BLOB' in value:
        return 'BLOB'
    if any(part in value for part in ('REAL', 'FLOA', 'DOUB')):
        return 'REAL'
    return 'NUMERIC'


def insert_rows(db, table, predicate, params=()):
    """Copy selected rows from attached incoming, preserving all representable fields."""
    if table not in tables(db, 'incoming'):
        return 0
    origin = f'incoming.{quoted(table)}'
    count = db.execute(f'SELECT count(*) FROM {origin} s WHERE {predicate}', params).fetchone()[0]
    if not count:
        return 0
    require(table in tables(db), f'目标数据库缺少所选聊天使用的表 {table}，无法完整导入。')
    source, target = columns(db, table, 'incoming'), columns(db, table)
    require({key for key, column in source.items() if column.primary_key}
            == {key for key, column in target.items() if column.primary_key},
            f'表 {table} 的主键字段发生变化，需要适配后再合并。')
    for key in source.keys() & target.keys():
        require(affinity(source[key].type_name) == affinity(target[key].type_name), f'表 {table} 的字段 {key} 类型不兼容。')
    for key in source.keys() - target.keys():
        populated = db.execute(f'SELECT 1 FROM {origin} s WHERE ({predicate}) AND s.{quoted(key)} IS NOT NULL LIMIT 1', params).fetchone()
        require(not populated, f'目标表 {table} 缺少源字段 {key}，该字段含有所选聊天的数据，无法完整导入。')
    for key in target.keys() - source.keys():
        require(not target[key].required or target[key].default is not None,
                f'目标表 {table} 的必填字段 {key} 没有默认值，源记录无法填充。')
    names = ','.join(quoted(key) for key in target if key in source)
    inserted = db.execute(f'INSERT INTO main.{quoted(table)} ({names}) SELECT {names} FROM {origin} s WHERE {predicate}', params)
    require(inserted.rowcount == count, f'表 {table} 的实际写入数量与选中数量不一致，合并已停止。')
    return count


def create_session_scope(db, schema, seed_query):
    """Include root/ancestor namespaces shared by selected workers, without importing their chats."""
    db.execute(f'''CREATE TEMP TABLE model_id_scope AS
        WITH RECURSIVE scope(id) AS (
            {seed_query}
            UNION
            SELECT t.attached_thread_parent_id FROM {schema}.threads t JOIN scope ON t.id=scope.id
                WHERE t.attached_thread_parent_id IS NOT NULL
            UNION
            SELECT e.parent_thread_id FROM {schema}.thread_spawn_edges e JOIN scope ON e.child_thread_id=scope.id
                WHERE e.parent_thread_id IS NOT NULL
        ) SELECT id FROM scope''')


def copy_session_model_ids(db):
    if 'session_model_ids' not in tables(db, 'incoming'):
        return
    create_session_scope(db, 'incoming', 'SELECT id FROM new_threads')
    predicate = 'session_id IN (SELECT id FROM model_id_scope)'
    if 'session_model_ids' in tables(db):
        shared = columns(db, 'session_model_ids').keys() & columns(db, 'session_model_ids', 'incoming').keys()
        differences = ' OR '.join(f's.{quoted(key)} IS NOT m.{quoted(key)}' for key in sorted(shared))
        conflict = db.execute(f'''SELECT 1 FROM incoming.session_model_ids s JOIN main.session_model_ids m
            ON s.session_id=m.session_id AND s.kind=m.kind
                AND (s.external_key=m.external_key OR s.ordinal=m.ordinal)
            WHERE s.{predicate} AND ({differences}) LIMIT 1''').fetchone()
        require(not conflict, 'session_model_ids 的共享会话标识与 Mac 现有记录冲突，无法完整导入子会话。')
        predicate += ''' AND NOT EXISTS (SELECT 1 FROM main.session_model_ids m
            WHERE m.session_id=s.session_id AND m.kind=s.kind AND m.external_key=s.external_key)'''
    insert_rows(db, 'session_model_ids', predicate)
