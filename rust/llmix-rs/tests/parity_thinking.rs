use llmix_rs::{strip_thinking, StripThinkingResult};

#[test]
fn no_tags_passthrough_matches_python_contract() {
    assert_eq!(
        strip_thinking("plain response"),
        StripThinkingResult {
            content: "plain response".to_owned(),
            thinking_content: None,
        }
    );
}

#[test]
fn closed_and_unclosed_blocks_are_stripped_and_collected() {
    let result = strip_thinking(
        "before <think>first</think>\n middle <think>second</think>\n after <think>tail",
    );

    assert_eq!(result.content, "before middle after");
    assert_eq!(
        result.thinking_content.as_deref(),
        Some("first\nsecond\ntail")
    );
}

#[test]
fn closed_block_trailing_whitespace_is_removed_from_visible_content() {
    let result = strip_thinking("answer<think>hidden</think>\n\t  next");

    assert_eq!(result.content, "answernext");
    assert_eq!(result.thinking_content.as_deref(), Some("hidden"));
}
