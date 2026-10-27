"""Binding tests; real-model cases run when LAYA_MODEL names the GGUF."""
import asyncio
import json
import os
import threading
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
            with self.assertRaisesRegex(laya.LayaLoadError, 'Unsupported platform win32/ARM64: .*Linux x86-64 and arm64 and Windows x64'):
                _native._target('ARM64', 'win32', bundled)
            self.assertEqual(_native._target('AMD64', 'win32', bundled), (_native._VERSIONED, False))

    def _open(self, plat, bundled, error=None, env=None):
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
            if not env:
                os.environ.pop('DUCKTAPE_LAYA_LIB_DIR', None)
            try:
                return calls, _native._open(cdll, 'AMD64', plat, bundled)
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


class Basics(unittest.TestCase):
    def test_runtime_info(self):
        self.assertEqual(laya.runtime_info()['abi_version'], 2)

    def test_rejects_bad_path(self):
        with self.assertRaises(laya.LayaError) as caught:
            laya.Engine.open('missing.gguf', threads=2)
        self.assertNotEqual(caught.exception.status, 0)

    def test_rejects_nul(self):
        with self.assertRaises(laya.LayaError) as caught:
            laya.Engine.open('a\0b', threads=2)
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
            self.assertEqual([event['event'] for event in calls], ['start', 'error'])

    def test_explicit_library_dir_is_honoured(self):
        import subprocess, sys
        env = dict(os.environ, DUCKTAPE_LAYA_LIB_DIR='/nonexistent')
        result = subprocess.run([sys.executable, '-c', 'import ducktape_laya; ducktape_laya.runtime_info()'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DUCKTAPE_LAYA_LIB_DIR is set', result.stderr)
        self.assertIn('ducktape_laya.LayaLoadError: DUCKTAPE_LAYA_LIB_DIR is set', result.stderr)

    def test_invalid_open_options(self):
        for kwargs in ({'threads': -1}, {'threads': 257}, {'threads': True}):
            with self.assertRaises(ValueError):
                laya.Engine.open('x.gguf', **kwargs)
        with self.assertRaises(TypeError):
            laya.Engine.open('x.gguf', context_tokens='64')
        with self.assertRaises(laya.LayaError) as caught:  # unknown config key
            laya.Engine.open_router({'models': {'unused': __file__}}, temperatures={'x': 'y'})
        self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)

    def test_router_config_rejects_nan(self):
        with self.assertRaises(ValueError):
            laya.Engine.open_router({'models': {'unused': __file__}, 'max_loaded': float('nan')})

    def test_unserializable_request_is_invalid(self):
        engine = laya.Engine.open_router({'models': {'unused': __file__}})
        try:
            for request in ({'state': {'amount': float('nan')}, 'questions': {}}, {'state': {1, 2}, 'questions': {}}):
                with self.subTest(request=repr(request)), self.assertRaises(laya.LayaError) as caught:
                    engine.execute(request)
                self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
        finally:
            engine.close()

    def test_router_auto_validation(self):
        def route(**extra):
            return dict({'models': {'a': __file__}}, **extra)
        for bad in ({'english': 'a'}, {'english': 'a', 'default': 'missing'},
                    {'english': 'a', 'default': 'a', 'extra': 'a'}):
            with self.assertRaises(laya.LayaError) as caught:
                laya.Engine.open_router(route(auto=bad), threads=1)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
        with self.assertRaises(laya.LayaError):
            laya.Engine.open_router({'models': {'auto': __file__}}, threads=1)
        request = json.loads(REQUEST)
        # Rejected before any model is loaded.
        with laya.Engine.open_router(route(), threads=1) as plain, \
                laya.Engine.open_router(route(auto={'english': 'a', 'default': 'a'}), threads=1) as auto:
            for engine, extra in ((plain, {'model': 'auto'}), (auto, {'lang': 'en'}),
                                  (auto, {'model': 'a', 'lang': 'en'}), (auto, {'model': 'auto', 'lang': ''})):
                with self.assertRaises(laya.LayaError) as caught:
                    engine.execute(dict(request, **extra))
                self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT, extra)

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

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_features(self):
        engine = self.engine
        tagged = REQUEST.replace('{', '{"metadata":"caller tag",', 1)
        with self.assertRaises(laya.LayaError) as caught:
            engine.execute(tagged)
        self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
        single = engine.execute(STRUCTURED)
        self.assertEqual(single['values'], {'refund': True})
        self.assertEqual(engine.execute(json.loads(STRUCTURED)), single)
        events = []
        self.assertEqual(engine.execute([json.loads(STRUCTURED)] * 2, request_id=21, on_event=events.append),
                         [single, single])
        self.assertEqual([event['event'] for event in events], ['start', 'end'])
        self.assertEqual(events[1]['request_id'], 21)
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

    def test_open_options(self):
        with laya.Engine.open(MODEL, threads=1, context_tokens=64,
                              temperatures={'noul': 1.0}) as engine:
            self.assertEqual(engine.info()['model']['context_tokens'], 64)
            self.assertIn('answers', engine.execute(REQUEST))
            # Per-request max_tokens is bounded by the engine's context_tokens.
            with self.assertRaises(laya.LayaError) as caught:
                engine.execute(dict(json.loads(REQUEST), max_tokens=65))
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
        for kwargs in ({'context_tokens': 1}, {'temperatures': {'noul': -1.0}},
                       {'temperatures': {'bogus': 1.0}}):
            with self.assertRaises(laya.LayaError) as caught:
                laya.Engine.open(MODEL, threads=1, **kwargs)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT, kwargs)
        with self.assertRaises(laya.LayaError) as caught:
            laya.Engine.open(MODEL, threads=1, context_tokens=10 ** 9)
        self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
        for threads in (-1, 257):
            with self.assertRaises(ValueError):
                laya.Engine.open(MODEL, threads=threads)

    def test_open_overflow(self):
        long_request = {'state': 'refund ' * 800, 'questions': {'refund': {
            'type': 'noul', 'instructions': 'Is a refund requested?'}}}
        # The policy reaches the engine: the default (reject) would refuse this request.
        with laya.Engine.open(MODEL, threads=2, overflow='truncate_end') as engine:
            self.assertIn('answers', engine.execute(long_request))
        with self.assertRaises(ValueError):
            laya.Engine.open(MODEL, threads=2, overflow='bogus')

    def test_request_settings_rejected(self):
        request = json.loads(REQUEST)
        for extra in ({'temperatures': {'noul': 0}}, {'max_tokens': 0}):
            with self.assertRaises(laya.LayaError) as caught:
                self.engine.execute(dict(request, **extra))
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT, extra)

    def test_close_inside_callback(self):
        engine = laya.Engine.open(MODEL, threads=2)
        # The running call finishes; the model is released when it returns.
        self.assertEqual(engine.execute(REQUEST, on_event=lambda _: engine.close()), self.baseline)
        self.assertIsNone(engine._handle)
        with self.assertRaises(laya.EngineClosedError):
            engine.execute(REQUEST)
        engine.close()

    def test_router(self):
        with laya.Engine.open_router({'models': {'english': MODEL}, 'max_loaded': 1}, threads=2) as router:
            routed = router.execute(STRUCTURED.replace('{', '{"model":"english",', 1))
            self.assertEqual(routed['model'], 'english')
            with self.assertRaises(laya.LayaError) as caught:
                router.execute(STRUCTURED)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
            self.assertEqual(router.info()['abi_version'], 2)

    def test_cancellation_and_deadline(self):
        engine = self.engine
        cancelled = laya.CancelToken()
        cancelled.cancel()
        with self.assertRaises(laya.LayaError) as caught:
            engine.execute(REQUEST, cancel=cancelled)
        self.assertEqual(caught.exception.status, laya.Status.CANCELLED)
        with self.assertRaises(laya.LayaError) as caught:
            engine.execute(REQUEST, timeout=0.001)
        self.assertEqual(caught.exception.status, laya.Status.DEADLINE_EXCEEDED)
        self.assertEqual(engine.execute(REQUEST), self.baseline)
        self.assertEqual(engine.execute(REQUEST, cancel=laya.CancelToken()), self.baseline)
        self.assertEqual(engine.execute(REQUEST, timeout=0), self.baseline)  # 0 means no deadline
        for bad in (-1, float('inf'), float('nan'), 86401):
            with self.assertRaises(ValueError):
                engine.execute(REQUEST, timeout=bad)
        with self.assertRaises(TypeError):
            engine.execute(REQUEST, cancel=threading.Event())
        # Cancelled from another thread while the call runs (or is about to).
        token, started = laya.CancelToken(), threading.Event()
        threading.Thread(target=lambda: (started.wait(), token.cancel())).start()
        with self.assertRaises(laya.LayaError) as caught:
            engine.execute(LONG, cancel=token, on_event=lambda _: started.set())
        self.assertEqual(caught.exception.status, laya.Status.CANCELLED)
        self.assertEqual(engine.execute(REQUEST), self.baseline)

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
            task = asyncio.ensure_future(self.engine.execute_async(REQUEST))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        asyncio.run(main())
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
