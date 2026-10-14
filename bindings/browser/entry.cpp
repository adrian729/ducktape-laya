#include "ducktape_laya.h"
#include <cstddef>
// The worker writes these ABI-v1 layouts directly into wasm32 memory. Refuse
// a different ABI/target at build time rather than corrupting callback pointers.
static_assert(sizeof(void *) == 4);
static_assert(sizeof(dtl_options) == 16);
static_assert(sizeof(dtl_observer) == 16);
static_assert(offsetof(dtl_observer, callback) == 8);
static_assert(sizeof(dtl_call_options) == 24);
static_assert(offsetof(dtl_call_options, timeout_ms) == 8);
static_assert(offsetof(dtl_call_options, should_cancel) == 16);
static_assert(offsetof(dtl_call_options, user_data) == 20);
int main() { return 0; }
