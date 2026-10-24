import asyncio
import httpx
from jmbot.model_probe import probe_route


def test_probe_checks_observed_output_and_never_echoes_provider_errors():
    def handle(request):
        import json
        body = json.loads(request.content)
        if body['model'] == 'bad':
            return httpx.Response(401, text='secret upstream diagnostics')
        if request.url.path.endswith('embeddings'):
            return httpx.Response(200, json={'data': [{'embedding': [0.1, 0.2]}]})
        if body['model'] == 'planner':
            return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [{'function': {'name': 'report_color', 'arguments': '{"color":"red"}'}}]}}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'red'}}]})
    async def check():
        route = {'base_url': 'https://local.test/v1', 'tasks': {
            'planner': {'model': 'planner', 'enabled': True},
            'vlm': {'model': 'vision', 'enabled': True},
            'emoji': {'model': 'bad', 'enabled': True},
            'embedding': {'model': 'embedding', 'enabled': True, 'dimension': 3},
        }}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await probe_route(route, 'local-key', client)
        assert result['planner']['status'] == 'passed'
        assert result['vlm']['status'] == 'passed'
        assert result['embedding'] == {'status': 'dimension_mismatch', 'observed_dimension': 2}
        assert result['emoji']['status'] == 'failed'
        assert 'secret' not in str(result)
        assert result['image_embedding']['status'] == 'disabled'
    asyncio.run(check())
