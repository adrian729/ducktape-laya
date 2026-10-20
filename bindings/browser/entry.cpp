#include "ducktape_laya.h"
#include <cstddef>
// The worker writes this ABI-v2 layout directly into wasm32 memory. Refuse a
// different ABI/target at build time rather than corrupting callback pointers.
static_assert(sizeof(void *) == 4);
static_assert(sizeof(dtl_call_options) == 40);
static_assert(offsetof(dtl_call_options, request_id) == 8);
static_assert(offsetof(dtl_call_options, timeout_ms) == 16);
static_assert(offsetof(dtl_call_options, on_event) == 24);
static_assert(offsetof(dtl_call_options, event_user_data) == 28);
static_assert(offsetof(dtl_call_options, should_cancel) == 32);
static_assert(offsetof(dtl_call_options, cancel_user_data) == 36);
int main() { return 0; }
