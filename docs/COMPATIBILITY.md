# Compatibility and release policy

Version 0.1.0 is released from tag `v0.1.0` (candidate `b375b04`). A passing demo or a model-less
test does not establish release readiness. Runtime/model combinations are identified
by `release/compatibility.json`, immutable model manifests and applied patch hashes.

The C ABI owns shared behavior. Additive functions/status codes keep the ABI
version; changes to existing layouts, ownership or semantics require a new ABI and
SONAME. The current ABI is 2 (`libducktape_laya.so.1`).
Bindings follow package semantic versions, and released artifacts must be immutable.
Before 1.0, breaking changes require a minor version and migration notes; after 1.0,
a major version. Do not silently replace artifacts under an existing version.

Existing status numbers, ordering, primitive probability interpretation, overflow
rejection, no-partial-result behavior and JSON field meanings are compatibility
contracts. Introspection may add fields; consumers ignore unknown fields there.
Requests are strict: unsupported fields are rejected. New model capabilities require
validation and explicit adapter support, not just an unchanged file extension.

A release identifies its architecture, CPU features, system-library floor, tested
Linux userspaces, Node/Electron/browser versions and backend. The same model may
have numeric differences between CPU/GPU/ISA profiles; bindings using the same
native binary and settings must agree exactly. Each backend must agree with
upstream processing within its declared strict tolerance. Cross-profile probability
and discrete-decision drift is reported, without an interchangeability guarantee.
Browser delivery remains experimental pending full qualification. Unvalidated platforms must not be
advertised as supported merely because the source can compile there.

Release acceptance includes actual artifact installation, model integrity, ABI and
cross-binding checks, upstream-reference agreement, sanitizer/resource regressions,
platform coverage, license/provenance inventory and reproducibility. Failed and
unrun gates remain visible. Public publication, signing keys, security contact and
release hosting are separate deployment decisions, not silently inferred from
local implementation work. See [SECURITY.md](../SECURITY.md) and [PLAN.md](PLAN.md) for boundaries.

## Migrating from 0.1.0 (ABI 1) to ABI 2

The next release is breaking. What changed and what to do:

- **One handle type.** `dtl_router`, `dtl_router_open`, `dtl_router_execute*` and
  `dtl_router_close` are gone: `dtl_open_router` returns a `dtl_engine`, and
  `dtl_execute`, `dtl_info` and `dtl_close` work on either kind.
- **One call function.** `dtl_decide`, `dtl_decide_observed`,
  `dtl_decide_with_options` and `dtl_execute_with_options` are gone. Call
  `dtl_execute(engine, request, options, &result, &error)`. The observer moved
  into `dtl_call_options` (`request_id`, `on_event`, `event_user_data`), so
  `dtl_observer` is gone too, and the cancellation user data is now
  `cancel_user_data`.
- **No lenient decide.** The legacy primitive API ignored unknown top-level
  request fields; every request is now strict, so remove fields such as
  `metadata` before sending, or keep them outside the request.
- **Open config instead of `dtl_options`.** `dtl_open(path, config_json, ...)`
  takes an optional JSON object (`threads`, `context_tokens`, `temperatures`);
  `dtl_options` and `dtl_default_options` are gone.
- **Library name.** The SONAME is now `libducktape_laya.so.1`; rebuild and relink.
- **Results.** Every answer has `answer_confidence` (the probability of the
  reported answer) and `confidence` (`1 − H(p)/log k`). The old `choice`/`score`
  `confidence` formulas are replaced; `noul` answers gained both fields.
- **Diagnostics.** Model info reports `context_tokens` and
  `trained_context_tokens` instead of `max_tokens`; the runtime limit
  `tokens_per_question` became `default_context_tokens`.
- **Bindings.** Every binding dropped its `decide` methods (Node also
  `decideStructured` and `decideBatch`); `execute` takes one request or an
  array. Open options gained context tokens and temperatures. Per-binding
  details are in each binding's README.
