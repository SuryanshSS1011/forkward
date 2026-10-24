"""OpenTelemetry traces for model calls and workflow steps, exported over OTLP/HTTP.

Langfuse Cloud is the default destination: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (and
LANGFUSE_HOST for a non-default region). Any other OTLP backend works through the standard
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT and OTEL_EXPORTER_OTLP_TRACES_HEADERS variables. With none of
these set, tracing is a no-op and costs nothing.

Model-call spans follow the OpenTelemetry GenAI conventions (gen_ai.* attributes). Those names
are still marked unstable upstream, so they are set in one place, `llm_span`. Prompts and
completions are not attached by default; set FORKWARD_TRACE_CONTENT=1 to include them.
"""

from __future__ import annotations

import base64
import os
import threading
from contextlib import contextmanager

_DONE = False
_LOCK = threading.Lock()


def traces_endpoint() -> str | None:
    if os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
        return f"{host}/api/public/otel/v1/traces"
    return None


def _headers() -> dict[str, str]:
    raw = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS") or os.environ.get(
        "OTEL_EXPORTER_OTLP_HEADERS"
    )
    if raw:
        return dict(kv.split("=", 1) for kv in (p.strip() for p in raw.split(",")) if "=" in kv)
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
    if pk and sk:
        return {"Authorization": "Basic " + base64.b64encode(f"{pk}:{sk}".encode()).decode()}
    return {}


def setup(service: str = "forkward") -> bool:
    """Install a global tracer provider that exports to the configured endpoint. Idempotent, and
    a no-op without an endpoint. DBOS reuses this provider for its workflow and step spans."""
    global _DONE
    endpoint = traces_endpoint()
    if not endpoint:
        return False
    with _LOCK:
        if _DONE:
            return True
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=_headers()))
        )
        trace.set_tracer_provider(provider)
        _DONE = True
        return True


def tracer():
    from opentelemetry import trace

    return trace.get_tracer("forkward")


@contextmanager
def llm_span(provider: str, model: str, role: str, **attrs):
    """A span around one model call. The caller sets token counts on the yielded span with
    `record_usage`."""
    with tracer().start_as_current_span(f"chat {model}") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.system", provider)
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("forkward.role", role)
        for k, v in attrs.items():
            if v is not None:
                span.set_attribute(f"forkward.{k}", v)
        yield span


def record_usage(span, input_tokens: int | None, output_tokens: int | None, **attrs) -> None:
    if input_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens))
    if output_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", int(output_tokens))
    for k, v in attrs.items():
        if v is not None:
            span.set_attribute(k, v)


def content_enabled() -> bool:
    return os.environ.get("FORKWARD_TRACE_CONTENT", "").lower() in ("1", "true", "yes")
