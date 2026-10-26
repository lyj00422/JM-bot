"""Download-owned queue and constrained QQ transport; no chat decision logic."""
import asyncio
import base64
from pathlib import Path, PurePosixPath
from jmbot.business_api import DownloadControl
from jmbot.service_rpc import mount_rpc


def mount_download_api(app, driver, queue, state, token, queue_path):
    import os
    class QQTransport:
        loop = None

        def send(self, account, scope, segments, configuration=False, request_id=None):
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
                    if type(data.get('sub_type', 0)) is not int or data.get('sub_type', 0) not in {0, 1}:
                        raise ValueError('unsupported QQ picture subtype')
                    if data.get('sub_type') == 1:
                        from jmbot.native_emoji import validate_image
                        validate_image(base64.b64decode(data['file'][9:], validate=True))
                elif kind == 'face':
                    from jmbot.core.chat_service import SUPER_FACES
                    if data.get('id') not in SUPER_FACES:
                        raise ValueError('face outside whitelist')
                else:
                    raise ValueError('unsupported outbound segment')
            if sum(item['type'] in {'face', 'image'} for item in segments) > 1:
                raise ValueError('at most one face or sticker per reply')
            async def invoke():
                bot = get_bot(str(account))
                kind, owner = scope.split(':')
                result = await bot.call_api('send_group_msg' if kind == 'group' else 'send_private_msg',
                                          **{'group_id' if kind == 'group' else 'user_id': int(owner)}, message=segments)
                if not isinstance(result, dict) or not result.get('message_id'):
                    raise RuntimeError('QQ send outcome unconfirmed; do not resend')
                return result
            return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(20)

        def send_configuration(self, account, scope, segments, request_id=None):
            if not scope.startswith('private:') or any(item.get('type') != 'text' for item in segments):
                raise ValueError('configuration replies must be private text')
            return self.send(account, scope, segments, configuration=True, request_id=request_id)

        def media(self, account, scope, file):
            from nonebot import get_bot
            if not isinstance(file, str) or len(file) > 512:
                raise ValueError('media denied')
            # NapCat resolves opaque image IDs. Never read arbitrary paths/URLs supplied by messages.
            if '/' in file or '\\' in file or ':' in file:
                raise ValueError('opaque image ID required')
            async def invoke():
                return await get_bot(str(account)).call_api('get_image', file=file)
            result = asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(15)
            raw_path = result.get('file', '')
            if not isinstance(raw_path, str) or not raw_path or '..' in PurePosixPath(raw_path).parts:
                raise ValueError('invalid NapCat image path')
            path = Path(raw_path)
            # get_image returns a container path when NapCat runs in Docker.
            # Translate only the operator-configured bind mount, then enforce
            # the host root again (including symlink resolution).
            container_root = os.environ.get('JM_BOT_NAPCAT_MEDIA_CONTAINER_ROOT')
            host_root = os.environ.get('JM_BOT_NAPCAT_MEDIA_HOST_ROOT')
            if container_root and host_root:
                container_path = PurePosixPath(raw_path)
                mount = PurePosixPath(container_root)
                if container_path.is_relative_to(mount):
                    path = Path(host_root).joinpath(*container_path.relative_to(mount).parts)
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

        def roster(self):
            import os as _os
            from nonebot import get_bot
            account = _os.environ.get('JM_BOT_QQ_ACCOUNT', '')
            async def invoke():
                bot = get_bot(account)
                friends = await bot.call_api('get_friend_list')
                groups = await bot.call_api('get_group_list')
                return {'friends': friends or [], 'groups': groups or []}
            return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(20)

        def set_friend_remark(self, user_id, remark):
            import os as _os
            from nonebot import get_bot
            if isinstance(remark, str) is False or len(remark) > 60:
                raise ValueError('invalid remark')
            account = _os.environ.get('JM_BOT_QQ_ACCOUNT', '')
            async def invoke():
                return await get_bot(account).call_api('set_friend_remark', user_id=int(user_id), remark=remark)
            return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(15)

        def set_group_remark(self, group_id, remark):
            import os as _os
            from nonebot import get_bot
            if isinstance(remark, str) is False or len(remark) > 60:
                raise ValueError('invalid remark')
            account = _os.environ.get('JM_BOT_QQ_ACCOUNT', '')
            async def invoke():
                return await get_bot(account).call_api('set_group_remark', group_id=int(group_id), remark=remark)
            return asyncio.run_coroutine_threadsafe(invoke(), self.loop).result(15)

    transport = QQTransport()
    @driver.on_startup
    async def capture_loop():
        transport.loop = asyncio.get_running_loop()
    mount_rpc(app, token=token, ledger=Path(queue_path).with_name('download-rpc.sqlite3'),
              targets={'download': DownloadControl(queue), 'download_state': state, 'qq': transport},
              allowed={'download': {'snapshot'}, 'download_state': {'get_policy', 'set_policy', 'has_policy', 'list_policies', 'global_enabled', 'set_global_enabled', 'permitted', 'policy_version'}, 'qq': {'send', 'send_configuration', 'media', 'status', 'roster', 'set_friend_remark', 'set_group_remark'}})
