import asyncio
import io
from types import SimpleNamespace

import httpx
import pytest

from jmbot import model_request
from jmbot.native_emoji import RegistrationBackoff, selection_pool
from jmbot.qq_stickers import send_native_message, sticker_delivery, sticker_segment


def test_model_retry_honors_retry_after_without_replaying_qq_sends(monkeypatch):
    calls = []
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    async def response(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={'Retry-After': '2'}, request=request)
        return httpx.Response(200, content=b'{"ok":true}', request=request)

    monkeypatch.setattr(model_request.asyncio, 'sleep', fake_sleep)

    async def run():
        transport = httpx.MockTransport(response)
        async with httpx.AsyncClient(transport=transport) as client:
            return await model_request.fetch_model(
                client, 'https://provider.test/v1/chat/completions', headers={}, body={}, retry=True
            )

    assert asyncio.run(run()) == b'{"ok":true}'
    assert len(calls) == 2
    assert delays == [2.0]


def test_model_request_rejects_invalid_timeout_and_unbounded_response():
    with pytest.raises(ValueError, match='attempt_seconds'):
        asyncio.run(model_request.fetch_model(None, 'https://provider.test', headers={}, body={}, attempt_seconds=0))

    async def response(request):
        return httpx.Response(200, content=b'x' * (4 * 1024 * 1024 + 1), request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            await model_request.fetch_model(client, 'https://provider.test', headers={}, body={})

    with pytest.raises(model_request.ModelUnavailable, match='memory budget'):
        asyncio.run(run())


def test_sticker_delivery_is_validated_and_never_retried_after_attempt():
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {'message_id': 'qq-1'}

    class Client:
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    async def run():
        token = sticker_delivery.set({'request_id': 'stable-emoji'})
        try:
            from PIL import Image
            image = io.BytesIO()
            Image.new('RGB', (2, 2), 'red').save(image, format='PNG')
            segments = [sticker_segment(image.getvalue())]
            result = await send_native_message(Client(), 'http://local/native/send', headers={}, message_id='m1', segments=segments)
            assert result['message_id'] == 'qq-1'
            with pytest.raises(RuntimeError, match='already attempted'):
                await send_native_message(Client(), 'http://local/native/send', headers={}, message_id='m1', segments=segments)
        finally:
            sticker_delivery.reset(token)

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0][1]['json']['request_id'] == 'stable-emoji'


def test_emoji_selection_and_registration_backoff_are_scoped():
    first = SimpleNamespace(file_hash='a')
    duplicate = SimpleNamespace(file_hash='a')
    second = SimpleNamespace(file_hash='b')
    assert selection_pool([first, duplicate], [second], 'collected') == [first]
    assert selection_pool([first], [second], 'curated') == [second]
    assert selection_pool([first, duplicate], [second], 'all') == [first, second]

    backoff = RegistrationBackoff()
    backoff.record('a', 'failed', now=10)
    assert backoff.ready({'a'}, now=10) == []
    assert backoff.ready({'a'}, now=70) == ['a']
