"""Route every native ORM session to an independent scope database."""
from pathlib import Path
import threading


def install(sessions):
    from jmbot.native_sessions import current_scope
    from src.common.database import database
    from src.common.database.migrations import create_database_migration_bootstrapper
    from sqlalchemy import event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel, Session, create_engine
    original = database.SessionLocal
    engines, lock = {}, threading.RLock()

    def factory(**kwargs):
        scope = current_scope.get()
        if scope is None:
            return original(**kwargs)
        sessions.context(scope)
        with lock:
            if scope not in engines:
                root = Path.cwd() / 'sessions' / scope.replace(':', '_') / 'data'
                root.mkdir(parents=True, exist_ok=True)
                engine = create_engine('sqlite:///' + str(root / 'MaiBot.db'), poolclass=NullPool, connect_args={'check_same_thread': False})
                @event.listens_for(engine, 'connect')
                def limits(connection, record):
                    connection.execute('PRAGMA cache_size=-4096')
                    connection.execute('PRAGMA busy_timeout=30000')
                bootstrap = create_database_migration_bootstrapper(engine)
                migration = bootstrap.prepare_database()
                SQLModel.metadata.create_all(engine)
                bootstrap.finalize_database(migration)
                engines[scope] = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)
            return engines[scope](**kwargs)
    database.SessionLocal = factory
    sessions.database_factories = engines
