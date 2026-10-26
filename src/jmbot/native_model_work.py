"""Per-request model metadata propagated through native SDK headers."""
from contextvars import ContextVar

model_workload = ContextVar('jmbot_model_workload', default='interactive')


def install_headers(sessions=None):
    from src.llm_models.model_client.openai_client import OpenaiClient
    original = OpenaiClient._build_request_headers

    def headers(client, extra_headers):
        result = dict(original(client, extra_headers) or {})
        result['X-JM-Workload'] = model_workload.get()
        if sessions is not None:
            context = sessions.context()
            result['X-JM-Scope'] = str(context['scope'])
            from jmbot.native_sessions import current_session_id
            session_id = current_session_id.get() or getattr(client, 'session_id', '')
            if session_id:
                result['X-JM-Session-ID'] = str(session_id)
        return result

    OpenaiClient._build_request_headers = headers
