//! 存储层：被 engine 引用。

/// 简单存储
pub struct Storage {
    path: String,
}

impl Storage {
    pub fn new(path: &str) -> Self {
        Self { path: path.to_string() }
    }

    /// 写入数据，返回字节数
    pub fn write(&self, data: &str) -> Result<u64, String> {
        let bytes = data.len() as u64;
        Ok(bytes)
    }

    /// 读取数据
    pub fn read(&self) -> Result<String, String> {
        Ok(format!("<{}>", self.path))
    }
}
