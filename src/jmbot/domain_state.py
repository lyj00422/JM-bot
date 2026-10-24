"""Management facade over independently owned chat/download policies."""
from types import SimpleNamespace
import json
import sqlite3
import threading
import time
from contextlib import closing


class PartialApplyError(RuntimeError):
    def __init__(self, domains):
        super().__init__('inspect domain application state before retry')
        self.domains = domains


class DomainState:
    def __init__(self, chat, download, journal=None):
        self.chat, self.download = chat, download
        self.journal = journal
        self.lock = threading.RLock()
        if journal:
            from pathlib import Path
            Path(journal).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(journal)) as conn, conn:
                conn.execute('CREATE TABLE IF NOT EXISTS policy_delivery(domain TEXT, scope TEXT, desired TEXT NOT NULL, applied INTEGER, error TEXT, updated REAL NOT NULL, PRIMARY KEY(domain,scope))')

    def delivery_status(self):
        if not self.journal:
            return []
        with closing(sqlite3.connect(self.journal)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row, desired=json.loads(row['desired'])) for row in conn.execute('SELECT * FROM policy_delivery ORDER BY updated DESC')]

    def _apply(self, domain, scope, values, expected=None, persist=True):
        with self.lock:
            desired = {'values': values, 'expected': expected}
            if self.journal and persist:
                with closing(sqlite3.connect(self.journal)) as conn, conn:
                    conn.execute('INSERT INTO policy_delivery VALUES(?,?,?,NULL,NULL,?) ON CONFLICT(domain,scope) DO UPDATE SET desired=excluded.desired,applied=NULL,error=NULL,updated=excluded.updated', (domain, scope, json.dumps(desired), time.time()))
            store = self.download if domain == 'download' else self.chat
            applied, error = True, None
            try:
                if scope == '*':
                    if store.global_enabled(domain) != values['enabled']:
                        store.set_global_enabled(domain, values['enabled'])
                else:
                    current = store.get_policy(scope)
                    if not store.has_policy(scope) or not all(getattr(current, key) == value for key, value in values.items()):
                        store.set_policy(scope, **({'expected_version': expected} if domain == 'chat' else {}), **values)
            except ValueError:
                applied, error = False, 'version_conflict_or_invalid_policy'
            except RuntimeError:
                applied, error = None, 'service_unavailable_or_result_unknown'
            if self.journal:
                with closing(sqlite3.connect(self.journal)) as conn, conn:
                    conn.execute('UPDATE policy_delivery SET applied=?,error=?,updated=? WHERE domain=? AND scope=?', (applied, error, time.time(), domain, scope))
            return {'desired': values, 'applied': applied, 'error': error}

    def reconcile(self):
        with self.lock:
            for row in self.delivery_status():
                if row['applied'] is None:
                    self._apply(row['domain'], row['scope'], row['desired']['values'], row['desired']['expected'], persist=False)

    def __getattr__(self, name):
        return getattr(self.chat, name)

    def global_enabled(self, domain):
        return (self.download if domain == 'download' else self.chat).global_enabled(domain)

    def set_global_enabled(self, domain, enabled):
        if domain not in {'chat', 'download'} or type(enabled) is not bool:
            raise ValueError('invalid domain or switch')
        outcome = self._apply(domain, '*', {'enabled': enabled})
        if outcome['applied'] is not True:
            raise PartialApplyError({domain: outcome})

    def get_policy(self, scope):
        chat = self.chat.get_policy(scope)
        try:
            download = self.download.get_policy(scope)
        except RuntimeError:
            download = SimpleNamespace(download_enabled=None, download_blacklisted=None)
        return SimpleNamespace(**dict(vars(chat), download_enabled=download.download_enabled, download_blacklisted=download.download_blacklisted))

    def set_policy(self, scope, *, expected_version=None, **changes):
        from jmbot.core.conversation_state import ConversationStateStore
        scope = ConversationStateStore._validate_key(getattr(scope, 'key', scope))
        download = {key: value for key, value in changes.items() if key.startswith('download_')}
        chat = {key: value for key, value in changes.items() if not key.startswith('download_')}
        outcomes = {}
        for name, store, values in (('chat', self.chat, chat), ('download', self.download, download)):
            if not values:
                continue
            outcomes[name] = self._apply(name, scope, values, expected_version if name == 'chat' else None)
        if any(item['applied'] is not True for item in outcomes.values()):
            raise PartialApplyError(outcomes)
        if download and not chat:
            return self.download.get_policy(scope)
        if chat and not download:
            return self.chat.get_policy(scope)
        try:
            return self.get_policy(scope)
        except RuntimeError:
            raise PartialApplyError(outcomes) from None

    def export_scope(self, scope):
        result = self.chat.export_scope(scope)
        result['policy'] = vars(self.get_policy(scope))
        return result

    def list_policies(self, kind=None):
        rows = {row['scope_key']: row for row in self.chat.list_policies(kind)}
        for row in self.download.list_policies(kind):
            key = row['scope_key']
            if key not in rows:
                rows[key] = dict(row, chat_enabled=False)
            rows[key].update(download_enabled=row['download_enabled'], download_blacklisted=row['download_blacklisted'])
        return list(rows.values())
