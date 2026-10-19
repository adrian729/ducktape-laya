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
real = unittest.skipUnless(MODEL, 'LAYA_MODEL is not set')


class Basics(unittest.TestCase):
    def test_runtime_info(self):
        self.assertEqual(laya.runtime_info()['abi_version'], 1)

    def test_rejects_bad_path(self):
        with self.assertRaises(laya.LayaError) as caught:
            laya.Engine.open('missing.gguf', 2)
        self.assertNotEqual(caught.exception.status, 0)

    def test_rejects_nul(self):
        with self.assertRaises(laya.LayaError) as caught:
            laya.Engine.open('a\0b', 2)
        self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)

    def test_constructor_is_private(self):
        with self.assertRaises(TypeError):
            laya.Engine()

    def test_observer_failures_are_contained(self):
        with laya.Engine.open_router({'models': {'unused': __file__}}, 1) as router:
            calls = []
            def explode(event):
                calls.append(event)
                raise RuntimeError('observer failure')
            with self.assertRaises(laya.LayaError) as caught:
                router.execute('{}', request_id=1, on_event=explode)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
            self.assertEqual([event['event'] for event in calls], ['start', 'error'])

    def test_close_is_idempotent(self):
        router = laya.Engine.open_router({'models': {'unused': __file__}}, 1)
        router.close()
        router.close()
        with self.assertRaises(laya.LayaError):
            router.execute(REQUEST)


@real
class RealModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = laya.Engine.open(MODEL, 2)
        cls.baseline = cls.engine.execute(REQUEST)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_features(self):
        engine = self.engine
        tagged = REQUEST.replace('{', '{"metadata":"caller tag",', 1)
        self.assertEqual(engine.decide(tagged), engine.decide(REQUEST))
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
        self.assertEqual(engine.info()['model']['decision_type'], 'laya')
        self.assertEqual(engine.execute(REQUEST), self.baseline)

    def test_close_inside_callback(self):
        engine = laya.Engine.open(MODEL, 2)
        # The running call finishes; the model is released when it returns.
        self.assertEqual(engine.execute(REQUEST, on_event=lambda _: engine.close()), self.baseline)
        self.assertIsNone(engine._handle)
        with self.assertRaises(laya.LayaError):
            engine.execute(REQUEST)
        engine.close()

    def test_router(self):
        with laya.Engine.open_router({'models': {'english': MODEL}, 'max_loaded': 1}, 2) as router:
            routed = router.execute(STRUCTURED.replace('{', '{"model":"english",', 1))
            self.assertEqual(routed['model'], 'english')
            with self.assertRaises(laya.LayaError) as caught:
                router.execute(STRUCTURED)
            self.assertEqual(caught.exception.status, laya.Status.INVALID_ARGUMENT)
            self.assertEqual(router.info()['abi_version'], 1)

    def test_cancellation_and_deadline(self):
        engine = self.engine
        cancelled = threading.Event()
        cancelled.set()
        for method in (engine.execute, engine.decide):
            with self.assertRaises(laya.LayaError) as caught:
                method(REQUEST, cancel=cancelled)
            self.assertEqual(caught.exception.status, laya.Status.CANCELLED)
        with self.assertRaises(laya.LayaError) as caught:
            engine.execute(REQUEST, timeout=0.001)
        self.assertEqual(caught.exception.status, laya.Status.DEADLINE_EXCEEDED)
        self.assertEqual(engine.execute(REQUEST), self.baseline)
        self.assertEqual(engine.execute(REQUEST, cancel=threading.Event()), self.baseline)
        with self.assertRaises(ValueError):
            engine.execute(REQUEST, timeout=0)

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
        engine = laya.Engine.open(MODEL, 2)
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
