"""Download-owned queue and constrained QQ transport; no chat decision logic."""
import asyncio
import base64
from pathlib import Path
from jmbot.business_api import DownloadControl
from jmbot.service_rpc import mount_rpc


def mount_download_api(app, driver, queue, state, token, queue_path):
    import os
    from jmbot.service_rpc import RPCClient, RemoteStore
    chat_state = RemoteStore(RPCClient(os.environ.get('JM_BOT_CHAT_SERVICE_URL', 'http://127.0.0.1:8082'), token), 'state')
    class QQTransport:
        loop = None

        def send(self, account, scope, segments, configuration=False):
            from nonebot import get_bot
            from jmbot.core.conversation_state import ConversationStateStore
            scope = ConversationStateStore._validate_key(scope)
            if not isinstance(segments, list) or not segments or len(segments) > 32:
                raise ValueError('invalid outbound segments')
            for segment in segments:
                kind, data = segment.get('type'), segment.get('data', {})
                if kind == 'text':
                    if not isinstance(data.get('text'), str) or '[cq:' in data['text'].lower() or len(data['text']) > 12000:
                        raise ValueError('unsafe outbound text')
                elif kind == 'at':
                    if not str(data.get('qq', '')).isdigit():
                        raise ValueError('invalid mention')
                elif kind == 'reply':
                    if not str(data.get('id', '')).isdigit():
                        raise ValueError('invalid quoted message')
                elif kind == 'image':
                    if not str(data.get('file', '')).startswith('base64://') or len(data['file']) > 12 * 1024 * 1024:
                        raise ValueError('only bounded binary images permitted')
                elif kind == 'face':
                    from jmbot.core.chat_service import SUPER_FACES
                    if data.get('id') not in SUPER_FACES:
                        raise ValueError('face outside whitelist')
                else:
                    raise ValueError('unsupported outbound segment')
            if sum(item['type'] in {'face', 'image'} for item in segments) > 1:
                raise ValueError('at most one face or sticker per reply')
            async def invoke():
                if not configuration and not await asyncio.to_thread(chat_state.permitted, scope):
                    raise ValueError('chat permission revoked')
                bot = get_bot(str(account))
                kind, owner = scope.split(':')
                return await bot.call_api('send_group_msg' if kind == 'group' else 'send_private_msg',
                                          **{'group_id' if kind == 'group' else 'user_id': int(owner)}, message=segments)
            return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(20)

        def send_configuration(self, account, scope, segments):
            if not scope.startswith('private:') or any(item.get('type') != 'text' for item in segments):
                raise ValueError('configuration replies must be private text')
            if chat_state.get_policy(scope).chat_blacklisted:
                raise ValueError('configuration reply blocked')
            return self.send(account, scope, segments, configuration=True)

        def media(self, account, scope, file):
            from nonebot import get_bot
            if not chat_state.permitted(scope) or not isinstance(file, str) or len(file) > 512:
                raise ValueError('media denied')
            # NapCat resolves opaque image IDs. Never read arbitrary paths/URLs supplied by messages.
            if '/' in file or '\\' in file or ':' in file:
                raise ValueError('opaque image ID required')
            async def invoke():
                return await get_bot(str(account)).call_api('get_image', file=file)
            result = asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(15)
            path = Path(result.get('file', ''))
            import os
            root = Path(os.environ.get('JM_BOT_NAPCAT_MEDIA_ROOT', '/opt/napcat')).resolve()
            if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('media path outside permitted NapCat root')
            from PIL import Image
            with Image.open(path) as image:
                if image.width * image.height > 32000000:
                    raise ValueError('media dimensions exceed limit')
                image.verify()
            return {'base64': base64.b64encode(path.read_bytes()).decode()}

        def status(self, account):
            from nonebot import get_bot
            async def invoke():
                return await get_bot(str(account)).call_api('get_status')
            return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(10)

    transport = QQTransport()
    @driver.on_startup
    async def capture_loop():
        transport.loop = asyncio.get_running_loop()
    mount_rpc(app, token=token, ledger=Path(queue_path).with_name('download-rpc.sqlite3'),
              targets={'download': DownloadControl(queue), 'download_state': state, 'qq': transport},
              allowed={'download': {'snapshot'}, 'download_state': {'get_policy', 'set_policy', 'has_policy', 'list_policies', 'global_enabled', 'set_global_enabled', 'permitted', 'policy_version'}, 'qq': {'send', 'send_configuration', 'media', 'status'}})
