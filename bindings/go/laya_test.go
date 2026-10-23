package laya

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"os"
	"runtime"
	"strings"
	"sync"
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

func status(t *testing.T, err error) int32 {
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

func TestRejectsBadPath(t *testing.T) {
	if _, err := Open("missing.gguf", nil); err == nil {
		t.Fatal("expected error")
	}
}

func TestRejectsNul(t *testing.T) {
	_, err := Open("a\x00b", nil)
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("want status 1")
	}
}

func TestPanicInEventContained(t *testing.T) {
	router, err := OpenRouter(`{"models":{"unused":"go.mod"}}`, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	calls := 0
	_, err = router.ExecuteContext(context.Background(), "{}", &CallOptions{RequestID: 1, OnEvent: func(string) {
		calls++
		panic("contained observer")
	}})
	if status(t, err) != StatusInvalidArgument || calls != 2 {
		t.Fatalf("err=%v calls=%d", err, calls)
	}
}

func TestCloseIdempotent(t *testing.T) {
	router, err := OpenRouter(`{"models":{"unused":"go.mod"}}`, nil)
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
	_, err = router.Info()
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("want status 1 after close")
	}
}

func TestNulRequest(t *testing.T) {
	router, err := OpenRouter(`{"models":{"unused":"go.mod"}}`, nil)
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
	_, err := e.Execute(strings.Replace(request, "{", `{"metadata":"caller tag",`, 1))
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("execute should reject unknown top-level keys")
	}
	single := ok(e.Execute(request))
	if !strings.Contains(single, `"values":{"refund":true}`) {
		t.Fatal(single)
	}
	var events []string
	result := ok(e.ExecuteContext(context.Background(), "["+request+","+request+"]",
		&CallOptions{RequestID: 21, OnEvent: func(s string) { events = append(events, s) }}))
	if result != "["+single+","+single+"]" {
		t.Fatal(result)
	}
	if len(events) != 2 || !strings.Contains(events[0], `"event":"start"`) || !strings.Contains(events[1], `"request_id":21`) {
		t.Fatal(events)
	}
	_, err = e.ExecuteContext(context.Background(), "{}", &CallOptions{RequestID: 22, OnEvent: func(string) { panic("contained observer") }})
	if err == nil {
		t.Fatal("expected error")
	}
	// Reentry from an observer.
	ok(e.ExecuteContext(context.Background(), request, &CallOptions{RequestID: 23, OnEvent: func(string) {
		if _, err := e.Execute(request); err != nil {
			t.Error(err)
		}
	}}))
	router, err := OpenRouter(`{"models":{"english":"`+quote(path)+`"},"max_loaded":1}`, &Config{Threads: 2})
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	if !strings.Contains(ok(router.Execute(withModel(request, "english"))), `"model":"english"`) {
		t.Fatal("missing routed model")
	}
	_, err = router.Execute(request)
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("router requires model")
	}
	if !strings.Contains(ok(router.Info()), `"abi_version":2`) {
		t.Fatal("router info")
	}
}

func TestCancellationAndDeadline(t *testing.T) {
	e := open(t)
	baseline := mustOK(t)(e.Execute(choice))
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := e.ExecuteContext(cancelled, choice, nil)
	if status(t, err) != StatusCancelled {
		t.Fatal("execute: want 5")
	}
	short, stop := context.WithTimeout(context.Background(), time.Millisecond)
	defer stop()
	_, err = e.ExecuteContext(short, choice, nil)
	if status(t, err) != StatusDeadlineExceeded {
		t.Fatalf("want 6, got %v", err)
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

func TestSharedAcrossGoroutines(t *testing.T) {
	path := model(t)
	e := open(t)
	baseline := mustOK(t)(e.Execute(choice))
	// Two names for one file with one slot: concurrent calls evict each other.
	q := quote(path)
	router, err := OpenRouter(`{"models":{"a":"`+q+`","b":"`+q+`"},"max_loaded":1}`, &Config{Threads: 2})
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

func TestCloseWaitsForActiveCalls(t *testing.T) {
	e := open(t)
	entered := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		_, err := e.ExecuteContext(context.Background(), request, &CallOptions{OnEvent: func(s string) {
			if strings.Contains(s, `"event":"start"`) {
				close(entered)
			}
		}})
		done <- err
	}()
	<-entered
	time.Sleep(10 * time.Millisecond) // let the callback return; inference is still running
	e.Close()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	default:
		t.Fatal("Close returned before the running call finished")
	}
	if _, err := e.Execute(request); !errors.Is(err, ErrClosed) {
		t.Fatal("want ErrClosed")
	}
}

func TestCloseInsideCallback(t *testing.T) {
	e := open(t)
	baseline, err := e.Execute(request)
	if err != nil {
		t.Fatal(err)
	}
	result, err := e.ExecuteContext(context.Background(), request, &CallOptions{OnEvent: func(s string) {
		if strings.Contains(s, `"event":"start"`) {
			e.CloseDeferred()
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
		_, err := e.ExecuteContext(context.Background(), request, &CallOptions{OnEvent: func(string) {
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
	router, err := OpenRouter(`{"models":{"unused":"go.mod"}}`, nil)
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

func TestConfigRejected(t *testing.T) {
	path := model(t)
	for name, c := range map[string]*Config{
		"threads":       {Threads: 257},
		"context small": {ContextTokens: 8},
		"context huge":  {ContextTokens: 1 << 30},
		"temp zero":     {Temperatures: map[string]float64{"choice": 0}},
		"temp negative": {Temperatures: map[string]float64{"choice": -1}},
		"temp NaN":      {Temperatures: map[string]float64{"choice": math.NaN()}},
	} {
		e, err := Open(path, c)
		if err == nil {
			e.Close()
			t.Fatalf("%s: expected error", name)
		}
		if status(t, err) != StatusInvalidArgument {
			t.Fatalf("%s: %v", name, err)
		}
	}
	if _, err := OpenRouter(`{"models":{"a":{"path":"`+quote(path)+`","config":{"bogus":1}}}}`, nil); status(t, err) != StatusInvalidArgument {
		t.Fatalf("route config: %v", err)
	}
}

func TestOverflowPolicy(t *testing.T) {
	path := model(t)
	ok := mustOK(t)
	long := `{"state":"` + strings.Repeat("refund ", 800) + `","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}`
	e, err := Open(path, &Config{Threads: 2, Overflow: "truncate_end"})
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	out := ok(e.Execute(long))
	if !strings.Contains(out, `"state_tokens_dropped":`) || strings.Contains(out, `"state_tokens_dropped":0`) {
		t.Fatal(out)
	}
	if _, err := e.Execute(strings.Replace(long, "{", `{"overflow":"reject",`, 1)); status(t, err) != 1 {
		t.Fatal("request overflow reject must win")
	}
	if _, err := Open(path, &Config{Threads: 2, Overflow: "bogus"}); status(t, err) != 1 {
		t.Fatal("invalid overflow accepted")
	}
}

func TestConfigApplied(t *testing.T) {
	path := model(t)
	ok := mustOK(t)
	base := open(t)
	baseline := ok(base.Execute(choice))
	e, err := Open(path, &Config{Threads: 1, ContextTokens: 256, Temperatures: map[string]float64{"choice.2": 6, "noul": 2, "choice.3_5": 1.7}})
	if err != nil {
		t.Fatal(err)
	}
	defer e.Close()
	if info := ok(e.Info()); !strings.Contains(info, `"context_tokens":256`) || !strings.Contains(info, `"trained_context_tokens"`) {
		t.Fatal(info)
	}
	if got := ok(e.Execute(choice)); got == baseline {
		t.Fatalf("temperature override did not change the result: %s", got)
	}
	// An empty (non-nil) config equals defaults.
	d, err := Open(path, &Config{})
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if got := ok(d.Execute(choice)); got != baseline {
		t.Fatal("empty config changed the result")
	}
}

func TestPerRequestSettings(t *testing.T) {
	e := open(t)
	ok := mustOK(t)
	baseline := ok(e.Execute(choice))
	withKey := func(extra string) string { return strings.Replace(choice, "{", "{"+extra+",", 1) }
	if got := ok(e.Execute(withKey(`"max_tokens":512`))); got != baseline {
		t.Fatal("max_tokens at the default capacity changed the result")
	}
	if got := ok(e.Execute(withKey(`"temperatures":{"choice.2":6}`))); got == baseline {
		t.Fatal("per-request temperature did not change the result")
	}
	for _, extra := range []string{`"max_tokens":0`, `"max_tokens":100000`, `"temperatures":{"choice":0}`} {
		if _, err := e.Execute(withKey(extra)); status(t, err) != StatusInvalidArgument {
			t.Fatalf("%s: %v", extra, err)
		}
	}
}

func TestRouterRouteConfig(t *testing.T) {
	path := quote(model(t))
	ok := mustOK(t)
	router, err := OpenRouter(`{"models":{"plain":"`+path+`","hot":{"path":"`+path+`","config":{"temperatures":{"choice.2":6}}}},"max_loaded":1}`, &Config{Threads: 2})
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	plain := ok(router.Execute(withModel(choice, "plain")))
	hot := ok(router.Execute(withModel(choice, "hot")))
	again := ok(router.Execute(withModel(choice, "plain")))
	if plain != again {
		t.Fatal("plain route not deterministic")
	}
	strip := func(s string) string { return strings.Split(s, `"model"`)[0] }
	if strip(plain) == strip(hot) {
		t.Fatalf("route config did not apply: %s vs %s", plain, hot)
	}
	if !strings.Contains(ok(router.Info()), `"abi_version":2`) {
		t.Fatal("router info")
	}
}

func TestInfoFields(t *testing.T) {
	e := open(t)
	info := mustOK(t)(e.Info())
	if strings.Contains(info, `"max_tokens"`) || !strings.Contains(info, `"context_tokens":512`) {
		t.Fatal(info)
	}
	rt, err := RuntimeInfo()
	if err != nil || !strings.Contains(rt, `"default_context_tokens"`) || strings.Contains(rt, "tokens_per_question") {
		t.Fatal(rt, err)
	}
	r := mustOK(t)(e.Execute(choice))
	if !strings.Contains(r, `"answer_confidence"`) || !strings.Contains(r, `"confidence"`) {
		t.Fatal(r)
	}
}

func TestRouterAutoValidation(t *testing.T) {
	open := func(json string) (*Engine, error) { return OpenRouter(json, &Config{Threads: 2}) }
	for _, bad := range []string{
		`{"models":{"a":"laya_test.go"},"auto":{"english":"a"}}`,
		`{"models":{"a":"laya_test.go"},"auto":{"english":"a","default":"missing"}}`,
		`{"models":{"a":"laya_test.go"},"auto":{"english":"a","default":"a","extra":"a"}}`,
		`{"models":{"auto":"laya_test.go"}}`,
	} {
		if _, err := open(bad); err == nil || status(t, err) != StatusInvalidArgument {
			t.Fatalf("%s: %v", bad, err)
		}
	}
	plain, err := open(`{"models":{"a":"laya_test.go"}}`)
	if err != nil {
		t.Fatal(err)
	}
	defer plain.Close()
	auto, err := open(`{"models":{"a":"laya_test.go"},"auto":{"english":"a","default":"a"}}`)
	if err != nil {
		t.Fatal(err)
	}
	defer auto.Close()
	with := func(extra string) string { return strings.Replace(choice, "{", "{"+extra+",", 1) }
	// Rejected before any model is loaded.
	for engine, extra := range map[*Engine]string{
		plain: `"model":"auto"`,
		auto:  `"lang":"en"`,
	} {
		if _, err := engine.Execute(with(extra)); status(t, err) != StatusInvalidArgument {
			t.Fatalf("%s: %v", extra, err)
		}
	}
	for _, extra := range []string{`"model":"a","lang":"en"`, `"model":"auto","lang":""`} {
		if _, err := auto.Execute(with(extra)); status(t, err) != StatusInvalidArgument {
			t.Fatalf("%s: %v", extra, err)
		}
	}
}

func TestRouterAutoRoutesByLanguage(t *testing.T) {
	path := quote(model(t))
	router, err := OpenRouter(`{"models":{"en":"`+path+`","ml":"`+path+`"},"max_loaded":2,"auto":{"english":"en","default":"ml"}}`, &Config{Threads: 2})
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	ok := mustOK(t)
	ask := func(state, extra string) (string, string) {
		result := ok(router.Execute(`{"model":"auto"` + extra + `,"state":"` + state + `","questions":{"kind":{"type":"choice","instructions":"What is requested?","criteria":["refund","other"]}}}`))
		var out struct {
			Model string
			Usage struct{ Routing struct{ Reason string } }
		}
		if err := json.Unmarshal([]byte(result), &out); err != nil {
			t.Fatal(err)
		}
		return out.Model, out.Usage.Routing.Reason
	}
	const english = "I was charged twice for my subscription this month, so please refund me."
	for _, c := range []struct{ state, extra, model, reason string }{
		{english, "", "en", "english_text"},
		{"Пожалуйста, верните мне деньги за подписку.", "", "ml", "non_latin_script"},
		{english, `,"lang":"fr"`, "ml", "lang"},
	} {
		if model, reason := ask(c.state, c.extra); model != c.model || reason != c.reason {
			t.Fatalf("%q%s: got %s/%s, want %s/%s", c.state, c.extra, model, reason, c.model, c.reason)
		}
	}
}

func TestAnswerConfidenceIsMaxProbability(t *testing.T) {
	e := open(t)
	var out struct {
		Answers struct {
			Kind struct {
				Probabilities    map[string]float64
				Confidence       float64
				AnswerConfidence float64 `json:"answer_confidence"`
			}
		}
	}
	if err := json.Unmarshal([]byte(mustOK(t)(e.Execute(choice))), &out); err != nil {
		t.Fatal(err)
	}
	best := 0.0
	for _, p := range out.Answers.Kind.Probabilities {
		best = math.Max(best, p)
	}
	if out.Answers.Kind.AnswerConfidence != best || out.Answers.Kind.Confidence < 0 || out.Answers.Kind.Confidence > 1 {
		t.Fatalf("%+v", out.Answers.Kind)
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
		router, err := OpenRouter(`{"models":{"unused":"go.mod"}}`, nil)
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
