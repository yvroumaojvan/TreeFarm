//! 入口：调用 engine。

mod engine;
mod storage;
mod unused;

use engine::{spawn_workers, AsyncEngine, Config, DefaultEngine, Engine};
use storage::Storage;

fn main() {
    let storage = Storage::new("./demo.db");
    let engine = DefaultEngine::new(storage);
    let config = Config {
        name: "demo",
        workers: 4,
    };
    match engine.run(&config) {
        Ok(msg) => println!("{msg}"),
        Err(e) => eprintln!("{e}"),
    }
    let workers = spawn_workers(engine, 2);
    println!("spawned {} workers", workers.len());

    // async 演示（block_on 是标准库没有的，仅示意语法）
    let _async_engine = AsyncEngine;
    println!("async engine ready");
    let _ = unused::unused_fn; // 故意不调用，让死代码检测生效
}
