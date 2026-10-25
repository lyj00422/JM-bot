"""Run the locked upstream planner/trigger/memory pipeline, without a second gate."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path


async def run():
    source = Path.cwd().resolve()
    sys.path.insert(0, str(source))
    os.environ['PYTHONPATH'] = os.pathsep.join([str(source), str(Path(__file__).resolve().parents[1])])
    # Upstream still has relative log/cache paths. They must be instance-local.
    os.chdir(os.environ['MAIBOT_INSTANCE_DIR'])
    context = json.loads(Path(os.environ['JM_BOT_NATIVE_CONTEXT']).read_text(encoding='utf-8'))
    os.environ['MAIBOT_INITIALIZE_ONLY'] = '1'
    from src.config.config import Config, ModelConfig, config_manager, global_config
    from src.config.model_configs import APIProvider, ModelInfo, ModelTaskConfig, TaskConfig
    from src.config.official_configs import LearningItem
    config = Config()
    from jmbot.native_emoji import collection_limit
    config.emoji.max_reg_num = collection_limit()
    if 'JM_BOT_EMOJI_MAX_SIZE_MB' in os.environ:
        config.emoji.max_emoji_size_mb = float(os.environ['JM_BOT_EMOJI_MAX_SIZE_MB'])
    if 'JM_BOT_EMOJI_CHECK_INTERVAL_MINUTES' in os.environ:
        config.emoji.check_interval = int(os.environ['JM_BOT_EMOJI_CHECK_INTERVAL_MINUTES'])
    config.bot.qq_account = context['account']
    config.bot.platform = 'qq'
    config.bot.nickname = '大肥鱼'
    config.personality.personality = '会话外不具有角色、记忆或模型调用权限。'
    config.personality.behavior_style = os.environ.get('JM_BOT_BEHAVIOR_STYLE') or (
        '你在群里和私聊里都是个有聊天欲的普通群友：听懂了话题、也有想说的，就主动接一句，不用等别人 @ 你；'
        '局面冷下来时，可以自己起个话题、分享刚看到的事或问一句。每次只说一两句，别连续自说自话、别刷屏、别硬凑。'
        '有人认真求助或情绪低落时，先把话接住、认真回应。不编造没发生过的经历，不重复用过的梗。'
    )
    timing = config.chat.reply_timing
    timing.talk_value = float(os.environ.get('JM_BOT_TALK_VALUE', '1'))
    timing.private_talk_value = float(os.environ.get('JM_BOT_PRIVATE_TALK_VALUE', '1'))
    timing.message_debounce_mode = os.environ.get('JM_BOT_DEBOUNCE_MODE', 'fast')
    timing.no_action_backoff_base_seconds = float(os.environ.get('JM_BOT_NO_ACTION_BACKOFF_BASE', '8'))
    timing.no_action_backoff_cap_seconds = float(os.environ.get('JM_BOT_NO_ACTION_BACKOFF_CAP', '90'))
    timing.max_consecutive_wait_count = int(os.environ.get('JM_BOT_MAX_CONSECUTIVE_WAIT', '2'))
    config.maim_message.enabled = False
    # The migration plan requires the normal MaiBot management surface to be
    # available.  Keep it loopback-only; the desktop connector forwards it
    # over SSH alongside the JMbot console.
    config.webui.enabled = True
    config.webui.host = ['127.0.0.1']
    config.webui.port = int(os.environ.get('JM_BOT_NATIVE_WEBUI_PORT', '8001'))
    config.webui.mode = os.environ.get('JM_BOT_NATIVE_WEBUI_MODE', 'production')
    config.debug.enable_console_input = False
    config.telemetry.enable = False
    config.plugin_runtime.enabled = True
    config.a_memorix.global_memory_sharing_enabled = False
    config.experimental.focus_mode = False
    config.expression.use_vector_expression = False
    config.a_memorix.plugin.enabled = False
    config.a_memorix.shared_memory_groups = []
    config.chat.max_context_size = 24
    config.chat.max_private_context_size = 24
    config.emoji.emoji_send_num = 8
    config.emoji.use_new_send_logic = context.get('emoji_mode', 'selector') == 'planner'
    if config.emoji.use_new_send_logic and not context['tasks'].get('planner', {}).get('visual', False):
        raise ValueError('planner emoji mode requires a visual planner')
    kind, owner = context['scope'].split(':')
    for section in (config.expression, config.jargon):
        section.learning_list = [LearningItem(platform='qq', item_id=owner, type=kind, use=context['growth_enabled'], learn=False)]
        # Native automatic writes cannot bypass reviewed growth candidates.
        if hasattr(section, 'sharing_list'):
            section.sharing_list = []
    providers, models = [], []
    tasks = ModelTaskConfig()
    # Explicit empty/disabled tasks must not use upstream fallback to utils.
    # Bind every native task to its own gateway; disabled tasks return 409.
    from jmbot.model_routes import TASKS
    bindings = {task: context['tasks'].get(task, {'enabled': False, 'model': 'unavailable'}) for task in TASKS}
    config.a_memorix.plugin.enabled = False
    config.a_memorix.embedding.model_name = 'embedding'
    config.a_memorix.embedding.dimension = bindings.get('embedding', {}).get('dimension', 1024)
    config.a_memorix.image_memory.enabled = bool(bindings.get('image_embedding', {}).get('enabled', False))
    for task, binding in bindings.items():
        if not hasattr(tasks, task):
            continue
        providers.append(APIProvider(name=task, base_url=context['gateway'] + '/native/model/' + task,
                                     api_key='unscoped-denied', client_type='openai', max_retry=1, timeout=90))
        models.append(ModelInfo(name=task, model_identifier=binding.get('model', 'unavailable'), api_provider=task, model_type=task if task in {'embedding', 'image_embedding'} else 'llm',
                                visual=task == 'vlm', extra_params={key: binding[key] for key in ('image_embedding_input', 'image_embedding_body') if key in binding}))
        setattr(tasks, task, TaskConfig(model_list=[task] if binding.get('enabled', False) else []))
    config_manager.global_config = config
    config_manager.model_config = ModelConfig(models=models, api_providers=providers, model_task_config=tasks)
    # Native memory and plugin children read these files during initialization.
    from src.config.config import write_config_to_file, BOT_CONFIG_PATH, MODEL_CONFIG_PATH, CONFIG_VERSION, MODEL_CONFIG_VERSION
    write_config_to_file(config, BOT_CONFIG_PATH, CONFIG_VERSION)
    write_config_to_file(config_manager.model_config, MODEL_CONFIG_PATH, MODEL_CONFIG_VERSION, True)
    # Keep MaiBot's normal configuration/plugin watcher.  The previous
    # wrapper replaced this with a no-op, which made the native WebUI appear
    # to work while every saved setting was silently ignored.
    from src.platform_io import (get_platform_io_manager, DriverDescriptor, DriverKind, RouteKey,
                                 RouteBinding, DeliveryReceipt, DeliveryStatus)
    from src.platform_io.drivers.base import PlatformIODriver
    import httpx
    client = httpx.AsyncClient(timeout=15, trust_env=False)
    from jmbot.native_sessions import NativeSessions, current_scope
    sessions = NativeSessions(config, config_manager.model_config, context)
    sessions.update(context)
    sessions.install()
    from jmbot.native_model_work import install_headers
    install_headers()
    from jmbot.native_emoji import install as install_emoji, validate_image
    install_emoji(sessions)
    class ScopedHeaders(dict):
        def items(self):
            return {'Authorization': 'Bearer ' + sessions.context()['token']}.items()
    headers = ScopedHeaders()
    async def refresh_scope(scope):
        with sessions.bind(scope):
            result = await client.get(context['gateway'] + '/native/context', headers=headers)
            result.raise_for_status()
            fresh = result.json()
            if fresh.get('scope') != scope:
                raise PermissionError('context scope mismatch')
            if fresh != sessions.context(scope):
                sessions.update(fresh)
    sessions.refresh = refresh_scope
    async def authorized():
        result = await client.get(context['gateway'] + '/native/status', headers=headers)
        result.raise_for_status()
    async def runtime_authorized():
        result = await client.get(context['gateway'] + '/native/runtime-status',
                                  headers={'Authorization': 'Bearer ' + context['runtime_token']})
        result.raise_for_status()

    # Native memory writes run in background threads as well as the main loop.
    # Guard the actual SQLite commit, not only the request that started it.
    from src.A_memorix.core.storage.sqlite_connection import ManagedSQLiteConnection
    original_commit = ManagedSQLiteConnection.force_commit
    from contextlib import nullcontext
    from jmbot.core.write_barrier import WriteBarrier
    barrier = WriteBarrier(context['write_barrier']) if context.get('write_barrier') else None
    def check_commit_permission():
        with httpx.Client(timeout=3, trust_env=False) as guard:
            if current_scope.get() is None:
                # Host startup/maintenance has no conversation write authority.
                # Only the dedicated runtime status endpoint accepts this token.
                result = guard.get(context['gateway'] + '/native/runtime-status', headers={'Authorization': 'Bearer ' + context['runtime_token']})
            else:
                result = guard.get(context['gateway'] + '/native/status', headers=headers)
            result.raise_for_status()
    def guarded_commit(connection):
        with barrier.hold() if barrier else nullcontext():
            try:
                check_commit_permission()
            except Exception:
                connection.force_rollback()
                raise RuntimeError('memory commit blocked by unavailable or revoked policy') from None
            return original_commit(connection)
    ManagedSQLiteConnection.force_commit = guarded_commit
    def guarded_regular_commit(connection):
        if connection._managed_transaction_depth == 0:
            return guarded_commit(connection)
    ManagedSQLiteConnection.commit = guarded_regular_commit
    def guarded_exit(connection, exc_type, exc, traceback):
        if exc_type is None:
            guarded_regular_commit(connection)
        else:
            connection.force_rollback()
        return False
    ManagedSQLiteConnection.__exit__ = guarded_exit
    from src.A_memorix.core.storage.vector_store import VectorStore
    from src.A_memorix.core.storage.graph_store import GraphStore
    def protect_write(original):
        def guarded(*args, **kwargs):
            # Native cleanup checkpoints already hold the vector RLock across
            # calls. Always take that lock before the policy barrier as well,
            # avoiding inversion against flushes invoked under the native lock.
            vector_lock = args[0]._lock if args and isinstance(args[0], VectorStore) else nullcontext()
            with vector_lock:
                with barrier.hold() if barrier else nullcontext():
                    if barrier is None or barrier.depth == 1:
                        check_commit_permission()
                    return original(*args, **kwargs)
        return guarded
    for storage, names in ((VectorStore, ('add', 'delete', 'save', '_flush_write_buffer', '_flush_write_buffer_unlocked', 'commit_cleanup_checkpoint')), (GraphStore, ('save',))):
        for name in names:
            setattr(storage, name, protect_write(getattr(storage, name)))
    # Native profiles, chat history and expression records use SQLAlchemy.
    # Keep their transaction boundary under the same revocation barrier.
    from sqlalchemy import event
    from src.common.database.database import engine
    @event.listens_for(engine, 'connect')
    def increase_sqlite_busy_timeout(dbapi_connection, connection_record):
        # MaiBot defaults to 1s. Emoji VLM requests and message persistence can
        # overlap; waiting avoids dropping a valid write during short WAL locks.
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA busy_timeout=30000')
        cursor.execute('PRAGMA cache_size=-4096')
        cursor.close()

    from sqlalchemy.orm import Session
    from jmbot.native_write_guard import guard_session_writes
    guard_session_writes(Session, barrier, check_commit_permission)
    Session.commit = protect_write(Session.commit)

    class Driver(PlatformIODriver):
        async def send_message(self, message, route_key, metadata=None):
            scope = route_key.scope
            with sessions.bind(scope):
                return await self.send_scoped(message, route_key, metadata)
        async def send_scoped(self, message, route_key, metadata=None):
            segments = []
            visual_sent = False
            for component in message.raw_message.components:
                seg = await component.to_seg()
                if seg.type == 'text':
                    if '[cq:' in seg.data.lower():
                        raise ValueError('CQ output prohibited')
                    if seg.data.strip():
                        segments.append({'type': 'text', 'data': {'text': seg.data}})
                elif seg.type in {'image', 'emoji'} and not visual_sent:
                    from jmbot.qq_stickers import sticker_segment_base64
                    segments.append(sticker_segment_base64(seg.data) if seg.type == 'emoji' else
                                    {'type': 'image', 'data': {'file': 'base64://' + seg.data}})
                    visual_sent = True
                elif seg.type == 'at':
                    target = seg.data if isinstance(seg.data, str) else seg.data.get('user_id', '')
                    segments.append({'type': 'at', 'data': {'qq': target}})
                elif seg.type == 'reply' and str(seg.data).isdigit():
                    segments.insert(0, {'type': 'reply', 'data': {'id': str(seg.data)}})
            if not segments:
                return DeliveryReceipt(message.message_id, route_key, DeliveryStatus.SENT, driver_id=self.driver_id)
            from jmbot.qq_stickers import send_native_message
            receipt = await send_native_message(client, context['gateway'] + '/native/send', headers=headers,
                                                 message_id=message.message_id, segments=segments)
            return DeliveryReceipt(message.message_id, route_key, DeliveryStatus.SENT, driver_id=self.driver_id,
                                   external_message_id=str(receipt.get('message_id', '')))

    manager = get_platform_io_manager()
    driver = Driver(DriverDescriptor('jmbot-scoped', DriverKind.LOCAL, 'qq', context['account'], None))
    manager.register_driver(driver)
    manager.bind_send_route(RouteBinding(RouteKey('qq', context['account'], context['scope']), driver.driver_id, DriverKind.LOCAL))
    from src.common.runtime_loop import set_main_loop
    set_main_loop(asyncio.get_running_loop())
    from src.main import main as native_main
    from src.chat.message_receive.bot import chat_bot
    from src.chat.message_receive.message import SessionMessage
    from src.common.data_models.mai_message_data_model import UserInfo, GroupInfo, MessageInfo
    from src.common.data_models.message_component_data_model import MessageSequence
    from jmbot.core.ingress import classify, text_of
    if os.environ.get('JM_BOT_NATIVE_CHECK_CONFIG') == '1':
        await client.aclose()
        print('Native configuration and runtime imports OK')
        return sessions
    from src.main import MainSystem
    ready = asyncio.Event()
    initialize = MainSystem.initialize
    async def initialize_ready(self):
        await initialize(self)
        Path('ready').write_text('ready', encoding='utf-8')
        ready.set()
    MainSystem.initialize = initialize_ready
    native = asyncio.create_task(native_main())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if os.name != 'nt':
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    try:
        waiter = asyncio.create_task(ready.wait())
        done, _ = await asyncio.wait({native, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if native in done:
            await native
            return
        if os.environ.get('JM_BOT_NATIVE_CHECK_STARTUP') == '1':
            print('Native initialization OK', flush=True)
            return
        from jmbot.native_control import serve_control
        control = asyncio.create_task(serve_control(Path.cwd(), sessions))
        async def report_idle():
            from src.chat.heart_flow.heartflow_manager import heartflow_manager
            while True:
                for session_id, chat in list(heartflow_manager.heartflow_chat_list.items()):
                    scope = sessions.scope_for_session(session_id)
                    last_active = heartflow_manager._chat_last_active_at.get(session_id, 0)
                    if sessions.idle(chat) and datetime.now().timestamp() - last_active > max(60, int(os.environ.get('JM_BOT_NATIVE_SESSION_IDLE_SECONDS', '300'))):
                        await heartflow_manager._evict_chat(session_id, reason='host_idle_cache')
                        continue
                    try:
                        await refresh_scope(scope)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 403:
                            await heartflow_manager._evict_chat(session_id, reason='adapter_policy_block')
                    except httpx.HTTPError:
                        pass
                chats = list(heartflow_manager.heartflow_chat_list.values())
                idle = all(chat._agent_state != 'running' and not chat._message_turn_scheduled and chat._internal_turn_queue.empty() and not chat._message_ingest_lock.locked() for chat in chats)
                marker = Path('idle')
                if idle:
                    marker.touch()
                else:
                    marker.unlink(missing_ok=True)
                await asyncio.sleep(5)
        idle_task = asyncio.create_task(report_idle())
        while not stop.is_set():
            line_task = asyncio.create_task(asyncio.to_thread(sys.stdin.buffer.readline, 1024 * 1024))
            stop_task = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait({line_task, stop_task, native}, return_when=asyncio.FIRST_COMPLETED)
            if line_task not in done:
                line_task.cancel()
                break
            stop_task.cancel()
            line = line_task.result()
            if not line:
                break
            scope_token = None
            try:
                frame = json.loads(line)
                event, incoming_context = frame['event'], frame['context']
                scope = incoming_context['scope']
                sessions.update(incoming_context)
                scope_token = current_scope.set(scope)
                Path('idle').unlink(missing_ok=True)
                route = classify(event)
                # Bootstrap/lifecycle frames are used only to bring up the
                # normal MaiBot runtime and WebUI before the first chat. They
                # have no scope grant and must never enter the chat pipeline.
                if route.domain == 'chat':
                    await authorized()
                else:
                    await runtime_authorized()
                if route.domain != 'chat' or route.scope != scope or str(event['self_id']) != context['account']:
                    continue
                kind, owner = scope.split(':')
                manager.bind_send_route(RouteBinding(RouteKey('qq', context['account'], scope), driver.driver_id, DriverKind.LOCAL))
                sender = event.get('sender', {})
                message = SessionMessage(str(event.get('message_id', 'notice-' + str(event.get('time')))),
                                         datetime.fromtimestamp(event.get('time', datetime.now().timestamp())), 'qq')
                message.account_id, message.scope = context['account'], scope
                message.message_info = MessageInfo(UserInfo(str(event['user_id']), sender.get('nickname', str(event['user_id'])), sender.get('card')),
                                                    GroupInfo(owner, event.get('group_name', '')) if kind == 'group' else None)
                sequence = MessageSequence([])
                segments = event.get('message', [])
                if isinstance(segments, str):
                    sequence.text(text_of(event))
                else:
                    for segment in segments:
                        data = segment.get('data', {})
                        if segment['type'] == 'text':
                            sequence.text(str(data.get('text', '')))
                        elif segment['type'] == 'at':
                            sequence.at(str(data.get('qq', '')))
                        elif segment['type'] == 'reply':
                            # Scope-local native lookup only; never retrieve arbitrary OneBot history.
                            sequence.reply(str(data.get('id', '')))
                        elif segment['type'] == 'image':
                            result = await client.post(context['gateway'] + '/native/media', headers=headers, json={'file': data.get('file', '')})
                            if result.status_code == 200:
                                import base64
                                binary = base64.b64decode(result.json()['base64'])
                                validate_image(binary)
                                (sequence.emoji if str(data.get('sub_type')) in {'1', '2'} else sequence.image)(binary)
                        message.is_at = message.is_mentioned = route.direct
                message.is_notify = event.get('post_type') == 'notice'
                if message.is_notify:
                    sequence.text('戳了戳你')
                message.raw_message = sequence
                await authorized()
                await chat_bot.receive_message(message)
            except Exception as exc:
                print("Native ingress rejected: " + type(exc).__name__, file=sys.stderr, flush=True)
            finally:
                if scope_token is not None:
                    current_scope.reset(scope_token)
    finally:
        if 'idle_task' in locals():
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)
        if 'control' in locals():
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)
        Path('ready').unlink(missing_ok=True)
        Path('idle').unlink(missing_ok=True)
        native.cancel()
        await asyncio.gather(native, return_exceptions=True)
        await client.aclose()


if __name__ == '__main__':
    asyncio.run(run())
