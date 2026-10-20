package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	laya "github.com/adrian729/ducktape-laya/bindings/go"
)

func run() error {
	if len(os.Args) < 2 || len(os.Args) > 3 {
		return fmt.Errorf("Usage: decide model.gguf [config.json] < request.json")
	}
	request, err := io.ReadAll(os.Stdin)
	if err != nil {
		return err
	}
	config := laya.Config{Threads: 4}
	if len(os.Args) == 3 {
		var body struct {
			Threads       uint32             `json:"threads"`
			ContextTokens uint32             `json:"context_tokens"`
			Overflow      string             `json:"overflow"`
			Temperatures  map[string]float64 `json:"temperatures"`
		}
		if err := json.Unmarshal([]byte(os.Args[2]), &body); err != nil {
			return err
		}
		config = laya.Config{Threads: body.Threads, ContextTokens: body.ContextTokens, Overflow: body.Overflow, Temperatures: body.Temperatures}
	}
	engine, err := laya.Open(os.Args[1], &config)
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

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
