use ducktape_laya::{Config, Engine};
use serde_json::{json, Map, Value};
use std::io::{self, Write};

fn ask(label: &str, fallback: &str) -> Result<String, Box<dyn std::error::Error>> {
    print!("{label} [{fallback}]: ");
    io::stdout().flush()?;
    let mut line = String::new();
    if io::stdin().read_line(&mut line)? == 0 {
        return Err("Input ended before the form was complete.".into());
    }
    Ok(if line.trim().is_empty() {
        fallback
    } else {
        line.trim()
    }
    .to_owned())
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let model = std::env::args()
        .nth(1)
        .or_else(|| std::env::var("LAYA_MODEL").ok())
        .ok_or("Use ./demo rust or supply a model path.")?;
    println!("Press Enter to use the example. Separate answer choices with |.");
    let state = ask("Text to classify", "I was charged twice. Please refund me.")?;
    let question = ask("Question", "Which department should handle this message?")?;
    let choices = ask("Possible answers", "billing | technical | sales")?;
    let mut criteria = Map::new();
    for label in choices.split('|').map(str::trim) {
        if label.is_empty() || criteria.contains_key(label) {
            return Err("Answer choices must be nonempty and unique.".into());
        }
        criteria.insert(label.into(), json!(label));
    }
    if !(2..=8).contains(&criteria.len()) {
        return Err("Enter 2 to 8 possible answers.".into());
    }
    let request = json!({"state": state, "questions": {"decision": {
        "type": "choice", "instructions": question, "criteria": criteria
    }}});
    println!("Running Laya...");
    let engine = Engine::open_with(&model, &Config::default().threads(2))?;
    let result: Value = serde_json::from_str(&engine.execute(&request.to_string())?)?;
    let answer = &result["answers"]["decision"];
    println!(
        "\nBest answer: {}\nProbabilities:",
        answer["choice"].as_str().ok_or("Missing choice")?
    );
    for (label, probability) in answer["probabilities"]
        .as_object()
        .ok_or("Missing probabilities")?
    {
        println!(
            "  {:.2}%  {label}",
            probability.as_f64().ok_or("Invalid probability")? * 100.0
        );
    }
    Ok(())
}
