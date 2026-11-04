"""LLM calls that return validated Pydantic objects.

Three backends (config `llm.backend`):
- "subscription" (default): runs the Claude Code CLI in print mode, so calls use your Claude
  Pro/Max subscription login and count toward its usage limits. No API credits needed.
- "api": Anthropic API with ANTHROPIC_API_KEY (pay-as-you-go API credits).
- "groq": Groq API with GROQ_API_KEY (OpenAI-compatible endpoint, e.g. llama or gpt-oss models).
  PDF attachments are sent as extracted text, since the Groq API takes no document blocks.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from jobagent.config import cfg

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def structured(
    schema: type[T],
    *,
    system: str,
    content: str,
    effort: str = "medium",
    max_tokens: int = 16000,
    pdfs: list[Path] | None = None,
) -> T:
    """One LLM call that must return `schema`. `pdfs` are attached documents (e.g. a resume)."""
    backend = cfg("llm.backend", "subscription")
    if backend == "api":
        return _via_api(schema, system, content, effort, max_tokens, pdfs or [])
    if backend == "groq":
        return _via_groq(schema, system, content, effort, max_tokens, pdfs or [])
    return _via_claude_code(schema, system, content, effort, pdfs or [])


# ---------------------------------------------------------------- subscription (Claude Code CLI)

@lru_cache
def _claude_cli() -> str:
    configured = cfg("llm.claude_cli")
    if configured:
        return configured
    found = shutil.which("claude")
    if found:
        return found
    # The VS Code / Cursor extensions bundle the CLI; use the newest installed copy.
    home = Path.home()
    candidates = []
    for editor in (".vscode", ".cursor", ".vscode-insiders"):
        candidates += (home / editor / "extensions").glob("anthropic.claude-code-*/resources/native-binary/claude*")
    candidates = [c for c in candidates if c.suffix in (".exe", "")]
    if not candidates:
        raise LLMError("Claude Code CLI not found. Install it (https://claude.com/claude-code) "
                       "or set llm.claude_cli in config.yaml.")
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def _via_claude_code(schema: type[T], system: str, content: str, effort: str, pdfs: list[Path]) -> T:
    # Without this the CLI would bill the API key (from .env) instead of the subscription.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    with tempfile.TemporaryDirectory(prefix="jobagent_") as tmp:
        sys_file = Path(tmp) / "system.txt"
        sys_file.write_text(system, encoding="utf-8")
        cmd = [
            _claude_cli(), "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema.model_json_schema()),
            "--system-prompt-file", str(sys_file),
            "--model", cfg("llm.model", "claude-opus-5"),
            "--effort", effort,
            "--setting-sources", "",       # ignore your personal Claude Code settings/CLAUDE.md
            "--strict-mcp-config",         # and your MCP servers
            "--no-session-persistence",
        ]
        if pdfs:
            # The CLI reads attachments with its Read tool, confined to these directories.
            cmd += ["--tools", "Read", "--allowedTools", "Read"]
            for d in {str(p.resolve().parent) for p in pdfs}:
                cmd += ["--add-dir", d]
            content += "\n\nAttached files (read them with the Read tool):\n" + \
                "\n".join(f"- {p.resolve()}" for p in pdfs)
        else:
            cmd += ["--tools", ""]
        try:
            proc = subprocess.run(cmd, input=content, capture_output=True, text=True,
                                  encoding="utf-8", env=env, cwd=tmp, timeout=900)
        except subprocess.TimeoutExpired as e:
            raise LLMError("Claude Code call timed out after 15 minutes.") from e

    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise LLMError(f"Claude Code failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[:500]}")
    if out.get("is_error"):
        msg = str(out.get("result") or out.get("subtype"))
        if "login" in msg.lower():
            msg += " - open Claude Code once and sign in with your Claude subscription."
        raise LLMError(f"Claude Code: {msg}")
    data = out.get("structured_output")
    if data is None:
        raise LLMError(f"Claude returned no structured output: {str(out.get('result'))[:300]}")
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"Claude's output didn't match the schema: {e}") from e


# ---------------------------------------------------------------- Anthropic API

@lru_cache
def _client():
    import anthropic

    return anthropic.Anthropic(max_retries=4)


def _via_api(schema: type[T], system: str, content: str, effort: str, max_tokens: int,
             pdfs: list[Path]) -> T:
    import anthropic

    blocks: list[dict] = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                        "data": base64.standard_b64encode(p.read_bytes()).decode()}}
        for p in pdfs
    ]
    blocks.append({"type": "text", "text": content})
    try:
        response = _client().beta.messages.parse(
            model=cfg("llm.model", "claude-opus-5"),
            max_tokens=max_tokens,
            # Cacheable: callers put stable context (instructions, master resume) in `system`.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": blocks}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            output_format=schema,
            # If a safety classifier declines, re-run server-side on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic auth failed - check ANTHROPIC_API_KEY in .env.") from e
    except anthropic.BadRequestError as e:
        raise LLMError(f"Bad request to Claude: {e.message}") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited by the Anthropic API even after retries; try again shortly.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API (network problem).") from e

    if response.stop_reason == "refusal":
        raise LLMError("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise LLMError("Claude's answer was cut off (max_tokens); try a lower effort setting.")
    if response.parsed_output is None:
        raise LLMError("Claude returned no parseable output.")
    return response.parsed_output


# ---------------------------------------------------------------- Groq API (OpenAI-compatible)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"   # Groq's production flagship; strict structured outputs


def _groq_model() -> str:
    """llm.model wins, unless it's still a Claude model - then llm.groq_model (or the default)."""
    m = str(cfg("llm.model") or "")
    if m and not m.lower().startswith("claude"):
        return m
    return str(cfg("llm.groq_model") or GROQ_DEFAULT_MODEL)


@lru_cache
def _groq_client():
    import openai

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is not set - add it to .env (get one at https://console.groq.com/keys).")
    return openai.OpenAI(api_key=key, base_url=cfg("llm.groq_base_url", GROQ_BASE_URL),
                         max_retries=3, timeout=600)


def _pdf_text(p: Path) -> str:
    """Groq takes no document blocks, so attachments are sent as extracted text."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(p))
    try:
        return "\n".join(page.get_textpage().get_text_range() for page in doc)
    finally:
        doc.close()


# Reasoning effort: auto-mapped from the Claude effort levels on Groq reasoning models
# (openai/gpt-oss-*), or set explicitly via llm.reasoning_effort. Sent only then.
def _groq_reasoning_effort(effort: str) -> str | None:
    if cfg("llm.reasoning_effort"):
        return str(cfg("llm.reasoning_effort"))
    if "gpt-oss" in _groq_model():
        return {"low": "low", "medium": "medium", "high": "high"}.get(effort, "high")
    return None


def _strict_schema(schema: dict) -> dict:
    """Adapt a JSON Schema for strict structured outputs (Groq/OpenAI): every object gets
    all properties required and additionalProperties: false. Optional Pydantic fields are
    nullable in the schema, so requiring them is safe (the model emits null / [])."""
    import copy

    node = copy.deepcopy(schema)

    def fix(n):
        if isinstance(n, dict):
            if "properties" in n:
                n["required"] = list(n["properties"])
                n["additionalProperties"] = False
            for v in n.values():
                fix(v)
        elif isinstance(n, list):
            for v in n:
                fix(v)

    fix(node)
    return node


def _json_loads(text: str) -> dict:
    """Parse the model's answer, tolerating markdown code fences around the JSON."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")  # drop a ```json / ``` fence
        text = text[text.find("{"):] if "{" in text else text
    return json.loads(text)


def _via_groq(schema: type[T], system: str, content: str, effort: str, max_tokens: int,
              pdfs: list[Path]) -> T:
    import openai

    if pdfs:
        content += "\n\nAttached documents (plain text extracted from the PDFs):\n" + \
            "\n\n".join(f"<{p.stem}>\n{_pdf_text(p)}\n</{p.stem}>" for p in pdfs)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    model = _groq_model()
    kwargs: dict = {"model": model, "messages": messages, "max_completion_tokens": max_tokens}
    reasoning = _groq_reasoning_effort(effort)
    if reasoning:
        kwargs["reasoning_effort"] = reasoning

    # 1st try: native structured outputs (constrained decoding). Strict mode needs every field
    # required and additionalProperties:false, so the Pydantic schema is adapted first. Not every
    # Groq model supports json_schema response_format, so fall back to JSON mode + schema in prompt.
    try:
        response = _groq_client().chat.completions.create(
            response_format={"type": "json_schema", "json_schema": {
                "name": schema.__name__, "description": schema.__doc__ or f"Output matching {schema.__name__}",
                "schema": _strict_schema(schema.model_json_schema()), "strict": True}},
            **kwargs,
        )
        data = _json_loads(response.choices[0].message.content)
    except (openai.BadRequestError, json.JSONDecodeError, ValidationError):
        json_mode_messages = list(messages)
        json_mode_messages[0] = {"role": "system", "content": system +
            "\n\nRespond with a single JSON object that exactly matches this JSON Schema "
            "(no markdown, no commentary):\n" + json.dumps(schema.model_json_schema())}
        try:
            response = _groq_client().chat.completions.create(
                response_format={"type": "json_object"},
                **{**kwargs, "messages": json_mode_messages},
            )
            data = _json_loads(response.choices[0].message.content)
        except openai.AuthenticationError as e:
            raise LLMError("Groq auth failed - check GROQ_API_KEY in .env.") from e
        except openai.RateLimitError as e:
            raise LLMError("Rate limited by the Groq API even after retries; try again shortly.") from e
        except openai.APIStatusError as e:
            raise LLMError(f"Groq API error {e.status_code}: {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError("Could not reach the Groq API (network problem).") from e
        except (json.JSONDecodeError, ValidationError) as e:
            raise LLMError(f"Groq's output wasn't valid JSON for {schema.__name__}: {str(e)[:300]}") from e
    except openai.AuthenticationError as e:
        raise LLMError("Groq auth failed - check GROQ_API_KEY in .env.") from e
    except openai.RateLimitError as e:
        raise LLMError("Rate limited by the Groq API even after retries; try again shortly.") from e
    except openai.APIStatusError as e:
        raise LLMError(f"Groq API error {e.status_code}: {e.message}") from e
    except openai.APIConnectionError as e:
        raise LLMError("Could not reach the Groq API (network problem).") from e

    if response.choices[0].finish_reason == "length":
        raise LLMError("The model's answer was cut off (max_tokens); try a lower effort setting.")
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"The model's output didn't match the schema: {e}") from e
