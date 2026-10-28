"""Status codes and error types shared by the package and its library loader."""
import enum


class Status(enum.IntEnum):
    __module__ = 'ducktape_laya'  # errors and statuses are public under the package name
    OK = 0
    INVALID_ARGUMENT = 1
    MODEL_ERROR = 2
    INFERENCE_ERROR = 3
    OUT_OF_MEMORY = 4
    CANCELLED = 5
    DEADLINE_EXCEEDED = 6
    UNSUPPORTED_PLATFORM = 7


class LayaError(Exception):
    """A native failure; ``status`` is the C ABI status as a ``Status``."""
    __module__ = 'ducktape_laya'
    def __init__(self, status, message):
        super().__init__(message)
        try:
            self.status = Status(status)
        except ValueError:  # impossible under ABI 2; keep the message
            self.status = Status.INFERENCE_ERROR


def argument_error(kind, message):
    """A binding-checked argument error: the language's standard exception carrying ``status``
    ``INVALID_ARGUMENT``, as the native core would report it."""
    error = kind(message)
    error.status = Status.INVALID_ARGUMENT
    return error


class LayaLoadError(LayaError, OSError):
    """The native library is missing, cannot be loaded or is incompatible. Its ``status`` is
    ``UNSUPPORTED_PLATFORM``, as in every other binding; it is also an ``OSError``."""
    __module__ = 'ducktape_laya'
    def __init__(self, message):
        LayaError.__init__(self, Status.UNSUPPORTED_PLATFORM, message)
