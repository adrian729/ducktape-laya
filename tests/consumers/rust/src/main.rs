use std::io::Read;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let path = args
        .next()
        .ok_or("Usage: decide model.gguf [config.json] < request.json")?;
    let mut request = String::new();
    std::io::stdin().read_to_string(&mut request)?;
    // Optional open config JSON; the default is {"threads":4}.
    let body: serde_json::Value = match args.next() {
        Some(text) => serde_json::from_str(&text)?,
        None => serde_json::json!({"threads": 4}),
    };
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
    let engine = ducktape_laya::Engine::open(&path, &config)?;
    println!("{}", engine.execute(&request)?);
    Ok(())
}
