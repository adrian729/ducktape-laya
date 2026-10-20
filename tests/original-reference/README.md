# Reference cases from the original Laya

Request cases and the results the original Laya implementation produced for
them, used by `tests/original-reference.py` to measure how closely
ducktape-laya follows the model's authors.

- Source: https://github.com/NandhaKishorM/laya, commit
  `ffc142012af63d015fa19f05f29f9c5ff329a2cc`, files
  `laya-rust/tests/golden/<model>/case_*.json` and `meta.json`, for the
  `english`, `multilingual` and `typed-decisions` checkpoints.
- Generated upstream by `tools/dump_golden.py` with laya 0.4.0 on ONNX Runtime
  1.30.0 (see `english/meta.json`).
- Modified: each case is reduced to `name`, `state`, `questions` and `result`;
  the upstream tensors (`batch`, `logits`, `act_logits`) are dropped. Probe
  files that are not single requests are omitted.
- License: Apache License 2.0, copyright the Laya authors. The license text is
  in `licenses/Apache-2.0.txt`. These files are test data only and are not part
  of any shipped artifact.
