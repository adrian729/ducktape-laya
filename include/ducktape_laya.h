#ifndef DUCKTAPE_LAYA_H
#define DUCKTAPE_LAYA_H
#include <stdint.h>
#if defined(_WIN32)
# if defined(DUCKTAPE_LAYA_BUILD)
#  define DTL_API __declspec(dllexport)
# else
#  define DTL_API __declspec(dllimport)
# endif
#else
# define DTL_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
#define DTL_ABI_VERSION 2u
/* One handle type for a single model or a router over several local models. */
typedef struct dtl_engine dtl_engine;
typedef int32_t dtl_status;
enum { DTL_OK = 0, DTL_INVALID_ARGUMENT = 1, DTL_MODEL_ERROR = 2,
       DTL_INFERENCE_ERROR = 3, DTL_OUT_OF_MEMORY = 4,
       DTL_CANCELLED = 5, DTL_DEADLINE_EXCEEDED = 6, DTL_UNSUPPORTED_PLATFORM = 7 };
DTL_API uint32_t dtl_abi_version(void);
/* Strings are UTF-8 and NUL-terminated, except model_path, which the file system
 * receives as given: bytes on POSIX, UTF-8 on Windows. Output pointers are reset on entry.
 * error may be NULL; otherwise free its non-NULL value with dtl_free(). On OOM
 * the status is authoritative: an error string may be unavailable. No network
 * access or implicit downloads. */

/* config_json may be NULL. Keys (unknown keys are rejected):
 *   "threads": 0 (automatic: at most 4 and at most the CPUs the process may use) or 1..256
 *   "context_tokens": capacity reserved for one prompt, 32 up to the model's
 *     trained length; default 512 (or the trained length if smaller). Values
 *     below 32 fail when the config is parsed; the upper bound is checked when
 *     the model loads (for a router route, on its first request)
 *   "overflow": default policy for prompts over their limit: "reject" (default),
 *     "truncate_end", "truncate_start" or "scan"; a request's own value wins
 *   "temperatures": calibration overrides keyed like the GGUF metadata, e.g.
 *     {"choice": 1.5, "choice.3_5": 1.7, "noul.2": 2.0}; each value finite, > 0
 *     and representable as a float. Used as given; temperatures stored in the
 *     model are bounded to [0.5, 5].
 *   "max_windows_per_call": 1..4096, default 256; the most forward passes one
 *     call may plan (a question without "scan" is one window, a single-option
 *     choice none). A call over it fails with status 1: on a single engine
 *     before any inference, on a router before the request that exceeds it. */
DTL_API dtl_status dtl_open(const char *model_path, const char *config_json,
                            dtl_engine **out, char **error);
/* router_json: {"models": {"id": "path.gguf" | {"path": "...", "config": {...}}},
 * "max_loaded": 1, "auto": {"english": "id", "default": "id"}}. 1..16 routes,
 * max_loaded 1..4 (loaded models; routes share one when the canonical path and
 * the configured threads, context_tokens and temperatures are equal), "auto" optional. Paths are
 * checked at open; models load lazily (least recently used evicted first).
 * config_json applies to every model; a route's own "config" overrides it key by
 * key, except "max_windows_per_call": it bounds the whole call, which spans routes,
 * so only config_json takes it and a route's "config" is rejected for it. Requests name a route in "model", or send "model": "auto" (with an
 * optional "lang") to choose "english" or "default" by language; "auto" cannot
 * be a route ID. */
DTL_API dtl_status dtl_open_router(const char *router_json, const char *config_json,
                                   dtl_engine **out, char **error);
/* Allocated diagnostics JSON, freed with dtl_free. A NULL engine reports runtime
 * identity, limits and CPU compatibility without loading a model; a single-model
 * engine adds its model metadata and calibration; a router reports the runtime
 * only. Every engine reports its "max_windows_per_call". No paths, input or result data are included; invalid UTF-8 in model
 * metadata is replaced. On a single-model engine this waits for a running call.
 * Ignore unknown fields. */
DTL_API dtl_status dtl_info(dtl_engine *engine, char **result_json, char **error);

typedef void (*dtl_event_callback)(const char *event_json, void *user_data);
typedef uint32_t (*dtl_cancel_callback)(void *user_data);
/* Per-call runtime controls, borrowed for the call. Initialize with
 * dtl_default_call_options().
 *   on_event: passive events, "start" then "end" or "error", run synchronously on
 *     the calling thread outside inference locks; event_json is borrowed for the
 *     callback. Reentry is allowed; do not close the active handle. C callbacks
 *     must not unwind; C++ exceptions are contained. Never changes results.
 *   timeout_ms: 0 (none) or any number of milliseconds, measured from execution setup.
 *   should_cancel: nonblocking, thread-safe, may run on backend worker threads,
 *     must not reenter the handle. Return 0 to continue, DTL_DEADLINE_EXCEEDED for
 *     a host deadline, anything else to cancel. The first stop is latched.
 *     Cancellation is cooperative; no partial result is returned. */
typedef struct dtl_call_options {
    uint32_t struct_size;
    uint32_t reserved; /* must be zero */
    uint64_t request_id;
    uint64_t timeout_ms;
    dtl_event_callback on_event;
    void *event_user_data;
    dtl_cancel_callback should_cancel;
    void *cancel_user_data;
} dtl_call_options;
DTL_API dtl_call_options dtl_default_call_options(void);
/* Ready-made should_cancel for hosts where running their own callback on backend
 * threads is costly (interpreter locks, VM thread attachment). user_data points
 * to an aligned uint32_t the host sets from any thread with an atomic or
 * naturally aligned 32-bit store: 0 continues, DTL_DEADLINE_EXCEEDED reports a
 * host deadline, any other value cancels. NULL reads as 0. */
DTL_API uint32_t dtl_cancel_flag(void *user_data);

/* One request object or an array of 1..32 (at most 128 questions, 1 MiB in
 * total, 1024 members per JSON object, the engine's max_windows_per_call (at most 4096) planned forward
 * passes, and, when
 * the request runs the model, 2 MiB of serialized state or rendered question);
 * arrays return arrays in input order.
 * Every request is validated first.
 * A single-model engine then prepares every request before any inference; a
 * router loads, prepares and runs request by request. Errors return no partial
 * result. Calls on one handle serialize; separate handles may run concurrently.
 * options may be NULL. Request format and per-request settings ("max_tokens",
 * "temperatures", "overflow", "max_windows", and "lang" with "model": "auto"):
 * docs/API.md. */
DTL_API dtl_status dtl_execute(dtl_engine *engine, const char *request_json,
                               const dtl_call_options *options, char **result_json, char **error);
/* NULL is accepted. Caller must ensure no call is running on the handle. */
DTL_API void dtl_close(dtl_engine *engine);
DTL_API void dtl_free(void *buffer);
#ifdef __cplusplus
}
#endif
#endif
