"""Binding tests; real-model cases run when LAYA_MODEL names the GGUF."""
import asyncio
import dataclasses
import json
import os
import threading
import time
import unittest

import ducktape_laya as laya

MODEL = os.environ.get('LAYA_MODEL')
REQUEST = '{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}'
STRUCTURED = ('{"state":"Please refund me.","schema":{"type":"object","properties":'
              '{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}')
LONG = json.dumps([json.loads(STRUCTURED)] * 8)
real = unittest.skipUnless(MODEL, 'LAYA_MODEL is not set')


class LibraryChoice(unittest.TestCase):
    def test_bundled_library_loads_regardless_of_machine(self):
        # x64 Python under emulation on Windows ARM64 reports platform.machine() == 'ARM64'.
        import pathlib
        import tempfile
        from ducktape_laya import _native
        with tempfile.TemporaryDirectory() as directory:
            bundled = pathlib.Path(directory, _native._NAME)
            bundled.write_bytes(b'')
            self.assertEqual(_native._target('ARM64', 'win32', bundled), (str(bundled), True))
            bundled.unlink()
            with self.assertRaisesRegex(laya.LayaLoadError, 'Unsupported platform win32/ARM64: .*Linux x86-64 and arm64, macOS arm64 and x86-64 and Windows x64'):
                _native._target('ARM64', 'win32', bundled)
            self.assertEqual(_native._target('AMD64', 'win32', bundled), (_native._VERSIONED, False))

    def _open(self, plat, bundled, error=None, env=None, machine='AMD64'):
        """Runs the loader with a fake ctypes.CDLL; returns the calls it made and the result or error."""
        from unittest import mock
        from ducktape_laya import _native
        calls = []
        def cdll(name, **kwargs):
            calls.append((name, kwargs))
            if error:
                raise OSError(error)
            return 'library'
        with mock.patch.dict(os.environ, env or {}, clear=False):
            for variable in ('DUCKTAPE_LAYA_LIBRARY', 'DUCKTAPE_LAYA_LIB_DIR'):
                if variable not in (env or {}):
                    os.environ.pop(variable, None)
            try:
                return calls, _native._open(cdll, machine, plat, bundled)
            except laya.LayaLoadError as caught:
                return calls, caught

    def test_load_failures_are_status_7_and_oserrors(self):
        import pathlib
        import tempfile
        from ducktape_laya import _native
        with tempfile.TemporaryDirectory() as directory:
            bundled = pathlib.Path(directory, _native._NAME)
            # A bundled file that exists but does not load is reported as found, with the reason and a hint.
            bundled.write_bytes(b'')
            _, error = self._open('linux', bundled, 'version GLIBC_2.38 not found')
            self.assertIsInstance(error, laya.LayaError)
            self.assertIsInstance(error, OSError)
            self.assertEqual(error.status, laya.Status.UNSUPPORTED_PLATFORM)
            self.assertIn(f'The bundled library {bundled} was found but could not be loaded: version GLIBC_2.38 not found', str(error))
            self.assertIn('glibc 2.38+', str(error))
            _, error = self._open('win32', bundled, 'The specified module could not be found')
            self.assertIn('Visual C++ Redistributable', str(error))
            # Without a bundled file, the system loader's failure is "not found".
            bundled.unlink()
            _, error = self._open('linux', bundled, 'cannot open shared object file')
            self.assertEqual(error.status, laya.Status.UNSUPPORTED_PLATFORM)
            self.assertIn('not found', str(error))
            # An explicit directory without the library fails without trying any other location.
            calls, error = self._open('linux', bundled, env={'DUCKTAPE_LAYA_LIB_DIR': directory})
            self.assertEqual(calls, [])
            self.assertIn('DUCKTAPE_LAYA_LIB_DIR is set but', str(error))
            self.assertEqual(error.status, laya.Status.UNSUPPORTED_PLATFORM)
            # DUCKTAPE_LAYA_LIBRARY names the file itself and wins over the directory.
            file = pathlib.Path(directory, 'custom.so')
            file.write_bytes(b'')
            calls, result = self._open('linux', bundled, env={'DUCKTAPE_LAYA_LIBRARY': str(file),
                                                              'DUCKTAPE_LAYA_LIB_DIR': '/nonexistent'})
            self.assertEqual((calls, result), ([(str(file), {})], 'library'))
            # Relative overrides are refused without loading anything.
            for variable in ('DUCKTAPE_LAYA_LIBRARY', 'DUCKTAPE_LAYA_LIB_DIR'):
                calls, error = self._open('linux', bundled, env={variable: 'relative'})
                self.assertEqual(calls, [])
                self.assertIn(f'{variable} must be an absolute path', str(error))
                self.assertEqual(error.status, laya.Status.UNSUPPORTED_PLATFORM)

    def test_check_version(self):
        from ducktape_laya import _native
        _native.check_version('{"package_version":"1.2.3","abi_version":2}', '1.2.3')
        for info in ('{"package_version":"1.2.4"}', '{}', 'not json', '[]'):
            with self.assertRaisesRegex(laya.LayaLoadError, 'another copy of the library') as caught:
                _native.check_version(info, '1.2.3')
            self.assertEqual(caught.exception.status, laya.Status.UNSUPPORTED_PLATFORM)

    def test_windows_fallback_searches_only_safe_directories(self):
        import pathlib
        import tempfile
        from ducktape_laya import _native
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory, _native._NAME)
            calls, result = self._open('win32', missing)
            self.assertEqual(result, 'library')
            # LOAD_LIBRARY_SEARCH_DEFAULT_DIRS: never the current directory or PATH.
            self.assertEqual(calls, [(_native._VERSIONED, {'winmode': 0x1000})])
            calls, _ = self._open('linux', missing)
            self.assertEqual(calls, [(_native._VERSIONED, {})])

    def test_macos_loads_absolute_paths_only_and_checks_the_image(self):
        import pathlib
        import tempfile
        from unittest import mock
        from ducktape_laya import _native
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(_native, '_verify_image') as verify:
            bundled = pathlib.Path(directory, _native._NAME)
            bundled.write_bytes(b'')
            for machine in ('arm64', 'x86_64'):
                self.assertEqual(_native._target(machine, 'darwin', bundled), (str(bundled), True))
            bundled.unlink()
            with mock.patch.object(_native.os.path, 'isfile', return_value=False):
                for machine in ('arm64', 'x86_64'):
                    with self.assertRaisesRegex(laya.LayaLoadError, r'not found: .*/opt/homebrew/lib nor /usr/local/lib'):
                        _native._target(machine, 'darwin', bundled)
                calls, error = self._open('darwin', bundled, machine='arm64')
                self.assertEqual(calls, [])  # no bare-name load
                self.assertIsInstance(error, laya.LayaLoadError)
            installed = os.path.join('/usr/local/lib', _native._VERSIONED)  # the host's separator: this also runs on Windows
            with mock.patch.object(_native.os.path, 'isfile', side_effect=lambda p: p == installed):
                calls, result = self._open('darwin', bundled, machine='x86_64')
                self.assertEqual(calls, [(installed, {})])
                verify.assert_called_with('library', installed)
            # The explicit directory is loaded and verified by its absolute path.
            os.makedirs(os.path.join(directory, 'lib'))
            pathlib.Path(directory, 'lib', _native._NAME).write_bytes(b'')
            calls, _ = self._open('darwin', bundled, machine='arm64', env={'DUCKTAPE_LAYA_LIB_DIR': os.path.join(directory, 'lib')})
            self.assertEqual(calls, [(os.path.join(directory, 'lib', _native._NAME), {})])

    def test_macos_refuses_a_swapped_library(self):
        from unittest import mock
        from ducktape_laya import _native
        swapped = laya.LayaLoadError('Refusing the native ducktape-laya library: /a was requested but dtl_abi_version comes from /b; DYLD_LIBRARY_PATH')
        with mock.patch.object(_native, '_verify_image', side_effect=swapped):
            _, error = self._open('darwin', __import__('pathlib').Path(__file__), machine='arm64')
        self.assertIs(error, swapped)
        self.assertEqual(error.status, laya.Status.UNSUPPORTED_PLATFORM)



class Basics(unittest.TestCase):
    def test_runtime_info(self):
        self.assertEqual(laya.runtime_info()['abi_version'], 2)

    def test_rejects_nul_before_loading_the_library(self):
        from unittest import mock
        with mock.patch.object(laya, '_library', side_effect=AssertionError('library loaded')):
            for path in ('a\0b', b'a\0b'):
                with self.assertRaises(laya.LayaError) as caught:
                    laya.Engine.open(path)
                self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)

    @unittest.skipIf(os.name == 'nt', 'POSIX paths are raw bytes')
    def test_paths_reach_the_core_as_bytes(self):
        import pathlib
        raw = os.fsencode(os.path.join(os.path.dirname(__file__), 'missing-\udcff.gguf'))
        for path in (raw, os.fsdecode(raw), pathlib.PurePath(os.fsdecode(raw)), b'/nonexistent/m.gguf'):
            with self.assertRaises(laya.LayaError) as caught:
                laya.Engine.open(path)
            self.assertEqual(caught.exception.status, laya.Status.MODEL_ERROR)
        with self.assertRaises(TypeError):
            laya.Engine.open(42)
        with self.assertRaises(laya.LayaError) as caught:  # a lone surrogate has no bytes
            laya.Engine.open('missing-\ud800.gguf')
        self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)

    def test_constructor_is_private(self):
        with self.assertRaises(TypeError):
            laya.Engine()

    def test_observer_failures_are_contained(self):
        with laya.Engine.open_router({'models': {'unused': __file__}}, threads=1) as router:
            calls = []
            def explode(event):
                calls.append(event)
                raise RuntimeError('observer failure')
            with self.assertRaises(laya.LayaError) as caught:
                router.execute('{}', request_id=1, on_event=explode)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
            self.assertEqual([event.kind for event in calls], [laya.EventKind.START, laya.EventKind.ERROR])

    def _child(self, code, **env):
        import subprocess, sys
        environment = {key: value for key, value in os.environ.items() if not key.startswith('DUCKTAPE_LAYA_LIB')}
        return subprocess.run([sys.executable, '-c', code], env=dict(environment, **env), capture_output=True, text=True)

    def test_explicit_library_is_honoured(self):
        info = 'import ducktape_laya; ducktape_laya.runtime_info()'
        missing = os.path.abspath(os.sep + 'nonexistent')  # absolute on this OS (with a drive on Windows)
        for variable, value, message in (('DUCKTAPE_LAYA_LIB_DIR', missing, 'DUCKTAPE_LAYA_LIB_DIR is set'),
                                         ('DUCKTAPE_LAYA_LIBRARY', os.path.join(missing, 'lib.so'), 'DUCKTAPE_LAYA_LIBRARY is set'),
                                         ('DUCKTAPE_LAYA_LIBRARY', 'relative.so', 'DUCKTAPE_LAYA_LIBRARY must be an absolute path')):
            result = self._child(info, **{variable: value})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('ducktape_laya.LayaLoadError: ' + message, result.stderr)

    def test_argument_errors_come_before_the_library_loads(self):
        # With a relative override the library can never load, so only an argument error can surface.
        result = self._child('import ducktape_laya as l\n'
                             'try:\n    l.Engine.open("a\\0b")\nexcept l.LayaError as e:\n    print(type(e).__name__, e.status.name)',
                             DUCKTAPE_LAYA_LIBRARY='relative.so')
        self.assertEqual(result.stdout.strip(), 'LayaError INVALID_ARGUMENT', result.stderr)

    def test_open_options_are_conversions_only(self):
        for kwargs in ({'threads': True}, {'threads': '2'}, {'context_tokens': '64'}, {'max_windows_per_call': 1.5}):
            with self.assertRaises(TypeError) as caught:
                laya.Engine.open('x.gguf', **kwargs)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
        # Range and name errors are the core's: they are sent as given, 0 and negative numbers included.
        text = lambda **kwargs: laya._open_config(*(kwargs.get(k) for k in (
            'threads', 'context_tokens', 'temperatures', 'overflow', 'max_windows_per_call')))
        self.assertIsNone(text())
        self.assertEqual(json.loads(text(threads=0, max_windows_per_call=0, overflow='bogus', context_tokens=-1)),
                         {'threads': 0, 'context_tokens': -1, 'overflow': 'bogus', 'max_windows_per_call': 0})
        self.assertEqual(list(json.loads(text(temperatures={'noul': 1.5}, max_windows_per_call=64))),
                         ['temperatures', 'max_windows_per_call'])
        with self.assertRaises(laya.LayaError) as caught:  # a native status 1 arrives as the binding's error
            laya.Engine.open_router({'models': {'unused': __file__}}, temperatures={'x': 'y'})
        self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)

    def test_status_is_typed(self):
        self.assertIs(laya.LayaError(1, 'x').status, laya.Status.INVALID_ARGUMENT)
        self.assertIs(laya.LayaError(99, 'x').status, laya.Status.INFERENCE_ERROR)

    def test_error_order(self):
        router = laya.Engine.open_router({'models': {'unused': __file__}})
        events = []
        cancelled = laya.CancellationToken()
        cancelled.cancel()
        try:
            # Argument errors beat an already-cancelled token; a cancelled token fails with no native call and no events.
            with self.assertRaises(ValueError):
                router.execute(REQUEST, timeout=-1, cancel=cancelled, on_event=events.append)
            with self.assertRaises(laya.LayaError) as caught:
                router.execute('{}', cancel=cancelled, on_event=events.append)
            self.assertEqual(caught.exception.status, laya.Status.CANCELLED)
            self.assertEqual(events, [])
        finally:
            router.close()
        # Closed beats everything else.
        with self.assertRaises(laya.EngineClosedError):
            router.execute(REQUEST, timeout=-1, cancel=cancelled)

    def test_router_config_rejects_nan(self):
        with self.assertRaises(ValueError) as caught:
            laya.Engine.open_router({'models': {'unused': __file__}, 'max_loaded': float('nan')})
        self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)

    def test_unserializable_request_is_invalid(self):
        engine = laya.Engine.open_router({'models': {'unused': __file__}})
        try:
            for request in ({'state': {'amount': float('nan')}, 'questions': {}}, {'state': {1, 2}, 'questions': {}}):
                with self.subTest(request=repr(request)), self.assertRaises(laya.LayaError) as caught:
                    engine.execute(request)
                self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
        finally:
            engine.close()

    def test_events_are_typed(self):
        with laya.Engine.open_router({'models': {'unused': __file__}}, threads=1) as router:
            for request_id in (0, 7, 2 ** 64 - 1):
                events = []
                with self.assertRaises(laya.LayaError):
                    router.execute('{}', request_id=request_id, on_event=events.append)
                start, error = events
                self.assertEqual((start.kind, error.kind), (laya.EventKind.START, laya.EventKind.ERROR))
                self.assertEqual((start.request_id, error.request_id), (request_id, request_id))
                self.assertEqual((start.status, error.status), (laya.Status.OK, laya.Status.INVALID_ARGUMENT))
                self.assertEqual(start.elapsed_ms, 0)
                self.assertIsInstance(error.elapsed_ms, float)
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    start.kind = laya.EventKind.END
        self.assertEqual(laya.EventKind.START, 'start')
        self.assertEqual(laya._parse_event(b'{"event":"end","request_id":3,"status":0,"elapsed_ms":2}'),
                         laya.Event(laya.EventKind.END, 3, laya.Status.OK, 2.0))
        for bad in (b'', b'{', b'{"event":"bogus","request_id":1,"status":0,"elapsed_ms":0}', b'{"event":"end"}'):
            self.assertIsNone(laya._parse_event(bad))

    def test_timeout_conversions(self):
        with laya.Engine.open_router({'models': {'unused': __file__}}, threads=1) as router:
            for bad in (-1, -0.001, float('inf'), float('-inf'), float('nan')):
                with self.assertRaises(ValueError) as caught:
                    router.execute('{}', timeout=bad)
                self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
            for bad in ('1', True):
                with self.assertRaises(TypeError):
                    router.execute('{}', timeout=bad)
            # There is no upper bound: a huge timeout reaches the native core, which then rejects the request.
            for huge in (86401, 10 ** 9, 1e300, 10 ** 400):
                events = []
                with self.assertRaises(laya.LayaError) as caught:
                    router.execute('{}', timeout=huge, on_event=events.append)
                self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
                self.assertEqual(len(events), 2)

    def test_deadline_counts_from_entry(self):
        with laya.Engine.open_router({'models': {'unused': __file__}}, threads=1) as router:
            events = []
            # Entered 10 s ago with a 1 s timeout: it fails at once, with no native call and no events.
            with self.assertRaises(laya.LayaError) as caught:
                router._call('{}', events.append, 0, 1, None, False, time.monotonic() - 10)
            self.assertEqual(caught.exception.status, laya.Status.DEADLINE_EXCEEDED)
            self.assertEqual(events, [])
            with self.assertRaises(laya.LayaError) as caught:  # not yet expired: reaches the core
                router._call('{}', events.append, 0, 60, None, False, time.monotonic() - 10)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
            self.assertEqual(len(events), 2)

    def test_raw_must_be_a_bool(self):
        with laya.Engine.open_router({'models': {'unused': __file__}}, threads=1) as router:
            with self.assertRaises(TypeError) as caught:
                router.execute('{}', raw=1)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)

    def test_cancel_argument(self):
        with laya.Engine.open_router({'models': {'unused': __file__}}, threads=1) as router:
            cancelled = laya.CancellationToken()
            cancelled.cancel()
            with self.assertRaises(laya.LayaError) as caught:
                router.execute(REQUEST, cancel=cancelled)
            self.assertEqual(caught.exception.status, laya.Status.CANCELLED)
            with self.assertRaises(TypeError):
                router.execute(REQUEST, cancel=threading.Event())

    def test_router_info(self):
        with laya.Engine.open_router({'models': {'unused': __file__}}, threads=1) as router:
            self.assertEqual(router.info()['abi_version'], 2)

    def test_close_inside_callback(self):
        # The running call finishes; the model is released when it returns.
        for cancel_pending in (False, True):
            engine = laya.Engine.open_router({'models': {'unused': __file__}}, threads=1)
            with self.assertRaises(laya.LayaError):
                engine.execute('{}', on_event=lambda _: engine.close(cancel_pending=cancel_pending))
            self.assertIsNone(engine._handle)
            with self.assertRaises(laya.EngineClosedError):
                engine.execute(REQUEST)
            engine.close(cancel_pending=cancel_pending)

    def test_close_cancel_pending_is_keyword_only(self):
        engine = laya.Engine.open_router({'models': {'unused': __file__}}, threads=1)
        with self.assertRaises(TypeError):
            engine.close(True)
        engine.close(cancel_pending=True)
        engine.close()

    def test_close_is_idempotent(self):
        router = laya.Engine.open_router({'models': {'unused': __file__}}, threads=1)
        router.close()
        router.close()
        with self.assertRaises(laya.EngineClosedError) as caught:
            router.execute(REQUEST)
        self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)


@real
class RealModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = laya.Engine.open(MODEL, threads=2)
        cls.baseline = cls.engine.execute(REQUEST)
        events = []
        cls.engine.execute(LONG, on_event=events.append)
        cls.long_ms = events[-1].elapsed_ms  # an uncancelled run, to show cancelled calls stop early

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_features(self):
        engine = self.engine
        single = engine.execute(STRUCTURED)
        self.assertEqual(single['values'], {'refund': True})
        self.assertEqual(engine.execute(json.loads(STRUCTURED)), single)
        events = []
        self.assertEqual(engine.execute([json.loads(STRUCTURED)] * 2, request_id=21, on_event=events.append),
                         [single, single])
        self.assertEqual([event.kind for event in events], [laya.EventKind.START, laya.EventKind.END])
        self.assertEqual([event.request_id for event in events], [21, 21])
        self.assertEqual(events[1].status, laya.Status.OK)
        self.assertGreater(events[1].elapsed_ms, 0)
        raw = engine.execute(STRUCTURED, raw=True)
        self.assertIsInstance(raw, str)
        self.assertEqual(json.loads(raw), single)
        # Reentry from an observer runs on the same thread and engine.
        nested = []
        engine.execute(STRUCTURED, request_id=23, on_event=lambda _: nested.append(engine.execute(STRUCTURED)))
        self.assertEqual(nested, [single, single])
        info = engine.info()
        self.assertEqual(info['model']['decision_type'], 'laya')
        self.assertIn('context_tokens', info['model'])
        self.assertIn('trained_context_tokens', info['model'])
        self.assertNotIn('max_tokens', info['model'])
        self.assertIn('default_context_tokens', info['limits'])
        self.assertEqual(engine.execute(REQUEST), self.baseline)

    def test_open_options_reach_the_model(self):
        with laya.Engine.open(MODEL, threads=1, context_tokens=64, temperatures={'noul': 1.0}) as engine:
            self.assertEqual(engine.info()['model']['context_tokens'], 64)
            self.assertIn('answers', engine.execute(REQUEST))

    def test_router(self):
        with laya.Engine.open_router({'models': {'english': MODEL}, 'max_loaded': 1}, threads=2) as router:
            routed = router.execute(STRUCTURED.replace('{', '{"model":"english",', 1))
            self.assertEqual(routed['model'], 'english')

    def test_cancellation_and_deadline(self):
        engine = self.engine
        with self.assertRaises(laya.LayaError) as caught:
            engine.execute(REQUEST, timeout=0.001)
        self.assertEqual(caught.exception.status, laya.Status.DEADLINE_EXCEEDED)
        self.assertEqual(engine.execute(REQUEST, cancel=laya.CancellationToken()), self.baseline)
        self.assertEqual(engine.execute(REQUEST, timeout=0), self.baseline)  # 0 means no deadline
        self.assertEqual(engine.execute(REQUEST, timeout=10 ** 9), self.baseline)  # no upper bound

    def test_cancellation_stops_the_native_call(self):
        token, events = laya.CancellationToken(), []
        def observe(event):
            events.append(event)
            if event.kind is laya.EventKind.START:
                token.cancel()
        with self.assertRaises(laya.LayaError) as caught:
            self.engine.execute(LONG, cancel=token, on_event=observe)
        self.assertEqual(caught.exception.status, laya.Status.CANCELLED)
        self.assertEqual([event.kind for event in events], [laya.EventKind.START, laya.EventKind.ERROR])
        self.assertEqual(events[1].status, laya.Status.CANCELLED)
        self.assertLess(events[1].elapsed_ms, self.long_ms / 4)
        self.assertEqual(self.engine.execute(REQUEST), self.baseline)

    def test_close_cancel_pending(self):
        engine = laya.Engine.open(MODEL, threads=2)
        started, events, failures = threading.Event(), [], []
        def observe(event):
            events.append(event)
            started.set()
        def run():
            try:
                engine.execute(LONG, on_event=observe)
            except laya.LayaError as error:
                failures.append(error)
        worker = threading.Thread(target=run)
        worker.start()
        started.wait()
        engine.close(cancel_pending=True)
        worker.join()
        self.assertEqual([error.status for error in failures], [laya.Status.CANCELLED])
        self.assertEqual(events[-1].status, laya.Status.CANCELLED)
        self.assertLess(events[-1].elapsed_ms, self.long_ms / 4)
        self.assertIsNone(engine._handle)

    def test_threads_and_async(self):
        results, errors = [], []
        def call():
            try:
                results.append(self.engine.execute(REQUEST))
            except BaseException as error:
                errors.append(error)
        threads = [threading.Thread(target=call) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(results, [self.baseline] * 3)

        async def main():
            self.assertEqual(await self.engine.execute_async(REQUEST), self.baseline)
            self.assertEqual(json.loads(await self.engine.execute_async(REQUEST, raw=True)), self.baseline)
        asyncio.run(main())
        self.assertEqual(self.engine.execute(REQUEST), self.baseline)

    def test_async_task_cancellation_stops_the_native_call(self):
        events, started, finished = [], threading.Event(), threading.Event()
        def observe(event):
            events.append(event)
            (started if event.kind is laya.EventKind.START else finished).set()
        shared = laya.CancellationToken()
        async def main():
            task = asyncio.ensure_future(self.engine.execute_async(LONG, on_event=observe, cancel=shared))
            await asyncio.to_thread(started.wait)  # the native call is running
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.to_thread(finished.wait)  # the native call itself reports how it ended
        asyncio.run(main())
        self.assertFalse(shared.cancelled)
        self.assertEqual(events[-1].kind, laya.EventKind.ERROR)
        self.assertEqual(events[-1].status, laya.Status.CANCELLED)
        self.assertLess(events[-1].elapsed_ms, self.long_ms / 4)
        self.assertEqual(self.engine.execute(REQUEST), self.baseline)

    def test_close_waits_for_running_calls(self):
        engine = laya.Engine.open(MODEL, threads=2)
        started, results = threading.Event(), []
        worker = threading.Thread(target=lambda: results.append(
            engine.execute(REQUEST, on_event=lambda _: started.set())))
        worker.start()
        started.wait()
        engine.close()
        worker.join()
        self.assertEqual(results, [self.baseline])
        with self.assertRaises(laya.LayaError):
            engine.execute(REQUEST)


if __name__ == '__main__':
    unittest.main()
