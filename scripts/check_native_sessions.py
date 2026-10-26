"""Local locked-runtime smoke check. No provider or QQ requests are made."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if self.path.endswith('/embeddings'):
            inputs = body.get('input', ['test'])
            if isinstance(inputs, str):
                inputs = [inputs]
            value = {'object': 'list', 'data': [{'object': 'embedding', 'index': i, 'embedding': [0.1] * 8} for i in range(len(inputs))], 'model': 'local', 'usage': {'prompt_tokens': 1, 'total_tokens': 1}}
        else:
            value = {'id': 'local', 'object': 'chat.completion', 'model': 'local', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '[]'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
        self.wfile.write(json.dumps(value).encode())

server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
threading.Thread(target=server.serve_forever, daemon=True).start()

project = Path(__file__).resolve().parents[1]
source = project / '.git/upstream/maibot'
root = Path(tempfile.mkdtemp(prefix='native-check-', dir=project / '.git'))
context = {'account': '123456', 'scope': 'group:654321',
           'gateway': 'http://127.0.0.1:' + str(server.server_port),
           'token': 'local-test-only', 'runtime_token': 'local-runtime-only',
           'tasks': {task: {'model': 'local', 'enabled': True, 'visual': task == 'vlm'}
                     for task in ('planner', 'replyer', 'utils', 'vlm')}}
path = root / 'context.json'
path.write_text(json.dumps(context), encoding='utf-8')
os.environ.update(MAIBOT_INSTANCE_DIR=str(root), JM_BOT_NATIVE_CONTEXT=str(path), JM_BOT_NATIVE_CHECK_CONFIG='1')
if '--startup' in sys.argv:
    os.environ.pop('JM_BOT_NATIVE_CHECK_CONFIG')
    os.environ['JM_BOT_NATIVE_CHECK_STARTUP'] = '1'
sys.path[:0] = [str(project / 'src'), str(source)]
os.chdir(source)

async def check():
    from jmbot.native_worker import run
    sessions = await run()
    if sessions is None:
        return
    from jmbot.native_sessions import NativeSessions
    from src.config.config import config_manager, global_config, model_config
    peer = {**context, 'scope': 'group:999', 'token': 'peer-token'}
    sessions.update(peer)
    async def read(scope):
        with sessions.bind(scope):
            await asyncio.sleep(0)
            return global_config.personality.personality, model_config.api_providers[0].api_key
    native_personality, first_key = await read(context['scope'])
    peer_personality, peer_key = await read(peer['scope'])
    assert native_personality == peer_personality
    assert (first_key, peer_key) == ('local-test-only', 'peer-token')
    assert 'native_prompt_seed' not in context
    assert sessions.configs[context['scope']].a_memorix.storage.data_dir != sessions.configs[peer['scope']].a_memorix.storage.data_dir
    from src.common.database.database import initialize_database, get_db_session
    from src.chat.message_receive.chat_manager import chat_manager
    from src.person_info.person_info import Person
    initialize_database()
    ids = []
    for item in (context, peer):
        with sessions.bind(item['scope']):
            session = await chat_manager.get_or_create_session('qq', '42', item['scope'].split(':')[1], account_id=context['account'], scope=item['scope'])
            assert sessions.scope_for_session(session.session_id) == item['scope']
            ids.append(session.session_id)
            with get_db_session() as db:
                db.connection().exec_driver_sql('CREATE TABLE isolation_test(value TEXT)')
                db.connection().exec_driver_sql('INSERT INTO isolation_test VALUES(?)', (item['scope'],))
    assert ids[0] != ids[1]
    for item in (context, peer):
        with sessions.bind(item['scope']):
            with get_db_session() as db:
                assert db.connection().exec_driver_sql('SELECT value FROM isolation_test').scalars().all() == [item['scope']]
    from src.emoji_system.emoji_manager import emoji_manager
    from src.prompt.prompt_manager import prompt_manager
    prompt_manager.load_prompts()
    from src.maisaka.builtin_tool import send_emoji, show_emoji_list
    from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
    from src.core.tooling import ToolInvocation
    from src.common.data_models.image_data_model import MaiEmoji
    from jmbot.core.chat_stickers import sticker_path
    with sessions.bind(context['scope']):
        emoji_manager.load_emojis_from_db()
        assert len(emoji_manager.emojis) == 88
        selected = emoji_manager.emojis[0]
        runtime = SimpleNamespace(run_sub_agent=AsyncMock(return_value=SimpleNamespace(content='{"emoji_index":1}')))
        with patch.object(send_emoji, 'sample', lambda values, size: values[:size], create=True):
            choice, _ = await send_emoji._select_emoji_with_sub_agent(SimpleNamespace(runtime=runtime), '', [], 8)
        assert choice in emoji_manager.emojis
        assert runtime.run_sub_agent.await_args.kwargs['model_task_name'] == 'vlm'
        for raw in ('{"emoji_index":0}', '{"emoji_index":999}', '{"emoji_index":true}', 'not json'):
            runtime.run_sub_agent.return_value.content = raw
            assert (await send_emoji._select_emoji_with_sub_agent(SimpleNamespace(runtime=runtime), '', [], 8))[0] is None
        emoji_manager.emojis = [selected]
        planner_context = {**context, 'emoji_mode': 'planner', 'tasks': {**context['tasks'], 'planner': {'model': 'local', 'enabled': True, 'visual': True, 'tools': True}}}
        sessions.update(planner_context)
        fake_ctx = SimpleNamespace(build_success_result=lambda *args, **kwargs: kwargs, build_failure_result=lambda *args: (_ for _ in ()).throw(AssertionError(args)))
        result = await show_emoji_list.handle_tool(fake_ctx, SimpleNamespace(tool_name='show_emoji_list'))
        candidate = result['post_history_messages'][0]
        index = next(iter(candidate.emoji_hashes))
        attachment = await BuiltinToolRuntimeContext._resolve_emoji_attachment(SimpleNamespace(runtime=SimpleNamespace(_chat_history=[candidate])), index)
        assert attachment.binary_data == selected.full_path.read_bytes()
        with sessions.bind(peer['scope']):
            assert not emoji_manager.emojis
            with patch.object(emoji_manager, 'update_emoji_usage'):
                try:
                    await BuiltinToolRuntimeContext._resolve_emoji_attachment(SimpleNamespace(runtime=SimpleNamespace(_chat_history=[])), index)
                except ValueError:
                    pass
                else:
                    raise AssertionError('foreign emoji index accepted')
    for item in (context, peer):
        memory_context = {**item, 'tasks': {**item['tasks'], 'embedding': {'model': 'local', 'enabled': True, 'dimension': 8}, 'memory': {'model': 'local', 'enabled': True}}}
        sessions.update(memory_context)
        async with sessions.memory.lease(item['scope']) as host:
            assert host is not None
            kernel = await host._ensure_kernel()
            assert kernel.metadata_store is not None
            from src.services.embedding_service import EmbeddingServiceClient
            embedding = await EmbeddingServiceClient().embed_text('local dimension probe')
            assert len(embedding.embedding) == 8
            import numpy as np
            vector = np.asarray([embedding.embedding], dtype=np.float32)
            assert kernel.vector_store.search(vector[0], k=1)[0] == []
            kernel.vector_store.add(vector, ['scope-only'])
            assert kernel.vector_store.search(vector[0], k=1)[0] == ['scope-only']
            assert host.get_runtime_data_dir() == Path(sessions.configs[item['scope']].a_memorix.storage.data_dir)
            from src.A_memorix.runtime_registry import get_runtime_kernel
            with sessions.bind(peer['scope'] if item['scope'] == context['scope'] else context['scope']):
                assert get_runtime_kernel() is None
    await sessions.memory.stop()
    # Eviction must release per-scope resources and remain safe when an idle
    # sweep races another eviction; re-admission must preserve persisted data.
    from src.chat.heart_flow.heartflow_manager import heartflow_manager
    await asyncio.gather(*(heartflow_manager._evict_chat(ids[0], reason='host_idle_cache') for _ in range(2)))
    assert context['scope'] not in sessions.contexts
    assert context['scope'] not in sessions.database_factories
    sessions.update(context)
    with sessions.bind(context['scope']):
        with get_db_session() as db:
            assert db.connection().exec_driver_sql('SELECT value FROM isolation_test').scalars().all() == [context['scope']]
    print('NATIVE_SESSION_CHECK_OK: scope configs/DB, both emoji tools, actual vector kernel switch')

try:
    asyncio.run(check())
finally:
    server.shutdown()
