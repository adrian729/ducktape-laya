package main

import (
	"fmt"
	"io"
	"os"

	laya "github.com/adrian729/ducktape-laya/bindings/go"
)

func run() error {
	if len(os.Args) < 2 {
		return fmt.Errorf("Usage: decide model.gguf < request.json")
	}
	request, err := io.ReadAll(os.Stdin)
	if err != nil {
		return err
	}
	engine, err := laya.Open(os.Args[1], 4)
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
