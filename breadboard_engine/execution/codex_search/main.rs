use bm25::{Document, Language, SearchEngineBuilder};
use serde::Deserialize;
use serde_json::json;

#[derive(Deserialize)]
struct Input {
    documents: Vec<String>,
    arguments: serde_json::Value,
}

#[derive(Deserialize)]
struct SearchParams {
    query: String,
    limit: Option<usize>,
}

fn main() {
    let input: Input = serde_json::from_reader(std::io::stdin()).expect("valid scorer transport");
    let arguments: SearchParams = match serde_json::from_value(input.arguments) {
        Ok(arguments) => arguments,
        Err(error) => {
            println!("{}", json!({"decode_error": error.to_string()}));
            return;
        }
    };
    // core/src/tools/handlers/tool_search.rs:39-46,85-96,117-122.
    // Run the pinned crate itself, retaining English tokenizer, stemming,
    // stopword removal, scoring, tie ordering, and zero-score filtering.
    let query = arguments.query.trim();
    if query.is_empty() {
        println!("{}", json!({"error": "query must not be empty"}));
        return;
    }
    let limit = arguments.limit.unwrap_or(8); // tools/src/tool_discovery.rs:7.
    if limit == 0 {
        println!("{}", json!({"error": "limit must be greater than zero"}));
        return;
    }
    if input.documents.is_empty() {
        println!("[]");
        return;
    }
    let documents: Vec<Document<usize>> = input.documents.into_iter()
        .enumerate().map(|(id, text)| Document::new(id, text)).collect();
    let engine = SearchEngineBuilder::<usize>::with_documents(Language::English, documents).build();
    let ids: Vec<usize> = engine.search(query, limit).into_iter()
        .map(|result| result.document.id).collect();
    println!("{}", serde_json::to_string(&ids).unwrap());
}
