"""Exceptions raised by process isolation boundaries."""

class ProcessIsolationUnavailable(RuntimeError):
    """Raised when the host cannot enforce the required process boundary."""