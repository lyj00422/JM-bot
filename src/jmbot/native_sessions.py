"""Session-scoped host integration for the pinned MaiBot runtime.

The stable native config proxies support a context-local getter. We bind at
native session lifecycle boundaries, never by mutating a shared persona/key.
Context propagates into asyncio tasks and asyncio.to_thread; native APIs that
carry a session ID explicitly rebind it before starting background work.
"""
from contextlib import contextmanager, asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
import asyncio
import copy
import functools
import json
import os
import threading


current_scope = ContextVar('jmbot_native_scope', default=None)
current_session_id = ContextVar('jmbot_native_session_id', default='')


class NativeSessions:
    def __init__(self, base_config, base_models, runtime):
        self.base_config, self.base_models, self.runtime = base_config, base_models, runtime
        self.contexts, self.configs, self.models = {}, {}, {}
        self.session_scopes = {}
        self.automation = {}
        self.emoji_caches = []
        # The official plugin runtime runs in a child process and receives
        # only serialized hook payloads. Keep the authoritative native
        # session -> business scope mapping in a small atomic file so the
        # coordinator can answer policy/persona lookups without guessing a
        # hash or exposing another session database.
        self.session_map_path = Path(os.environ.get('MAIBOT_INSTANCE_DIR', Path.cwd())) / 'scope-sessions.json'
        self._session_map_lock = threading.RLock()

    def _persist_session_scope(self, session_id, scope):
        if not session_id or scope not in self.contexts:
            return
        with self._session_map_lock:
            mapping = {}
            try:
                mapping = json.loads(self.session_map_path.read_text(encoding='utf-8'))
            except (FileNotFoundError, OSError, ValueError, TypeError):
                pass
            if not isinstance(mapping, dict):
                mapping = {}
            mapping[str(session_id)] = scope
            temporary = self.session_map_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self.session_map_path)

    def update(self, context):
        from src.config.official_configs import LearningItem
        from src.config.model_configs import APIProvider, ModelInfo, ModelTaskConfig, TaskConfig
        from src.config.config import ModelConfig
        scope = context['scope']
        config = copy.deepcopy(self.base_config)
        # The upstream default Focus scope may merge all chats into one planner.
        # Independent native session loops are required for group isolation.
        config.experimental.focus_mode = False
        config.capabilities.planner_subagent = True
        config.capabilities.advanced_sub_agent = True
        config.expression.use_vector_expression = False
        config.emoji.use_new_send_logic = context.get('emoji_mode', 'selector') == 'planner'
        if config.emoji.use_new_send_logic and not all(context['tasks'].get('planner', {}).get(flag, False) for flag in ('visual', 'tools')):
            raise ValueError('planner emoji mode requires visual and tools capabilities')
        kind, owner = scope.split(':')
        for section in (config.expression, config.jargon):
            section.learning_list = [LearningItem(platform='qq', item_id=owner, type=kind, use=context['growth_enabled'], learn=context['growth_enabled'])]
        config.expression.expression_groups = []
        config.jargon.jargon_groups = []
        # Native expression SQL retrieval is session-scoped. Its singleton
        # vector index is deliberately unused; A_Memorix owns scoped vectors.
        config.a_memorix.plugin.enabled = all(context['tasks'].get(name, {}).get('enabled', False) for name in ('embedding', 'memory'))
        config.a_memorix.storage.data_dir = str((Path.cwd() / 'sessions' / scope.replace(':', '_') / 'data').resolve())
        config.a_memorix.embedding.dimension = context['tasks'].get('embedding', {}).get('dimension', 1024)
        config.a_memorix.image_memory.enabled = config.a_memorix.plugin.enabled and bool(context['tasks'].get('image_embedding', {}).get('enabled'))
        tasks, providers, models = ModelTaskConfig(), [], []
        from jmbot.model_routes import TASKS
        for task in TASKS:
            binding = context['tasks'].get(task, {'enabled': False, 'model': 'unavailable'})
            if not hasattr(tasks, task):
                continue
            # Route disabled tasks to a rejecting gateway, avoiding utils fallback.
            setattr(tasks, task, TaskConfig(model_list=[task]))
            # Persisted vector fingerprints must survive ephemeral grant rotation.
            # A real provider route change still creates a distinct model space.
            provider_name = scope + ':' + task + ':' + context.get('route_identity', 'local')
            providers.append(APIProvider(name=provider_name, base_url=context['gateway'] + '/native/model/' + task, api_key=context['token'], client_type='openai', max_retry=1, timeout=180))
            models.append(ModelInfo(name=task, model_identifier=binding.get('model', 'unavailable'), api_provider=provider_name, model_type=task if task in {'embedding', 'image_embedding'} else 'llm', visual=binding.get('visual', task == 'vlm'), extra_params={key: binding[key] for key in ('image_embedding_input', 'image_embedding_body') if key in binding}))
        old = self.contexts.get(scope)
        if old is not None and old.get('token') != context['token']:
            from src.llm_models.model_client.base_client import client_registry
            for key in list(client_registry.client_instance_cache):
                if key[1].startswith(scope + ':'):
                    client_registry.client_instance_cache.pop(key, None)
        self.contexts[scope], self.configs[scope] = context, config
        self.models[scope] = ModelConfig(models=models, api_providers=providers, model_task_config=tasks)

    def scope_for_session(self, session_id):
        from src.chat.message_receive.chat_manager import chat_manager
        session = chat_manager.sessions.get(session_id)
        mapped = self.session_scopes.get(session_id)
        if mapped in self.contexts:
            self._persist_session_scope(session_id, mapped)
            return mapped
        if session is None or session.scope not in self.contexts:
            raise PermissionError('native session has no authorized scope')
        self.session_scopes[session_id] = session.scope
        self._persist_session_scope(session_id, session.scope)
        return session.scope

    @contextmanager
    def bind(self, scope, session_id=None):
        if scope not in self.contexts:
            raise PermissionError('unregistered native scope')
        scope_token = current_scope.set(scope)
        session_token = current_session_id.set(
            str(session_id) if session_id is not None else current_session_id.get()
        )
        try:
            yield
        finally:
            current_session_id.reset(session_token)
            current_scope.reset(scope_token)

    def context(self, scope=None):
        scope = scope or current_scope.get()
        if scope not in self.contexts:
            raise PermissionError('missing native session context')
        return self.contexts[scope]

    def runtime_kernel_for_webui(self, get_kernel):
        # WebUI requests have no chat ContextVar. Expose only the currently
        # leased, registered scope's kernel; never fall back to another scope.
        if self.memory.scope not in self.contexts or self.memory.host is None:
            return None
        return get_kernel()

    def install(self):
        from src.config.config import global_config, model_config, config_manager
        # _ConfigProxy retains this getter, including in already imported modules.
        object.__setattr__(global_config, '_getter', lambda: self.configs.get(current_scope.get(), self.base_config))
        object.__setattr__(model_config, '_getter', lambda: self.models.get(current_scope.get(), self.base_models))
        config_manager.get_global_config = lambda: self.configs.get(current_scope.get(), self.base_config)
        config_manager.get_model_config = lambda: self.models.get(current_scope.get(), self.base_models)
        from jmbot.native_storage import install as install_storage
        install_storage(self)
        self.install_memory()
        from src.chat.message_receive.chat_manager import chat_manager
        original_register_message = chat_manager.register_message
        def register_message(message):
            result = original_register_message(message)
            scope = getattr(message, 'scope', None)
            if not scope or scope not in self.contexts:
                from src.platform_io.route_key_factory import RouteKeyFactory
                scope = RouteKeyFactory.from_session_message(message).scope
            if scope not in self.contexts:
                raise PermissionError('message registered outside native scope')
            self.session_scopes[message.session_id] = scope
            self._persist_session_scope(message.session_id, scope)
            return result
        chat_manager.register_message = register_message
        from src.maisaka.runtime import MaisakaHeartFlowChatting
        from src.maisaka.reasoning_engine import MaisakaReasoningEngine
        def wrap_method(owner, name, session):
            original = getattr(owner, name)
            @functools.wraps(original)
            async def scoped(instance, *args, **kwargs):
                scope = self.scope_for_session(session(instance))
                with self.bind(scope, session_id=session(instance)):
                    return await original(instance, *args, **kwargs)
            setattr(owner, name, scoped)
        for name in ('start', 'stop', 'register_message', 'run_sub_agent', '_recognize_sent_images'):
            wrap_method(MaisakaHeartFlowChatting, name, lambda instance: instance.session_id)
        wrap_method(MaisakaReasoningEngine, 'run_loop', lambda instance: instance._runtime.session_id)
        refresh = MaisakaReasoningEngine._start_cycle_round
        @functools.wraps(refresh)
        async def start_round(engine, *args, **kwargs):
            scope = self.scope_for_session(engine._runtime.session_id)
            with self.bind(scope, session_id=engine._runtime.session_id):
                if hasattr(self, 'refresh'):
                    await self.refresh(scope)
                return await refresh(engine, *args, **kwargs)
        MaisakaReasoningEngine._start_cycle_round = start_round
        from src.llm_models.utils_model import LLMOrchestrator
        original_execute = LLMOrchestrator._execute_request
        @functools.wraps(original_execute)
        async def execute(orchestrator, *args, **kwargs):
            session_id = kwargs.get('session_id') or getattr(orchestrator, 'session_id', '')
            scope = self.scope_for_session(session_id) if session_id else current_scope.get()
            with self.bind(scope, session_id=session_id):
                return await original_execute(orchestrator, *args, **kwargs)
        LLMOrchestrator._execute_request = execute
        self.install_background_scopes()
        self.install_session_budget()
        from src.maisaka import subagent_tasks
        subagent_tasks.SUBAGENT_MAX_RUNNING_TASKS = 1

    @staticmethod
    def idle(chat):
        return (chat._agent_state != 'running' and not chat._message_turn_scheduled
                and chat._internal_turn_queue.empty() and not chat._message_ingest_lock.locked()
                and not any(task.status == 'running' for task in chat._subagent_tasks.list_tasks()))

    def install_session_budget(self):
        from src.chat.heart_flow.heartflow_manager import heartflow_manager
        original = heartflow_manager.get_or_create_heartflow_chat
        admission = asyncio.Lock()
        limit = max(1, min(32, int(os.environ.get('JM_BOT_NATIVE_SESSION_LIMIT', '8'))))
        evict = heartflow_manager._evict_chat
        release_lock = asyncio.Lock()
        async def release_scoped(session_id, *, reason):
            from src.chat.message_receive.chat_manager import chat_manager
            if session_id not in chat_manager.sessions:
                return
            scope = self.scope_for_session(session_id)
            await evict(session_id, reason=reason)
            service = self.automation.pop(scope, None)
            if service is not None:
                with self.bind(scope):
                    await service.shutdown()
            for cache in self.emoji_caches:
                values = cache.pop(scope, None)
                if isinstance(values, dict):
                    for value in values.values():
                        if isinstance(value, asyncio.Task):
                            value.cancel()
            from src.llm_models.model_client.base_client import client_registry
            for key in list(client_registry.client_instance_cache):
                if key[1].startswith(scope + ':'):
                    client_registry.client_instance_cache.pop(key, None)
            await self.memory.release(scope)
            factory = self.database_factories.pop(scope, None)
            if factory is not None:
                factory.kw['bind'].dispose()
            from src.chat.message_receive.chat_manager import chat_manager
            chat_manager.sessions.pop(session_id, None)
            chat_manager.last_messages.pop(session_id, None)
            self.session_scopes.pop(session_id, None)
            for cache in (self.contexts, self.configs, self.models):
                cache.pop(scope, None)
        async def release(session_id, *, reason):
            async with release_lock:
                await release_scoped(session_id, reason=reason)
        heartflow_manager._evict_chat = release
        async def get_chat(session_id):
            if session_id in heartflow_manager.heartflow_chat_list:
                with self.bind(self.scope_for_session(session_id)):
                    return await original(session_id)
            deadline = asyncio.get_running_loop().time() + 120
            async with admission:
                while True:
                    chats = heartflow_manager.heartflow_chat_list
                    available = session_id in chats or len(chats) < limit or any(self.idle(chat) for chat in chats.values())
                    if available:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError('native session admission deadline exceeded')
                    await asyncio.sleep(.25)
                chats = heartflow_manager.heartflow_chat_list
                if session_id not in chats and len(chats) >= limit:
                    victim = next((sid for sid, chat in chats.items() if self.idle(chat)), None)
                    if victim is None:
                        raise TimeoutError('native session admission lost idle slot')
                    await heartflow_manager._evict_chat(victim, reason='host_cache_limit')
                with self.bind(self.scope_for_session(session_id)):
                    return await original(session_id)
        heartflow_manager.get_or_create_heartflow_chat = get_chat

    def install_background_scopes(self):
        # Workers consume a shared queue created outside any chat context. Bind
        # from the actual message at consumption, never from worker creation.
        from src.services import memory_flow_service as flow
        for name in ('PersonFactWritebackService', 'ChatSummaryWritebackService', 'ImageMemoryWritebackService'):
            owner = getattr(flow, name, None)
            if owner is None or not hasattr(owner, '_handle_message'):
                continue
            original = owner._handle_message
            def wrap(method):
                @functools.wraps(method)
                async def handle(worker, message, *args, **kwargs):
                    session_id = getattr(message, 'session_id', '')
                    with self.bind(self.scope_for_session(session_id)):
                        return await method(worker, message, *args, **kwargs)
                return handle
            owner._handle_message = wrap(original)
        sessions = self
        class Automation:
            async def start(self):
                pass
            async def shutdown(self):
                for scope, service in list(sessions.automation.items()):
                    with sessions.bind(scope):
                        await service.shutdown()
                sessions.automation.clear()
            async def service(self, message):
                scope = sessions.scope_for_session(message.session_id)
                with sessions.bind(scope):
                    service = sessions.automation.get(scope)
                    if service is None:
                        service = flow.MemoryAutomationService()
                        service.image_writeback = flow.ImageMemoryWritebackService(
                            Path(sessions.configs[scope].a_memorix.storage.data_dir) / 'image_writeback.sqlite3')
                        sessions.automation[scope] = service
                    await service.start()
                return scope, service
            async def on_incoming_message(self, message):
                scope, service = await self.service(message)
                with sessions.bind(scope):
                    await service.on_incoming_message(message)
            async def on_message_sent(self, message):
                scope, service = await self.service(message)
                with sessions.bind(scope):
                    await service.on_message_sent(message)
        flow.memory_automation_service = Automation()
        from src.chat.message_receive.chat_manager import chat_manager
        original_save = chat_manager._save_session
        def save(session):
            if session.scope in self.contexts:
                with self.bind(session.scope):
                    return original_save(session)
            raise PermissionError('cannot save an unregistered conversation')
        chat_manager._save_session = save
        def save_all():
            for session in list(chat_manager.sessions.values()):
                if session.scope in self.contexts:
                    save(session)
        chat_manager.save_all_sessions = save_all


    def install_memory(self):
        from src.A_memorix import host_service, runtime_registry
        owner = self
        class MemoryHost:
            # One resident vector kernel, separate persistent roots per scope.
            # Serialization covers stop/start and each host operation.
            def __init__(self):
                self.lock, self.host, self.scope = asyncio.Lock(), None, None
                self.signature = None
            def register_config_reload_callback(self):
                pass
            def is_enabled(self):
                return owner.configs.get(current_scope.get(), owner.base_config).a_memorix.plugin.enabled
            def get_runtime_data_dir(self):
                config = owner.configs.get(current_scope.get(), owner.base_config)
                return Path(config.a_memorix.storage.data_dir).resolve()
            async def start(self):
                pass
            async def release(self, scope):
                if self.scope == scope:
                    await self.stop()
            async def stop(self):
                async with self.lock:
                    if self.host:
                        with owner.bind(self.scope):
                            await self.host.stop()
                        self.host = self.scope = None
            @asynccontextmanager
            async def lease(self, scope):
                owner.context(scope)
                async with self.lock:
                    signature = json.dumps(owner.context(scope)['tasks'], sort_keys=True) + owner.context(scope)['token']
                    if self.host and (self.scope != scope or self.signature != signature):
                        with owner.bind(self.scope):
                            await self.host.stop()
                        self.host = None
                    with owner.bind(scope):
                        if self.host is None:
                            self.host, self.scope = host_service.AMemorixHostService(), scope
                            self.signature = signature
                        if not self.host.is_enabled():
                            yield None
                            return
                        await self.host.start()
                        if self.host._startup_task:
                            await self.host._startup_task
                        if not getattr(self.host, '_jmbot_probed', False):
                            # Use upstream self-check and vector-space synchronization;
                            # a configured dimension alone is not a verified model.
                            kernel = await self.host._ensure_kernel()
                            await kernel._recover_embedding_once()
                            self.host._jmbot_probed = True
                        yield self.host
            async def invoke(self, component_name, args=None, *, timeout_ms=None):
                args = args or {}
                session_id = args.get('chat_id') or args.get('session_id')
                scope = owner.scope_for_session(session_id) if session_id else current_scope.get()
                async with self.lease(scope) as host:
                    if host is None:
                        return host_service.AMemorixHostService._disabled_response(component_name)
                    return await host.invoke(component_name, args, timeout_ms=timeout_ms)
        host_service.a_memorix_host_service = MemoryHost()
        self.memory = host_service.a_memorix_host_service
        kernel = runtime_registry.get_runtime_kernel
        runtime_registry.get_runtime_kernel = lambda: self.runtime_kernel_for_webui(kernel)
