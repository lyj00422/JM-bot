"""Explicit capability probes using synthetic input; never edits configuration."""
import base64
import io
import json
import math


def render_template(value, replacements):
    if isinstance(value, str):
        for name, replacement in replacements.items():
            value = value.replace('{' + name + '}', replacement)
        return value
    if isinstance(value, list):
        return [render_template(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: render_template(item, replacements) for key, item in value.items()}
    return value


async def probe_route(route, key, client):
    from PIL import Image
    picture = io.BytesIO()
    Image.new('RGB', (32, 32), 'red').save(picture, format='PNG')
    encoded = base64.b64encode(picture.getvalue()).decode()
    data_uri = 'data:image/png;base64,' + encoded
    results = {}
    for task in ('planner', 'vlm', 'emoji', 'embedding', 'image_embedding'):
        binding = route['tasks'].get(task, {})
        if not binding.get('enabled', False):
            results[task] = {'status': 'disabled'}
            continue
        try:
            if task in {'embedding', 'image_embedding'}:
                operation = 'embeddings'
                body = {'model': binding['model'], 'input': 'synthetic capability probe'}
                if task == 'image_embedding':
                    replacements = {'base64': encoded, 'data_uri': data_uri, 'mime_type': 'image/png'}
                    body['input'] = render_template(binding.get('image_embedding_input'), replacements)
                    body.update(render_template(binding.get('image_embedding_body', {}), replacements))
                    if body.get('input') is None:
                        raise ValueError('missing image protocol')
            else:
                operation = 'chat/completions'
                body = {'model': binding['model'], 'max_tokens': 100, 'temperature': 0, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Identify the dominant color of this image. Reply in English with one color word.'}, {'type': 'image_url', 'image_url': {'url': data_uri}}]}]}
                if task == 'planner':
                    body['tools'] = [{'type': 'function', 'function': {'name': 'report_color', 'description': 'Report the color', 'parameters': {'type': 'object', 'properties': {'color': {'type': 'string'}}, 'required': ['color']}}}]
                    body['tool_choice'] = {'type': 'function', 'function': {'name': 'report_color'}}
            response = await client.post(route['base_url'].rstrip('/') + '/' + operation, headers={'Authorization': 'Bearer ' + key}, json=body)
            response.raise_for_status()
            value = response.json()
            if task in {'embedding', 'image_embedding'}:
                vector = value['data'][0]['embedding']
                if not vector or any(type(item) not in (int, float) or not math.isfinite(item) for item in vector):
                    raise ValueError('invalid embedding')
                status = 'passed' if task == 'image_embedding' or len(vector) == binding['dimension'] else 'dimension_mismatch'
                results[task] = {'status': status, 'observed_dimension': len(vector)}
            else:
                message = value['choices'][0]['message']
                text = message.get('content') or ''
                if task == 'planner':
                    call = message.get('tool_calls', [])[0]['function']
                    text = json.loads(call['arguments']).get('color', '') if call['name'] == 'report_color' else ''
                results[task] = {'status': 'passed' if 'red' in text.lower() else 'inconclusive'}
        except Exception as exc:
            results[task] = {'status': 'failed', 'error_type': type(exc).__name__}
    return results
