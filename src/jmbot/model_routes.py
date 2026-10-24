"""Explicit provider/task configuration, atomically replaced by its owner."""
import hashlib
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

TASKS = ('planner', 'replyer', 'utils', 'fast_model', 'memory', 'mid_memory', 'learner', 'emoji', 'vlm', 'voice', 'embedding', 'image_embedding')


class ModelRoutes:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            raw = self.path.read_bytes() if self.path.exists() else b'{"providers":{}}'
            return {'document': json.loads(raw), 'version': hashlib.sha256(raw).hexdigest()}

    def write(self, document, expected_version):
        self.validate(document)
        with self.lock:
            if self.read()['version'] != expected_version:
                raise ValueError('model routes changed; reload before saving')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.json.tmp')
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            return self.read()

    @staticmethod
    def validate(document):
        providers = document.get('providers') if isinstance(document, dict) else None
        if not isinstance(providers, dict) or len(providers) > 8:
            raise ValueError('invalid provider configuration')
        allowed = {family + '_' + origin for family in ('ds', 'glm', 'kimi', 'qwen') for origin in ('school', 'official')}
        for provider, route in providers.items():
            if provider not in allowed or not isinstance(route, dict) or set(route) - {'base_url', 'tasks'}:
                raise ValueError('unknown provider or route field')
            url = urlsplit(route.get('base_url', ''))
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('provider must use an HTTPS base URL without credentials')
            tasks = route.get('tasks')
            if not isinstance(tasks, dict) or set(tasks) - set(TASKS):
                raise ValueError('invalid task bindings')
            for name, binding in tasks.items():
                if not isinstance(binding, dict) or set(binding) - {'model', 'enabled', 'concurrency', 'dimension', 'visual', 'tools', 'image_embedding_input', 'image_embedding_body'}:
                    raise ValueError('invalid task configuration')
                for capability in ('visual', 'tools'):
                    if capability in binding and type(binding[capability]) is not bool:
                        raise ValueError('model capabilities must be boolean')
                if type(binding.get('enabled', True)) is not bool:
                    raise ValueError('enabled must be boolean')
                if binding.get('enabled', True) and (not isinstance(binding.get('model'), str) or not 1 <= len(binding['model']) <= 200):
                    raise ValueError('enabled task needs an explicit model')
                if type(binding.get('concurrency', 1)) is not int or not 1 <= binding.get('concurrency', 1) <= 8:
                    raise ValueError('invalid task concurrency')
                if 'dimension' in binding and (name != 'embedding' or type(binding['dimension']) is not int or not 1 <= binding['dimension'] <= 65536):
                    raise ValueError('invalid embedding dimension')
                if name == 'embedding' and binding.get('enabled', True) and 'dimension' not in binding:
                    raise ValueError('embedding requires an explicit dimension')
                templates = {'image_embedding_input', 'image_embedding_body'} & binding.keys()
                if templates and name != 'image_embedding':
                    raise ValueError('image templates belong to image_embedding')
                if name == 'image_embedding' and binding.get('enabled', True) and not templates:
                    raise ValueError('image embedding requires an explicit provider protocol template')
                if templates and len(json.dumps(binding)) > 16000:
                    raise ValueError('image embedding template exceeds limit')
                if name == 'voice' and binding.get('enabled', True):
                    raise ValueError('voice transport is unavailable; keep this task disabled')
            for required in ('planner', 'replyer', 'utils'):
                if not tasks.get(required, {}).get('model') or not tasks.get(required, {}).get('enabled', True):
                    raise ValueError('planner, replyer and utils are required')
