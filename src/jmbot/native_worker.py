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
    config.bot.qq_account = context['account']
    config.bot.platform = 'qq'
    config.bot.nickname = '大肥鱼'
    config.personality.personality = context['persona']
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
    config.webui.enabled = False
    config.debug.enable_console_input = False
    config.telemetry.enable = False
    config.a_memorix.global_memory_sharing_enabled = False
    config.a_memorix.plugin.enabled = True
    config.a_memorix.shared_memory_groups = []
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
    config.a_memorix.plugin.enabled = all(bindings.get(task, {}).get('enabled', False) for task in ('embedding', 'memory'))
    config.a_memorix.embedding.model_name = 'embedding'
    config.a_memorix.embedding.dimension = bindings.get('embedding', {}).get('dimension', 1024)
    config.a_memorix.image_memory.enabled = bool(bindings.get('image_embedding', {}).get('enabled', False))
    for task, binding in bindings.items():
        if not hasattr(tasks, task):
            continue
        providers.append(APIProvider(name=task, base_url=context['gateway'] + '/native/model/' + task,
                                     api_key=context['token'], client_type='openai', max_retry=1, timeout=90))
        models.append(ModelInfo(name=task, model_identifier=binding.get('model', 'unavailable'), api_provider=task, model_type=task if task in {'embedding', 'image_embedding'} else 'llm',
                                visual=task == 'vlm', extra_params={key: binding[key] for key in ('image_embedding_input', 'image_embedding_body') if key in binding}))
        setattr(tasks, task, TaskConfig(model_list=[task]))
    config_manager.global_config = config
    config_manager.model_config = ModelConfig(models=models, api_providers=providers, model_task_config=tasks)
    # Native memory and plugin children read these files during initialization.
    from src.config.config import write_config_to_file, BOT_CONFIG_PATH, MODEL_CONFIG_PATH, CONFIG_VERSION, MODEL_CONFIG_VERSION
    write_config_to_file(config, BOT_CONFIG_PATH, CONFIG_VERSION)
    write_config_to_file(config_manager.model_config, MODEL_CONFIG_PATH, MODEL_CONFIG_VERSION, True)
    async def no_watcher():
        return None
    config_manager.start_file_watcher = no_watcher
    from src.platform_io import (get_platform_io_manager, DriverDescriptor, DriverKind, RouteKey,
                                 RouteBinding, DeliveryReceipt, DeliveryStatus)
    from src.platform_io.drivers.base import PlatformIODriver
    import httpx
    client = httpx.AsyncClient(timeout=15, trust_env=False)
    headers = {'Authorization': 'Bearer ' + context['token']}
    async def authorized():
        result = await client.get(context['gateway'] + '/native/status', headers=headers)
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
    from sqlalchemy.orm import Session
    Session.commit = protect_write(Session.commit)

    class Driver(PlatformIODriver):
        async def send_message(self, message, route_key, metadata=None):
            segments = []
            visual_sent = False
            for component in message.raw_message.components:
                seg = await component.to_seg()
                if seg.type == 'text':
                    if '[cq:' in seg.data.lower():
                        raise ValueError('CQ output prohibited')
                    import re
                    from jmbot.core.chat_service import SUPER_FACES, _UNICODE_EMOJI
                    position = 0
                    for match in re.finditer(r'\[(sticker|qq_face):(\d+)\]', seg.data, re.I):
                        text = _UNICODE_EMOJI.sub('', seg.data[position:match.start()])
                        if text.strip():
                            segments.append({'type': 'text', 'data': {'text': text}})
                        if not visual_sent and match[1].lower() == 'qq_face' and int(match[2]) in SUPER_FACES:
                            segments.append({'type': 'face', 'data': {'id': int(match[2])}})
                            visual_sent = True
                        elif not visual_sent and match[1].lower() == 'sticker':
                            # Resolve only the explicit ID; missing IDs never select another image.
                            result = await client.post(context['gateway'] + '/native/sticker', headers=headers,
                                json={'id': int(match[2]), 'request_id': str(message.message_id) + '-sticker-' + str(match.start())})
                            if result.status_code not in {200, 404}:
                                result.raise_for_status()
                            visual_sent = result.status_code == 200
                        position = match.end()
                    text = _UNICODE_EMOJI.sub('', seg.data[position:])
                    if text.strip():
                        segments.append({'type': 'text', 'data': {'text': text}})
                elif seg.type in {'image', 'emoji'} and not visual_sent:
                    segments.append({'type': 'image', 'data': {'file': 'base64://' + seg.data}})
                    visual_sent = True
                elif seg.type == 'at':
                    target = seg.data if isinstance(seg.data, str) else seg.data.get('user_id', '')
                    segments.append({'type': 'at', 'data': {'qq': target}})
                elif seg.type == 'reply' and str(seg.data).isdigit():
                    segments.insert(0, {'type': 'reply', 'data': {'id': str(seg.data)}})
            if not segments:
                return DeliveryReceipt(message.message_id, route_key, DeliveryStatus.SENT, driver_id=self.driver_id)
            result = await client.post(context['gateway'] + '/native/send', headers=headers,
                                       json={'request_id': message.message_id, 'message': segments})
            result.raise_for_status()
            receipt = result.json()
            return DeliveryReceipt(message.message_id, route_key, DeliveryStatus.SENT, driver_id=self.driver_id,
                                   external_message_id=str(receipt.get('message_id', '')))

    manager = get_platform_io_manager()
    driver = Driver(DriverDescriptor('jmbot-scoped', DriverKind.LOCAL, 'qq', context['account'], context['scope']))
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
        return
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
        control = asyncio.create_task(serve_control(Path.cwd()))
        async def report_idle():
            from src.chat.heart_flow.heartflow_manager import heartflow_manager
            while True:
                chats = list(heartflow_manager.heartflow_chat_list.values())
                idle = all(chat._agent_state != 'running' and not chat._message_turn_scheduled and chat._internal_turn_queue.empty() and not chat._message_ingest_lock.locked() for chat in chats)
                marker = Path('idle')
                if idle:
                    marker.touch()
                else:
                    marker.unlink(missing_ok=True)
                await asyncio.sleep(1)
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
            await authorized()
            event = json.loads(line)
            Path('idle').unlink(missing_ok=True)
            catalog_response = await client.get(context['gateway'] + '/native/stickers', headers=headers)
            catalog_response.raise_for_status()
            from jmbot.core.chat_service import SUPER_FACE_RULE
            config.personality.personality = context['persona'] + '\n' + SUPER_FACE_RULE + '\n人工表情包只可按目录语义选择 [sticker:编号]，无匹配就不发。禁止 CQ 码。目录：' + json.dumps(catalog_response.json()['stickers'], ensure_ascii=False)
            route = classify(event)
            if route.domain != 'chat' or route.scope != context['scope'] or str(event['self_id']) != context['account']:
                continue
            sender = event.get('sender', {})
            message = SessionMessage(str(event.get('message_id', 'notice-' + str(event.get('time')))),
                                     datetime.fromtimestamp(event.get('time', datetime.now().timestamp())), 'qq')
            message.account_id, message.scope = context['account'], context['scope']
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
                            (sequence.emoji if str(data.get('sub_type')) in {'1', '2'} else sequence.image)(binary)
                    message.is_at = message.is_mentioned = route.direct
            message.is_notify = event.get('post_type') == 'notice'
            if message.is_notify:
                sequence.text('戳了戳你')
            message.raw_message = sequence
            await authorized()
            await chat_bot.receive_message(message)
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
