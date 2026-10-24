"""Model client. Luminal Cloud and local servers speak the OpenAI chat API; Claude models go
through the Anthropic SDK. Never raises on provider failure.

A model is picked with a spec `provider:model` (LLM_MODEL, `--model`, or the MCP `model`
argument), for example `luminal:deepseek-ai/DeepSeek-V4.1-Flash`, `local:qwen2.5-coder:7b` or
`anthropic:claude-sonnet-5`.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import math
import os
import random
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from forkward import home, telemetry

ROLES = {"generator": "GENERATOR_MODEL", "reflector": "REFLECTOR_MODEL", "curator": "CURATOR_MODEL"}
PROVIDERS = ("luminal", "deepseek", "local", "anthropic")
CLAUDE_MODELS = ("claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5")
_SELECTED: contextvars.ContextVar[str | None] = contextvars.ContextVar("model_spec", default=None)
SETTINGS = home() / "settings.json"
# Benchmark runs (search, the test evaluation) never follow the saved default model.
BENCHMARK = False


def saved_default() -> str | None:
    try:
        return json.loads(SETTINGS.read_text()).get("model") or None
    except (OSError, ValueError):
        return None


def save_default(spec: str | None) -> None:
    if spec:
        parse_spec(spec)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if SETTINGS.exists():
        try:
            data = json.loads(SETTINGS.read_text())
        except ValueError:
            data = {}
    data["model"] = spec
    SETTINGS.write_text(json.dumps(data, indent=1))


def parse_spec(spec: str) -> tuple[str, str]:
    provider_name, sep, model = spec.partition(":")
    if not sep or provider_name not in PROVIDERS or not model:
        raise ValueError(f"model must be provider:model with provider in {PROVIDERS}, got {spec!r}")
    return provider_name, model


@contextlib.contextmanager
def use_model(spec: str | None):
    """Route every call made inside the block (and tasks it starts) to `spec`."""
    if spec:
        parse_spec(spec)
    token = _SELECTED.set(spec or None)
    try:
        yield
    finally:
        _SELECTED.reset(token)


def _selected() -> tuple[str, str] | None:
    spec = _SELECTED.get() or os.environ.get("LLM_MODEL")
    if not spec and not BENCHMARK:
        spec = saved_default()
    return parse_spec(spec) if spec else None


def load_env(path: Path | None = None) -> None:
    path = path or Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    mean_logprob: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Usage:
    calls: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_model: dict[str, int] = field(default_factory=dict)


USAGE = Usage()
_SEM: asyncio.Semaphore | None = None
_CLIENT: httpx.AsyncClient | None = None
_LOGPROBS_SUPPORTED: dict[str, bool] = {}
_NEXT_SLOT = 0.0
# Pacing is process-wide and never reset: every request and every thread shares one RPM budget.
_PACE_LOCK = threading.Lock()
# Long-lived processes (the MCP server) set this so a finished request never closes the clients
# that concurrent requests are still using.
PERSISTENT = False
# One client set per event loop, since httpx and the Anthropic SDK bind connections to a loop.
_PER_LOOP: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
RETRY_AFTER_MAX = 60.0


async def _pace() -> None:
    """Space request starts evenly under LLM_RPM so the account limit is not tripped."""
    global _NEXT_SLOT
    gap = 60.0 / float(os.environ.get("LLM_RPM", "90"))
    with _PACE_LOCK:
        now = time.monotonic()
        wait = max(0.0, _NEXT_SLOT - now)
        _NEXT_SLOT = max(now, _NEXT_SLOT) + gap
    if wait:
        await asyncio.sleep(wait)


def retry_after(value: str | None) -> float:
    """Seconds to wait from a Retry-After header, which may be a number or an HTTP date. Never
    raises, and never waits longer than RETRY_AFTER_MAX."""
    if not value:
        return 1.0
    try:
        secs = float(value)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            secs = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError, IndexError):
            secs = 1.0
    if not math.isfinite(secs):
        secs = 1.0
    return min(RETRY_AFTER_MAX, max(0.0, secs))


def provider() -> tuple[str, str, str]:
    """(name, base_url, api_key).

    LLM_PROVIDER=local points at any OpenAI-compatible server on your own hardware (Ollama,
    llama.cpp's llama-server, vLLM, LM Studio). Otherwise Luminal Cloud is used.
    """
    load_env()
    sel = _selected()
    choice = sel[0] if sel else os.environ.get("LLM_PROVIDER", "").lower()
    if choice == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if not key:
            raise RuntimeError("Claude models need ANTHROPIC_API_KEY in the environment")
        return "anthropic", "", key
    if choice == "deepseek":
        # DeepSeek's own OpenAI-compatible API, so the service does not depend on one host.
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise RuntimeError("DeepSeek models need DEEPSEEK_API_KEY in the environment")
        base = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        return "deepseek", base.rstrip("/"), os.environ["DEEPSEEK_API_KEY"]
    if choice == "local" or (not choice and not os.environ.get("LUMINAL_API_KEY")):
        base = os.environ.get("LOCAL_BASE_URL") or "http://localhost:11434/v1"
        if choice != "local" and not os.environ.get("LOCAL_MODEL") and not sel:
            raise RuntimeError(
                "no LLM provider configured: set LUMINAL_API_KEY, or LLM_PROVIDER=local and LOCAL_MODEL"
            )
        return "local", base.rstrip("/"), os.environ.get("LOCAL_API_KEY") or "local"
    if not os.environ.get("LUMINAL_API_KEY"):
        raise RuntimeError("LLM_PROVIDER=luminal needs LUMINAL_API_KEY")
    base = os.environ.get("LUMINAL_BASE_URL") or "https://luminal.cloud/v1"
    return "luminal", base.rstrip("/"), os.environ["LUMINAL_API_KEY"]


def model_for(role: str) -> str:
    load_env()
    sel = _selected()
    if sel:
        return sel[1]
    name, _, _ = provider()
    if name == "deepseek":
        return os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
    if name == "local":
        model = os.environ.get("LOCAL_MODEL")
        if not model:
            raise RuntimeError(
                "LLM_PROVIDER=local needs LOCAL_MODEL (for example qwen2.5-coder:7b)"
            )
        return model
    model = (
        os.environ.get(ROLES[role]) or os.environ.get("GENERATOR_MODEL") or os.environ.get("MODEL")
    )
    if not model:
        raise RuntimeError(f"{ROLES[role]} is not set")
    return model


def thinking() -> bool:
    """Reasoning is off by default: with it on, most hunks exceed Luminal's ~20 s gateway limit."""
    return os.environ.get("LLM_THINKING", "off").lower() == "on"


def reportable() -> bool:
    """Only Luminal runs may appear in reported numbers."""
    return provider()[0] == "luminal"


def _loop_state() -> dict:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {}
    return _PER_LOOP.setdefault(loop, {})


def _sem() -> asyncio.Semaphore:
    if _SEM is not None:
        return _SEM
    st = _loop_state()
    if "sem" not in st:
        st["sem"] = asyncio.Semaphore(int(os.environ.get("MAX_LLM_CONCURRENCY", "16")))
    return st["sem"]


def _client() -> httpx.AsyncClient:
    if _CLIENT is not None:
        # Set directly by tests and callers that bring their own client.
        return _CLIENT
    st = _loop_state()
    c = st.get("client")
    if c is None or c.is_closed:
        timeout = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "90"))
        c = st["client"] = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15))
    return c


async def close() -> None:
    """Release this event loop's clients at the end of a CLI run. In a long-lived server
    (PERSISTENT) this does nothing, so one request finishing never breaks another that is still
    using the same clients; the rate limiter is never reset."""
    if PERSISTENT:
        return
    st = _loop_state()
    client, anth = st.pop("client", None), st.pop("anthropic", None)
    st.pop("sem", None)
    if client is not None:
        await client.aclose()
    if anth is not None:
        await anth.close()


_SEED_SUPPORTED: dict[str, bool] = {}


async def complete(
    messages: list[dict[str, str]],
    *,
    role: str = "generator",
    temperature: float = 0.2,
    max_tokens: int = 2048,
    want_logprobs: bool = False,
    retries: int = 5,
    seed: int | None = None,
) -> Completion:
    """One chat completion, traced as an OpenTelemetry GenAI span when tracing is configured."""
    name, _, _ = provider()
    with telemetry.llm_span(name, model_for(role), role, temperature=temperature) as span:
        c = await _complete(
            messages,
            role=role,
            temperature=temperature,
            max_tokens=max_tokens,
            want_logprobs=want_logprobs,
            retries=retries,
            seed=seed,
        )
        telemetry.record_usage(
            span,
            c.prompt_tokens,
            c.completion_tokens,
            **{
                "gen_ai.response.model": c.model,
                "forkward.latency_s": c.latency_s,
                "forkward.mean_logprob": c.mean_logprob,
                "error.type": c.error[:200] if c.error else None,
            },
        )
        if telemetry.content_enabled():
            span.set_attribute("gen_ai.input.messages", json.dumps(messages)[:20000])
            span.set_attribute("gen_ai.output.text", c.text[:20000])
        return c


async def _complete(
    messages: list[dict[str, str]],
    *,
    role: str = "generator",
    temperature: float = 0.2,
    max_tokens: int = 2048,
    want_logprobs: bool = False,
    retries: int = 5,
    seed: int | None = None,
) -> Completion:
    """One chat completion. `seed` is sent to OpenAI-compatible providers that honour it and
    dropped if the provider rejects it; the Anthropic API has no seed."""
    name, base, key = provider()
    model = model_for(role)
    if name == "anthropic":
        return await _complete_anthropic(messages, model, key, max_tokens, temperature)
    use_lp = want_logprobs and _LOGPROBS_SUPPORTED.get(model, True)
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if name == "luminal" and not thinking():
        body["thinking"] = {"type": "disabled"}
    if seed is not None and _SEED_SUPPORTED.get(model, True):
        body["seed"] = seed
    last_err = "unknown"
    t0 = time.monotonic()
    attempt = rate_limited = 0
    async with _sem():
        while attempt < retries and rate_limited < 30:
            if name == "luminal":
                await _pace()
            if use_lp:
                body["logprobs"] = True
            else:
                body.pop("logprobs", None)
            try:
                r = await _client().post(
                    f"{base}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {key}"},
                )
            except (httpx.HTTPError, RuntimeError) as e:
                # RuntimeError covers a client closed under us; it is a failed call, not a crash.
                last_err = f"{type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    return _parse(r.json(), model, name, time.monotonic() - t0)
                last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code == 429:
                    rate_limited += 1
                    await asyncio.sleep(retry_after(r.headers.get("retry-after")) + random.random())
                    continue
                if r.status_code == 400 and "seed" in body and "seed" in r.text.lower():
                    _SEED_SUPPORTED[model] = False
                    body.pop("seed")
                    attempt += 1
                    continue
                if r.status_code == 400 and use_lp and "logprob" in r.text.lower():
                    _LOGPROBS_SUPPORTED[model] = False
                    use_lp = False
                    attempt += 1
                    continue
                if r.status_code not in (408, 409, 429) and r.status_code < 500:
                    break
            await asyncio.sleep(min(30.0, 1.5 * 2**attempt) + random.random())
            attempt += 1
    USAGE.calls += 1
    USAGE.errors += 1
    return Completion("", model, name, latency_s=time.monotonic() - t0, error=last_err)


def _parse(data: dict, model: str, name: str, latency: float) -> Completion:
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    usage = data.get("usage") or {}
    lp = None
    content_lp = (choice.get("logprobs") or {}).get("content")
    if content_lp:
        vals = [t["logprob"] for t in content_lp if isinstance(t.get("logprob"), (int, float))]
        if vals:
            lp = sum(vals) / len(vals)
            if not math.isfinite(lp):
                lp = None
    c = Completion(
        text=text,
        model=model,
        provider=name,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        latency_s=latency,
        mean_logprob=lp,
        error="truncated: reasoning used the token budget"
        if choice.get("finish_reason") == "length" and not text
        else None,
    )
    USAGE.calls += 1
    USAGE.prompt_tokens += c.prompt_tokens
    USAGE.completion_tokens += c.completion_tokens
    USAGE.by_model[model] = USAGE.by_model.get(model, 0) + 1
    return c


_ANTHROPIC = None


def _anthropic_client(key: str):
    if _ANTHROPIC is not None:
        return _ANTHROPIC
    st = _loop_state()
    if st.get("anthropic") is None:
        import anthropic

        st["anthropic"] = anthropic.AsyncAnthropic(api_key=key, max_retries=5, timeout=120.0)
    return st["anthropic"]


async def _complete_anthropic(
    messages: list[dict[str, str]], model: str, key: str, max_tokens: int, temperature: float
) -> Completion:
    import anthropic

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]
    kwargs: dict = {"model": model, "max_tokens": max(max_tokens, 4096), "messages": turns}
    if system:
        kwargs["system"] = system
    if model.startswith("claude-haiku"):
        kwargs["temperature"] = temperature
    else:
        # Sonnet 5 / Opus 5 reject sampling parameters; low-effort adaptive thinking keeps
        # these short merge decisions cheap without the failure modes of disabled thinking.
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "low"}
    t0 = time.monotonic()
    try:
        async with _sem():
            client = _anthropic_client(key)
            if model == "claude-opus-5":
                r = await client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                r = await client.messages.create(**kwargs)
    except anthropic.RateLimitError as e:
        err = f"rate limited: {e.message}"
    except anthropic.APIStatusError as e:
        err = f"HTTP {e.status_code}: {e.message}"[:300]
    except anthropic.APIConnectionError as e:
        err = f"connection error: {e}"[:300]
    else:
        text = "".join(b.text for b in r.content if b.type == "text")
        c = Completion(
            text=text,
            model=model,
            provider="anthropic",
            prompt_tokens=r.usage.input_tokens,
            completion_tokens=r.usage.output_tokens,
            latency_s=time.monotonic() - t0,
            error="refused" if r.stop_reason == "refusal" else None,
        )
        USAGE.calls += 1
        USAGE.prompt_tokens += c.prompt_tokens
        USAGE.completion_tokens += c.completion_tokens
        USAGE.by_model[model] = USAGE.by_model.get(model, 0) + 1
        return c
    USAGE.calls += 1
    USAGE.errors += 1
    return Completion("", model, "anthropic", latency_s=time.monotonic() - t0, error=err)


async def available_models() -> list[dict]:
    """Models this machine can use right now, by provider."""
    load_env()
    out = []
    if os.environ.get("LUMINAL_API_KEY"):
        base = (os.environ.get("LUMINAL_BASE_URL") or "https://luminal.cloud/v1").rstrip("/")
        try:
            r = await _client().get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {os.environ['LUMINAL_API_KEY']}"},
            )
            ids = [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []
        except httpx.HTTPError:
            ids = []
        out += [{"spec": f"luminal:{m}", "provider": "luminal", "model": m} for m in ids]
    if os.environ.get("DEEPSEEK_API_KEY"):
        model = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
        out.append({"spec": f"deepseek:{model}", "provider": "deepseek", "model": model})
    base = (os.environ.get("LOCAL_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
    try:
        r = await _client().get(f"{base}/models", timeout=3.0)
        ids = [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        ids = []
    out += [{"spec": f"local:{m}", "provider": "local", "model": m} for m in ids]
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        out += [
            {"spec": f"anthropic:{m}", "provider": "anthropic", "model": m} for m in CLAUDE_MODELS
        ]
    return out
