package main

import "fmt"

// Logger 被 Greeter 嵌入（struct 嵌入 → inherit 基因）
type Logger struct{}

func (l *Logger) Log(msg string) {
	fmt.Println("[" + msg + "]")
}

// Greeter 嵌入 Logger
type Greeter struct {
	Logger
	name string
}

func NewGreeter(name string) *Greeter {
	return &Greeter{name: name}
}

func (g *Greeter) Hello() {
	g.Log("hi, " + g.name)
}

func square(n int) int {
	return n * n
}

// unusedFunc 从未被调用（死代码演示）
func unusedFunc() int {
	return 1
}
