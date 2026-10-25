"""Build NapCat native custom emoji messages from validated image bytes.

OneBot uses an image segment for both photos and custom emoji. NapCat maps
sub_type=1 to QQ's picSubType=1; changing the encoding to GIF is unnecessary.
"""
import base64
from contextvars import ContextVar

from jmbot.native_emoji import validate_image

sticker_delivery = ContextVar('jmbot_sticker_delivery', default=None)


def sticker_segment(binary: bytes) -> dict:
    validate_image(binary)
    return sticker_segment_base64(base64.b64encode(binary).decode('ascii'))


def sticker_segment_base64(encoded: str) -> dict:
    return {'type': 'image', 'data': {
        'file': 'base64://' + encoded, 'sub_type': 1, 'summary': '[动画表情]',
    }}


async def send_native_message(client, url, *, headers, message_id, segments):
    delivery = sticker_delivery.get() if any(item['type'] == 'image' for item in segments) else None
    request_id = message_id
    if delivery is not None:
        if delivery.get('attempted'):
            raise RuntimeError('sticker delivery already attempted; do not resend')
        delivery['attempted'] = True
        request_id = delivery.get('request_id') or request_id
    result = await client.post(url, headers=headers, timeout=35,
                               json={'request_id': request_id, 'message': segments})
    result.raise_for_status()
    receipt = result.json()
    if not receipt.get('message_id'):
        raise RuntimeError('QQ send outcome unconfirmed; do not resend')
    if delivery is not None:
        delivery['confirmed'] = True
        delivery['message_id'] = receipt['message_id']
    return receipt
