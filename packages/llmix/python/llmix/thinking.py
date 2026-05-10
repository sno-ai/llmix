"""
Thinking Token Stripping

Strips <think>...</think> blocks from LLM response content.
Used to clean reasoning model output (e.g., Qwen3, DeepSeek-R1) before
returning to callers.

Controlled by `common.keep_thinking_output` in LLMix config:
- False (default): strips thinking blocks, captures to response.thinking_content
- True: preserves thinking blocks in content as-is
"""

import re

# Closed blocks: <think>...</think> with optional trailing whitespace
_THINK_CLOSED_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

# Unclosed fallback: <think> to end of string
_THINK_UNCLOSED_RE = re.compile(r"<think>.*$", re.DOTALL)


def strip_thinking(content: str) -> tuple[str, str | None]:
    """Strip <think>...</think> blocks from LLM response content.

    Returns:
        Tuple of (stripped_content, thinking_content_or_none).
        thinking_content is None if no thinking blocks were found.
        Multiple thinking blocks are joined with newlines.

    Note:
        This will match ``<think>`` tags anywhere in the content, including
        inside code blocks or XML output. If this is a concern, set
        ``common.keep_thinking_output: true`` in config to bypass
        stripping entirely and preserve the raw content.
    """
    # Fast guard: skip regex if no thinking tags present
    if "<think>" not in content:
        return (content, None)

    # Extract closed thinking blocks
    closed_blocks = [
        m.group()[len("<think>") : m.group().rindex("</think>")]
        for m in _THINK_CLOSED_RE.finditer(content)
    ]

    # Strip closed blocks
    stripped = _THINK_CLOSED_RE.sub("", content)

    # Handle unclosed <think> tag (strip from <think> to end)
    unclosed_match = _THINK_UNCLOSED_RE.search(stripped)
    if unclosed_match:
        unclosed_text = unclosed_match.group()[len("<think>") :]
        closed_blocks.append(unclosed_text)
        stripped = _THINK_UNCLOSED_RE.sub("", stripped)

    stripped = stripped.strip()

    if closed_blocks:
        thinking = "\n".join(closed_blocks)
        return (stripped, thinking)

    return (stripped, None)


# TODO: Wire into client.py call path during Phase 2 integration:
# - Check config common.keep_thinking_output before stripping
# - Set response.thinking_content with captured thinking
# - Apply strip_thinking() before JSON parsing when parse_json=True
# - Cache stores raw response; stripping applied on read
