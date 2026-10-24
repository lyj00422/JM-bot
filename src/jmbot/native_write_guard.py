"""Hold the policy barrier before native ORM writes, through transaction end."""
from sqlalchemy import event


def guard_session_writes(session_class, barrier, check_permission):
    key = '_jmbot_write_barrier'

    def hold(session):
        if barrier is None or key in session.info:
            return
        context = barrier.hold()
        context.__enter__()
        try:
            check_permission()
        except BaseException:
            context.__exit__(None, None, None)
            raise
        session.info[key] = context

    @event.listens_for(session_class, 'before_flush')
    def before_flush(session, flush_context, instances):
        hold(session)

    @event.listens_for(session_class, 'do_orm_execute')
    def before_execute(state):
        text = getattr(state.statement, 'text', '').lstrip()
        textual_write = text.split(' ', 1)[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}
        if state.is_insert or state.is_update or state.is_delete or textual_write:
            hold(state.session)

    @event.listens_for(session_class, 'before_commit')
    def before_commit(session):
        held = key in session.info
        hold(session)
        if held:
            check_permission()

    @event.listens_for(session_class, 'after_transaction_end')
    def after_transaction_end(session, transaction):
        if transaction.parent is None:
            context = session.info.pop(key, None)
            if context is not None:
                context.__exit__(None, None, None)
