// Usage: decide model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json
// With --requests: one engine for all files (one per file with --fresh-engine), one result line per file.
use std::io::Read;
const USAGE: &str = "Usage: decide model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json";
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let path = args.first().ok_or(USAGE)?;
    let mut at = 1;
    // Optional open config JSON; the default is {"threads":4}.
    let body: serde_json::Value = match args.get(1) {
        Some(text) if !text.starts_with("--") => {
            at = 2;
            serde_json::from_str(text)?
        }
        _ => serde_json::json!({"threads": 4}),
    };
    let (mut files, mut fresh) = (None::<Vec<String>>, false);
    while at < args.len() {
        match args[at].as_str() {
            "--fresh-engine" => fresh = true,
            "--requests" if files.is_none() => {
                let mut list = Vec::new();
                while at + 1 < args.len() && !args[at + 1].starts_with("--") {
                    at += 1;
                    list.push(args[at].clone());
                }
                if list.is_empty() {
                    return Err(USAGE.into());
                }
                files = Some(list);
            }
            _ => return Err(USAGE.into()),
        }
        at += 1;
    }
    if fresh && files.is_none() {
        return Err(USAGE.into());
    }
    let mut config = ducktape_laya::Config::default();
    if let Some(n) = body["threads"].as_u64() {
        config = config.threads(n as u32);
    }
    if let Some(n) = body["context_tokens"].as_u64() {
        config = config.context_tokens(n as u32);
    }
    if let Some(policy) = body["overflow"].as_str() {
        config = config.overflow(policy);
    }
    if let Some(map) = body["temperatures"].as_object() {
        for (key, value) in map {
            config = config.temperature(key.clone(), value.as_f64().ok_or("bad temperature")?);
        }
    }
    let Some(files) = files else {
        let mut request = String::new();
        std::io::stdin().read_to_string(&mut request)?;
        let engine = ducktape_laya::Engine::open(path, &config)?;
        println!("{}", engine.execute(&request)?);
        return Ok(());
    };
    let mut engine = None;
    for file in files {
        let request = std::fs::read_to_string(&file)?;
        if engine.is_none() {
            engine = Some(ducktape_laya::Engine::open(path, &config)?);
        }
        println!("{}", engine.as_ref().unwrap().execute(&request)?);
        if fresh {
            engine = None;
        }
    }
    Ok(())
}
