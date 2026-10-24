"""One NapCat connection, filtering before OneBot models/logs/media."""
import asyncio
import os
from nonebot.adapters.onebot.v11 import Adapter
from jmbot.core.ingress import classify
from jmbot.service_rpc import RPCClient
from jmbot.core.ingress_queue import IngressQueue


class SplitAdapter(Adapter):
    inbox = None
    worker = None

    @classmethod
    def json_to_event(cls, data):
        if not isinstance(data, dict) or 'post_type' not in data:
            return super().json_to_event(data)
        route = classify(data, os.environ.get('JM_BOT_COMMAND_PREFIX', '/jm'))
        if route.domain in {'drop'}:
            return None
        if route.domain in {'chat', 'credential'}:
            if not os.environ.get('JM_BOT_SERVICE_TOKEN'):
                return None
            if cls.inbox is None:
                cls.inbox = IngressQueue(maxsize=128)
            if cls.worker is None or cls.worker.done():
                cls.worker = asyncio.create_task(cls.dispatch())
            try:
                cls.inbox.put_nowait(data)
            except asyncio.QueueFull:
                # Bounded ingress: never create one task per event or log payload.
                pass
            return None
        return super().json_to_event(data)

    @classmethod
    async def dispatch(cls):
        client = RPCClient(os.environ.get('JM_BOT_CHAT_SERVICE_URL', 'http://127.0.0.1:8082'), os.environ['JM_BOT_SERVICE_TOKEN'], timeout=5)
        while True:
            data = await cls.inbox.get()
            try:
                await asyncio.to_thread(client.call, 'ingress', 'accept', data)
            except Exception:
                from nonebot import logger
                logger.warning('Chat service unavailable; ingress discarded without payload logging')
            finally:
                cls.inbox.task_done()
