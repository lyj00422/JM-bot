"""Explicit RPC allowlists; private storage helpers are never remotely callable."""
STATE_METHODS = set('get_policy set_policy has_policy list_policies global_enabled set_global_enabled get_base_persona set_base_persona get_default_persona set_default_persona add_memory list_memories delete_memory edit_memory add_growth list_growth set_growth_frozen edit_growth rollback_growth effective_persona export_scope revision policy_version permitted commit_candidate'.split())
CREDENTIAL_METHODS = set('credential_scopes get_default_provider set_default_provider get_default_credential set_default_credential has_scope_credentials uses_default_private_key uses_default_group_key replace_scope_credential set_private_default_inheritance set_group_default_inheritance is_chat_enabled set_chat_enabled'.split())
ADMIN_METHODS = set('heartbeat service_status is_admin is_super upsert_approval pending_approval_actions begin_approval acknowledge_approval complete_approval_action claim_control_actions complete_control_action get_help'.split())


class CredentialView:
    def __init__(self, store, barrier=None, lock=None):
        self.store = store
        self.barrier, self.lock = barrier, lock

    def get_default_credential(self, provider):
        # The management API receives only presence, never decrypted secrets.
        return bool(self.store.get_default_credential(provider))

    def __getattr__(self, name):
        method = getattr(self.store, name)
        if not name.startswith(('set_', 'replace_')):
            return method
        def mutate(*args, **kwargs):
            from contextlib import nullcontext
            with self.barrier.hold() if self.barrier else nullcontext():
                with self.lock if self.lock else nullcontext():
                    return method(*args, **kwargs)
        return mutate


class DownloadControl:
    def __init__(self, queue):
        self.queue = queue

    def snapshot(self):
        from dataclasses import asdict
        return [asdict(job) for job in self.queue.list_jobs()[-200:]][::-1]
