#!/usr/bin/env python3
"""Suite 5B: Built-in OpenAI-compatible provider dispatchers.

Every test makes a REAL HTTP call through the shipped LLMix dispatchers.
No mocks. Requires provider API keys in the environment.

Coverage:
  - DeepInfra via llmix.deepinfra_dispatch()
  - Together via llmix.together_dispatch()
  - Novita via llmix.novita_dispatch()
  - Cross-provider parity on a shared prompt
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

from conftest import (
    assert_contains,
    assert_eq,
    assert_gt,
    assert_success,
    assert_true,
    assert_valid_usage,
    make_call_input,
    make_real_pipeline,
    print_summary,
    skip_unless,
)
from llmix import deepinfra_dispatch, novita_dispatch, together_dispatch


DEEPINFRA_MODEL = "Qwen/Qwen3-32B"
TOGETHER_MODEL = "Qwen/Qwen2.5-7B-Instruct-Turbo"
NOVITA_MODEL = "qwen/qwen3.5-27b"


def _call_input(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = 0,
    max_output_tokens: int = 64,
    provider_options: dict | None = None,
    keep_thinking_output: bool = False,
):
    return make_call_input(
        provider=provider,
        model=model,
        messages=messages,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        provider_options=provider_options,
        keep_thinking_output=keep_thinking_output,
    )


@skip_unless("DEEPINFRA_API_KEY")
async def test_5b_1_deepinfra_simple_completion():
    pipeline, inst = make_real_pipeline(deepinfra_dispatch(), "deepinfra")
    result = await pipeline.call(
        _call_input(
            provider="deepinfra",
            model=DEEPINFRA_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "What is the capital of Japan? Reply with just the city name.",
                }
            ],
            max_output_tokens=24,
        )
    )

    assert_success(result, "5B.1 DeepInfra simple completion")
    assert_contains(result.content.lower(), "tokyo", "5B.1 DeepInfra correct answer")
    assert_valid_usage(result, "5B.1 DeepInfra usage")
    assert_eq(inst.call_count, 1, "5B.1 DeepInfra dispatch called once")
    assert_true(len(result.model) > 0, "5B.1 DeepInfra model field populated")


@skip_unless("DEEPINFRA_API_KEY")
async def test_5b_2_deepinfra_thinking_smoke():
    pipeline, inst = make_real_pipeline(deepinfra_dispatch(), "deepinfra")
    result = await pipeline.call(
        _call_input(
            provider="deepinfra",
            model=DEEPINFRA_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "Think step by step, then answer: what is 23 * 47? Reply with the final number too.",
                }
            ],
            max_output_tokens=256,
            provider_options={"deepinfra": {"enable_thinking": True, "thinking_budget": 128}},
            keep_thinking_output=True,
        )
    )

    assert_success(result, "5B.2 DeepInfra thinking-mode call")
    assert_contains(result.content, "<think>", "5B.2 DeepInfra raw thinking blocks preserved")
    assert_valid_usage(result, "5B.2 DeepInfra thinking usage")
    assert_eq(inst.call_count, 1, "5B.2 DeepInfra thinking dispatch called once")


@skip_unless("TOGETHER_API_KEY")
async def test_5b_3_together_simple_completion():
    pipeline, inst = make_real_pipeline(together_dispatch(), "together")
    result = await pipeline.call(
        _call_input(
            provider="together",
            model=TOGETHER_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "What is 2+2? Reply with just the number.",
                }
            ],
            max_output_tokens=16,
        )
    )

    assert_success(result, "5B.3 Together simple completion")
    assert_contains(result.content, "4", "5B.3 Together correct answer")
    assert_valid_usage(result, "5B.3 Together usage")
    assert_eq(inst.call_count, 1, "5B.3 Together dispatch called once")
    assert_true(len(result.model) > 0, "5B.3 Together model field populated")


@skip_unless("NOVITA_API_KEY")
async def test_5b_4_novita_simple_completion():
    pipeline, inst = make_real_pipeline(novita_dispatch(), "novita")
    result = await pipeline.call(
        _call_input(
            provider="novita",
            model=NOVITA_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "What is the capital of Germany? Reply with just the city name.",
                }
            ],
            max_output_tokens=24,
        )
    )

    assert_success(result, "5B.4 Novita simple completion")
    assert_contains(result.content.lower(), "berlin", "5B.4 Novita correct answer")
    assert_valid_usage(result, "5B.4 Novita usage")
    assert_eq(inst.call_count, 1, "5B.4 Novita dispatch called once")
    assert_true(len(result.model) > 0, "5B.4 Novita model field populated")


@skip_unless("DEEPINFRA_API_KEY", "TOGETHER_API_KEY", "NOVITA_API_KEY")
async def test_5b_5_cross_provider_parity():
    prompt = "List exactly 3 primary colors. Reply with just the color names, comma-separated."
    messages = [{"role": "user", "content": prompt}]
    expected_colors = ("red", "blue", "yellow", "green")

    deepinfra_pipeline, deepinfra_inst = make_real_pipeline(deepinfra_dispatch(), "deepinfra")
    together_pipeline, together_inst = make_real_pipeline(together_dispatch(), "together")
    novita_pipeline, novita_inst = make_real_pipeline(novita_dispatch(), "novita")

    deepinfra_result = await deepinfra_pipeline.call(
        _call_input(provider="deepinfra", model=DEEPINFRA_MODEL, messages=messages, max_output_tokens=40)
    )
    together_result = await together_pipeline.call(
        _call_input(provider="together", model=TOGETHER_MODEL, messages=messages, max_output_tokens=40)
    )
    novita_result = await novita_pipeline.call(
        _call_input(provider="novita", model=NOVITA_MODEL, messages=messages, max_output_tokens=40)
    )

    assert_success(deepinfra_result, "5B.5 DeepInfra parity call")
    assert_success(together_result, "5B.5 Together parity call")
    assert_success(novita_result, "5B.5 Novita parity call")

    for provider_name, result in (
        ("DeepInfra", deepinfra_result),
        ("Together", together_result),
        ("Novita", novita_result),
    ):
        lowered = result.content.lower()
        found = [color for color in expected_colors if color in lowered]
        assert_gt(len(found), 1, f"5B.5 {provider_name} parity response mentions multiple primary colors")

    assert_eq(deepinfra_inst.call_count, 1, "5B.5 DeepInfra parity dispatch count")
    assert_eq(together_inst.call_count, 1, "5B.5 Together parity dispatch count")
    assert_eq(novita_inst.call_count, 1, "5B.5 Novita parity dispatch count")


async def main():
    print("Suite 5B: Built-in OpenAI-compatible Dispatchers")
    print("=" * 60)
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        await test()
    return print_summary("Suite 5B: Built-in OpenAI-compatible Dispatchers")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
