import threading
import time
from contextlib import nullcontext

import pytest

pytest.importorskip('sqlalchemy')  # Native dependency lives in MaiBot's environment.
from sqlalchemy import Column, Integer, create_engine
from sqlalchemy.orm import Session, declarative_base

from jmbot.core.write_barrier import WriteBarrier
from jmbot.native_write_guard import guard_session_writes


def check_flush_lock_order(tmp_path, guarded=True):
    engine = create_engine('sqlite:///' + str(tmp_path / 'native.db'), connect_args={'timeout': .2, 'check_same_thread': False})
    base = declarative_base()
    class Row(base):
        __tablename__ = 'rows'
        id = Column(Integer, primary_key=True)
    base.metadata.create_all(engine)
    class NativeSession(Session):
        pass
    barrier = WriteBarrier(tmp_path / 'policy.lock')
    if guarded:
        guard_session_writes(NativeSession, barrier, lambda: None)
    first_flushed = threading.Event()
    second_started = threading.Event()
    errors = []

    def writer_one():
        try:
            with NativeSession(engine) as session:
                session.add(Row(id=1))
                session.flush()
                first_flushed.set()
                assert second_started.wait(2)
                time.sleep(.05)
                with nullcontext() if guarded else barrier.hold():
                    session.commit()
        except Exception as exc:
            errors.append(exc)

    def writer_two():
        try:
            assert first_flushed.wait(2)
            with NativeSession(engine) as session:
                session.add(Row(id=2))
                second_started.set()
                with nullcontext() if guarded else barrier.hold():
                    session.commit()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=fn, daemon=True) for fn in (writer_one, writer_two)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    with NativeSession(engine) as session:
        count = session.query(Row).count()
    assert barrier.depth == 0
    engine.dispose()
    return errors, count


def test_flush_and_commit_use_same_lock_order(tmp_path):
    errors, count = check_flush_lock_order(tmp_path)
    assert not errors
    assert count == 2


def test_commit_only_guard_reproduces_sqlite_lock(tmp_path):
    errors, count = check_flush_lock_order(tmp_path, guarded=False)
    assert any('database is locked' in str(error) for error in errors)
    assert count == 1


def test_denied_flush_releases_barrier_and_writes_nothing(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'native.db'))
    base = declarative_base()
    class Row(base):
        __tablename__ = 'rows'
        id = Column(Integer, primary_key=True)
    base.metadata.create_all(engine)
    class NativeSession(Session):
        pass
    barrier = WriteBarrier(tmp_path / 'policy.lock')
    def denied():
        raise RuntimeError('revoked')
    guard_session_writes(NativeSession, barrier, denied)
    with NativeSession(engine) as session:
        session.add(Row(id=1))
        with pytest.raises(RuntimeError, match='revoked'):
            session.flush()
        session.rollback()
    assert barrier.depth == 0
    with Session(engine) as session:
        assert session.query(Row).count() == 0
    engine.dispose()
