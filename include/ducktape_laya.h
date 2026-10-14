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
#define DTL_ABI_VERSION 1u
typedef struct dtl_engine dtl_engine;
typedef int32_t dtl_status;
enum { DTL_OK = 0, DTL_INVALID_ARGUMENT = 1, DTL_MODEL_ERROR = 2,
       DTL_INFERENCE_ERROR = 3, DTL_OUT_OF_MEMORY = 4,
       DTL_CANCELLED = 5, DTL_DEADLINE_EXCEEDED = 6, DTL_UNSUPPORTED_PLATFORM = 7 };
typedef struct dtl_options {
    uint32_t struct_size;
    uint32_t abi_version;
    uint32_t threads; /* 0 = automatic (capped at 4); otherwise 1..256 */
    uint32_t reserved; /* must be zero */
} dtl_options;
DTL_API uint32_t dtl_abi_version(void);
DTL_API dtl_options dtl_default_options(void);
/* Allocated diagnostics JSON, freed with dtl_free. NULL engine returns runtime
 * identity, limits and CPU compatibility without loading a model. An engine
 * additionally reports its model metadata/calibration. No paths, input or result
 * data are included. Added JSON fields are backward compatible; ignore unknown
 * fields. This describes capabilities, not model hash/provenance verification. */
DTL_API dtl_status dtl_info(dtl_engine *engine, char **result_json, char **error);
/* UTF-8, NUL-terminated inputs. All output pointers are reset on entry.
 * error may be NULL; otherwise free its non-NULL value with dtl_free().
 * On OOM the status is authoritative: an error string may be unavailable.
 * options may be NULL for defaults. No network or implicit downloads. */
DTL_API dtl_status dtl_open(const char *model_path, const dtl_options *options,
                          dtl_engine **out, char **error);
/* Calls on one engine serialize. Separate engines may run concurrently.
 * result_json is allocated on success; caller releases it using dtl_free().
 * A request is limited to 1 MiB / 32 questions / 512 tokens per question.
 * Question/option overflow is rejected, never silently truncated. */
DTL_API dtl_status dtl_decide(dtl_engine *engine, const char *request_json,
                            char **result_json, char **error);
/* Additive ABI v1 APIs. execute accepts one questions/schema request or an array
 * of 1..32 requests (at most 128 questions total, 1 MiB total). Arrays return
 * arrays in input order. All requests are validated/prepared before inference
 * on an engine; errors return no partial result. Execution is sequential.
 * Schema, routing and observer contracts: docs/API.md. */
typedef void (*dtl_event_callback)(const char *event_json, void *user_data);
typedef struct dtl_observer {
    uint64_t request_id;
    dtl_event_callback callback;
    void *user_data;
} dtl_observer;
/* Observer is borrowed for the call. Events: start then end OR error. Callback
 * runs synchronously on the calling thread, outside inference/cache locks.
 * event_json is borrowed only during callback. Reentry is allowed; do not close
 * the active handle. C callbacks must not unwind; C++ exceptions are contained.
 * Observer failures never change inference results. NULL disables events. */
DTL_API dtl_status dtl_execute(dtl_engine *engine, const char *request_json,
                             const dtl_observer *observer, char **result_json, char **error);
/* Legacy primitive request semantics of dtl_decide, with passive observers.
 * In particular, unrelated top-level metadata remains ignored. */
DTL_API dtl_status dtl_decide_observed(dtl_engine *engine, const char *request_json,
                                     const dtl_observer *observer, char **result_json, char **error);
typedef struct dtl_router dtl_router;
/* Config: {"models":{"id":"local/path.gguf"},"max_loaded":1}.
 * 1..16 routes, max_loaded 1..4. Paths checked at open; Laya metadata checked
 * on lazy load. Requests require model: "id". No downloads or guessed routing.
 * Cache deduplicates canonical paths and evicts LRU before loading at capacity.
 * Router calls serialize. Close must not race an active call. */
DTL_API dtl_status dtl_router_open(const char *config_json, const dtl_options *options,
                                 dtl_router **out, char **error);
DTL_API dtl_status dtl_router_execute(dtl_router *router, const char *request_json,
                                    const dtl_observer *observer, char **result_json, char **error);
DTL_API void dtl_router_close(dtl_router *router);
/* Per-call control: borrowed until the call returns. timeout_ms is 0 (none)
 * or 1..86400000, measured from execution setup after the start observer.
 * Cancellation callback must be nonblocking and thread-safe, must not reenter
 * the active handle, and may be called by backend worker threads. Return zero
 * to continue, DTL_DEADLINE_EXCEEDED for a host deadline, or any other nonzero
 * value to cancel. The first observed stop is latched for this call.
 * C++ callback exceptions are treated as cancellation.
 * Queued native calls poll while acquiring their handle lock. Running work
 * stops at safe boundaries; CPU backend interruption is cooperative. No thread
 * is killed, and no partial result is returned. Keep user_data alive throughout. */
typedef uint32_t (*dtl_cancel_callback)(void *user_data);
typedef struct dtl_call_options {
    uint32_t struct_size;
    uint32_t reserved;
    uint64_t timeout_ms;
    dtl_cancel_callback should_cancel;
    void *user_data;
} dtl_call_options;
DTL_API dtl_call_options dtl_default_call_options(void);
DTL_API dtl_status dtl_execute_with_options(dtl_engine *engine, const char *request_json,
    const dtl_observer *observer, const dtl_call_options *options, char **result_json, char **error);
DTL_API dtl_status dtl_decide_with_options(dtl_engine *engine, const char *request_json,
    const dtl_observer *observer, const dtl_call_options *options, char **result_json, char **error);
DTL_API dtl_status dtl_router_execute_with_options(dtl_router *router, const char *request_json,
    const dtl_observer *observer, const dtl_call_options *options, char **result_json, char **error);
/* NULL is accepted. Caller must ensure no pending/running calls use engine. */
DTL_API void dtl_close(dtl_engine *engine);
DTL_API void dtl_free(void *buffer);
#ifdef __cplusplus
}
#endif
#endif
