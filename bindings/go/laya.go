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

static dtl_status open_model(const char *path, const char *config, void **out, char **err) {
    return dtl_open(path, config, (dtl_engine **)out, err);
}

static dtl_status open_router(const char *router, const char *config, void **out, char **err) {
    return dtl_open_router(router, config, (dtl_engine **)out, err);
}

static dtl_status run(void *handle, const char *request, uint64_t request_id,
                      void *event_user, uint64_t timeout_ms, uint32_t *flag,
                      char **result, char **err) {
    dtl_call_options co = dtl_default_call_options();
    co.request_id = request_id;
    co.timeout_ms = timeout_ms;
    if (event_user) {
        co.on_event = observe;
        co.event_user_data = event_user;
    }
    if (flag) {
        co.should_cancel = dtl_cancel_flag;
        co.cancel_user_data = flag;
    }
    return dtl_execute((dtl_engine *)handle, request, &co, result, err);
}
*/
import "C"

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"runtime"
	"runtime/cgo"
	"strings"
	"sync"
	"time"
	"unsafe"
)

// Native status codes (dtl_status).
const (
	StatusOK                  = 0
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

// checkABI refuses a library built for a different C ABI version.
func checkABI() error {
	if v := uint32(C.dtl_abi_version()); v != 2 {
		return &Error{StatusUnsupportedPlatform, fmt.Sprintf("Native ducktape-laya ABI %d is not supported (expected 2)", v)}
	}
	return nil
}

func readInfo(handle unsafe.Pointer) (string, error) {
	if err := checkABI(); err != nil {
		return "", err
	}
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
	// RequestID is reported in events.
	RequestID uint64
	// OnEvent receives passive events synchronously on the calling
	// goroutine's thread, outside native locks. Panics are contained. It may
	// reenter the engine, but must not wait for a call that waits on this one.
	// Do not call Close from inside it (Close waits for the running call and
	// would deadlock); use CloseDeferred instead.
	OnEvent func(eventJSON string)
}

// Engine owns a native model engine or router. It is safe for concurrent use;
// native calls on one handle are serialized. Call Close (or CloseDeferred) for
// prompt release; a finalizer releases the handle as a safety net if an Engine
// becomes unreachable without being closed.
type Engine struct {
	c *engineCore
}

// engineCore is the state behind an Engine. It must never point back to the
// Engine: the finalizer sits on the Engine, and a self-referencing object
// (the sync.Cond's Locker is &mu) is never collected, so the finalizer would
// never run.
type engineCore struct {
	mu       sync.Mutex
	idle     *sync.Cond
	active   int
	closed   bool
	deferred bool // CloseDeferred: the last running call releases
	handle   unsafe.Pointer
	done     chan struct{} // closed once the handle has been released
}

func newEngine(handle unsafe.Pointer) *Engine {
	c := &engineCore{handle: handle, done: make(chan struct{})}
	c.idle = sync.NewCond(&c.mu)
	e := &Engine{c: c}
	runtime.SetFinalizer(e, func(e *Engine) { e.c.release() })
	return e
}

// Config holds open-time settings. A nil *Config or zero fields select the
// engine defaults; only set fields are sent to the native library.
type Config struct {
	// Threads is 0 (automatic: at most 4 and at most the CPUs the process may use) or 1..256.
	Threads uint32
	// ContextTokens is the capacity reserved for one prompt (32 up to the
	// model's trained length; default 512). 0 keeps the default.
	ContextTokens uint32
	// Overflow is the engine-wide policy for prompts over the token limit:
	// "reject" (default), "truncate_end", "truncate_start" or "scan". A
	// request's own "overflow" wins. Empty keeps the default.
	Overflow string
	// Temperatures overrides calibration temperatures keyed like the GGUF
	// metadata, e.g. "choice", "choice.3_5", "noul.2". Values must be finite
	// and > 0.
	Temperatures map[string]float64
}

// configJSON serializes c with sorted temperature keys; nil when there is
// nothing to send.
func configJSON(c *Config) (*C.char, error) {
	if c == nil {
		return nil, nil
	}
	var body struct {
		Threads       uint32             `json:"threads,omitempty"`
		ContextTokens uint32             `json:"context_tokens,omitempty"`
		Overflow      string             `json:"overflow,omitempty"`
		Temperatures  map[string]float64 `json:"temperatures,omitempty"`
	}
	body.Threads, body.ContextTokens, body.Overflow, body.Temperatures = c.Threads, c.ContextTokens, c.Overflow, c.Temperatures
	data, err := json.Marshal(body) // map keys are emitted sorted
	if err != nil {
		return nil, invalid("Invalid config: " + err.Error())
	}
	return cstring(string(data))
}

// Open loads a local GGUF model. config may be nil for defaults.
func Open(modelPath string, config *Config) (*Engine, error) {
	return openNative(modelPath, config, false, "Native engine returned null")
}

// OpenRouter opens a bounded, lazy local model registry described by
// routerJSON (see docs/API.md). config applies to every route; a route's own
// config overrides it key by key. Requests must name a route in "model", or
// use "model": "auto" when routerJSON has an "auto" config.
func OpenRouter(routerJSON string, config *Config) (*Engine, error) {
	return openNative(routerJSON, config, true, "Native router returned null")
}

func openNative(first string, config *Config, router bool, nullMsg string) (*Engine, error) {
	if err := checkABI(); err != nil {
		return nil, err
	}
	arg, err := cstring(first)
	if err != nil {
		return nil, err
	}
	defer C.free(unsafe.Pointer(arg))
	cfg, err := configJSON(config)
	if err != nil {
		return nil, err
	}
	if cfg != nil {
		defer C.free(unsafe.Pointer(cfg))
	}
	var raw unsafe.Pointer
	var msg *C.char
	var status C.dtl_status
	if router {
		status = C.open_router(arg, cfg, &raw, &msg)
	} else {
		status = C.open_model(arg, cfg, &raw, &msg)
	}
	if err := check(status, msg); err != nil {
		return nil, err
	}
	if raw == nil {
		return nil, &Error{StatusModelError, nullMsg}
	}
	return newEngine(raw), nil
}

func (e *engineCore) begin() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return ErrClosed
	}
	e.active++
	return nil
}

func (e *engineCore) end() {
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

// closeNative releases a native handle; a variable so tests can observe it.
var closeNative = func(handle unsafe.Pointer) { C.dtl_close((*C.dtl_engine)(handle)) }

// release frees the native handle exactly once and signals done. Callers must
// ensure no call is running.
func (e *engineCore) release() {
	e.mu.Lock()
	handle := e.handle
	e.handle = nil
	e.closed = true
	e.mu.Unlock()
	if handle == nil {
		return
	}
	closeNative(handle)
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
	c := e.c
	c.mu.Lock()
	c.closed = true
	for c.active > 0 {
		c.idle.Wait()
	}
	c.mu.Unlock()
	c.release()
	<-c.done
	runtime.SetFinalizer(e, nil)
	return nil
}

// CloseDeferred marks the engine closed so new calls fail with ErrClosed and
// returns immediately without waiting. The last running call releases the
// handle (or it is released at once if none is running). It is the way to
// close from inside an OnEvent callback, where Close would wait for its own
// call and deadlock.
func (e *Engine) CloseDeferred() {
	c := e.c
	c.mu.Lock()
	c.closed = true
	c.deferred = true
	idle := c.active == 0
	c.mu.Unlock()
	if idle {
		c.release()
	}
	runtime.KeepAlive(e)
}

// Info reports model metadata for a single engine; routers report runtime
// information only.
func (e *Engine) Info() (string, error) {
	defer runtime.KeepAlive(e)
	c := e.c
	if err := c.begin(); err != nil {
		return "", err
	}
	defer c.end()
	return readInfo(c.handle)
}

// Execute runs one request or a bounded JSON array.
func (e *Engine) Execute(request string) (string, error) {
	return e.ExecuteContext(context.Background(), request, nil)
}

// ExecuteContext is Execute with cooperative cancellation and deadline. A
// cancelled ctx yields an *Error with StatusCancelled (errors.Is
// context.Canceled), an expired deadline StatusDeadlineExceeded (errors.Is
// context.DeadlineExceeded). An already-done ctx returns immediately without
// a native call. No partial result is returned.
func (e *Engine) ExecuteContext(ctx context.Context, request string, opts *CallOptions) (string, error) {
	defer runtime.KeepAlive(e)
	return e.c.call(ctx, request, opts)
}

//export goObserver
func goObserver(event *C.char, user unsafe.Pointer) {
	handle := cgo.Handle(*(*C.uintptr_t)(user))
	defer func() { _ = recover() }()
	handle.Value().(func(string))(C.GoString(event))
}

func (e *engineCore) call(ctx context.Context, request string, opts *CallOptions) (string, error) {
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
	status := C.run(e.handle, req, C.uint64_t(requestID), observer, C.uint64_t(timeout), flag, &result, &msg)
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
