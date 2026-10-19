package laya

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	primitive = `{"state":"Refund me","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}`
	request   = `{"state":"Please refund me.","schema":{"type":"object","properties":{"refund":{"type":"boolean","description":"Is a refund requested?"}}}}`
	legacyReq = `{"state":"Please refund me.","questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}}}`
)

func model(t *testing.T) string {
	path := os.Getenv("LAYA_MODEL")
	if path == "" {
		t.Skip("LAYA_MODEL is not set")
	}
	return path
}

func open(t *testing.T) *Engine {
	e, err := Open(model(t), 2)
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
	if _, err := Open("missing.gguf", 2); err == nil {
		t.Fatal("expected error")
	}
}

func TestRejectsNul(t *testing.T) {
	_, err := Open("a\x00b", 2)
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("want status 1")
	}
}

func TestPanicInObserverContained(t *testing.T) {
	router, err := OpenRouter(`{"models":{"unused":"go.mod"}}`, 1)
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
	router, err := OpenRouter(`{"models":{"unused":"go.mod"}}`, 1)
	if err != nil {
		t.Fatal(err)
	}
	if router.Close() != nil || router.Close() != nil {
		t.Fatal("close failed")
	}
	_, err = router.Execute("{}")
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("want status 1 after close")
	}
	_, err = router.Info()
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("want status 1 after close")
	}
}

func TestNulRequest(t *testing.T) {
	router, err := OpenRouter(`{"models":{"unused":"go.mod"}}`, 1)
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
	if err != nil || !strings.Contains(runtime, `"abi_version":1`) {
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
	tagged := strings.Replace(primitive, "{", `{"metadata":"caller tag",`, 1)
	if ok(e.Decide(tagged)) != ok(e.Decide(primitive)) {
		t.Fatal("legacy metadata changed result")
	}
	_, err := e.Execute(tagged)
	if status(t, err) != StatusInvalidArgument {
		t.Fatal("execute should reject unknown metadata")
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
	router, err := OpenRouter(`{"models":{"english":"`+quote(path)+`"},"max_loaded":1}`, 2)
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
	if !strings.Contains(ok(router.Info()), `"abi_version":1`) {
		t.Fatal("router info")
	}
}

func TestCancellationAndDeadline(t *testing.T) {
	e := open(t)
	baseline := mustOK(t)(e.Execute(legacyReq))
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := e.ExecuteContext(cancelled, legacyReq, nil)
	if status(t, err) != StatusCancelled {
		t.Fatal("execute: want 5")
	}
	_, err = e.DecideContext(cancelled, legacyReq, nil)
	if status(t, err) != StatusCancelled {
		t.Fatal("decide: want 5")
	}
	short, stop := context.WithTimeout(context.Background(), time.Millisecond)
	defer stop()
	_, err = e.ExecuteContext(short, legacyReq, nil)
	if status(t, err) != StatusDeadlineExceeded {
		t.Fatalf("want 6, got %v", err)
	}
	if got := mustOK(t)(e.Execute(legacyReq)); got != baseline {
		t.Fatal("result changed after cancellation")
	}
	live, stop2 := context.WithCancel(context.Background())
	defer stop2()
	if got := mustOK(t)(e.ExecuteContext(live, legacyReq, nil)); got != baseline {
		t.Fatal("result changed with live context")
	}
}

func TestSharedAcrossGoroutines(t *testing.T) {
	path := model(t)
	e := open(t)
	baseline := mustOK(t)(e.Execute(legacyReq))
	// Two names for one file with one slot: concurrent calls evict each other.
	q := quote(path)
	router, err := OpenRouter(`{"models":{"a":"`+q+`","b":"`+q+`"},"max_loaded":1}`, 2)
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
		go func() { defer wg.Done(); results[i], errs[i] = e.Execute(legacyReq) }()
	}
	routed := make([]string, 2)
	for i, name := range []string{"a", "b"} {
		wg.Add(1)
		go func() { defer wg.Done(); routed[i], errs[3+i] = router.Execute(withModel(legacyReq, name)) }()
	}
	var cancelledResult string
	wg.Add(1)
	go func() { defer wg.Done(); cancelledResult, errs[5] = e.ExecuteContext(ctx, legacyReq, nil) }()
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
				time.Sleep(50 * time.Millisecond)
			}
		}})
		done <- err
	}()
	<-entered
	e.Close()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if _, err := e.Execute(request); status(t, err) != StatusInvalidArgument {
		t.Fatal("want closed error")
	}
}
