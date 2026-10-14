/* Example-only terminal/JSON helpers shared by the C and C++ demos.
 * demo_form allocates its result with malloc; the caller uses free. */
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
char *demo_form(void);
int demo_print(const char *result);
#ifdef __cplusplus
}
#endif
