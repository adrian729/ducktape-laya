/* Experiment only (windows-drift investigation, never merged): route the float
 * libm functions the CPU graph uses through their double versions, rounded to
 * float, so their results no longer depend on the C runtime's float math.
 * Force-included into every C/C++ translation unit by -DDTL_LIBM_PROBE=ON. */
#ifndef DTL_LIBM_PROBE_H
#define DTL_LIBM_PROBE_H
#include <math.h>
#ifdef __cplusplus
#include <cmath>
#endif
static inline float dtl_probe_erff(float x) { return (float)erf((double)x); }
static inline float dtl_probe_expf(float x) { return (float)exp((double)x); }
static inline float dtl_probe_sinf(float x) { return (float)sin((double)x); }
static inline float dtl_probe_cosf(float x) { return (float)cos((double)x); }
static inline float dtl_probe_powf(float x, float y) { return (float)pow((double)x, (double)y); }
static inline float dtl_probe_logf(float x) { return (float)log((double)x); }
static inline float dtl_probe_tanhf(float x) { return (float)tanh((double)x); }
static inline float dtl_probe_expm1f(float x) { return (float)expm1((double)x); }
static inline float dtl_probe_log2f(float x) { return (float)log2((double)x); }
#define erff(x) dtl_probe_erff(x)
#define expf(x) dtl_probe_expf(x)
#define sinf(x) dtl_probe_sinf(x)
#define cosf(x) dtl_probe_cosf(x)
#define powf(x, y) dtl_probe_powf(x, y)
#define logf(x) dtl_probe_logf(x)
#define tanhf(x) dtl_probe_tanhf(x)
#define expm1f(x) dtl_probe_expm1f(x)
#define log2f(x) dtl_probe_log2f(x)
#endif
