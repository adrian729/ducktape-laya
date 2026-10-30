package laya

import (
	"context"
	"errors"
	"math"
	"os"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
	"unsafe"
)

const (
	request = `{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}`
	choice  = `{"state":"Please refund me.","questions":{"kind":{"type":"choice","instructions":"What is requested?","criteria":["refund","other"]}}}`
)

func model(t *testing.T) string {
	path := os.Getenv("LAYA_MODEL")
	if path == "" {
		t.Skip("LAYA_MODEL is not set")
	}
	return path
}

func open(t *testing.T) *Engine {
	e, err := Open(model(t), &Config{Threads: 2})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { e.Close() })
	return e
}

func status(t *testing.T, err error) Status {
	t.Helper()
	var e *Error
	if !errors.As(err, &e) {
		t.Fatalf("want *Error, got %v", err)
	}
	return e.Status
}

func quote(s string) string { return strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s) }

func withModel(request, name string) string {
	return strings.Replace(request, "{", `{"model":"`+name+`",`, 1)
}

func mustOK(t *testing.T) func(string, error) string {
	return func(s string, err error) string {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
}

func TestRejectsNul(t *testing.T) {
	_, err := Open("a\x00b", nil)
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("want status 1")
	}
}

func TestPanicInEventContained(t *testing.T) {
	router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	calls := 0
	_, err = router.ExecuteContext(context.Background(), "{}", &CallOptions{RequestID: 1, OnEvent: func(Event) {
		calls++
		panic("contained observer")
	}})
	if status(t, err) != StatusInvalidArgument || calls != 2 {
		t.Fatalf("err=%v calls=%d", err, calls)
	}
}

func TestCloseIdempotent(t *testing.T) {
	router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	if router.Close() != nil || router.Close() != nil {
		t.Fatal("close failed")
	}
	_, err = router.Execute("{}")
	if status(t, err) != StatusInvalidArgument || !errors.Is(err, ErrClosed) {
		t.Fatal("want ErrClosed (status 1) after close")
	}
	_, err2 := router.Info()
	var first, second *Error
	if status(t, err2) != StatusInvalidArgument || !errors.Is(err2, ErrClosed) {
		t.Fatal("want ErrClosed (status 1) after close")
	}
	if !errors.As(err, &first) || !errors.As(err2, &second) || first == second {
		t.Fatal("closed errors must be distinct *Error values")
	}
	first.Message = "changed"
	if second.Message == "changed" || ErrClosed.Error() == "changed" {
		t.Fatal("closed errors share state")
	}
}

// Closed, then argument conversions, then an already-done context, then the
// native call.
func TestErrorOrder(t *testing.T) {
	router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	events := 0
	opts := &CallOptions{OnEvent: func(Event) { events++ }}
	// A NUL request beats a cancelled context.
	if _, err := router.ExecuteContext(cancelled, "{\x00}", opts); status(t, err) != StatusInvalidArgument || errors.Is(err, context.Canceled) {
		t.Fatalf("NUL vs cancelled: %v", err)
	}
	// A cancelled context fails at once: no native call, so no events.
	if _, err := router.ExecuteContext(cancelled, choice, opts); !errors.Is(err, context.Canceled) || events != 0 {
		t.Fatalf("cancelled: %v, %d events", err, events)
	}
	router.Close()
	// Closed beats both.
	if _, err := router.ExecuteContext(cancelled, "{\x00}", opts); !errors.Is(err, ErrClosed) {
		t.Fatalf("closed first: %v", err)
	}
}

func TestConfigJSON(t *testing.T) {
	data, err := (&Config{Threads: 2, MaxWindowsPerCall: 4096, Overflow: "scan", Temperatures: map[string]float64{"noul": 2, "choice": 1.5}}).marshal()
	want := `{"threads":2,"max_windows_per_call":4096,"overflow":"scan","temperatures":{"choice":1.5,"noul":2}}`
	if err != nil || string(data) != want {
		t.Fatalf("%s %v", data, err)
	}
	if data, _ := (&Config{}).marshal(); string(data) != "{}" {
		t.Fatalf("zero value sends %s", data)
	}
	for name, c := range map[string]*Config{
		"overflow":  {Overflow: "a\xffb"},
		"temp key":  {Temperatures: map[string]float64{"a\xff": 1}},
		"temp NaN":  {Temperatures: map[string]float64{"choice": math.NaN()}},
		"temp +Inf": {Temperatures: map[string]float64{"choice": math.Inf(1)}},
	} {
		if _, err := c.marshal(); status(t, err) != StatusInvalidArgument {
			t.Fatalf("%s: %v", name, err)
		}
	}
	if _, err := Open("model.gguf", &Config{Overflow: "\xff"}); status(t, err) != StatusInvalidArgument {
		t.Fatalf("Open: %v", err)
	}
}

func TestRouterConfigJSON(t *testing.T) {
	got, err := (&RouterConfig{
		Models: map[string]Route{
			"b": {Path: "b.gguf", Config: &Config{ContextTokens: 256}},
			"a": {Path: "a.gguf"},
		},
		MaxLoaded: 2,
		Auto:      &AutoRoutes{English: "a", Default: "b"},
	}).marshal()
	want := `{"models":{"a":"a.gguf","b":{"path":"b.gguf","config":{"context_tokens":256}}},"max_loaded":2,"auto":{"english":"a","default":"b"}}`
	if err != nil || got != want {
		t.Fatalf("%s %v", got, err)
	}
	for name, r := range map[string]*RouterConfig{
		"nil":       nil,
		"id":        {Models: map[string]Route{"a\xff": {Path: "a"}}},
		"path":      {Models: map[string]Route{"a": {Path: "a\xff"}}},
		"auto":      {Models: map[string]Route{"a": {Path: "a"}}, Auto: &AutoRoutes{English: "\xff"}},
		"route cfg": {Models: map[string]Route{"a": {Path: "a", Config: &Config{Overflow: "\xff"}}}},
	} {
		if _, err := OpenRouter(r, nil); status(t, err) != StatusInvalidArgument {
			t.Fatalf("%s: %v", name, err)
		}
	}
	router, err := OpenRouter(&RouterConfig{Models: map[string]Route{"unused": {Path: "go.mod"}}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	router.Close()
}

func TestNulRequest(t *testing.T) {
	router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	_, err = router.Execute("{\x00}")
	if status(t, err) != StatusInvalidArgument || !strings.Contains(err.Error(), "NUL") {
		t.Fatal(err)
	}
}

func TestDiagnostics(t *testing.T) {
	runtime, err := RuntimeInfo()
	if err != nil || !strings.Contains(runtime, `"abi_version":2`) {
		t.Fatal(runtime, err)
	}
	if os.Getenv("LAYA_MODEL") == "" {
		return
	}
	e := open(t)
	info := mustOK(t)(e.Info())
	if !strings.Contains(info, `"decision_type":"laya"`) {
		t.Fatal(info)
	}
}

func TestRealFeatures(t *testing.T) {
	path := model(t)
	ok := mustOK(t)
	e := open(t)
	single := ok(e.Execute(request))
	if !strings.Contains(single, `"values":{"refund":true}`) {
		t.Fatal(single)
	}
	var events []Event
	result := ok(e.ExecuteContext(context.Background(), "["+request+","+request+"]",
		&CallOptions{RequestID: math.MaxUint64, OnEvent: func(ev Event) { events = append(events, ev) }}))
	if result != "["+single+","+single+"]" {
		t.Fatal(result)
	}
	if len(events) != 2 || events[0].Kind != EventStart || events[0].ElapsedMs != 0 || events[1].Kind != EventEnd || events[1].ElapsedMs <= 0 {
		t.Fatal(events)
	}
	for _, ev := range events {
		if ev.RequestID != math.MaxUint64 || ev.Status != StatusOK {
			t.Fatal(events)
		}
	}
	// Reentry from an observer.
	ok(e.ExecuteContext(context.Background(), request, &CallOptions{RequestID: 23, OnEvent: func(Event) {
		if _, err := e.Execute(request); err != nil {
			t.Error(err)
		}
	}}))
	router, err := OpenRouterJSON(`{"models":{"english":"`+quote(path)+`"},"max_loaded":1}`, &Config{Threads: 2})
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	if !strings.Contains(ok(router.Execute(withModel(request, "english"))), `"model":"english"`) {
		t.Fatal("missing routed model")
	}
	if !strings.Contains(ok(router.Info()), `"abi_version":2`) {
		t.Fatal("router info")
	}
}

func TestSharedAcrossGoroutines(t *testing.T) {
	path := model(t)
	e := open(t)
	baseline := mustOK(t)(e.Execute(choice))
	// Two names for one file with one slot: concurrent calls evict each other.
	q := quote(path)
	router, err := OpenRouterJSON(`{"models":{"a":"`+q+`","b":"`+q+`"},"max_loaded":1}`, &Config{Threads: 2})
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	results := make([]string, 3)
	errs := make([]error, 6)
	for i := range results {
		wg.Add(1)
		go func() { defer wg.Done(); results[i], errs[i] = e.Execute(choice) }()
	}
	routed := make([]string, 2)
	for i, name := range []string{"a", "b"} {
		wg.Add(1)
		go func() { defer wg.Done(); routed[i], errs[3+i] = router.Execute(withModel(choice, name)) }()
	}
	var cancelledResult string
	wg.Add(1)
	go func() { defer wg.Done(); cancelledResult, errs[5] = e.ExecuteContext(ctx, choice, nil) }()
	cancel()
	wg.Wait()
	for i := 0; i < 5; i++ {
		if errs[i] != nil {
			t.Fatal(errs[i])
		}
	}
	for _, r := range results {
		if r != baseline {
			t.Fatal("concurrent result differs")
		}
	}
	for i, name := range []string{"a", "b"} {
		if want := baseline[:len(baseline)-1] + `,"model":"` + name + `"}`; routed[i] != want {
			t.Fatalf("routed %s: %s", name, routed[i])
		}
	}
	if errs[5] != nil {
		if status(t, errs[5]) != StatusCancelled {
			t.Fatal(errs[5])
		}
	} else if cancelledResult != baseline {
		t.Fatal("cancelled call result differs")
	}
}

func TestCloseInsideCallback(t *testing.T) {
	e := open(t)
	baseline, err := e.Execute(request)
	if err != nil {
		t.Fatal(err)
	}
	result, err := e.ExecuteContext(context.Background(), request, &CallOptions{OnEvent: func(ev Event) {
		if ev.Kind == EventStart {
			e.Close()
		}
	}})
	if err != nil || result != baseline {
		t.Fatalf("call %v, result changed %v", err, result != baseline)
	}
	if _, err := e.Execute(request); !errors.Is(err, ErrClosed) {
		t.Fatal("want ErrClosed")
	}
	if err := e.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestCloseFromOtherGoroutineWaitsDuringCallback(t *testing.T) {
	e := open(t)
	inCallback := make(chan struct{})
	release := make(chan struct{})
	var once sync.Once
	callDone := make(chan error, 1)
	go func() {
		_, err := e.ExecuteContext(context.Background(), request, &CallOptions{OnEvent: func(Event) {
			once.Do(func() {
				close(inCallback)
				<-release
			})
		}})
		callDone <- err
	}()
	<-inCallback
	closed := make(chan struct{}, 2)
	for i := 0; i < 2; i++ {
		go func() { e.Close(); closed <- struct{}{} }()
	}
	select {
	case <-closed:
		t.Fatal("Close returned while a call was inside its callback")
	case <-time.After(100 * time.Millisecond):
	}
	if _, err := e.Execute(request); !errors.Is(err, ErrClosed) {
		t.Fatal("want ErrClosed while closing")
	}
	close(release)
	if err := <-callDone; err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		select {
		case <-closed:
		case <-time.After(30 * time.Second):
			t.Fatal("Close did not return after the call finished")
		}
	}
	select {
	case <-e.c.done:
	default:
		t.Fatal("Close returned before release")
	}
}

func TestContextErrorsMatch(t *testing.T) {
	if !errors.Is(&Error{Status: StatusCancelled}, context.Canceled) ||
		!errors.Is(&Error{Status: StatusDeadlineExceeded}, context.DeadlineExceeded) ||
		errors.Is(&Error{Status: StatusCancelled}, context.DeadlineExceeded) {
		t.Fatal("Is mapping wrong")
	}
	router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	c, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := router.ExecuteContext(c, choice, nil); !errors.Is(err, context.Canceled) {
		t.Fatalf("want Canceled, got %v", err)
	}
	d, stop := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer stop()
	if _, err := router.ExecuteContext(d, choice, nil); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("want DeadlineExceeded, got %v", err)
	}
}

// A dropped, unclosed engine must be released by the finalizer. (The engine
// state used to point at itself through its sync.Cond, which kept the
// finalizer from ever running.)
func TestFinalizerReleasesDroppedEngine(t *testing.T) {
	released := make(chan unsafe.Pointer, 1)
	orig := closeNative
	closeNative = func(h unsafe.Pointer) { released <- h; orig(h) }
	defer func() { closeNative = orig }()

	var handle unsafe.Pointer
	func() {
		router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
		if err != nil {
			t.Fatal(err)
		}
		handle = router.c.handle
	}()
	deadline := time.Now().Add(10 * time.Second)
	for {
		runtime.GC()
		select {
		case h := <-released:
			if h != handle {
				t.Fatal("released a different handle")
			}
			return
		case <-time.After(20 * time.Millisecond):
		}
		if time.Now().After(deadline) {
			t.Fatal("finalizer never released the dropped engine")
		}
	}
}

// Close from an OnEvent callback does not deadlock: it closes at once and the
// running call releases the handle when it returns. (Model-free: the router's
// request is rejected after the start event.)
func TestGoroutineIDsDiffer(t *testing.T) {
	mine, ok := goroutineID()
	if !ok || mine == 0 {
		t.Fatalf("goroutineID() = %d, %v", mine, ok)
	}
	other := make(chan uint64)
	go func() {
		id, ok := goroutineID()
		if !ok {
			id = 0
		}
		other <- id
	}()
	if id := <-other; id == 0 || id == mine {
		t.Fatalf("another goroutine's id is %d, this one's %d", id, mine)
	}
}

func TestCloseFromCallbackDefers(t *testing.T) {
	router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	var closeErr error
	_, err = router.ExecuteContext(context.Background(), "{}", &CallOptions{OnEvent: func(Event) { closeErr = router.Close() }})
	if closeErr != nil || status(t, err) != StatusInvalidArgument {
		t.Fatalf("close %v, call %v", closeErr, err)
	}
	select {
	case <-router.c.done:
	default:
		t.Fatal("handle not released when the call returned")
	}
	if _, err := router.Execute("{}"); !errors.Is(err, ErrClosed) {
		t.Fatal("want ErrClosed")
	}
	if router.Close() != nil {
		t.Fatal("second Close failed")
	}
}

func TestStatusPropagation(t *testing.T) {
	router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	var events []Event
	_, err = router.ExecuteContext(context.Background(), "{}", &CallOptions{RequestID: math.MaxUint64, OnEvent: func(ev Event) { events = append(events, ev) }})
	var e *Error
	if !errors.As(err, &e) || e.Status != StatusInvalidArgument || errors.Is(err, ErrClosed) || e.Message == "" {
		t.Fatalf("want a native status 1 error, got %v", err)
	}
	if len(events) != 2 || events[0].Kind != EventStart || events[0].Status != StatusOK || events[0].ElapsedMs != 0 ||
		events[1].Kind != EventError || events[1].Status != StatusInvalidArgument || events[1].ElapsedMs < 0 {
		t.Fatalf("events %+v", events)
	}
	for _, ev := range events {
		if ev.RequestID != math.MaxUint64 {
			t.Fatalf("request id %d", ev.RequestID)
		}
	}
	events = nil
	router.ExecuteContext(context.Background(), "{}", &CallOptions{OnEvent: func(ev Event) { events = append(events, ev) }})
	if len(events) != 2 || events[0].RequestID != 0 {
		t.Fatalf("default request id: %+v", events)
	}
	router.c.mu.Lock()
	flags := len(router.c.flags)
	router.c.mu.Unlock()
	if flags != 0 {
		t.Fatalf("%d call flags leaked", flags)
	}
}

func TestParseEvent(t *testing.T) {
	ev, ok := parseEvent(`{"event":"end","request_id":18446744073709551615,"status":0,"elapsed_ms":12.5}`)
	if !ok || ev != (Event{EventEnd, math.MaxUint64, StatusOK, 12.5}) {
		t.Fatalf("%+v %v", ev, ok)
	}
	if _, ok := parseEvent(`{"event":`); ok {
		t.Fatal("malformed event accepted")
	}
}

// An expired deadline fails at once with status 6: no native call, no events.
func TestExpiredDeadlineNoEvents(t *testing.T) {
	router, err := OpenRouterJSON(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	expired, stop := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer stop()
	events := 0
	_, err = router.ExecuteContext(expired, choice, &CallOptions{OnEvent: func(Event) { events++ }})
	if status(t, err) != StatusDeadlineExceeded || events != 0 {
		t.Fatalf("%v, %d events", err, events)
	}
}

func TestRemainingMs(t *testing.T) {
	for _, c := range []struct {
		left    time.Duration
		ms      uint64
		expired bool
	}{
		{-time.Second, 0, true},
		{0, 0, true},
		{time.Nanosecond, 1, false},
		{time.Millisecond, 1, false},
		{time.Millisecond + 1, 2, false},
		{48 * time.Hour, 48 * 3600 * 1000, false},
		{math.MaxInt64, uint64(math.MaxInt64/int64(time.Millisecond)) + 1, false},
	} {
		if ms, expired := remainingMs(c.left); ms != c.ms || expired != c.expired {
			t.Fatalf("%v: %d %v", c.left, ms, expired)
		}
	}
}

// longScan is a request that runs for seconds: the overflow policy "scan"
// plans many windows over a long state.
func longScan() string {
	return `{"overflow":"scan","state":"` + strings.Repeat("Please refund me. ", 100) + `","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}`
}

var (
	baselineOnce sync.Once
	baselineTime time.Duration
)

// uncancelled is how long longScan takes to run to the end, measured once.
func uncancelled(t *testing.T, e *Engine) time.Duration {
	baselineOnce.Do(func() {
		start := time.Now()
		mustOK(t)(e.Execute(longScan()))
		baselineTime = time.Since(start)
	})
	return baselineTime
}

func TestCancellationStopsEarly(t *testing.T) {
	e := open(t)
	full := uncancelled(t, e)
	baseline := mustOK(t)(e.Execute(choice))
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := e.ExecuteContext(cancelled, choice, nil); status(t, err) != StatusCancelled {
		t.Fatalf("want 5, got %v", err)
	}
	// Cancelled from the start event: the native call must stop early.
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	begin := time.Now()
	_, err := e.ExecuteContext(ctx, longScan(), &CallOptions{OnEvent: func(ev Event) {
		if ev.Kind == EventStart {
			stop()
		}
	}})
	if elapsed := time.Since(begin); status(t, err) != StatusCancelled || elapsed > full/2 {
		t.Fatalf("err %v after %v (uncancelled run %v)", err, elapsed, full)
	}
	if got := mustOK(t)(e.Execute(choice)); got != baseline {
		t.Fatal("result changed after cancellation")
	}
	live, stop2 := context.WithCancel(context.Background())
	defer stop2()
	if got := mustOK(t)(e.ExecuteContext(live, choice, nil)); got != baseline {
		t.Fatal("result changed with live context")
	}
}

func TestDeadlineStopsEarly(t *testing.T) {
	e := open(t)
	full := uncancelled(t, e)
	ctx, stop := context.WithTimeout(context.Background(), full/10)
	defer stop()
	begin := time.Now()
	_, err := e.ExecuteContext(ctx, longScan(), nil)
	if elapsed := time.Since(begin); status(t, err) != StatusDeadlineExceeded || elapsed > full/2 {
		t.Fatalf("err %v after %v (uncancelled run %v)", err, elapsed, full)
	}
	// No upper bound: a deadline days away is a plain timeout.
	long, stop2 := context.WithTimeout(context.Background(), 72*time.Hour)
	defer stop2()
	mustOK(t)(e.ExecuteContext(long, choice, nil))
}

// Close does not return before the running call has finished.
func TestCloseWaitsForActiveCalls(t *testing.T) {
	e := open(t)
	started := make(chan struct{})
	proceed := make(chan struct{})
	var finished atomic.Bool
	done := make(chan error, 1)
	go func() {
		_, err := e.ExecuteContext(context.Background(), request, &CallOptions{OnEvent: func(ev Event) {
			switch ev.Kind {
			case EventStart:
				close(started)
				<-proceed
			case EventEnd:
				finished.Store(true)
			}
		}})
		done <- err
	}()
	<-started
	closed := make(chan struct{})
	go func() { e.Close(); close(closed) }()
	// Close has begun once new calls are refused; the call is still held in its start event.
	for !errors.Is(func() error { _, err := e.Info(); return err }(), ErrClosed) {
		runtime.Gosched()
	}
	select {
	case <-closed:
		t.Fatal("Close returned while the call was running")
	default:
	}
	close(proceed)
	<-closed
	if !finished.Load() {
		t.Fatal("Close returned before the running call finished")
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if _, err := e.Execute(request); !errors.Is(err, ErrClosed) {
		t.Fatal("want ErrClosed")
	}
}

func TestCloseCancelPending(t *testing.T) {
	e := open(t)
	full := uncancelled(t, e)
	started := make(chan struct{})
	type outcome struct {
		err     error
		elapsed time.Duration
	}
	done := make(chan outcome, 1)
	go func() {
		begin := time.Now()
		_, err := e.ExecuteContext(context.Background(), longScan(), &CallOptions{OnEvent: func(ev Event) {
			if ev.Kind == EventStart {
				close(started)
			}
		}})
		done <- outcome{err, time.Since(begin)}
	}()
	<-started
	if err := e.CloseCancelPending(); err != nil {
		t.Fatal(err)
	}
	// Close returned, so the call has returned.
	out := <-done
	if status(t, out.err) != StatusCancelled || out.elapsed > full/2 {
		t.Fatalf("err %v after %v (uncancelled run %v)", out.err, out.elapsed, full)
	}
	if _, err := e.Execute(request); !errors.Is(err, ErrClosed) {
		t.Fatal("want ErrClosed")
	}
}

func TestCloseCancelPendingFromCallback(t *testing.T) {
	e := open(t)
	full := uncancelled(t, e)
	var closeErr error
	begin := time.Now()
	_, err := e.ExecuteContext(context.Background(), longScan(), &CallOptions{OnEvent: func(ev Event) {
		if ev.Kind == EventStart {
			closeErr = e.CloseCancelPending()
		}
	}})
	if elapsed := time.Since(begin); closeErr != nil || status(t, err) != StatusCancelled || elapsed > full/2 {
		t.Fatalf("close %v, call %v after %v (uncancelled run %v)", closeErr, err, elapsed, full)
	}
	select {
	case <-e.c.done:
	default:
		t.Fatal("handle not released when the call returned")
	}
}

// A context linked to one call never cancels another, and the shared engine
// keeps running calls it does not cancel.
func TestCancelIsCallScoped(t *testing.T) {
	e := open(t)
	baseline := mustOK(t)(e.Execute(choice))
	ctx, cancel := context.WithCancel(context.Background())
	started := make(chan struct{})
	other := make(chan string, 1)
	go func() {
		<-started
		got, err := e.Execute(choice)
		if err != nil {
			t.Error(err)
		}
		other <- got
	}()
	_, err := e.ExecuteContext(ctx, longScan(), &CallOptions{OnEvent: func(ev Event) {
		if ev.Kind == EventStart {
			close(started)
			cancel()
		}
	}})
	if status(t, err) != StatusCancelled {
		t.Fatal(err)
	}
	if got := <-other; got != baseline {
		t.Fatal("a second call was disturbed by the first's cancellation")
	}
}
