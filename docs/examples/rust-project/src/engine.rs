//! 引擎：演示 trait 继承、泛型、生命周期、unsafe/async。

use std::fmt::Debug;

use crate::storage::Storage;

/// 可运行的引擎 trait
pub trait Engine: Debug + Send {
    fn run<'a>(&self, config: &'a Config) -> Result<String, String>;
}

/// 引擎配置（含生命周期引用）
#[derive(Debug)]
pub struct Config<'a> {
    pub name: &'a str,
    pub workers: u32,
}

/// 默认引擎实现：组合 Storage
#[derive(Debug)]
pub struct DefaultEngine {
    storage: Storage,
}

impl DefaultEngine {
    pub fn new(storage: Storage) -> Self {
        Self { storage }
    }

    /// 处理请求：泛型 + 错误传播 ?
    fn process<T: Debug>(&self, payload: T) -> Result<u64, String> {
        let data = format!("{:?}", payload);
        let size = self.storage.write(&data)?;
        Ok(size)
    }
}

impl Engine for DefaultEngine {
    fn run<'a>(&self, config: &'a Config) -> Result<String, String> {
        if config.workers == 0 {
            return Err("workers 不能为 0".into());
        }
        let payload = format!("{}:{}", config.name, config.workers);
        let n = self.process(payload)?;
        Ok(format!("run {n} bytes"))
    }
}

/// 异步引擎（演示 async fn）
pub struct AsyncEngine;

impl AsyncEngine {
    pub async fn fetch<'a>(&self, url: &'a str) -> Result<String, String> {
        // 演示 async/await 语法（不真实联网）
        let _url = url;
        Ok("fetched".into())
    }
}

/// 泛型辅助函数
pub fn spawn_workers<T: Engine>(engine: T, count: u32) -> Vec<String> {
    let mut out = Vec::new();
    for i in 0..count {
        out.push(format!("worker-{i}: {:?}", engine));
    }
    out
}
