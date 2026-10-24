"""Bounded file-mailbox IPC, consumed by the scope's native event loop."""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path


class NativeControl:
    def __init__(self, pool, loop, state):
        self.pool, self.loop, self.state = pool, loop, state

    def status(self):
        return self.pool.snapshot()

    def memory(self, identity, action='list', **arguments):
        parts = identity.split(':')
        if len(parts) != 4 or parts[0] != 'qq' or not parts[1].isdigit():
            raise ValueError('invalid account scope')
        scope = ':'.join(parts[2:])
        self.state._validate_key(scope)
        if action not in {'list', 'get', 'create', 'update', 'retract', 'restore'}:
            raise ValueError('unsupported memory action')
        if set(arguments) - {'scope_type', 'scope_id', 'claim_id', 'fact_key', 'value_text', 'confidence', 'reason', 'statuses', 'limit'}:
            raise ValueError('unsupported memory fields')
        arguments['limit'] = min(int(arguments.get('limit', 200)), 500)
        async def invoke():
            entry = self.pool.processes.get(identity)
            if not entry or entry['process'].returncode is not None:
                raise RuntimeError('native instance is offline; send a permitted message to start it')
            directory = self.pool.root / identity.replace(':', '_') / 'control'
            directory.mkdir(exist_ok=True)
            if len(list(directory.glob('*.request'))) >= 16:
                raise RuntimeError('native control capacity reached')
            name = uuid.uuid4().hex
            temporary, request, response = (directory / (name + ext) for ext in ('.tmp', '.request', '.response'))
            temporary.write_text(json.dumps({'action': action, 'arguments': arguments}), encoding='utf-8')
            os.replace(temporary, request)
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if response.is_file():
                        result = json.loads(response.read_text(encoding='utf-8'))
                        if 'error' in result:
                            raise RuntimeError(result['error'])
                        return result
                    await asyncio.sleep(.1)
                raise RuntimeError('native memory outcome unknown; inspect before retrying')
            finally:
                request.unlink(missing_ok=True)
                response.unlink(missing_ok=True)
        return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(28)


async def serve_control(directory):
    from src.A_memorix.host_service import a_memorix_host_service
    directory = Path(directory) / 'control'
    directory.mkdir(exist_ok=True)
    while True:
        for request in sorted(directory.glob('*.request'))[:16]:
            try:
                body = json.loads(request.read_text(encoding='utf-8'))
                if body['action'] == 'list' and not body['arguments'].get('scope_id'):
                    # This kernel owns only this account/scope instance. Discover
                    # native subjects instead of asking operators to guess hashes.
                    kernel = await a_memorix_host_service._ensure_kernel()
                    store = kernel.metadata_store
                    limit = min(int(body['arguments'].get('limit', 200)), 500)
                    def list_owned():
                        rows = store._conn.execute('SELECT claim_id FROM fact_claims ORDER BY updated_at DESC LIMIT ?', (limit,)).fetchall()
                        return [store.get_fact_claim(row[0]) for row in rows]
                    items = await asyncio.to_thread(list_owned)
                    result = {'success': True, 'items': items, 'count': len(items)}
                else:
                    result = await a_memorix_host_service.invoke(
                        'memory_fact_admin', {'action': body['action'], **body['arguments']}, timeout_ms=20000)
            except Exception:
                result = {'error': 'native memory operation failed'}
            response = request.with_suffix('.response')
            temporary = request.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
            os.replace(temporary, response)
            request.unlink(missing_ok=True)
        for old in directory.glob('*.response'):
            if old.stat().st_mtime < time.time() - 120:
                old.unlink(missing_ok=True)
        await asyncio.sleep(.2)
