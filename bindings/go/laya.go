// Package laya provides safe Go ownership over the shared CPU Laya engine
// (libducktape_laya). Inference is blocking; run it in goroutines.
package laya

/*
#include <stdint.h>
#include <stdlib.h>
#include <ducktape_laya.h>

extern void goObserver(char *event, void *user);

static void observe(const char *event, void *user) { goObserver((char *)event, user); }

static uint32_t cancelled(void *user) {
    return __atomic_load_n((uint32_t *)user, __ATOMIC_RELAXED) ? DTL_CANCELLED : 0;
}

static void set_flag(uint32_t *flag) { __atomic_store_n(flag, 1, __ATOMIC_RELAXED); }

static dtl_status open_model(const char *path, uint32_t threads, void **out, char **err) {
    dtl_options o = dtl_default_options();
    o.threads = threads;
    return dtl_open(path, &o, (dtl_engine **)out, err);
}

static dtl_status open_router(const char *config, uint32_t threads, void **out, char **err) {
    dtl_options o = dtl_default_options();
    o.threads = threads;
    return dtl_router_open(config, &o, (dtl_router **)out, err);
}

// kind: 0 model execute, 1 model decide, 2 router execute.
static dtl_status run(int kind, void *handle, const char *request, uint64_t request_id,
                      void *observer_user, uint64_t timeout_ms, uint32_t *flag,
                      char **result, char **err) {
    dtl_observer ob = {request_id, observer_user ? observe : NULL, observer_user};
    dtl_call_options co = dtl_default_call_options();
    co.timeout_ms = timeout_ms;
    if (flag) {
        co.should_cancel = cancelled;
        co.user_data = flag;
    }
    switch (kind) {
    case 1: return dtl_decide_with_options((dtl_engine *)handle, request, &ob, &co, result, err);
    case 2: return dtl_router_execute_with_options((dtl_router *)handle, request, &ob, &co, result, err);
    default: return dtl_execute_with_options((dtl_engine *)handle, request, &ob, &co, result, err);
    }
}

static void close_handle(int router, void *handle) {
    if (router) dtl_router_close((dtl_router *)handle);
    else dtl_close((dtl_engine *)handle);
}
*/
import "C"

import (
	"context"
	"errors"
	"runtime/cgo"
	"strings"
	"sync"
	"time"
	"unsafe"
)

// Native status codes (dtl_status).
const (
	StatusInvalidArgument     = 1
	StatusModelError          = 2
	StatusInferenceError      = 3
	StatusOutOfMemory         = 4
	StatusCancelled           = 5
	StatusDeadlineExceeded    = 6
	StatusUnsupportedPlatform = 7
)

const maxTimeoutMs = 86400000

// Error is a failed native operation.
type Error struct {
	Status  int32
	Message string
}

func (e *Error) Error() string { return e.Message }

func invalid(msg string) *Error { return &Error{StatusInvalidArgument, msg} }

func cstring(s string) (*C.char, error) {
	if strings.IndexByte(s, 0) >= 0 {
		return nil, invalid("Input contains a NUL byte")
	}
	return C.CString(s), nil
}

func check(status C.dtl_status, msg *C.char) error {
	if msg != nil {
		defer C.dtl_free(unsafe.Pointer(msg))
	}
	if status == 0 {
		return nil
	}
	text := "Laya operation failed"
	if msg != nil {
		text = C.GoString(msg)
	}
	return &Error{int32(status), text}
}

func readInfo(handle unsafe.Pointer) (string, error) {
	var result, msg *C.char
	status := C.dtl_info((*C.dtl_engine)(handle), &result, &msg)
	if result != nil {
		defer C.dtl_free(unsafe.Pointer(result))
	}
	if err := check(status, msg); err != nil {
		return "", err
	}
	if result == nil {
		return "", &Error{StatusInferenceError, "Native result was null"}
	}
	return C.GoString(result), nil
}

// RuntimeInfo returns runtime identity and capabilities without loading a
// model. It contains no telemetry or input data.
func RuntimeInfo() (string, error) { return readInfo(nil) }

// CallOptions controls one call. The zero value (or nil) means defaults.
type CallOptions struct {
	// RequestID is reported in observer events.
	RequestID uint64
	// OnEvent receives passive observer events synchronously on the calling
	// goroutine's thread, outside native locks. Panics are contained. It may
	// reenter the engine, but must not Close it or wait for a call that waits
	// on this one.
	OnEvent func(eventJSON string)
}

// Engine owns a native model engine or router. It is safe for concurrent use;
// native calls on one handle are serialized.
type Engine struct {
	mu     sync.Mutex
	idle   *sync.Cond
	active int
	closed bool
	router bool
	handle unsafe.Pointer
}

func newEngine(handle unsafe.Pointer, router bool) *Engine {
	e := &Engine{handle: handle, router: router}
	e.idle = sync.NewCond(&e.mu)
	return e
}

// Open loads a local GGUF model. threads 0 selects automatically (capped at 4).
func Open(modelPath string, threads uint32) (*Engine, error) {
	path, err := cstring(modelPath)
	if err != nil {
		return nil, err
	}
	defer C.free(unsafe.Pointer(path))
	var raw unsafe.Pointer
	var msg *C.char
	if err := check(C.open_model(path, C.uint32_t(threads), &raw, &msg), msg); err != nil {
		return nil, err
	}
	if raw == nil {
		return nil, &Error{StatusModelError, "Native engine returned null"}
	}
	return newEngine(raw, false), nil
}

// OpenRouter opens a bounded, lazy local model registry. See docs/API.md.
func OpenRouter(configJSON string, threads uint32) (*Engine, error) {
	config, err := cstring(configJSON)
	if err != nil {
		return nil, err
	}
	defer C.free(unsafe.Pointer(config))
	var raw unsafe.Pointer
	var msg *C.char
	if err := check(C.open_router(config, C.uint32_t(threads), &raw, &msg), msg); err != nil {
		return nil, err
	}
	if raw == nil {
		return nil, &Error{StatusModelError, "Native router returned null"}
	}
	return newEngine(raw, true), nil
}

func (e *Engine) begin() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return invalid("Engine is closed")
	}
	e.active++
	return nil
}

func (e *Engine) end() {
	e.mu.Lock()
	e.active--
	if e.active == 0 {
		e.idle.Broadcast()
	}
	e.mu.Unlock()
}

// Close waits for in-flight calls, then releases the native handle. It is
// idempotent; later calls on the engine fail with StatusInvalidArgument.
// Calling Close from an OnEvent callback of the same engine deadlocks.
func (e *Engine) Close() error {
	e.mu.Lock()
	if e.closed {
		e.mu.Unlock()
		return nil
	}
	e.closed = true
	for e.active > 0 {
		e.idle.Wait()
	}
	handle := e.handle
	e.handle = nil
	e.mu.Unlock()
	C.close_handle(boolInt(e.router), handle)
	return nil
}

func boolInt(b bool) C.int {
	if b {
		return 1
	}
	return 0
}

// Info reports model metadata for a single engine; routers report common
// runtime capabilities.
func (e *Engine) Info() (string, error) {
	if err := e.begin(); err != nil {
		return "", err
	}
	defer e.end()
	if e.router {
		return readInfo(nil)
	}
	return readInfo(e.handle)
}

// Decide runs a legacy primitive request (unrelated top-level metadata is ignored).
func (e *Engine) Decide(request string) (string, error) {
	return e.DecideContext(context.Background(), request, nil)
}

// Execute runs one primitive/structured request or a bounded JSON array.
func (e *Engine) Execute(request string) (string, error) {
	return e.ExecuteContext(context.Background(), request, nil)
}

// ExecuteContext is Execute with cooperative cancellation and deadline: a
// cancelled ctx yields StatusCancelled, an expired deadline StatusDeadlineExceeded
// (clamped to 24 h). No partial result is returned.
func (e *Engine) ExecuteContext(ctx context.Context, request string, opts *CallOptions) (string, error) {
	return e.call(ctx, request, opts, 0)
}

// DecideContext is Decide with the same controls as ExecuteContext.
func (e *Engine) DecideContext(ctx context.Context, request string, opts *CallOptions) (string, error) {
	return e.call(ctx, request, opts, 1)
}

//export goObserver
func goObserver(event *C.char, user unsafe.Pointer) {
	handle := cgo.Handle(*(*C.uintptr_t)(user))
	defer func() { _ = recover() }()
	handle.Value().(func(string))(C.GoString(event))
}

func (e *Engine) call(ctx context.Context, request string, opts *CallOptions, kind C.int) (string, error) {
	req, err := cstring(request)
	if err != nil {
		return "", err
	}
	defer C.free(unsafe.Pointer(req))
	if err := e.begin(); err != nil {
		return "", err
	}
	defer e.end()
	if e.router {
		kind = 2
	}
	if ctx == nil {
		ctx = context.Background()
	}
	var requestID uint64
	var observer unsafe.Pointer
	if opts != nil {
		requestID = opts.RequestID
		if opts.OnEvent != nil {
			handle := cgo.NewHandle(opts.OnEvent)
			defer handle.Delete()
			slot := (*C.uintptr_t)(C.malloc(C.size_t(unsafe.Sizeof(C.uintptr_t(0)))))
			*slot = C.uintptr_t(handle)
			defer C.free(unsafe.Pointer(slot))
			observer = unsafe.Pointer(slot)
		}
	}
	var timeout uint64
	if deadline, ok := ctx.Deadline(); ok {
		ms := time.Until(deadline).Milliseconds() + 1
		timeout = uint64(min(max(ms, 1), maxTimeoutMs))
	}
	var flag *C.uint32_t
	if ctx.Done() != nil {
		flag = (*C.uint32_t)(C.calloc(1, C.size_t(unsafe.Sizeof(C.uint32_t(0)))))
		if errors.Is(ctx.Err(), context.Canceled) {
			C.set_flag(flag)
		}
		done := make(chan struct{})
		stopped := make(chan struct{})
		go func() {
			defer close(stopped)
			select {
			case <-ctx.Done():
				if errors.Is(ctx.Err(), context.Canceled) {
					C.set_flag(flag)
				}
			case <-done:
			}
		}()
		defer func() {
			close(done)
			<-stopped
			C.free(unsafe.Pointer(flag))
		}()
	}
	var result, msg *C.char
	status := C.run(kind, e.handle, req, C.uint64_t(requestID), observer, C.uint64_t(timeout), flag, &result, &msg)
	if result != nil {
		defer C.dtl_free(unsafe.Pointer(result))
	}
	if err := check(status, msg); err != nil {
		return "", err
	}
	if result == nil {
		return "", &Error{StatusInferenceError, "Native result was null"}
	}
	return C.GoString(result), nil
}
