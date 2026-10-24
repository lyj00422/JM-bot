"""Restricted client for the locked NapCat WebUI login API."""
import hashlib
import os
import threading
import time
from urllib.parse import urlsplit

import httpx


class NapCatAdmin:
    def __init__(self):
        self.url = os.environ.get('JM_BOT_NAPCAT_WEBUI_URL', '').rstrip('/')
        self.token = os.environ.get('JM_BOT_NAPCAT_WEBUI_TOKEN', '')
        self.credential = ''
        self.expires = 0
        self.lock = threading.Lock()
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.query:
                raise ValueError('NapCat WebUI must use loopback HTTP')

    def _request(self, client, path, body=None, credential=''):
        response = client.post(self.url + path, json=body or {}, headers={'Authorization': 'Bearer ' + credential} if credential else {})
        response.raise_for_status()
        result = response.json()
        if result.get('code') != 0:
            raise RuntimeError('NapCat WebUI rejected request; check version and credentials')
        return result.get('data') or {}

    def login_state(self, refresh=False):
        if not self.url or not self.token:
            return {'configured': False, 'detail': '配置 NapCat 回环 WebUI URL 和 token 后可读取二维码'}
        with self.lock, httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
            try:
                if time.time() >= self.expires:
                    auth = self._request(client, '/api/auth/login', {'hash': hashlib.sha256((self.token + '.napcat').encode()).hexdigest()})
                    if auth.get('require2FA'):
                        raise RuntimeError('NapCat requires 2FA; use its WebUI to authenticate')
                    self.credential = auth['Credential']
                    self.expires = time.time() + 3300
                if refresh:
                    self._request(client, '/api/QQLogin/RefreshQRcode', credential=self.credential)
                state = self._request(client, '/api/QQLogin/CheckLoginStatus', credential=self.credential)
                return {'configured': True, **{key: state.get(key) for key in ('isLogin', 'isOffline', 'loginPhase', 'qrLoginAccepted', 'coreReady', 'qrcodeurl', 'loginError')}}
            except (httpx.HTTPError, ValueError, KeyError, RuntimeError):
                self.expires = 0
                raise RuntimeError('NapCat WebUI unavailable or incompatible') from None
