"""Per-request model workload labels, propagated through native SDK headers."""
from contextvars import ContextVar

model_workload = ContextVar('jmbot_model_workload', default='interactive')


def install_headers():
    from src.llm_models.model_client.openai_client import OpenaiClient
    original = OpenaiClient._build_request_headers

    def headers(client, extra_headers):
        result = dict(original(client, extra_headers) or {})
        result['X-JM-Workload'] = model_workload.get()
        return result

    OpenaiClient._build_request_headers = headers
