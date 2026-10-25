"""Bounded provider requests; retries apply to inference, never QQ sends."""
import asyncio

import httpx


class ModelUnavailable(RuntimeError):
    pass


def _retry_delay(response_headers, retry_index):
    """Return a small bounded delay, honoring a numeric Retry-After hint."""
    retry_after = response_headers.get('retry-after', '')
    try:
        if retry_after:
            return min(10.0, max(0.0, float(retry_after)))
    except (TypeError, ValueError):
        pass
    return min(2.0, 0.25 * (2 ** retry_index))


async def fetch_model(client, url, *, headers, body, retry=False, attempt_seconds=60):
    if attempt_seconds <= 0:
        raise ValueError('attempt_seconds must be positive')

    async def attempt():
        async with client.stream('POST', url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                return response.status_code, response.headers, b''
            parts, size = [], 0
            async for part in response.aiter_bytes():
                size += len(part)
                if size > 4 * 1024 * 1024:
                    raise ModelUnavailable('model response exceeds memory budget')
                parts.append(part)
            return response.status_code, response.headers, b''.join(parts)

    for index in range(2 if retry else 1):
        try:
            status, response_headers, content = await asyncio.wait_for(attempt(), attempt_seconds)
        except (httpx.TransportError, TimeoutError):
            status, response_headers, content = 503, {}, b''
        if status < 400:
            return content
        if not retry or index == 1 or status not in {429, 500, 502, 503, 504}:
            raise ModelUnavailable('model provider unavailable')
        await asyncio.sleep(_retry_delay(response_headers, index))
    raise ModelUnavailable('model provider unavailable')
