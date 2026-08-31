//! 死代码演示：以下函数/结构体从未被调用。

/// 从未被调用的函数
pub fn unused_fn() -> u32 {
    42
}

/// 从未被使用的结构体
pub struct DeadStruct {
    pub id: u32,
}

impl DeadStruct {
    pub fn new(id: u32) -> Self {
        Self { id }
    }
}
