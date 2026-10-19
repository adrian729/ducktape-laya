// Package laya provides safe Go ownership over the shared CPU Laya engine
// (libducktape_laya). Inference is blocking; run it in goroutines.
package laya

/*
#include <stdint.h>
#include <stdlib.h>
#include <ducktape_laya.h>

extern void goObserver(char *event, void *user);

static void observe(const char *event, void *user) { goObserver((char *)event, user); }

static void set_flag(uint32_t *flag, uint32_t value) { __atomic_store_n(flag, value, __ATOMIC_RELAXED); }

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
        co.should_cancel = dtl_cancel_flag;
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
	"runtime"
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

// Is lets errors.Is match status 5 with context.Canceled and status 6 with
// context.DeadlineExceeded.
func (e *Error) Is(target error) bool {
	switch target {
	case context.Canceled:
		return e.Status == StatusCancelled
	case context.DeadlineExceeded:
		return e.Status == StatusDeadlineExceeded
	}
	return false
}

func ctxError(err error) *Error {
	if errors.Is(err, context.DeadlineExceeded) {
		return &Error{StatusDeadlineExceeded, "Laya call deadline exceeded"}
	}
	return &Error{StatusCancelled, "Laya call cancelled"}
}

func invalid(msg string) *Error { return &Error{StatusInvalidArgument, msg} }

// ErrClosed is returned by calls made after Close. Match it with errors.Is; it
// is also an *Error with StatusInvalidArgument.
var ErrClosed error = &Error{StatusInvalidArgument, "Engine is closed"}

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
	// reenter the engine or Close it, but must not wait for a call that waits
	// on this one.
	OnEvent func(eventJSON string)
}

// Engine owns a native model engine or router. It is safe for concurrent use;
// native calls on one handle are serialized. Call Close (or CloseDeferred) for
// prompt release; a finalizer releases the handle as a safety net if an Engine
// becomes unreachable without being closed.
type Engine struct {
	mu       sync.Mutex
	idle     *sync.Cond
	active   int
	closed   bool
	deferred bool // CloseDeferred: the last running call releases
	router   bool
	handle   unsafe.Pointer
	done     chan struct{} // closed once the handle has been released
}

func newEngine(handle unsafe.Pointer, router bool) *Engine {
	e := &Engine{handle: handle, router: router, done: make(chan struct{})}
	e.idle = sync.NewCond(&e.mu)
	runtime.SetFinalizer(e, (*Engine).release)
	return e
}

// Open loads a local GGUF model. threads 0 selects automatically (capped at
// 4); otherwise it must be 1..256.
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
// threads 0 selects automatically (capped at 4); otherwise it must be 1..256.
// Decide and DecideContext on a router use strict Execute semantics: requests
// require "model" and unrelated metadata is rejected.
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
		return ErrClosed
	}
	e.active++
	return nil
}

func (e *Engine) end() {
	e.mu.Lock()
	e.active--
	release := e.active == 0 && e.deferred
	if e.active == 0 {
		e.idle.Broadcast()
	}
	e.mu.Unlock()
	if release {
		e.release()
	}
}

// release frees the native handle exactly once and signals done. Callers must
// ensure no call is running.
func (e *Engine) release() {
	e.mu.Lock()
	handle := e.handle
	e.handle = nil
	e.closed = true
	e.mu.Unlock()
	if handle == nil {
		return
	}
	runtime.SetFinalizer(e, nil)
	C.close_handle(boolInt(e.router), handle)
	close(e.done)
}

// Close rejects new calls (they fail with ErrClosed), waits for all in-flight
// calls to finish, then releases the native handle. It is idempotent, and
// concurrent Close calls all return only after the handle is released.
//
// Close must not be called from inside an OnEvent callback of the same
// engine: it would wait for its own call and deadlock. Use CloseDeferred
// there.
func (e *Engine) Close() error {
	e.mu.Lock()
	e.closed = true
	for e.active > 0 {
		e.idle.Wait()
	}
	e.mu.Unlock()
	e.release()
	<-e.done
	return nil
}

// CloseDeferred marks the engine closed so new calls fail with ErrClosed and
// returns immediately without waiting. The last running call releases the
// handle (or it is released at once if none is running). It is the way to
// close from inside an OnEvent callback, where Close would wait for its own
// call and deadlock.
func (e *Engine) CloseDeferred() {
	e.mu.Lock()
	e.closed = true
	e.deferred = true
	idle := e.active == 0
	e.mu.Unlock()
	if idle {
		e.release()
	}
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

// Decide runs a legacy primitive request (unrelated top-level metadata is
// ignored). On a router it uses strict Execute semantics: "model" is required
// and unrelated metadata is rejected.
func (e *Engine) Decide(request string) (string, error) {
	return e.DecideContext(context.Background(), request, nil)
}

// Execute runs one primitive/structured request or a bounded JSON array.
func (e *Engine) Execute(request string) (string, error) {
	return e.ExecuteContext(context.Background(), request, nil)
}

// ExecuteContext is Execute with cooperative cancellation and deadline. A
// cancelled ctx yields an *Error with StatusCancelled (errors.Is
// context.Canceled), an expired deadline StatusDeadlineExceeded (errors.Is
// context.DeadlineExceeded). An already-done ctx returns immediately without
// a native call. No partial result is returned.
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
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return "", ctxError(err)
	}
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
	var requestID uint64
	var observer unsafe.Pointer
	if opts != nil {
		requestID = opts.RequestID
		if onEvent := opts.OnEvent; onEvent != nil {
			handle := cgo.NewHandle(onEvent)
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
		if ms <= maxTimeoutMs {
			timeout = uint64(max(ms, 1))
		} // beyond the native maximum: no native timeout; the watcher enforces it
	}
	var flag *C.uint32_t
	if ctx.Done() != nil {
		flag = (*C.uint32_t)(C.calloc(1, C.size_t(unsafe.Sizeof(C.uint32_t(0)))))
		stop := func() {
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				C.set_flag(flag, C.DTL_DEADLINE_EXCEEDED)
			} else {
				C.set_flag(flag, C.DTL_CANCELLED)
			}
		}
		done := make(chan struct{})
		stopped := make(chan struct{})
		go func() {
			defer close(stopped)
			select {
			case <-ctx.Done():
				stop()
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
