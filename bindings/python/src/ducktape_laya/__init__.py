"""Local Laya GGUF decisions through the shared ducktape-laya C library.

Calls block while the model runs and release the GIL, so threads and
``asyncio.to_thread`` run other Python code meanwhile. Requests and results are
JSON: a ``str`` is sent as is, any other value is serialized with ``json.dumps``
(dict insertion order is kept).

Errors: native failures and invalid request text raise ``LayaError`` (with a C ABI
``status``); calls after ``close()`` raise ``EngineClosedError``; arguments the
binding checks itself (``threads``, ``timeout``, ``request_id``) raise
``ValueError``/``TypeError``; a missing or incompatible library raises ``OSError``.
"""
import asyncio
import ctypes
import enum
import json
import math
import os
import threading

from . import _native

__all__ = ['CancelToken', 'Engine', 'EngineClosedError', 'LayaError', 'Status', 'runtime_info']
__version__ = '0.1.0'


class Status(enum.IntEnum):
    OK = 0
    INVALID_ARGUMENT = 1
    MODEL_ERROR = 2
    INFERENCE_ERROR = 3
    OUT_OF_MEMORY = 4
    CANCELLED = 5
    DEADLINE_EXCEEDED = 6
    UNSUPPORTED_PLATFORM = 7


class LayaError(Exception):
    """A native failure; ``status`` is the C ABI status code."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class EngineClosedError(LayaError):
    """A call was made after ``close()``. Its ``status`` is ``INVALID_ARGUMENT``."""
    def __init__(self):
        super().__init__(Status.INVALID_ARGUMENT, 'Engine is closed')


class CancelToken:
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
            _lib = _native.load()
        return _lib


def _encode(text, what):
    if not isinstance(text, str):
        raise TypeError(f'{what} must be a str')
    if '\0' in text:
        raise LayaError(Status.INVALID_ARGUMENT, f'{what} contains a NUL character')
    try:
        return text.encode('utf-8')
    except UnicodeEncodeError:
        raise LayaError(Status.INVALID_ARGUMENT, f'{what} is not valid Unicode') from None


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


_OVERFLOW = ('reject', 'truncate_end', 'truncate_start', 'scan')


def _open_config(threads, context_tokens, temperatures, overflow=None):
    """Serialize the set open options to config JSON (``None`` when none are set)."""
    config = {}
    if threads != 0:
        if isinstance(threads, bool) or not isinstance(threads, int) or not 0 <= threads <= 256:
            raise ValueError('threads must be 0..256 (0 = automatic)')
        config['threads'] = threads
    if context_tokens is not None:
        if isinstance(context_tokens, bool) or not isinstance(context_tokens, int):
            raise TypeError('context_tokens must be an int')
        config['context_tokens'] = context_tokens
    if overflow is not None:
        if overflow not in _OVERFLOW:
            raise ValueError('overflow must be one of ' + ', '.join(_OVERFLOW))
        config['overflow'] = overflow
    if temperatures is not None:
        config['temperatures'] = dict(temperatures)
    if not config:
        return None
    return _encode(json.dumps(config, ensure_ascii=False, allow_nan=False), 'open config')


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
    def open(cls, model_path, *, threads=0, context_tokens=None, temperatures=None, overflow=None):
        """Load a local GGUF.

        ``threads`` 0 selects automatically (at most 4). ``context_tokens`` is the
        prompt capacity (default 512, at most the model's trained length).
        ``temperatures`` maps calibration keys such as ``"choice"`` or
        ``"noul.2"`` to positive overrides. ``overflow`` is the engine-wide policy for
        prompts over the token limit (``"reject"`` (default), ``"truncate_end"``,
        ``"truncate_start"`` or ``"scan"``; a request's own ``overflow`` wins).
        Unset options use the library defaults.
        """
        lib = _library()
        config = _open_config(threads, context_tokens, temperatures, overflow)
        path = _encode(os.fspath(model_path), 'model path')
        return cls._create(lib, False, path, config)

    @classmethod
    def open_router(cls, config, *, threads=0, context_tokens=None, temperatures=None, overflow=None):
        """Open a bounded registry of local models.

        ``config`` is a dict or JSON string:
        ``{"models": {"id": "path.gguf" | {"path": ..., "config": {...}}}, "max_loaded": 1}``.
        The open options apply to every route; a route's own ``config`` overrides
        them key by key.
        """
        lib = _library()
        text = config if isinstance(config, str) else json.dumps(config, ensure_ascii=False)
        return cls._create(lib, True, _encode(text, 'router config'),
                           _open_config(threads, context_tokens, temperatures, overflow))

    def _enter(self):
        with self._state:
            if self._closing:
                raise EngineClosedError()
            self._active += 1
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
        the call. ``timeout`` is in seconds (``None`` for no deadline; status 6 when
        it expires). ``cancel`` is a ``CancelToken``; cancelling it stops the call
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
        task = CancelToken()  # cancelling the task must not cancel a shared token
        options['cancel'] = (options.get('cancel'), task)
        try:
            return await asyncio.to_thread(method, request, **options)
        except asyncio.CancelledError:
            task.cancel()
            raise

    def _call(self, request, on_event, request_id, timeout, cancel):
        text = request if isinstance(request, str) else json.dumps(request, ensure_ascii=False, allow_nan=False)
        payload = _encode(text, 'request')
        if on_event is not None and not callable(on_event):
            raise TypeError('on_event must be callable')
        if not isinstance(request_id, int) or not 0 <= request_id < 2 ** 64:
            raise ValueError('request_id must be an unsigned 64-bit integer')
        lib = self._lib
        options = lib.dtl_default_call_options()
        if timeout is not None:
            milliseconds = math.ceil(timeout * 1000) if math.isfinite(timeout) else 0
            if not 1 <= milliseconds <= 86400000:
                raise ValueError('timeout must be positive and at most 86400 seconds')
            options.timeout_ms = milliseconds
        tokens = [token for token in (cancel if isinstance(cancel, tuple) else (cancel,)) if token is not None]
        if not all(isinstance(token, CancelToken) for token in tokens):
            raise TypeError('cancel must be a CancelToken')
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
        for token in tokens:
            token._attach(flag)
        try:
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
