#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StripThinkingResult {
    pub content: String,
    pub thinking_content: Option<String>,
}

const THINK_OPEN: &str = "<think>";
const THINK_CLOSE: &str = "</think>";

pub fn strip_thinking(content: &str) -> StripThinkingResult {
    if !content.contains(THINK_OPEN) {
        return StripThinkingResult {
            content: content.to_owned(),
            thinking_content: None,
        };
    }

    let mut remaining = content;
    let mut stripped = String::new();
    let mut thinking_blocks = Vec::new();

    while let Some(start) = remaining.find(THINK_OPEN) {
        stripped.push_str(&remaining[..start]);
        let after_open = &remaining[start + THINK_OPEN.len()..];

        if let Some(end) = after_open.find(THINK_CLOSE) {
            thinking_blocks.push(after_open[..end].to_owned());
            let mut rest = &after_open[end + THINK_CLOSE.len()..];
            rest = rest.trim_start_matches(char::is_whitespace);
            remaining = rest;
            continue;
        }

        thinking_blocks.push(after_open.to_owned());
        remaining = "";
        break;
    }

    stripped.push_str(remaining);

    StripThinkingResult {
        content: stripped.trim().to_owned(),
        thinking_content: (!thinking_blocks.is_empty()).then(|| thinking_blocks.join("\n")),
    }
}

#[cfg(test)]
mod tests {
    use super::{strip_thinking, StripThinkingResult};

    #[test]
    fn returns_input_when_no_think_tag_is_present() {
        let result = strip_thinking("hello");
        assert_eq!(
            result,
            StripThinkingResult {
                content: "hello".to_owned(),
                thinking_content: None,
            }
        );
    }

    #[test]
    fn strips_closed_think_blocks_and_trailing_whitespace() {
        let result = strip_thinking("before <think>hidden</think>\n\t after");
        assert_eq!(result.content, "before after");
        assert_eq!(result.thinking_content.as_deref(), Some("hidden"));
    }

    #[test]
    fn strips_unclosed_think_block_to_end_of_string() {
        let result = strip_thinking("answer <think>deliberation");
        assert_eq!(result.content, "answer");
        assert_eq!(result.thinking_content.as_deref(), Some("deliberation"));
    }
}
