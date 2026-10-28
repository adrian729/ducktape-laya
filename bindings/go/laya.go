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
	"unicode/utf8"
	"unsafe"
)

// Status is a native status code (dtl_status). The values are written out so
// the binding-abi policy check can compare them with ducktape_laya.h.
type Status int32

const (
	StatusOK                  Status = 0
	StatusInvalidArgument     Status = 1
	StatusModelError          Status = 2
	StatusInferenceError      Status = 3
	StatusOutOfMemory         Status = 4
	StatusCancelled           Status = 5
	StatusDeadlineExceeded    Status = 6
	StatusUnsupportedPlatform Status = 7
)

const maxTimeoutMs = 86400000

// Error is a failed operation: a native failure, or an argument or lifecycle
// error the binding reports with the status the core would use.
type Error struct {
	Status  Status
	Message string
	err     error
}

func (e *Error) Error() string { return e.Message }

// Unwrap returns the sentinel behind the error (ErrClosed), if any.
func (e *Error) Unwrap() error { return e.err }

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
		return &Error{Status: StatusDeadlineExceeded, Message: "Laya call deadline exceeded"}
	}
	return &Error{Status: StatusCancelled, Message: "Laya call cancelled"}
}

func invalid(msg string) *Error { return &Error{Status: StatusInvalidArgument, Message: msg} }

// ErrClosed is the sentinel behind every error returned by a call on a closed
// engine. Match it with errors.Is. Each such error is a fresh *Error with
// StatusInvalidArgument that wraps ErrClosed; nothing shared is returned.
var ErrClosed = errors.New("laya: engine is closed")

func closedError() *Error {
	return &Error{Status: StatusInvalidArgument, Message: "Engine is closed", err: ErrClosed}
}

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
	return &Error{Status: Status(status), Message: text}
}

// checkABI refuses a library built for a different C ABI version.
func checkABI() error {
	if v := uint32(C.dtl_abi_version()); v != 2 {
		return &Error{Status: StatusUnsupportedPlatform, Message: fmt.Sprintf("Native ducktape-laya ABI %d is not supported (expected 2)", v)}
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
		return "", &Error{Status: StatusInferenceError, Message: "Native result was null"}
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
	// Close called from inside it, on the same goroutine, does not wait for
	// the running call: the engine is closed at once and released when the
	// call returns.
	OnEvent func(eventJSON string)
}

// Engine owns a native model engine or router. It is safe for concurrent use;
// native calls on one handle are serialized. Call Close for prompt release; a finalizer releases the handle as a safety net if an Engine
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
	deferred bool // closed from a callback: the last running call releases
	// callbacks counts, per goroutine id, the calls whose OnEvent may run on
	// that goroutine; Close from such a goroutine must not wait.
	callbacks map[uint64]int
	handle    unsafe.Pointer
	done      chan struct{} // closed once the handle has been released
}

func newEngine(handle unsafe.Pointer) *Engine {
	c := &engineCore{handle: handle, done: make(chan struct{})}
	c.idle = sync.NewCond(&c.mu)
	e := &Engine{c: c}
	runtime.SetFinalizer(e, func(e *Engine) { e.c.release() })
	return e
}

// Config holds open-time settings. A nil *Config selects the engine defaults.
// Go's documented exception to the other bindings: a zero field means unset
// and is not sent, so 0 cannot be passed to the core to be rejected. Every
// value that is sent is validated by the core (status 1), not here; the
// binding rejects only what JSON cannot carry (invalid UTF-8, non-finite
// numbers).
type Config struct {
	// Threads is 1..256; 0 keeps the default (automatic: at most 4 and at
	// most the CPUs the process may use).
	Threads uint32
	// ContextTokens is the capacity reserved for one prompt (32 up to the
	// model's trained length; default 512). 0 keeps the default.
	ContextTokens uint32
	// MaxWindowsPerCall bounds the forward passes one call may plan (1..4096;
	// default 256). 0 keeps the default. A router takes it only in its shared
	// config, where it bounds the whole call.
	MaxWindowsPerCall uint32
	// Overflow is the engine-wide policy for prompts over the token limit:
	// "reject" (default), "truncate_end", "truncate_start" or "scan". A
	// request's own "overflow" wins. Empty keeps the default.
	Overflow string
	// Temperatures overrides calibration temperatures keyed like the GGUF
	// metadata, e.g. "choice", "choice.3_5", "noul.2".
	Temperatures map[string]float64
}

// configBody is the JSON shape of a Config (native keys).
type configBody struct {
	Threads           uint32             `json:"threads,omitempty"`
	ContextTokens     uint32             `json:"context_tokens,omitempty"`
	MaxWindowsPerCall uint32             `json:"max_windows_per_call,omitempty"`
	Overflow          string             `json:"overflow,omitempty"`
	Temperatures      map[string]float64 `json:"temperatures,omitempty"`
}

// marshal serializes c with sorted temperature keys. json.Marshal would
// replace invalid UTF-8 with U+FFFD, so the strings are checked first.
func (c *Config) marshal() ([]byte, error) {
	if !utf8.ValidString(c.Overflow) {
		return nil, invalid("Invalid config: overflow is not valid UTF-8")
	}
	for key := range c.Temperatures {
		if !utf8.ValidString(key) {
			return nil, invalid("Invalid config: a temperature key is not valid UTF-8")
		}
	}
	data, err := json.Marshal(configBody{c.Threads, c.ContextTokens, c.MaxWindowsPerCall, c.Overflow, c.Temperatures}) // map keys are emitted sorted
	if err != nil {
		return nil, invalid("Invalid config: " + err.Error())
	}
	return data, nil
}

// configJSON is nil when there is nothing to send.
func configJSON(c *Config) (*C.char, error) {
	if c == nil {
		return nil, nil
	}
	data, err := c.marshal()
	if err != nil {
		return nil, err
	}
	return cstring(string(data))
}

// AutoRoutes names the routes `"model": "auto"` chooses between by the
// state's language.
type AutoRoutes struct {
	English string
	Default string
}

// Route is one router entry. Path travels in JSON, so it must be valid UTF-8.
// A non-nil Config overrides the router's shared config key by key; it must
// not set MaxWindowsPerCall.
type Route struct {
	Path   string
	Config *Config
}

// RouterConfig describes a bounded, lazy local model registry. MaxLoaded 0
// keeps the library default (1), like the other zero values.
type RouterConfig struct {
	Models    map[string]Route
	MaxLoaded int
	Auto      *AutoRoutes
}

// marshal serializes r with sorted route ids. Ids, paths and auto names must
// be valid UTF-8 (json.Marshal would replace the rest).
func (r *RouterConfig) marshal() (string, error) {
	if r == nil {
		return "", invalid("Router config is nil")
	}
	models := make(map[string]json.RawMessage, len(r.Models))
	for id, route := range r.Models {
		if !utf8.ValidString(id) || !utf8.ValidString(route.Path) {
			return "", invalid("Route id or path is not valid UTF-8")
		}
		var raw []byte
		var err error
		if route.Config == nil {
			raw, err = json.Marshal(route.Path)
		} else {
			var config []byte
			if config, err = route.Config.marshal(); err == nil {
				raw, err = json.Marshal(struct {
					Path   string          `json:"path"`
					Config json.RawMessage `json:"config"`
				}{route.Path, config})
			}
		}
		if err != nil {
			return "", invalid("Invalid router config: " + err.Error())
		}
		models[id] = raw
	}
	type auto struct {
		English string `json:"english"`
		Default string `json:"default"`
	}
	body := struct {
		Models    map[string]json.RawMessage `json:"models"`
		MaxLoaded int                        `json:"max_loaded,omitempty"`
		Auto      *auto                      `json:"auto,omitempty"`
	}{Models: models, MaxLoaded: r.MaxLoaded}
	if r.Auto != nil {
		if !utf8.ValidString(r.Auto.English) || !utf8.ValidString(r.Auto.Default) {
			return "", invalid("Auto route name is not valid UTF-8")
		}
		body.Auto = &auto{r.Auto.English, r.Auto.Default}
	}
	data, err := json.Marshal(body)
	if err != nil {
		return "", invalid("Invalid router config: " + err.Error())
	}
	return string(data), nil
}

// Open loads a local GGUF model. config may be nil for defaults. The path
// reaches the core as bytes, whatever its encoding.
func Open(modelPath string, config *Config) (*Engine, error) {
	return openNative(modelPath, config, false, "Native engine returned null")
}

// OpenRouter opens a bounded, lazy local model registry. config applies to
// every route; a route's own config overrides it key by key. Requests must
// name a route in "model", or use "model": "auto" when router.Auto is set.
func OpenRouter(router *RouterConfig, config *Config) (*Engine, error) {
	text, err := router.marshal()
	if err != nil {
		return nil, err
	}
	return openNative(text, config, true, "Native router returned null")
}

// OpenRouterJSON is OpenRouter with the router described by JSON text (see
// docs/API.md).
func OpenRouterJSON(routerJSON string, config *Config) (*Engine, error) {
	return openNative(routerJSON, config, true, "Native router returned null")
}

// openNative converts its arguments before the ABI check, so a bad argument
// is reported the same way whatever the library.
func openNative(first string, config *Config, router bool, nullMsg string) (*Engine, error) {
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
	if err := checkABI(); err != nil {
		return nil, err
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
		return nil, &Error{Status: StatusModelError, Message: nullMsg}
	}
	return newEngine(raw), nil
}

func (e *engineCore) begin() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return closedError()
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

// Close rejects new calls (they fail with an error that wraps ErrClosed),
// waits for all in-flight calls to finish, then releases the native handle. It
// is idempotent, and concurrent Close calls all return only after the handle
// is released.
//
// Called from inside an OnEvent callback of the same engine, on the
// callback's goroutine, Close would wait for its own call. There it does not
// wait: it closes the engine at once and returns nil, and the running call
// releases the handle when it returns.
func (e *Engine) Close() error {
	c := e.c
	if c.inCallback() {
		c.closeDeferred()
		runtime.KeepAlive(e)
		return nil
	}
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

// closeDeferred marks the engine closed so new calls fail and returns without
// waiting. The last running call releases the handle (at once if none runs).
func (c *engineCore) closeDeferred() {
	c.mu.Lock()
	c.closed = true
	c.deferred = true
	idle := c.active == 0
	c.mu.Unlock()
	if idle {
		c.release()
	}
}

// goroutineID reads the current goroutine's id from the stack header
// ("goroutine N [running]:"); Go has no API for it.
func goroutineID() uint64 {
	var buf [64]byte
	text := buf[:runtime.Stack(buf[:], false)]
	text = text[len("goroutine "):]
	var id uint64
	for _, ch := range text {
		if ch < '0' || ch > '9' {
			break
		}
		id = id*10 + uint64(ch-'0')
	}
	return id
}

// inCallback reports whether the calling goroutine is running a call of this
// engine that has an OnEvent callback (native callbacks run on the calling
// goroutine, so Close there is a Close from a callback).
func (c *engineCore) inCallback() bool {
	c.mu.Lock()
	any := len(c.callbacks) > 0
	c.mu.Unlock()
	if !any {
		return false
	}
	id := goroutineID()
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.callbacks[id] > 0
}

// enterCallbacks registers the calling goroutine for the duration of a call
// with an OnEvent; the returned function unregisters it.
func (c *engineCore) enterCallbacks() func() {
	id := goroutineID()
	c.mu.Lock()
	if c.callbacks == nil {
		c.callbacks = map[uint64]int{}
	}
	c.callbacks[id]++
	c.mu.Unlock()
	return func() {
		c.mu.Lock()
		if c.callbacks[id]--; c.callbacks[id] == 0 {
			delete(c.callbacks, id)
		}
		c.mu.Unlock()
	}
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
// context.DeadlineExceeded). Errors are reported in this order: closed engine,
// argument conversions (NUL), an already-done ctx (no native call, no events),
// then the native call. No partial result is returned.
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
	// Order: closed, then argument conversions, then an already-done context
	// (no native call, no events), then the native call.
	if err := e.begin(); err != nil {
		return "", err
	}
	defer e.end()
	req, err := cstring(request)
	if err != nil {
		return "", err
	}
	defer C.free(unsafe.Pointer(req))
	if err := ctx.Err(); err != nil {
		return "", ctxError(err)
	}
	var requestID uint64
	var observer unsafe.Pointer
	if opts != nil {
		requestID = opts.RequestID
		if onEvent := opts.OnEvent; onEvent != nil {
			defer e.enterCallbacks()()
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
		return "", &Error{Status: StatusInferenceError, Message: "Native result was null"}
	}
	return C.GoString(result), nil
}
