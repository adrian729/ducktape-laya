use std::io::Read;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .ok_or("Usage: decide model.gguf < request.json")?;
    let mut request = String::new();
    std::io::stdin().read_to_string(&mut request)?;
    let engine = ducktape_laya::Engine::open(&path, 2)?;
    println!("{}", engine.execute(&request)?);
    Ok(())
}
