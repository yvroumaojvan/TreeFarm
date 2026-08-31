// Go 后台任务：模拟调用 Python API
package main

import "fmt"

func main() {
	// 模拟：worker 调用 py_service 的 create_user / get_user API
	fmt.Println("calling create_user(name, email)")
	fmt.Println("calling get_user(user_id)")
	process()
}

func process() {
	fmt.Println("worker processing...")
}
