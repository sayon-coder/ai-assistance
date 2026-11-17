"""Windows shutdown API tests.  No real shutdown is ever requested."""
from __future__ import annotations

from talcon import shutdown


class FakeShutdownApi:
    def __init__(self, privilege=True, initiate=True, abort=True):
        self.privilege = privilege
        self.initiate_result = initiate
        self.abort_result = abort
        self.calls = []

    def enable_privilege(self):
        self.calls.append(("privilege",))
        return (self.privilege, "privilege ok" if self.privilege else "privilege refused")

    def initiate(self, machine, message, timeout, force, reboot, reason):
        self.calls.append(("initiate", machine, message, timeout, force, reboot, reason))
        return (self.initiate_result, "scheduled" if self.initiate_result else "refused")

    def abort(self, machine):
        self.calls.append(("abort", machine))
        return (self.abort_result, "aborted" if self.abort_result else "not running")


def test_shutdown_is_immediate_but_never_force_closes_applications():
    api = FakeShutdownApi()
    ok, detail = shutdown.shutdown_computer(api=api)
    assert ok is True
    assert api.calls[0] == ("privilege",)
    kind, machine, message, timeout, force, reboot, reason = api.calls[1]
    assert kind == "initiate"
    assert machine is None, "the local machine is used, never a remote host"
    assert timeout == 0, "shutdown must start immediately after confirmation"
    assert force is False, "unsaved applications must not be force-closed"
    assert reboot is False
    assert "now" in message.lower(), "the message must describe an immediate shutdown"
    assert reason & shutdown.SHTDN_REASON_FLAG_PLANNED


def test_shutdown_api_errors_are_reported():
    api = FakeShutdownApi(initiate=False)
    ok, detail = shutdown.shutdown_computer(api=api)
    assert ok is False
    assert detail == "refused"


def test_privilege_failure_prevents_the_shutdown_call():
    api = FakeShutdownApi(privilege=False)
    ok, detail = shutdown.shutdown_computer(api=api)
    assert ok is False
    assert "privilege" in detail
    assert all(call[0] != "initiate" for call in api.calls)


def test_cancellation_calls_abort_system_shutdown():
    api = FakeShutdownApi()
    ok, detail = shutdown.abort_shutdown(api=api)
    assert ok is True
    assert api.calls == [("abort", None)]
    assert "abort" in detail.lower()


def test_abort_failure_is_reported():
    api = FakeShutdownApi(abort=False)
    ok, detail = shutdown.abort_shutdown(api=api)
    assert ok is False
    assert detail == "not running"


def test_shutdown_api_exceptions_are_not_hidden():
    class BrokenApi(FakeShutdownApi):
        def initiate(self, *args, **kwargs):
            raise OSError("Windows refused")

    ok, detail = shutdown.shutdown_computer(api=BrokenApi())
    assert ok is False
    assert "Windows refused" in detail
