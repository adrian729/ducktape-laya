"""Local Laya GGUF decisions through the shared ducktape-laya C library.

Calls block while the model runs and release the GIL, so threads and
``asyncio.to_thread`` run other Python code meanwhile. Requests and results are
JSON: a ``str`` is sent as is, any other value is serialized with ``json.dumps``
(dict insertion order is kept).

Errors: native failures and invalid request text raise ``LayaError`` (its ``status`` is
a ``Status``); calls after ``close()`` raise ``EngineClosedError``; conversions the
binding checks itself (types, ``timeout``, ``request_id``, non-finite numbers) raise
``TypeError``/``ValueError`` carrying ``status`` ``INVALID_ARGUMENT``; config values
go to the native core unchecked; a missing, unloadable or incompatible library raises
``LayaLoadError`` (status ``UNSUPPORTED_PLATFORM``, also an ``OSError``). When several
checks fail, the first of these wins: closed engine, argument conversions, an
already-cancelled token, then the native call.
"""
import asyncio
import ctypes
import json
import math
import os
import threading

from . import _native
from ._errors import LayaError, LayaLoadError, Status, argument_error

__all__ = ['CancellationToken', 'Engine', 'EngineClosedError', 'LayaError', 'LayaLoadError', 'Status', 'runtime_info']
__version__ = '0.3.0'


class EngineClosedError(LayaError):
    """A call was made after ``close()``. Its ``status`` is ``INVALID_ARGUMENT``."""
    def __init__(self):
        super().__init__(Status.INVALID_ARGUMENT, 'Engine is closed')


class CancellationToken:
    """Cancels the calls it is passed to (``cancel=``) from any thread.

    Cancellation is cooperative: a running call stops at the next safe point and
    raises ``LayaError`` with status ``CANCELLED``. A token stays cancelled; use a
    new one for later calls.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._flags = {}
        self._cancelled = False

    def cancel(self):
        with self._lock:
            self._cancelled = True
            for flag in self._flags.values():
                flag.value = Status.CANCELLED

    @property
    def cancelled(self):
        return self._cancelled

    def _attach(self, flag):
        with self._lock:
            self._flags[id(flag)] = flag
            if self._cancelled:
                flag.value = Status.CANCELLED

    def _detach(self, flag):
        with self._lock:
            self._flags.pop(id(flag), None)


_lib = None
_lib_lock = threading.Lock()


def _library():
    global _lib
    with _lib_lock:
        if _lib is None:
            _lib = _native.load(__version__)
        return _lib


def _encode(text, what):
    if not isinstance(text, str):
        raise argument_error(TypeError, f'{what} must be a str')
    if '\0' in text:
        raise LayaError(Status.INVALID_ARGUMENT, f'{what} contains a NUL character')
    try:
        return text.encode('utf-8')
    except UnicodeEncodeError:
        raise LayaError(Status.INVALID_ARGUMENT, f'{what} is not valid Unicode') from None


def _encode_path(path):
    """A model path as the bytes the native core opens: raw bytes on POSIX (so any file name works), UTF-8
    on Windows."""
    try:
        path = os.fspath(path)
    except TypeError:
        raise argument_error(TypeError, 'model path must be a str, bytes or os.PathLike') from None
    if isinstance(path, str):
        path = _encode(path, 'model path') if os.name == 'nt' else os.fsencode(path)
    elif os.name == 'nt':
        try:
            path.decode('utf-8')
        except UnicodeDecodeError:
            raise LayaError(Status.INVALID_ARGUMENT, 'model path is not valid UTF-8') from None
    if b'\0' in path:
        raise LayaError(Status.INVALID_ARGUMENT, 'model path contains a NUL character')
    return path


def _take(lib, pointer):
    """Copy and free a native string."""
    if not pointer.value:
        return None
    try:
        return ctypes.string_at(pointer.value).decode('utf-8', 'replace')
    finally:
        lib.dtl_free(pointer)


def _check(lib, status, error):
    message = _take(lib, error)
    if status:
        raise LayaError(status, message or 'Laya operation failed')


def _info(lib, handle):
    result, error = ctypes.c_void_p(), ctypes.c_void_p()
    status = lib.dtl_info(handle, ctypes.byref(result), ctypes.byref(error))
    text = _take(lib, result)
    _check(lib, status, error)
    return json.loads(text)


def runtime_info():
    """Runtime identity, limits and CPU compatibility, without loading a model."""
    return _info(_library(), None)


def _dumps(value, what, as_native=False):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:  # non-finite numbers, sets and other non-JSON values, cycles
        if as_native:  # request text is the core's input: the same status as invalid text
            raise LayaError(Status.INVALID_ARGUMENT, f'{what} is not serializable as JSON: {error}') from error
        raise argument_error(TypeError if isinstance(error, TypeError) else ValueError,
                             f'{what} is not serializable as JSON: {error}') from error


def _open_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise argument_error(TypeError, f'{name} must be an int')
    return value


def _open_config(threads, context_tokens, temperatures, overflow, max_windows_per_call):
    """Serialize the set open options to config JSON (``None`` when none are set). Only conversions are
    checked here; the native core validates every value."""
    config = {}
    for name, value in (('threads', threads), ('context_tokens', context_tokens)):
        if value is not None:
            config[name] = _open_int(name, value)
    if overflow is not None:
        config['overflow'] = overflow
    if temperatures is not None:
        config['temperatures'] = dict(temperatures)
    if max_windows_per_call is not None:
        config['max_windows_per_call'] = _open_int('max_windows_per_call', max_windows_per_call)
    if not config:
        return None
    return _encode(_dumps(config, 'open config'), 'open config')


class Engine:
    """One loaded model (``open``) or a lazy local model registry (``open_router``).

    Safe to share between threads; native calls on one engine run one at a time.
    Use it as a context manager or call ``close()``.
    """
    _lib: ctypes.CDLL
    _handle: 'ctypes.c_void_p | None'
    _state: threading.Condition
    _active: int
    _closing: bool
    _deferred: bool
    _local: threading.local

    def __init__(self, *_, **__):
        raise TypeError('Use Engine.open() or Engine.open_router()')

    @classmethod
    def _create(cls, lib, router, argument, config):
        opener = lib.dtl_open_router if router else lib.dtl_open
        handle, error = ctypes.c_void_p(), ctypes.c_void_p()
        status = opener(argument, config, ctypes.byref(handle), ctypes.byref(error))
        _check(lib, status, error)
        if not handle.value:
            raise LayaError(Status.MODEL_ERROR, 'Native engine returned null')
        engine = object.__new__(cls)
        engine._lib, engine._handle = lib, handle
        engine._state = threading.Condition()
        engine._active, engine._closing, engine._deferred = 0, False, False
        engine._local = threading.local()
        return engine

    @classmethod
    def open(cls, model_path, *, threads=None, context_tokens=None, temperatures=None, overflow=None,
             max_windows_per_call=None):
        """Load a local GGUF.

        ``model_path`` is a ``str``, ``bytes`` or ``os.PathLike``; on POSIX the bytes reach the file
        system unchanged. ``None`` (the default) leaves an option to the library; any other value,
        including 0, is sent and validated by the library (status ``INVALID_ARGUMENT``).
        ``threads`` 0 selects automatically (at most 4 and at most the CPUs the process may use). ``context_tokens`` is the
        prompt capacity (default 512, at most the model's trained length).
        ``temperatures`` maps calibration keys such as ``"choice"`` or
        ``"noul.2"`` to positive overrides. ``overflow`` is the engine-wide policy for
        prompts over the token limit (``"reject"`` (default), ``"truncate_end"``,
        ``"truncate_start"`` or ``"scan"``; a request's own ``overflow`` wins).
        ``max_windows_per_call`` is the most forward passes one call may plan (1..4096, default 256).
        """
        config = _open_config(threads, context_tokens, temperatures, overflow, max_windows_per_call)
        path = _encode_path(model_path)
        return cls._create(_library(), False, path, config)

    @classmethod
    def open_router(cls, config, *, threads=None, context_tokens=None, temperatures=None, overflow=None,
                    max_windows_per_call=None):
        """Open a bounded registry of local models.

        ``config`` is a dict or JSON string:
        ``{"models": {"id": "path.gguf" | {"path": ..., "config": {...}}}, "max_loaded": 1,
        "auto": {"english": "id", "default": "id"}}`` (``auto`` is optional).
        Requests name a route in ``"model"``, or send ``"model": "auto"`` (with an
        optional ``"lang"``) to choose by language; ``"auto"`` cannot be a route ID.
        The open options apply to every route; a route's own ``config`` overrides
        them key by key (``max_windows_per_call`` bounds the whole call and is engine-level only).
        """
        text = config if isinstance(config, str) else _dumps(config, 'router config')
        router = _encode(text, 'router config')
        options = _open_config(threads, context_tokens, temperatures, overflow, max_windows_per_call)
        return cls._create(_library(), True, router, options)

    def _enter(self):
        with self._state:
            if self._closing:
                raise EngineClosedError()
            self._active += 1
            # Counted under the lock so no asynchronous exception can separate the two.
            self._local.depth = getattr(self._local, 'depth', 0) + 1

    def _leave(self):
        self._local.depth -= 1
        with self._state:
            self._active -= 1
            if not self._active:
                if self._deferred and self._handle is not None:
                    handle, self._handle = self._handle, None
                    self._lib.dtl_close(handle)
                self._state.notify_all()

    def info(self):
        """Model metadata and calibration; routers report the runtime only."""
        self._enter()
        try:
            return _info(self._lib, self._handle)
        finally:
            self._leave()

    def execute(self, request, *, on_event=None, request_id=0, timeout=None, cancel=None):
        """One primitive or structured request, or a list of up to 32 requests.

        ``on_event(event)`` receives start/end/error events on the thread running
        the call. ``timeout`` is in seconds (``None`` or 0 for no deadline; status 6 when
        it expires). ``cancel`` is a ``CancellationToken``; cancelling it stops the call
        at the next safe point with status 5.
        """
        return self._call(request, on_event, request_id, timeout, cancel)

    async def execute_async(self, request, **options):
        """``execute`` on a worker thread; cancelling the task cancels the native call.

        ``on_event`` then runs on that worker thread, not the event loop's.
        """
        return await self._run_async(self.execute, request, options)

    @staticmethod
    async def _run_async(method, request, options):
        task = CancellationToken()  # cancelling the task must not cancel a shared token
        options['cancel'] = (options.get('cancel'), task)
        try:
            return await asyncio.to_thread(method, request, **options)
        except asyncio.CancelledError:
            task.cancel()
            raise

    def _call(self, request, on_event, request_id, timeout, cancel):
        if self._closing:  # fast check; _enter below is the authoritative one
            raise EngineClosedError()
        text = request if isinstance(request, str) else _dumps(request, 'request', True)
        payload = _encode(text, 'request')
        if on_event is not None and not callable(on_event):
            raise argument_error(TypeError, 'on_event must be callable')
        if isinstance(request_id, bool) or not isinstance(request_id, int) or not 0 <= request_id < 2 ** 64:
            raise argument_error(ValueError, 'request_id must be an unsigned 64-bit integer')
        lib = self._lib
        options = lib.dtl_default_call_options()
        if timeout is not None:
            if not math.isfinite(timeout) or not 0 <= timeout <= 86400:
                raise argument_error(ValueError, 'timeout must be 0..86400 seconds (0 or None for no deadline)')
            options.timeout_ms = math.ceil(timeout * 1000)  # 0 means no deadline
        tokens = [token for token in (cancel if isinstance(cancel, tuple) else (cancel,)) if token is not None]
        if not all(isinstance(token, CancellationToken) for token in tokens):
            raise argument_error(TypeError, 'cancel must be a CancellationToken')
        if any(token.cancelled for token in tokens):  # fails at once: no native call, no events
            raise LayaError(Status.CANCELLED, 'The call was cancelled before it started')
        flag = ctypes.c_uint32(0)
        if tokens:
            # A native flag read by the library itself: backend worker threads
            # poll it without taking the GIL.
            options.should_cancel = ctypes.cast(lib.dtl_cancel_flag, _native.CANCEL_CALLBACK)
            options.cancel_user_data = ctypes.addressof(flag)
        options.request_id = request_id
        if on_event is not None:
            def notify(event, _):
                try:
                    on_event(json.loads(event.decode('utf-8', 'replace')))
                except Exception:
                    pass  # Observer failures never change inference results.
            callback = _native.EVENT_CALLBACK(notify)  # kept alive until the call returns
            options.on_event = callback
        result, error = ctypes.c_void_p(), ctypes.c_void_p()
        self._enter()
        try:  # attach inside the try so a failure still leaves the count
            for token in tokens:
                token._attach(flag)
            status = lib.dtl_execute(self._handle, payload, ctypes.byref(options),
                                     ctypes.byref(result), ctypes.byref(error))
        finally:
            for token in tokens:
                token._detach(flag)
            self._leave()
        text = _take(lib, result)
        _check(lib, status, error)
        if text is None:
            raise LayaError(Status.INFERENCE_ERROR, 'Native result was null')
        return json.loads(text)

    def close(self):
        """Wait for running calls, then release the model. Idempotent.

        Called from inside one of this engine's own calls (an ``on_event``
        callback), it cannot wait for that call: it rejects new calls and
        returns, and the model is released when the running calls finish.
        """
        with self._state:
            self._closing = True
            if getattr(self._local, 'depth', 0):
                self._deferred = True
                return
            while self._active:
                self._state.wait()
            # Released under the lock, so a concurrent close() also returns only after it.
            handle, self._handle = self._handle, None
            if handle is not None:
                self._lib.dtl_close(handle)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        if getattr(self, '_handle', None) is not None and not self._active:
            self._lib.dtl_close(self._handle)
            self._handle = None
