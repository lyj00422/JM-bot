import sqlite3

import pytest
from jmbot.native_migration import migrate_native_roots


def test_offline_copy_preserves_rows_and_rewrites_media(tmp_path):
    source = tmp_path / 'old'
    for group in (1, 2):
        data = source / ('qq_99_group_' + str(group)) / 'data'
        data.mkdir(parents=True)
        image = data / 'emoji' / 'sample.jpg'
        image.parent.mkdir()
        image.write_bytes(b'local-image')
        with sqlite3.connect(data / 'MaiBot.db') as db:
            db.execute('CREATE TABLE images(full_path TEXT, description TEXT)')
            db.execute('INSERT INTO images VALUES(?,?)', ('data/emoji/sample.jpg', 'group ' + str(group)))
    report = migrate_native_roots(source, tmp_path / 'copied', '99')
    assert report['complete']
    for group in (1, 2):
        new = tmp_path / 'copied/qq_99_runtime/sessions' / ('group_' + str(group)) / 'data/MaiBot.db'
        with sqlite3.connect(new) as db:
            path, description = db.execute('SELECT * FROM images').fetchone()
            assert path == 'sessions/group_' + str(group) + '/data/emoji/sample.jpg'
            assert description == 'group ' + str(group)
        with sqlite3.connect(source / ('qq_99_group_' + str(group)) / 'data/MaiBot.db') as db:
            assert db.execute('SELECT full_path FROM images').fetchone()[0] == 'data/emoji/sample.jpg'
    with pytest.raises(ValueError, match='new directory'):
        migrate_native_roots(source, tmp_path / 'copied', '99')
