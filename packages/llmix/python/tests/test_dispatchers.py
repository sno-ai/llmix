#!/usr/bin/env python3
"""Focused tests for built-in Python provider dispatchers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

import llmix.dispatchers as dispatchers
from llmix.pipeline import DispatchInput
from llmix.providers.base import LLMResponse

passed = 0
failed = 0


def assert_true(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}")


def assert_eq(actual: object, expected: object, msg: str) -> None:
    if actual == expected:
        assert_true(True, msg)
    else:
        assert_true(False, f"{msg}: expected {expected!r}, got {actual!r}")


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def response_completion(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        return LLMResponse(
            content="ok",
            usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            model=kwargs.get("model", "fallback-model"),
            success=True,
        )

    async def chat_completion(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(dict(kwargs))
        return LLMResponse(
            content="ok",
            usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            model=kwargs.get("model", "fallback-model"),
            success=True,
        )


def make_ctx(
    *,
    provider: str,
    model: str,
    kwargs: dict[str, Any] | None = None,
    common: dict[str, Any] | None = None,
    provider_options: dict[str, Any] | None = None,
    base_url: str | None = None,
    base_url_key: str = "baseUrl",
) -> DispatchInput:
    config: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "common": common or {},
    }
    if provider_options is not None:
        config["provider_options"] = provider_options
    if base_url is not None:
        config[base_url_key] = base_url

    return DispatchInput(
        provider=provider,
        model=model,
        api_key="runtime-key",
        messages=[
            {"role": "system", "content": "Follow the system rules."},
            {"role": "user", "content": "Say hello."},
        ],
        kwargs=kwargs or {},
        config=config,
    )


async def test_deepinfra_dispatch_forwards_sampling_controls() -> None:
    client = RecordingClient()
    dispatch = dispatchers.deepinfra_dispatch(client=client)
    response = await dispatch(
        make_ctx(
            provider="deepinfra",
            model="Qwen/Qwen3-32B",
            kwargs={
                "temperature": 0.2,
                "max_tokens": 512,
                "text": {"format": "json_object"},
                "top_p": 0.9,
                "seed": 7,
                "response_format": {"type": "json_object"},
                "stop": ["END"],
                "presence_penalty": 0.3,
                "frequency_penalty": 0.4,
                "top_k": 55,
            },
        )
    )

    assert_eq(response.content, "ok", "deepinfra dispatch: normalized response content")
    call = client.calls[0]
    assert_eq(call["instructions"], "Follow the system rules.", "deepinfra dispatch: system messages become instructions")
    assert_eq(call["input"], [{"role": "user", "content": "Say hello."}], "deepinfra dispatch: non-system messages are forwarded")
    assert_eq(call["temperature"], 0.2, "deepinfra dispatch: temperature forwarded")
    assert_eq(call["max_output_tokens"], 512, "deepinfra dispatch: max_output_tokens forwarded")
    assert_eq(call["text"], {"format": "json_object"}, "deepinfra dispatch: text format forwarded")
    assert_eq(call["top_p"], 0.9, "deepinfra dispatch: top_p forwarded")
    assert_eq(call["seed"], 7, "deepinfra dispatch: seed forwarded")
    assert_eq(call["response_format"], {"type": "json_object"}, "deepinfra dispatch: response_format forwarded")
    assert_eq(call["stop"], ["END"], "deepinfra dispatch: stop forwarded")
    assert_eq(call["presence_penalty"], 0.3, "deepinfra dispatch: presence_penalty forwarded")
    assert_eq(call["frequency_penalty"], 0.4, "deepinfra dispatch: frequency_penalty forwarded")
    assert_eq(call["top_k"], 55, "deepinfra dispatch: top_k forwarded")


async def test_deepinfra_dispatch_builds_client_with_thinking_controls() -> None:
    original_load = dispatchers._load_provider_attr
    captured_inits: list[dict[str, Any]] = []

    class FakeDeepInfraClient:
        def __init__(self, **kwargs: Any) -> None:
            captured_inits.append(dict(kwargs))

        async def response_completion(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(content="ok", usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, model="deepinfra-model", success=True)

    def fake_load(name: str) -> Any:
        if name == "DeepInfraClient":
            return FakeDeepInfraClient
        return original_load(name)

    dispatchers._load_provider_attr = fake_load
    try:
        override_dispatch = dispatchers.deepinfra_dispatch()
        await override_dispatch(
            make_ctx(
                provider="deepinfra",
                model="Qwen/Qwen3-32B",
                common={"enable_thinking": True},
                provider_options={"deepinfra": {"enable_thinking": False, "thinking_budget": 128}},
                base_url="https://deepinfra.override/v1/openai",
            )
        )
        fallback_dispatch = dispatchers.deepinfra_dispatch()
        await fallback_dispatch(
            make_ctx(
                provider="deepinfra",
                model="Qwen/Qwen3-32B",
                common={"enable_thinking": True},
                provider_options={"deepinfra": {"thinking_budget": 256}},
                base_url="https://deepinfra.fallback/v1/openai",
            )
        )
    finally:
        dispatchers._load_provider_attr = original_load

    first_init, second_init = captured_inits
    assert_eq(first_init["api_key"], "runtime-key", "deepinfra dispatch: runtime API key wins")
    assert_eq(first_init["base_url"], "https://deepinfra.override/v1/openai", "deepinfra dispatch: base URL forwarded")
    assert_eq(first_init["model"], "Qwen/Qwen3-32B", "deepinfra dispatch: model forwarded")
    assert_eq(first_init["enable_thinking"], False, "deepinfra dispatch: provider option overrides common thinking flag")
    assert_eq(first_init["thinking_budget"], 128, "deepinfra dispatch: provider thinking budget forwarded")
    assert_eq(second_init["enable_thinking"], True, "deepinfra dispatch: common thinking flag is fallback")
    assert_eq(second_init["thinking_budget"], 256, "deepinfra dispatch: fallback case keeps provider thinking budget")


async def test_together_dispatch_forwards_sampling_controls() -> None:
    client = RecordingClient()
    dispatch = dispatchers.together_dispatch(client=client)
    response = await dispatch(
        make_ctx(
            provider="together",
            model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            kwargs={
                "temperature": 0.6,
                "max_output_tokens": 256,
                "response_format": {"type": "json_schema"},
                "stop_sequences": ["DONE"],
                "top_p": 0.85,
                "seed": 11,
                "presence_penalty": 0.1,
                "frequency_penalty": 0.2,
                "top_k": 32,
            },
        )
    )

    assert_eq(response.model, "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", "together dispatch: normalized response model")
    call = client.calls[0]
    assert_eq(call["max_output_tokens"], 256, "together dispatch: max_output_tokens forwarded")
    assert_eq(call["response_format"], {"type": "json_schema"}, "together dispatch: response_format forwarded")
    assert_eq(call["stop"], ["DONE"], "together dispatch: stop_sequences normalized to stop")
    assert_eq(call["top_p"], 0.85, "together dispatch: top_p forwarded")
    assert_eq(call["seed"], 11, "together dispatch: seed forwarded")
    assert_eq(call["presence_penalty"], 0.1, "together dispatch: presence_penalty forwarded")
    assert_eq(call["frequency_penalty"], 0.2, "together dispatch: frequency_penalty forwarded")
    assert_eq(call["top_k"], 32, "together dispatch: top_k forwarded")


async def test_novita_dispatch_forwards_sampling_controls() -> None:
    client = RecordingClient()
    dispatch = dispatchers.novita_dispatch(client=client)
    response = await dispatch(
        make_ctx(
            provider="novita",
            model="qwen/qwen3-235b-a22b-instruct-2507",
            kwargs={
                "temperature": 0.4,
                "max_output_tokens": 384,
                "response_format": {"type": "json_object"},
                "stop_sequences": ["FINAL"],
                "top_p": 0.8,
                "seed": 19,
                "presence_penalty": 0.15,
                "frequency_penalty": 0.25,
                "top_k": 48,
            },
        )
    )

    assert_eq(response.model, "qwen/qwen3-235b-a22b-instruct-2507", "novita dispatch: normalized response model")
    call = client.calls[0]
    assert_eq(call["max_output_tokens"], 384, "novita dispatch: max_output_tokens forwarded")
    assert_eq(call["response_format"], {"type": "json_object"}, "novita dispatch: response_format forwarded")
    assert_eq(call["stop"], ["FINAL"], "novita dispatch: stop_sequences normalized to stop")
    assert_eq(call["top_p"], 0.8, "novita dispatch: top_p forwarded")
    assert_eq(call["seed"], 19, "novita dispatch: seed forwarded")
    assert_eq(call["presence_penalty"], 0.15, "novita dispatch: presence_penalty forwarded")
    assert_eq(call["frequency_penalty"], 0.25, "novita dispatch: frequency_penalty forwarded")
    assert_eq(call["top_k"], 48, "novita dispatch: top_k forwarded")


async def test_novita_dispatch_builds_client_with_thinking_controls() -> None:
    original_load = dispatchers._load_provider_attr
    captured_inits: list[dict[str, Any]] = []

    class FakeNovitaClient:
        def __init__(self, **kwargs: Any) -> None:
            captured_inits.append(dict(kwargs))

        async def response_completion(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(content="ok", usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, model="novita-model", success=True)

    def fake_load(name: str) -> Any:
        if name == "NovitaClient":
            return FakeNovitaClient
        return original_load(name)

    dispatchers._load_provider_attr = fake_load
    try:
        override_dispatch = dispatchers.novita_dispatch()
        await override_dispatch(
            make_ctx(
                provider="novita",
                model="qwen/qwen3-235b-a22b-instruct-2507",
                common={"enable_thinking": True},
                provider_options={"novita": {"enable_thinking": False, "thinking_budget": 96}},
                base_url="https://novita.override/v3/openai",
            )
        )
        fallback_dispatch = dispatchers.novita_dispatch()
        await fallback_dispatch(
            make_ctx(
                provider="novita",
                model="qwen/qwen3-235b-a22b-instruct-2507",
                common={"enable_thinking": True},
                provider_options={"novita": {"thinking_budget": 160}},
                base_url="https://novita.fallback/v3/openai",
            )
        )
    finally:
        dispatchers._load_provider_attr = original_load

    first_init, second_init = captured_inits
    assert_eq(first_init["api_key"], "runtime-key", "novita dispatch: runtime API key wins")
    assert_eq(first_init["base_url"], "https://novita.override/v3/openai", "novita dispatch: base URL forwarded")
    assert_eq(first_init["model"], "qwen/qwen3-235b-a22b-instruct-2507", "novita dispatch: model forwarded")
    assert_eq(first_init["enable_thinking"], False, "novita dispatch: provider option overrides common thinking flag")
    assert_eq(first_init["thinking_budget"], 96, "novita dispatch: provider thinking budget forwarded")
    assert_eq(second_init["enable_thinking"], True, "novita dispatch: common thinking flag is fallback")
    assert_eq(second_init["thinking_budget"], 160, "novita dispatch: fallback case keeps provider thinking budget")


async def test_resolve_base_url_accepts_snake_case_from_loader() -> None:
    # Regression: the MDA loader normalizes baseUrl -> base_url via
    # _CAMEL_TO_SNAKE. Dispatchers must respect the normalized form, otherwise
    # configs loaded through the public loader silently fall back to the
    # provider default endpoint.
    ctx_snake = make_ctx(
        provider="openai",
        model="gpt-4.1",
        base_url="https://snake.example.com/v1",
        base_url_key="base_url",
    )
    ctx_camel = make_ctx(
        provider="openai",
        model="gpt-4.1",
        base_url="https://camel.example.com/v1",
        base_url_key="baseUrl",
    )
    assert_eq(
        dispatchers._resolve_base_url(ctx_snake, "https://default/v1"),
        "https://snake.example.com/v1",
        "_resolve_base_url: snake_case base_url from loader wins",
    )
    assert_eq(
        dispatchers._resolve_base_url(ctx_camel, "https://default/v1"),
        "https://camel.example.com/v1",
        "_resolve_base_url: legacy baseUrl still resolved",
    )
    assert_eq(
        dispatchers._resolve_gpu_base_url(ctx_snake),
        "https://snake.example.com/v1",
        "_resolve_gpu_base_url: snake_case base_url from loader wins",
    )


async def test_set_key_pool_rejects_prebuilt_client_dispatch() -> None:
    """set_key_pool must raise when the dispatch was built with client=,
    because KeyPool rotation would silently corrupt on auth failures. (GH #7)
    """
    from llmix.key_pool import KeyPool
    from llmix.pipeline import CallPipeline, PipelineConfig

    client = RecordingClient()
    dispatch = dispatchers.together_dispatch(client=client)
    pipeline = CallPipeline(PipelineConfig(dispatch=dispatch))

    # Bypass is recorded on the dispatch function itself.
    bypass = getattr(dispatch, "__llmix_bypass_key_pool_providers__", frozenset())
    assert_true("together" in bypass, "bypass hard-gate: factory stamps provider name on dispatch")

    raised = False
    try:
        pipeline.set_key_pool("together", KeyPool(["sk-1", "sk-2"]))
    except ValueError as exc:
        raised = True
        assert_true("prebuilt client" in str(exc).lower() or "client=" in str(exc), "bypass hard-gate: error mentions prebuilt client")
    assert_true(raised, "bypass hard-gate: set_key_pool raises ValueError")

    # A different provider on the same pipeline is unaffected.
    try:
        pipeline.set_key_pool("openai", KeyPool(["sk-a"]))
        ok = True
    except ValueError:
        ok = False
    assert_true(ok, "bypass hard-gate: other providers still accept pools")


async def test_factory_without_client_does_not_gate() -> None:
    """Factories called without client= do not stamp bypass — pool registration works."""
    from llmix.key_pool import KeyPool
    from llmix.pipeline import CallPipeline, PipelineConfig

    dispatch = dispatchers.openai_dispatch()  # no client=
    pipeline = CallPipeline(PipelineConfig(dispatch=dispatch))

    bypass = getattr(dispatch, "__llmix_bypass_key_pool_providers__", frozenset())
    assert_eq(bool(bypass), False, "no-client factory: no bypass stamped")

    try:
        pipeline.set_key_pool("openai", KeyPool(["sk-x"]))
        ok = True
    except ValueError:
        ok = False
    assert_true(ok, "no-client factory: set_key_pool succeeds")


async def test_gemini_client_dispatch_rejects_google_pool() -> None:
    from llmix.key_pool import KeyPool
    from llmix.pipeline import CallPipeline, PipelineConfig

    client = RecordingClient()
    dispatch = dispatchers.gemini_dispatch(client=cast(Any, client))
    pipeline = CallPipeline(PipelineConfig(dispatch=dispatch))

    bypass = getattr(dispatch, "__llmix_bypass_key_pool_providers__", frozenset())
    assert_true("google" in bypass, "gemini dispatch: bypass stamps canonical google provider")

    raised = False
    try:
        pipeline.set_key_pool("google", KeyPool(["sk-google"]))
    except ValueError:
        raised = True
    assert_true(raised, "gemini dispatch: set_key_pool rejects google pool for prebuilt client")


async def test_openrouter_client_dispatch_rejects_deepseek_pool() -> None:
    from llmix.key_pool import KeyPool
    from llmix.pipeline import CallPipeline, PipelineConfig

    dispatch = dispatchers.openrouter_dispatch(client=cast(Any, RecordingClient()))

    bypass = getattr(dispatch, "__llmix_bypass_key_pool_providers__", frozenset())
    assert_true("deepseek" in bypass, "openrouter dispatch: bypass stamps canonical deepseek provider")
    assert_true("openrouter" in bypass, "openrouter dispatch: bypass stamps canonical openrouter provider")

    for provider in ("deepseek", "openrouter"):
        pipeline = CallPipeline(PipelineConfig(dispatch=dispatch))
        raised = False
        try:
            pipeline.set_key_pool(provider, KeyPool([f"sk-{provider}"]))
        except ValueError:
            raised = True
        assert_true(raised, f"openrouter dispatch: set_key_pool rejects {provider} pool for prebuilt client")


async def test_openrouter_dispatch_maps_deepseek_v4_flash_alias() -> None:
    cases = [
        ("deepseek-v4-flash", "deepseek/deepseek-v4-flash"),
        ("qwen3.5-27b", "qwen/qwen3.5-27b"),
        ("qwen3.6-27b", "qwen/qwen3.6-27b"),
    ]
    for model, expected in cases:
        client = RecordingClient()
        dispatch = dispatchers.openrouter_dispatch(client=cast(Any, client))
        response = await dispatch(
            make_ctx(
                provider="openrouter",
                model=model,
                kwargs={"provider": {"sort": "price"}, "reasoning": {"enabled": False}},
            )
        )

        assert_eq(response.model, expected, f"openrouter dispatch: response uses {expected}")
        assert_eq(client.calls[0]["model"], expected, f"openrouter dispatch: {model} alias maps to provider model ID")
        assert_eq(client.calls[0]["provider"], {"sort": "price"}, "openrouter dispatch: provider routing forwarded")
        assert_eq(client.calls[0]["reasoning"], {"enabled": False}, "openrouter dispatch: reasoning options forwarded")


async def test_openrouter_dispatch_config_overrides_default_routing() -> None:
    client = RecordingClient()
    dispatch = dispatchers.openrouter_dispatch(client=cast(Any, client))
    await dispatch(
        make_ctx(
            provider="openrouter",
            model="deepseek-v4-flash",
            kwargs={"extra_body": {"provider": {"sort": "price"}}},
            provider_options={
                "openrouter": {
                    "provider": {"sort": "latency"},
                    "reasoning": {"enabled": False},
                }
            },
        )
    )

    assert_eq(
        client.calls[0]["provider"],
        {"sort": "latency"},
        "openrouter dispatch: config provider routing overrides default price sorting",
    )
    assert_eq(
        client.calls[0]["reasoning"],
        {"enabled": False},
        "openrouter dispatch: config reasoning forwarded",
    )


async def test_pipeline_config_bypass_survives_wrapped_dispatch() -> None:
    from llmix.key_pool import KeyPool
    from llmix.pipeline import CallPipeline, PipelineConfig

    base_dispatch = dispatchers.openai_dispatch(client=cast(Any, RecordingClient()))

    async def wrapped_dispatch(ctx: DispatchInput):
        return await base_dispatch(ctx)

    pipeline = CallPipeline(
        PipelineConfig(
            dispatch=wrapped_dispatch,
            bypass_key_pool_providers=frozenset({"openai"}),
        )
    )

    raised = False
    try:
        pipeline.set_key_pool("openai", KeyPool(["sk-openai"]))
    except ValueError:
        raised = True
    assert_true(raised, "pipeline config bypass: wrapped dispatch still rejects matching provider pool")


async def main() -> None:
    await test_deepinfra_dispatch_forwards_sampling_controls()
    await test_deepinfra_dispatch_builds_client_with_thinking_controls()
    await test_together_dispatch_forwards_sampling_controls()
    await test_novita_dispatch_forwards_sampling_controls()
    await test_novita_dispatch_builds_client_with_thinking_controls()
    await test_resolve_base_url_accepts_snake_case_from_loader()
    await test_set_key_pool_rejects_prebuilt_client_dispatch()
    await test_factory_without_client_does_not_gate()
    await test_gemini_client_dispatch_rejects_google_pool()
    await test_openrouter_client_dispatch_rejects_deepseek_pool()
    await test_openrouter_dispatch_maps_deepseek_v4_flash_alias()
    await test_openrouter_dispatch_config_overrides_default_routing()
    await test_pipeline_config_bypass_survives_wrapped_dispatch()

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
