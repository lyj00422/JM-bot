"""Bounded native MaiBot processes. Each scope owns an entire native data root."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

MAIBOT_COMMIT = 'c602a2780f4f4dda4a6be46d67abc1cfb1ad420c'


class NativePool:
    def __init__(self, root, *, limit=4):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit = limit
        if not 1 <= limit <= 32:
            raise ValueError('native instance limit must be between 1 and 32')
        self.processes = {}
        self.lock = asyncio.Lock()
        self.errors = {}
        self.waiting = {}

    async def deliver(self, identity, event, context):
        async with self.lock:
            entry = self.processes.get(identity)
            fingerprint = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
            if entry and (entry['process'].returncode is not None or entry['fingerprint'] != fingerprint):
                await self._stop(identity)
                entry = None
            if entry is None:
                previous = self.errors.get(identity)
                if previous and time.time() - previous['at'] < 30:
                    raise RuntimeError('native startup cooling down; inspect scoped worker log')
                # Queue admission here; the supervisor rotates workers with a
                # bounded wait rather than starting an unbounded fork storm.
                if len(self.processes) >= self.limit:
                    from jmbot.core.ingress import classify
                    route = classify(event)
                    if identity not in self.waiting and len(self.waiting) >= 64:
                        raise RuntimeError('native admission queue full')
                    pending = self.waiting.setdefault(identity, [])
                    if len(pending) >= 8:
                        passive = next((i for i, (_, old, _) in enumerate(pending) if not classify(old).direct), None)
                        if passive is None:
                            raise RuntimeError('native scope admission queue full')
                        del pending[passive]
                    pending.append((time.time(), event, context))
                    return
                source = Path(os.environ['JM_BOT_MAIBOT_SOURCE']).resolve()
                import subprocess
                commit = await asyncio.to_thread(subprocess.check_output, ['git', '-C', str(source), 'rev-parse', 'HEAD'])
                if commit.decode().strip() != MAIBOT_COMMIT:
                    raise RuntimeError('MaiBot source differs from source lock')
                dirty = await asyncio.to_thread(subprocess.check_output, ['git', '-C', str(source), 'diff', 'HEAD', '--', 'src', 'main.py', 'pyproject.toml', 'uv.lock'])
                if dirty or hashlib.sha256((source / 'uv.lock').read_bytes()).hexdigest() != '71069a89a2eb84ded3cd72814de45f70e4fd02970bb0aeb532009006c4744b9d':
                    raise RuntimeError('MaiBot source or dependency lock has local modifications')
                directory = self.root / identity.replace(':', '_')
                directory.mkdir(parents=True, exist_ok=True)
                if os.environ.get('JM_BOT_MAIBOT_TERMS_ACCEPTED') != MAIBOT_COMMIT:
                    raise RuntimeError('operator must review upstream EULA/privacy and acknowledge the locked commit')
                (directory / 'ready').unlink(missing_ok=True)
                (directory / 'idle').unlink(missing_ok=True)
                for stale in (directory / 'control').glob('*'):
                    if stale.is_file():
                        stale.unlink()
                context_file = directory / 'jmbot-context.json'
                context_file.write_text(json.dumps(context, ensure_ascii=False), encoding='utf-8')
                if os.name != 'nt':
                    context_file.chmod(0o600)
                env = os.environ.copy()
                # Real keys stay in the coordinator, not the child environment.
                for key in list(env):
                    if any(word in key.upper() for word in ('API_KEY', 'TOKEN', 'PASSWORD', 'SECRET')):
                        env.pop(key)
                env.update(MAIBOT_INSTANCE_DIR=str(directory), MAIBOT_WORKER_PROCESS='1',
                           JM_BOT_NATIVE_IDENTITY=identity, JM_BOT_NATIVE_CONTEXT=str(context_file),
                           PYTHONPATH=str(Path(__file__).resolve().parents[1]))
                log = (directory / 'worker.log').open('ab')
                try:
                    process = await asyncio.create_subprocess_exec(
                        os.environ.get('JM_BOT_MAIBOT_PYTHON', sys.executable), '-m', 'jmbot.native_worker',
                        cwd=source, env=env, stdin=asyncio.subprocess.PIPE, stdout=log, stderr=log, limit=1024 * 1024, start_new_session=os.name != 'nt')
                finally:
                    log.close()
                entry = {'process': process, 'fingerprint': fingerprint, 'last_event': time.time(), 'started': time.time(), 'revision': context['revision'], 'token': context['token']}
                self.processes[identity] = entry
                # The bounded stdin pipe queues startup input. Readiness is
                # supervised independently so a cold scope cannot hold all peers.
            process = entry['process']
            frame = json.dumps(event, ensure_ascii=False).encode() + b'\n'
            if len(frame) > 1024 * 1024:
                raise ValueError('native event exceeds frame limit')
            process.stdin.write(frame)
            await asyncio.wait_for(process.stdin.drain(), 5)
            entry['last_event'] = time.time()

    async def stop(self, identity):
        async with self.lock:
            await self._stop(identity)

    async def _stop(self, identity):
        entry = self.processes.get(identity)
        if not entry:
            return
        entry['stopping'] = True
        process = entry['process']
        if process.returncode is None:
            if process.stdin:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                if os.name != 'nt':
                    import signal
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    if os.name != 'nt':
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    await process.wait()
        if os.name != 'nt':
            import signal
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.processes.pop(identity, None)

    async def close(self):
        self.waiting.clear()
        for identity in list(self.processes):
            await self.stop(identity)

    def snapshot(self):
        return {'limit': self.limit, 'waiting': {key: len(rows) for key, rows in self.waiting.items()}, 'instances': {key: {'pid': item['process'].pid,
                'running': item['process'].returncode is None, 'ready': (self.root / key.replace(':', '_') / 'ready').exists(), 'last_event': item['last_event']}
                for key, item in self.processes.items()}, 'errors': self.errors}
