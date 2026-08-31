package main

import "fmt"

func main() {
	g := NewGreeter("world")
	g.Hello()
	// 演示死代码：注释掉 unusedFunc 的调用
	fmt.Println(square(3))
}
