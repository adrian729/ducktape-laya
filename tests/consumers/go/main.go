// Usage: decide model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json
// With --requests: one engine for all files (one per file with --fresh-engine), one result line per file.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"

	laya "github.com/adrian729/ducktape-laya/bindings/go"
)

const usage = "Usage: decide model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json"

func run() error {
	args := os.Args[1:]
	if len(args) < 1 {
		return fmt.Errorf(usage)
	}
	at := 1
	config := laya.Config{Threads: 4}
	if len(args) > 1 && !strings.HasPrefix(args[1], "--") {
		at = 2
		var body struct {
			Threads       uint32             `json:"threads"`
			ContextTokens uint32             `json:"context_tokens"`
			Overflow      string             `json:"overflow"`
			Temperatures  map[string]float64 `json:"temperatures"`
		}
		if err := json.Unmarshal([]byte(args[1]), &body); err != nil {
			return err
		}
		config = laya.Config{Threads: body.Threads, ContextTokens: body.ContextTokens, Overflow: body.Overflow, Temperatures: body.Temperatures}
	}
	var files []string
	requests, fresh := false, false
	for ; at < len(args); at++ {
		switch {
		case args[at] == "--fresh-engine":
			fresh = true
		case args[at] == "--requests" && !requests:
			requests = true
			for at+1 < len(args) && !strings.HasPrefix(args[at+1], "--") {
				at++
				files = append(files, args[at])
			}
			if len(files) == 0 {
				return fmt.Errorf(usage)
			}
		default:
			return fmt.Errorf(usage)
		}
	}
	if fresh && !requests {
		return fmt.Errorf(usage)
	}
	if !requests {
		request, err := io.ReadAll(os.Stdin)
		if err != nil {
			return err
		}
		engine, err := laya.Open(args[0], &config)
		if err != nil {
			return err
		}
		defer engine.Close()
		result, err := engine.Execute(string(request))
		if err != nil {
			return err
		}
		fmt.Println(result)
		return nil
	}
	var engine *laya.Engine
	defer func() {
		if engine != nil {
			engine.Close()
		}
	}()
	for _, file := range files {
		request, err := os.ReadFile(file)
		if err != nil {
			return err
		}
		if engine == nil {
			if engine, err = laya.Open(args[0], &config); err != nil {
				return err
			}
		}
		result, err := engine.Execute(string(request))
		if err != nil {
			return err
		}
		fmt.Println(result)
		if fresh {
			engine.Close()
			engine = nil
		}
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
