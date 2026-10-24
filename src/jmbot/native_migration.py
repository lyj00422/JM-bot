"""Offline copy of per-scope native roots into the single-runtime layout."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3


def migrate_native_roots(source, target, account):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not str(account).isdigit() or not source.is_dir():
        raise ValueError('valid source and numeric account required')
    if target.exists() or target.is_relative_to(source):
        raise ValueError('target must be a new directory outside source')
    roots = []
    for path in sorted(source.glob('qq_' + str(account) + '_*')):
        match = re.fullmatch(r'qq_' + str(account) + r'_(group|private)_([1-9][0-9]*)', path.name)
        if not match:
            continue
        if any(item.is_symlink() for item in [path, *path.rglob('*')]):
            raise ValueError('symlinks are not supported in native migration')
        roots.append((path, match.group(1) + '_' + match.group(2)))
    if not roots:
        raise ValueError('no legacy native scope roots found')
    target.mkdir(parents=True)
    report = {'account': str(account), 'scopes': {}, 'complete': False}
    runtime = target / ('qq_' + str(account) + '_runtime')
    runtime.mkdir()
    ownership = sqlite3.connect(runtime / 'emoji-ownership.sqlite3')
    ownership.execute('CREATE TABLE emoji_scopes(scope TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(scope,hash))')
    try:
        for old, scope_dir in roots:
            destination = runtime / 'sessions' / scope_dir
            data = old / 'data'
            if not data.is_dir():
                raise ValueError('legacy scope has no data directory')
            shutil.copytree(data, destination / 'data', ignore=shutil.ignore_patterns('*-wal', '*-shm'))
            tables = {}
            for database in sorted(data.rglob('*')):
                if not database.is_file():
                    continue
                with database.open('rb') as stream:
                    is_sqlite = stream.read(16) == b'SQLite format 3\x00'
                if not is_sqlite:
                    continue
                copied = destination / 'data' / database.relative_to(data)
                with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(copied) as output:
                    original.backup(output)
                    if output.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('SQLite integrity check failed')
                    names = [row[0] for row in output.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    counts = {}
                    for name in names:
                        quoted = '"' + name.replace('"', '""') + '"'
                        before = original.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
                        after = output.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
                        if before != after:
                            raise ValueError('SQLite row count changed during copy')
                        counts[name] = after
                    # Native media paths are relative to the old instance root.
                    for table in ('images', 'binary_data'):
                        if table not in names:
                            continue
                        for rowid, stored in output.execute('SELECT rowid,full_path FROM ' + table).fetchall():
                            resolved = Path(stored) if Path(stored).is_absolute() else old / stored
                            resolved = resolved.resolve()
                            if not resolved.is_relative_to(data):
                                raise ValueError('legacy media path is outside its data directory')
                            new_path = destination / 'data' / resolved.relative_to(data)
                            output.execute('UPDATE ' + table + ' SET full_path=? WHERE rowid=?', (new_path.relative_to(runtime).as_posix(), rowid))
                    tables[database.relative_to(data).as_posix()] = counts
            for image in (destination / 'data' / 'emoji').glob('*'):
                if not image.is_file():
                    continue
                digest = hashlib.sha256(image.read_bytes()).hexdigest()
                ownership.execute('INSERT OR IGNORE INTO emoji_scopes VALUES(?,?)', (scope_dir.replace('_', ':'), digest))
                common = runtime / 'data' / 'emoji' / (digest + image.suffix)
                common.parent.mkdir(parents=True, exist_ok=True)
                if not common.exists():
                    shutil.copyfile(image, common)
            report['scopes'][scope_dir.replace('_', ':')] = {'databases': tables}
        ownership.commit()
        report['complete'] = True
    finally:
        ownership.close()
        (target / 'native-migration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
