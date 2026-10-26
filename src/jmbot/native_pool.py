"""One native runtime per account, carrying independent session grants."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

MAIBOT_COMMIT = 'c602a2780f4f4dda4a6be46d67abc1cfb1ad420c'


class NativePool:
    def __init__(self, root, *, limit=1):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit = 1
        self.processes = {}
        self.lock = asyncio.Lock()
        self.errors = {}
        self.scopes = {}
        self.context_templates = {}

    def runtime_identity(self, identity):
        return ':'.join(identity.split(':')[:2]) + ':runtime'

    def directory(self, identity):
        return self.root / self.runtime_identity(identity).replace(':', '_')

    def webui_login(self):
        """Read only the active WebUI login token, never the worker lease."""
        for identity, entry in self.processes.items():
            if entry['process'].returncode is not None:
                continue
            path = self.directory(identity) / 'data' / 'webui.json'
            try:
                config = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(config, dict):
                    continue
                token = config.get('access_token')
                source = config.get('token_source', 'temporary')
                # Temporary tokens rotate during WebUI startup. Do not offer
                # the previous process's token while the new one is starting.
                if source != 'configured' and path.stat().st_mtime < entry['started']:
                    continue
                if isinstance(token, str) and token.strip():
                    return {'token': token, 'source': source}
            except (OSError, ValueError):
                continue
        return {'token': None, 'source': None}

    def _prepare_plugins(self, directory: Path) -> None:
        """Copy repository plugins into the isolated MaiBot instance."""
        source = Path(__file__).resolve().parents[2] / 'plugins'
        if not source.is_dir():
            return
        target = directory / 'plugins'
        target.mkdir(parents=True, exist_ok=True)
        # Older releases copied a JMbot scope-policy plugin into each native
        # instance. It injected a second persona system message and would
        # survive a normal dirs_exist_ok copy even after the source plugin was
        # removed. Delete that retired plugin before loading native plugins.
        retired_scope_policy = target / 'jmbot-scope-policy'
        if retired_scope_policy.exists():
            shutil.rmtree(retired_scope_policy)
        for plugin in source.iterdir():
            if not plugin.is_dir() or plugin.name.startswith('.'):
                continue
            if plugin.name == 'jmbot-scope-policy':
                continue
            destination = target / plugin.name
            config = destination / 'config.toml'
            saved = config.read_bytes() if config.is_file() else None
            shutil.copytree(plugin, destination, dirs_exist_ok=True)
            if saved is not None:
                config.write_bytes(saved)
        adapter = target / 'maibot-team.snowluma-adapter'
        config = adapter / 'config.toml'
        if adapter.is_dir() and not config.exists():
            enabled = os.environ.get('JM_BOT_OFFICIAL_CONNECTOR_ENABLED', '').lower() in {'1', 'true', 'yes', 'on'}
            server = os.environ.get('JM_BOT_OFFICIAL_CONNECTOR_SERVER', '127.0.0.1')
            port = int(os.environ.get('JM_BOT_OFFICIAL_CONNECTOR_PORT', '3001'))
            token = os.environ.get('JM_BOT_OFFICIAL_CONNECTOR_TOKEN', '')
            ignored_prefixes = [
                value.strip()
                for value in os.environ.get('JM_BOT_OFFICIAL_CONNECTOR_IGNORE_PREFIXES', '/jm,/jmg').split(',')
                if value.strip()
            ]
            config.write_text(
                '[plugin]\n'
                f'enabled = {str(enabled).lower()}\n'
                'config_version = "1.1.8"\n\n'
                '[client]\n'
                'client_type = "napcat"\n'
                f'server = "{server}"\n'
                f'port = {port}\n'
                f'token = "{token}"\n\n'
                '[filters]\n'
                f'ignored_command_prefixes = {json.dumps(ignored_prefixes, ensure_ascii=False)}\n',
                encoding='utf-8')

    async def ensure_runtime(self, identity, context):
        """Start the normal runtime so WebUI is available before chat."""
        runtime = self.runtime_identity(identity)
        self.context_templates[runtime] = dict(context)
        async with self.lock:
            entry = self.processes.get(runtime)
            if entry and entry['process'].returncode is None:
                return entry
        # Starting the worker itself initializes MainSystem and WebUI.  Do not
        # enqueue a synthetic message: that would create a fake scope grant
        # and make the bootstrap observable as a chat session.
        await self.deliver(identity, None, context)
        return self.processes.get(runtime)

    async def deliver(self, identity, event, context):
        scope_identity = identity
        identity = self.runtime_identity(identity)
        async with self.lock:
            entry = self.processes.get(identity)
            # Content and scoped grants refresh without restarting other chats.
            fingerprint_context = {key: context[key] for key in ('account', 'gateway', 'write_barrier') if key in context}
            fingerprint = hashlib.sha256(json.dumps(fingerprint_context, sort_keys=True).encode()).hexdigest()
            if entry and (entry['process'].returncode is not None or entry['fingerprint'] != fingerprint):
                await self._stop(identity)
                entry = None
            if entry is None:
                previous = self.errors.get(identity)
                if previous and time.time() - previous['at'] < 30:
                    raise RuntimeError('native startup cooling down; inspect scoped worker log')
                if len(self.processes) >= self.limit:
                    raise RuntimeError('single-account runtime capacity reached')
                source = Path(os.environ['JM_BOT_MAIBOT_SOURCE']).resolve()
                import subprocess
                commit = await asyncio.to_thread(subprocess.check_output, ['git', '-C', str(source), 'rev-parse', 'HEAD'])
                if commit.decode().strip() != MAIBOT_COMMIT:
                    raise RuntimeError('MaiBot source differs from source lock')
                dirty = await asyncio.to_thread(subprocess.check_output, ['git', '-C', str(source), 'diff', 'HEAD', '--', 'src', 'main.py', 'pyproject.toml', 'uv.lock'])
                if dirty or hashlib.sha256((source / 'uv.lock').read_bytes()).hexdigest() != '71069a89a2eb84ded3cd72814de45f70e4fd02970bb0aeb532009006c4744b9d':
                    raise RuntimeError('MaiBot source or dependency lock has local modifications')
                directory = self.directory(identity)
                legacy_prefix = identity.split(':')[0] + '_' + identity.split(':')[1]
                if not directory.exists() and any(path.is_dir() for kind in ('group', 'private') for path in self.root.glob(legacy_prefix + '_' + kind + '_*')):
                    raise RuntimeError('legacy native data requires offline scope migration before starting')
                directory.mkdir(parents=True, exist_ok=True)
                self._prepare_plugins(directory)
                if os.environ.get('JM_BOT_MAIBOT_TERMS_ACCEPTED') != MAIBOT_COMMIT:
                    raise RuntimeError('operator must review upstream EULA/privacy and acknowledge the locked commit')
                (directory / 'ready').unlink(missing_ok=True)
                (directory / 'idle').unlink(missing_ok=True)
                for stale in (directory / 'control').glob('*'):
                    if stale.is_file():
                        stale.unlink()
                context_file = directory / 'jmbot-context.json'
                runtime_context = {**context, 'runtime_token': os.urandom(32).hex()}
                context_file.write_text(json.dumps(runtime_context, ensure_ascii=False), encoding='utf-8')
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
                entry = {'process': process, 'fingerprint': fingerprint, 'last_event': time.time(), 'started': time.time(),
                         'token': runtime_context['runtime_token']}
                self.processes[identity] = entry
                # The bounded stdin pipe queues startup input. Readiness is
                # supervised independently so a cold scope cannot hold all peers.
            if event is None:
                return
            process = entry['process']
            frame = json.dumps({'event': event, 'context': context}, ensure_ascii=False).encode() + b'\n'
            if len(frame) > 1024 * 1024:
                raise ValueError('native event exceeds frame limit')
            (self.root / identity.replace(':', '_') / 'idle').unlink(missing_ok=True)
            previous_scope = self.scopes.get(scope_identity)
            self.scopes[scope_identity] = {'context': context, 'last_event': time.time()}
            process.stdin.write(frame)
            try:
                await asyncio.wait_for(process.stdin.drain(), 5)
            except BaseException:
                if previous_scope is None:
                    self.scopes.pop(scope_identity, None)
                else:
                    self.scopes[scope_identity] = previous_scope
                raise
            entry['last_event'] = time.time()

    async def stop(self, identity):
        async with self.lock:
            await self._stop(identity)

    async def stop_if_idle(self, identity):
        """Refresh content only after rechecking idleness under the delivery lock."""
        async with self.lock:
            entry = self.processes.get(identity)
            if not entry or time.time() - entry['last_event'] <= 5:
                return False
            if not (self.root / identity.replace(':', '_') / 'idle').exists():
                return False
            await self._stop(identity)
            return True

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
        for identity in list(self.processes):
            await self.stop(identity)

    def snapshot(self):
        return {'limit': self.limit, 'sessions': {key: {'last_event': value['last_event']} for key, value in self.scopes.items()}, 'instances': {key: {'pid': item['process'].pid,
                'running': item['process'].returncode is None, 'ready': (self.root / key.replace(':', '_') / 'ready').exists(), 'last_event': item['last_event']}
                for key, item in self.processes.items()}, 'errors': self.errors}
