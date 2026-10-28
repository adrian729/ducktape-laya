"""The evaluation tool's metrics and controls, with a stand-in engine (no model)."""
import unittest

from ducktape_laya import evaluate


class FakeEngine:
    """Answers from the state text: a noul and a two-option choice that favour the first option listed."""
    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        p = 0.9 if 'please' in request['state'] else 0.2
        answers = {}
        for name, q in request['questions'].items():
            if q['type'] == 'noul':
                answers[name] = {'noul': p}
            else:
                first = next(iter(q['criteria']))
                yes = p if first == 'ask' else p - 0.15
                answers[name] = {'choice': 'ask' if yes >= 0.5 else 'report',
                                 'probabilities': {'ask': yes, 'report': 1 - yes}}
        return {'answers': answers}


class Metrics(unittest.TestCase):
    def test_auc_counts_ties_as_half(self):
        self.assertEqual(evaluate.auc([0.1, 0.4, 0.35, 0.8], [False, False, True, True]), 0.75)
        self.assertEqual(evaluate.auc([0.5, 0.5], [True, False]), 0.5)
        self.assertIsNone(evaluate.auc([0.1, 0.2], [True, True]))

    def test_threshold_is_picked_on_the_other_half(self):
        # Picked on all eight, 0.8 would report recall 0.5 at precision 1. Halves {1, 2, 3, 6} and {0, 4, 5, 7}
        # pick 0.8 and 0.9, which find one of the other half's positives between them.
        scores = [0.9, 0.8, 0.7, 0.6, 0.3, 0.2, 0.75, 0.1]
        labels = [True, True, True, False, False, False, False, True]
        self.assertEqual([sorted(half) for half in evaluate.folds(labels)], [[1, 2, 3, 6], [0, 4, 5, 7]])
        self.assertEqual(evaluate.held_out(scores, labels, lambda s, y: evaluate._pick_threshold(s, y, 1.0)),
                         {'precision': 1.0, 'recall': 0.25, 'thresholds': [0.8, 0.9]})


class Evaluate(unittest.TestCase):
    EXAMPLES = [{'state': 'please run it', 'label': True, 'rule': False},
                {'state': 'could you please check', 'label': True, 'rule': True},
                {'state': 'done, tests pass', 'label': False, 'rule': False},
                {'state': 'pushed the branch', 'label': False, 'rule': False}]
    QUESTIONS = {'asks': {'type': 'noul', 'instructions': 'It asks the reader to act.'},
                 'pick': {'type': 'choice', 'instructions': 'How does it end?', 'positive': 'ask',
                          'criteria': {'ask': 'It asks', 'report': 'It reports'}}}

    def test_binary_report_strips_positive_and_runs_the_controls(self):
        engine = FakeEngine()
        report = evaluate.evaluate(engine, self.EXAMPLES, self.QUESTIONS, controls={'order', 'shuffle'})
        self.assertEqual(report['mode'], 'binary')
        self.assertIn('positive', self.QUESTIONS['pick'])  # the caller's questions are left as they were
        self.assertNotIn('positive', engine.requests[0]['questions']['pick'])
        self.assertEqual(report['questions']['asks']['auc'], 1.0)
        self.assertEqual(report['baselines']['rule'], {'precision': 1.0, 'recall': 0.5})
        order = report['questions']['pick']['order_control']
        self.assertEqual(list(engine.requests[4]['questions']['pick']['criteria']), ['report', 'ask'])
        self.assertAlmostEqual(order['mean_abs_difference'], 0.15)
        self.assertEqual(order['flip_rate'], 0.0)
        self.assertNotIn('order_control', report['questions']['asks'])
        self.assertEqual(report['questions']['asks']['shuffle_control']['auc_shuffled_words'], 1.0)
        self.assertEqual(len(engine.requests), 12)
        self.assertIn('rule OR score', evaluate.summary(report))

    def test_label_types_select_the_mode_and_are_checked(self):
        choice = [dict(e, label='ask' if e['label'] else 'report') for e in self.EXAMPLES]
        report = evaluate.evaluate(FakeEngine(), choice, {'pick': self.QUESTIONS['pick']})
        self.assertEqual(report['questions']['pick']['accuracy'], 1.0)
        self.assertEqual(report['baselines']['majority_accuracy'], 0.5)
        with self.assertRaisesRegex(SystemExit, 'needs "positive"'):
            evaluate.evaluate(FakeEngine(), self.EXAMPLES, {'pick': dict(self.QUESTIONS['pick'], positive=None)})
        with self.assertRaisesRegex(SystemExit, 'labels must be'):
            evaluate.evaluate(FakeEngine(), [dict(self.EXAMPLES[0], label=1), self.EXAMPLES[1]], self.QUESTIONS)


if __name__ == '__main__':
    unittest.main()
