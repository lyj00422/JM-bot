"""Download-side admin link. Outages cannot stop the download event loop."""
import threading
from jmbot.service_rpc import RPCClient


class ResilientAdmin:
    def __init__(self, url, token):
        self.client = RPCClient(url, token, timeout=3)
        self.help = '发送 /jm help 查看下载帮助。'
        self.lock = threading.Lock()

    def get_help(self):
        return self.help

    def heartbeat(self, *args):
        try:
            self.client.call('admin', 'heartbeat', *args)
            self.help = self.client.call('admin', 'get_help')
        except RuntimeError:
            pass

    def __getattr__(self, name):
        def invoke(*args, **kwargs):
            try:
                return self.client.call('admin', name, *args, **kwargs)
            except RuntimeError:
                if name in {'pending_approval_actions', 'claim_control_actions'}:
                    return []
                raise
        return invoke
