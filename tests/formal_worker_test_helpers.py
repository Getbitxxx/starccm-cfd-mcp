"""Current-byte review fixtures for tests that never contact a worker.

Deployment approval is an independent gate. Tests of request serialization may
patch only formal_worker.request_pins with mock_approved_request_pins; tests of
review validation keep load_review_record and all actual byte checks intact.
"""
from backend import Backend
from formal_worker import runtime_pins


def current_runtime_review_pins():
    """Compute the real runtime pins without constructing a connection."""
    return runtime_pins(Backend.__new__(Backend))


def mock_approved_request_pins(backend):
    """A fake installation result, only for tests whose remote ps is mocked."""
    return dict(runtime_pins(backend),
                expected_worker_sid='S-1-5-21-111-222-333-1001',
                deployment_review_sha256='a' * 64,
                health_watchdog_seconds=900)
