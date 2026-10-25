import asyncio
import base64
import sys
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from jmbot import download_api, service_rpc


@pytest.fixture
def media_transport(tmp_path, monkeypatch):
    root = tmp_path / 'napcat'
    host = root / 'ntqq'
    host.mkdir(parents=True)
    image = host / 'received.gif'
    Image.new('RGB', (4, 4), 'red').save(image)
    result = {'file': '/app/.config/QQ/received.gif'}
    calls = []

    async def call_api(action, **kwargs):
        calls.append((action, kwargs))
        if action in {'send_group_msg', 'send_private_msg'}:
            return {'message_id': 'sent-1'}
        return result

    monkeypatch.setitem(sys.modules, 'nonebot', SimpleNamespace(get_bot=lambda account: SimpleNamespace(call_api=call_api)))
    monkeypatch.setattr(service_rpc, 'RemoteStore', lambda *args: SimpleNamespace(permitted=lambda scope: True))
    captured = {}
    monkeypatch.setattr(download_api, 'mount_rpc', lambda app, **kwargs: captured.update(kwargs['targets']))
    monkeypatch.setenv('JM_BOT_NAPCAT_MEDIA_ROOT', str(root))
    monkeypatch.setenv('JM_BOT_NAPCAT_MEDIA_CONTAINER_ROOT', '/app/.config/QQ')
    monkeypatch.setenv('JM_BOT_NAPCAT_MEDIA_HOST_ROOT', str(host))
    download_api.mount_download_api(None, SimpleNamespace(on_startup=lambda fn: fn), None, None, 't' * 40, tmp_path / 'queue.db')
    transport = captured['qq']
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    transport.loop = loop
    try:
        yield transport, image, result, calls
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()


@pytest.mark.parametrize('scope', ['group:1129150377', 'private:3065532050'])
def test_received_image_resolves_container_mount_for_both_chat_types(media_transport, scope):
    transport, image, _, calls = media_transport
    result = transport.media('2661195769', scope, 'opaque-image-id')
    assert base64.b64decode(result['base64']) == image.read_bytes()
    assert calls == [('get_image', {'file': 'opaque-image-id'})]


def test_media_still_rejects_untrusted_paths_and_urls(media_transport):
    transport, _, result, calls = media_transport
    for value in ('../secret', 'https://example.com/a.gif', '/etc/passwd'):
        with pytest.raises(ValueError):
            transport.media('2661195769', 'private:3065532050', value)
    assert not calls
    for value in ('/etc/passwd', '/app/.config/QQ/../../etc/passwd', ''):
        result['file'] = value
        with pytest.raises(ValueError):
            transport.media('2661195769', 'private:3065532050', 'opaque-image-id')


def test_send_accepts_stable_rpc_request_id(media_transport):
    transport, _, _, calls = media_transport
    result = transport.send(
        '2661195769', 'private:3065532050',
        [{'type': 'text', 'data': {'text': 'configured'}}],
        request_id='send-once-1',
    )
    assert result['message_id'] == 'sent-1'
    assert calls[-1] == ('send_private_msg', {'user_id': 3065532050, 'message': [{'type': 'text', 'data': {'text': 'configured'}}]})
