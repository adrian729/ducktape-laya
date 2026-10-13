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
       DTL_INFERENCE_ERROR = 3, DTL_OUT_OF_MEMORY = 4 };
typedef struct dtl_options {
    uint32_t struct_size;
    uint32_t abi_version;
    uint32_t threads; /* 0 = automatic (capped at 4); otherwise 1..256 */
    uint32_t reserved; /* must be zero */
} dtl_options;
DTL_API uint32_t dtl_abi_version(void);
DTL_API dtl_options dtl_default_options(void);
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
/* NULL is accepted. Caller must ensure no pending/running calls use engine. */
DTL_API void dtl_close(dtl_engine *engine);
DTL_API void dtl_free(void *buffer);
#ifdef __cplusplus
}
#endif
#endif
