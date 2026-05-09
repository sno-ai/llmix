/**
 * Thinking Token Stripping
 *
 * Strips <think>...</think> blocks from LLM response content.
 * Used to clean reasoning model output (e.g., Qwen3, DeepSeek-R1) before
 * returning to callers.
 *
 * Controlled by `common.keepThinkingOutput` in LLMix config:
 * - false (default): strips thinking blocks, captures to response.thinkingContent
 * - true: preserves thinking blocks in content as-is
 */

// Closed blocks: <think>...</think> with optional trailing whitespace
const THINK_CLOSED_RE = /<think>.*?<\/think>\s*/gs;

// Unclosed fallback: <think> to end of string
const THINK_UNCLOSED_RE = /<think>.*$/s;

export interface StripThinkingResult {
  content: string;
  thinkingContent: string | null;
}

/**
 * Strip <think>...</think> blocks from LLM response content.
 *
 * Returns stripped content and captured thinking content (null if none found).
 * Multiple thinking blocks are joined with newlines.
 */
export function stripThinking(content: string): StripThinkingResult {
  // Fast guard: skip regex if no thinking tags present
  if (!content.includes("<think>")) {
    return { content, thinkingContent: null };
  }

  const thinkingBlocks: string[] = [];

  // Extract closed thinking blocks
  // Reset lastIndex since we use the global flag
  THINK_CLOSED_RE.lastIndex = 0;
  while (true) {
    const match = THINK_CLOSED_RE.exec(content);
    if (match === null) {
      break;
    }

    const block = match[0];
    const inner = block.substring(
      "<think>".length,
      block.lastIndexOf("</think>"),
    );
    thinkingBlocks.push(inner);
  }

  // Strip closed blocks
  THINK_CLOSED_RE.lastIndex = 0;
  let stripped = content.replace(THINK_CLOSED_RE, "");

  // Handle unclosed <think> tag (strip from <think> to end)
  const unclosedMatch = THINK_UNCLOSED_RE.exec(stripped);
  if (unclosedMatch) {
    const unclosedText = unclosedMatch[0].substring("<think>".length);
    thinkingBlocks.push(unclosedText);
    stripped = stripped.replace(THINK_UNCLOSED_RE, "");
  }

  stripped = stripped.trim();

  if (thinkingBlocks.length > 0) {
    return { content: stripped, thinkingContent: thinkingBlocks.join("\n") };
  }

  return { content: stripped, thinkingContent: null };
}

// TODO: Wire into client.ts call path during Phase 2 integration:
// - Check config common.keepThinkingOutput before stripping
// - Set response.thinkingContent with captured thinking
// - Apply stripThinking() before JSON parsing when parseJson: true
// - Cache stores raw response; stripping applied on read
