"""Offline split migration. Never overwrites a running or populated target."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from jmbot.core.process_lease import ProcessLease


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def snapshot(source, target, master_key=None):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target == source or target.is_relative_to(source):
        raise ValueError('snapshot must be outside the live data directory')
    target.mkdir(parents=True, exist_ok=False)
    target.chmod(0o700)
    manifest = {'schema': 1, 'files': {}, 'sqlite': {}, 'master_key_external': bool(master_key)}
    try:
        for path in sorted(source.rglob('*')):
            if not path.is_file() or path.is_symlink() or path.name.endswith(('-wal', '-shm', '.lock', '.log')) or path.name == 'ready' or 'control' in path.relative_to(source).parts:
                continue
            relative = path.relative_to(source)
            destination = target / 'data' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with path.open('rb') as stream:
                sqlite = stream.read(16) == b'SQLite format 3\x00'
            if sqlite:
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as old, closing(sqlite3.connect(destination)) as new:
                    old.backup(new)
                    if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('snapshot integrity check failed')
                    manifest['sqlite'][str(relative).replace('\\', '/')] = {row[0]: new.execute('SELECT count(*) FROM "' + row[0].replace('"', '""') + '"').fetchone()[0] for row in new.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            else:
                shutil.copy2(path, destination)
            destination.chmod(0o600)
            manifest['files'][str(destination.relative_to(target)).replace('\\', '/')] = digest(destination)
        if master_key:
            key = target / 'secrets' / 'chat-master.key'
            key.parent.mkdir(mode=0o700)
            shutil.copyfile(master_key, key)
            key.chmod(0o600)
            manifest['files']['secrets/chat-master.key'] = digest(key)
        (target / 'snapshot.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return manifest
    except Exception:
        # Keep partial output for diagnosis, explicitly unusable without manifest.
        raise


def verify(source):
    source = Path(source).resolve()
    manifest = json.loads((source / 'snapshot.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 1:
        raise ValueError('unsupported snapshot')
    for name, expected in manifest['files'].items():
        from pathlib import PurePosixPath
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or any(part in {'..', '.'} or ':' in part or '\\' in part for part in relative.parts):
            raise ValueError('invalid snapshot path')
        path = (source / name).resolve()
        if not path.is_relative_to(source) or not path.is_file() or digest(path) != expected:
            raise ValueError('snapshot file missing or hash mismatch')
    return manifest


def restore(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    manifest = verify(source)
    target.mkdir(parents=True, exist_ok=False)
    for name in manifest['files']:
        destination = (target / name).resolve()
        if not destination.is_relative_to(target):
            raise ValueError('invalid restore destination')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
        destination.chmod(0o600)
    return {'restored': len(manifest['files']), 'directory': str(target)}


def migrate(source, target, *, apply=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    manifest = verify(source)
    report = {'schema': 1, 'source_hash': digest(source / 'snapshot.json'), 'tables': manifest['sqlite'],
              'legacy_history': 'retained in chat.sqlite3; never automatically summarized or merged across scopes',
              'scope_mapping': 'private:user_id / group:group_id; native data qq:account:kind:owner',
              'applied': False}
    data_source = source / 'data'
    credential_db = data_source / 'chat.sqlite3'
    credential_rows = []
    if credential_db.exists():
        from cryptography.fernet import Fernet, InvalidToken
        key_path = source / 'secrets' / 'chat-master.key'
        if not key_path.exists():
            key_path = data_source / 'chat-master.key'
        with closing(sqlite3.connect(credential_db.as_uri() + '?mode=ro', uri=True)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('chat_credentials', 'private_api_keys', 'group_api_keys'):
                if table not in tables:
                    continue
                for row in conn.execute('SELECT * FROM ' + table):
                    if table == 'chat_credentials':
                        kind, owner, provider, encrypted = row[:4]
                    else:
                        kind = 'private' if table.startswith('private') else 'group'
                        owner, encrypted = row[:2]
                        provider = 'ds_school'
                    credential_rows.append({'scope': f'{kind}:{owner}', 'provider': provider})
                    if str(encrypted).startswith('gAAAA'):
                        if not key_path.is_file():
                            raise ValueError('encrypted credentials require the matching master key in the snapshot')
                        try:
                            Fernet(key_path.read_bytes().strip()).decrypt(str(encrypted).encode())
                        except (InvalidToken, ValueError):
                            raise ValueError('snapshot master key cannot decrypt credentials') from None
    report['credentials'] = credential_rows
    role_file = data_source / 'chat_role.json'
    report['role'] = {'present': role_file.exists(), 'sha256': digest(role_file) if role_file.exists() else None}
    sticker_file = data_source / 'stickers' / 'manifest.json'
    rows = json.loads(sticker_file.read_text(encoding='utf-8')) if sticker_file.exists() else []
    if isinstance(rows, dict):
        rows = rows.get('stickers', [])
    report['stickers'] = {'count': len(rows), 'max_id': max([int(row['id']) for row in rows if str(row.get('id', '')).isdigit()] or [0])}
    policy_source = data_source / 'conversation.sqlite3'
    report['policies'] = []
    if policy_source.exists():
        with closing(sqlite3.connect(policy_source.as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='conversation_policies'").fetchone():
                report['policies'] = [dict(row) for row in conn.execute('SELECT * FROM conversation_policies')]
    report['retained_files'] = sorted(manifest['files'])
    report['unknown_data'] = 'All snapshot files are preserved; legacy history remains archived and is not merged into native scope stores.'
    if not apply:
        return report
    receipt = target / 'migration.json'
    if receipt.exists():
        previous = json.loads(receipt.read_text(encoding='utf-8'))
        if previous['source_hash'] != report['source_hash']:
            raise ValueError('target already belongs to another snapshot')
        return previous
    pending = target / 'migration.pending.json'
    if target.exists():
        if not pending.exists() or json.loads(pending.read_text(encoding='utf-8')).get('source_hash') != report['source_hash']:
            raise ValueError('target is not a resumable migration for this snapshot')
    else:
        target.mkdir(parents=True)
        pending.write_text(json.dumps({'source_hash': report['source_hash'], 'stage': 'copy'}), encoding='utf-8')
    progress = json.loads(pending.read_text(encoding='utf-8'))
    if progress.get('stage', 'converted') == 'copy':
        for name in manifest['files']:
            destination = (target / name).resolve()
            if not destination.is_relative_to(target):
                raise ValueError('invalid migration destination')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.is_file() or digest(destination) != manifest['files'][name]:
                temporary = destination.with_name(destination.name + '.restore-tmp')
                shutil.copyfile(source / name, temporary)
                temporary.chmod(0o600)
                os.replace(temporary, destination)
        progress['stage'] = 'converted'
        temporary = pending.with_suffix('.tmp')
        temporary.write_text(json.dumps(progress), encoding='utf-8')
        os.replace(temporary, pending)
    from jmbot.core.conversation_state import ConversationStateStore
    data = target / 'data'
    old = data / 'conversation.sqlite3'
    state = ConversationStateStore(old)
    if (data / 'chat.sqlite3').is_file():
        with closing(sqlite3.connect(data / 'chat.sqlite3')) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='chat_settings'").fetchone():
                enabled = conn.execute("SELECT value FROM chat_settings WHERE key='enabled'").fetchone()
                if enabled and enabled[0] == '0':
                    state.set_global_enabled('chat', False)
    download = ConversationStateStore(data / 'download-policy.sqlite3')
    for row in state.list_policies():
        download.set_policy(row['scope_key'], download_enabled=bool(row['download_enabled']), download_blacklisted=bool(row['download_blacklisted']))
    download.set_global_enabled('download', state.global_enabled('download'))
    if state.get_default_persona('', '')[2] == 0 and (data / 'chat_role.json').exists():
        from jmbot.core.chat_service import load_chat_role
        role = load_chat_role(data / 'chat_role.json')
        state.set_default_persona(role.name, role.system_prompt)
    report['policies'] = state.list_policies()
    report['download_global_enabled'] = download.global_enabled('download')
    # Credentials and the fixed role remain byte-preserved. Missing keys must
    # never silently generate replacement encryption material during migration.
    report['applied'] = True
    temporary = receipt.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, receipt)
    pending.unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(description='JMbot offline backup / split migration / isolated restore')
    parser.add_argument('operation', choices=['backup', 'verify', 'dry-run', 'migrate', 'restore', 'preserve-rollback'])
    parser.add_argument('--source', required=True)
    parser.add_argument('--target')
    parser.add_argument('--master-key')
    args = parser.parse_args()
    if args.operation == 'verify':
        result = verify(args.source)
    elif args.operation in {'backup', 'preserve-rollback'}:
        if not args.target:
            parser.error('--target is required')
        # Operators stop senders first. This lock rejects another migration.
        lock_root = Path(os.environ.get('JM_BOT_RUNTIME_LOCK_DIR', args.source))
        lease = ProcessLease(lock_root / 'qq-sender.lock')
        chat_lease = None
        try:
            chat_lease = ProcessLease(Path(os.environ.get('JM_BOT_CHAT_DATA', str(lock_root / 'chat-service'))) / 'writer.lock')
            result = snapshot(args.source, args.target, args.master_key)
        finally:
            if chat_lease:
                chat_lease.close()
            lease.close()
    else:
        if not args.target:
            parser.error('--target is required')
        target = Path(args.target).resolve()
        migration_lease = ProcessLease(target.parent / (target.name + '.migration.lock'))
        try:
            result = restore(args.source, target) if args.operation == 'restore' else migrate(args.source, target, apply=args.operation == 'migrate')
        finally:
            migration_lease.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
