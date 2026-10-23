"""Claude Code subscription transport.

Analysis calls go through the local `claude` executable signed in with a Claude
plan. The API key path stays on AnthropicProvider. Subscription usage is not an
API invoice, so those calls are recorded unpriced.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import pty
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.providers.types import (
    AuthenticationError,
    GenerationRequest,
    ProviderError,
    ProviderResponse,
    Usage,
)
from app.services.storage import data_path, ensure_data_dirs

TRANSPORTS = ("api", "claude_code")
_SETTINGS = "claude_code.json"
_API_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
)
_login_proc: asyncio.subprocess.Process | None = None
_login_master: int | None = None
_login_url = ""
_login_lock = asyncio.Lock()


def load_claude_code() -> dict[str, str]:
    ensure_data_dirs()
    path = data_path(_SETTINGS)
    binary = "claude"
    transport = "api"
    if path.is_file():
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw = {}
        if isinstance(raw, dict):
            if isinstance(raw.get("binary"), str) and raw["binary"].strip():
                binary = raw["binary"].strip()
            if raw.get("transport") in TRANSPORTS:
                transport = str(raw["transport"])
    return {"binary": binary, "transport": transport}


def save_claude_code(binary: str, transport: str) -> dict[str, str]:
    binary = (binary or "").strip() or "claude"
    if transport not in TRANSPORTS:
        raise ValueError("transport must be api or claude_code")
    if any(char in binary for char in "\n\r\0"):
        raise ValueError("executable path is invalid")
    resolved = resolve_binary(binary)
    if binary != "claude" and resolved is None:
        raise ValueError(f"Claude Code executable not found: {binary}")
    if transport == "claude_code" and resolved is None:
        raise ValueError(f"Claude Code executable not found: {binary}")
    ensure_data_dirs()
    payload = {"binary": binary, "transport": transport}
    data_path(_SETTINGS).write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def resolve_binary(binary: str) -> str | None:
    candidate = Path(binary).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    return shutil.which(binary)


def subscription_env() -> dict[str, str]:
    """Drop API credentials so the CLI uses the Claude plan login."""
    env = os.environ.copy()
    for name in _API_ENV:
        env.pop(name, None)
    return env


# Above every model's upper limit. The CLI clamps this to that model's maximum,
# so a subscription run is not stopped at Claude Code's lower default.
_MODEL_OUTPUT_MAXIMUM = "128000"


def generation_env() -> dict[str, str]:
    env = subscription_env()
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = _MODEL_OUTPUT_MAXIMUM
    return env


def mask_account(email: str) -> str:
    if "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    if not local or not domain:
        return ""
    return f"{local[0]}…@{domain}"


def _empty_status(binary: str, transport: str, error: str = "") -> dict:
    return {
        "binary": binary,
        "transport": transport,
        "installed": False,
        "version": "",
        "logged_in": False,
        "auth_method": "",
        "subscription": "",
        "account": "",
        "error": error,
        "login_started": False,
        "login_url": "",
    }


def _run_text(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=subscription_env(),
    )


def probe_sync() -> dict:
    settings = load_claude_code()
    binary = settings["binary"]
    transport = settings["transport"]
    resolved = resolve_binary(binary)
    if resolved is None:
        return _empty_status(binary, transport, f"Claude Code executable not found: {binary}")
    try:
        version = _run_text([resolved, "--version"], 15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _empty_status(binary, transport, str(exc))
    if version.returncode != 0:
        detail = (version.stderr or version.stdout or "claude --version failed").strip()
        return _empty_status(binary, transport, detail[:500])
    try:
        auth = _run_text([resolved, "auth", "status", "--json"], 20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        status = _empty_status(binary, transport, str(exc))
        status["installed"] = True
        status["version"] = version.stdout.strip().splitlines()[0][:80]
        return status
    payload: dict = {}
    try:
        parsed = json.loads(auth.stdout or "")
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        payload = {}
    logged_in = bool(payload.get("loggedIn"))
    error = ""
    if not logged_in and payload:
        # A signed-out status exits non-zero and prints the JSON document.
        error = "Claude Code is installed and not signed in."
    elif auth.returncode != 0 and not logged_in:
        error = (auth.stderr or auth.stdout or "claude auth status failed").strip()[:500]
    elif not logged_in:
        error = "Claude Code is installed and not signed in."
    return {
        "binary": binary,
        "transport": transport,
        "installed": True,
        "version": version.stdout.strip().splitlines()[0][:80] if version.stdout.strip() else "",
        "logged_in": logged_in,
        "auth_method": str(payload.get("authMethod") or ""),
        "subscription": str(payload.get("subscriptionType") or ""),
        "account": mask_account(str(payload.get("email") or "")),
        "error": error,
        "login_started": False,
        "login_url": "",
    }


async def probe() -> dict:
    return await asyncio.to_thread(probe_sync)


def login_url_from(text: str) -> str:
    """Pull the authorize URL out of a terminal login prompt.

    Current Claude Code wraps the URL in an OSC 8 hyperlink, so a raw
    ``https://`` match runs into the escape and the visible copy.
    """
    cleaned = re.sub(r"\x1b\]8;;.*?\x07", "", text)
    cleaned = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", cleaned)
    match = re.search(r"https://[^\s<>\"']+", cleaned)
    if not match:
        return ""
    return match.group(0).rstrip(").,")


def _close_login_master() -> None:
    global _login_master
    master = _login_master
    _login_master = None
    if master is None:
        return
    try:
        os.close(master)
    except OSError:
        pass


async def stop_login() -> None:
    global _login_proc, _login_url
    proc = _login_proc
    _login_proc = None
    _login_url = ""
    _close_login_master()
    if proc is not None and proc.returncode is None:
        proc.kill()
        await proc.wait()


async def _read_login_output(master: int, timeout: float) -> tuple[str, str]:
    os.set_blocking(master, False)
    loop = asyncio.get_running_loop()
    collected = bytearray()
    found: asyncio.Future[str] = loop.create_future()

    def on_readable() -> None:
        while True:
            try:
                chunk = os.read(master, 4096)
            except BlockingIOError:
                return
            except OSError:
                break
            if not chunk:
                break
            collected.extend(chunk)
            url = login_url_from(collected.decode(errors="replace"))
            if url and not found.done():
                found.set_result(url)
                return
        if not found.done():
            found.set_result(login_url_from(collected.decode(errors="replace")))

    loop.add_reader(master, on_readable)
    try:
        url = await asyncio.wait_for(found, timeout)
    except TimeoutError:
        url = login_url_from(collected.decode(errors="replace"))
    finally:
        loop.remove_reader(master)
        os.set_blocking(master, True)
    return url, collected.decode(errors="replace")


async def begin_login(force: bool = False) -> dict:
    """Refresh a signed-in CLI, or start `claude auth login` and keep it alive."""
    global _login_proc, _login_url, _login_master
    status = await probe()
    if not status["installed"]:
        raise ProviderError(status["error"] or "Claude Code executable not found")
    if status["logged_in"] and not force:
        return status
    async with _login_lock:
        if not force and _login_proc is not None and _login_proc.returncode is None:
            status["login_started"] = True
            status["login_url"] = _login_url
            status["error"] = ""
            return status
        await stop_login()
        resolved = resolve_binary(status["binary"])
        if resolved is None:
            raise ProviderError(status["error"] or "Claude Code executable not found")
        master, slave = pty.openpty()
        try:
            proc = await asyncio.create_subprocess_exec(
                resolved,
                "auth",
                "login",
                "--claudeai",
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=subscription_env(),
            )
        except OSError:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        _login_proc = proc
        _login_master = master
        url, collected = await _read_login_output(master, 8)
        _login_url = url
        if proc.returncode is not None and not url:
            detail = login_url_from(collected) or collected.strip()
            raise ProviderError(detail[-500:] or "Claude Code login exited before a sign-in link was shown")
        status["logged_in"] = False
        status["login_started"] = True
        status["login_url"] = url
        status["error"] = "" if url else "Claude Code did not show a sign-in link."
        return status


async def submit_login_code(code: str) -> dict:
    """Write the browser sign-in code into the waiting Claude Code login."""
    code = (code or "").strip()
    if not code or any(char in code for char in "\r\n\0"):
        raise ProviderError("Paste the sign-in code from the browser.")
    proc = _login_proc
    master = _login_master
    if proc is None or master is None or proc.returncode is not None:
        raise ProviderError("Sign-in is not waiting for a code. Use Link Claude Code first.")
    os.write(master, (code + "\n").encode())
    try:
        await asyncio.wait_for(proc.wait(), timeout=30)
    except TimeoutError:
        pass
    status = await probe()
    if status["logged_in"]:
        status["login_started"] = False
        status["login_url"] = ""
        status["error"] = ""
    return status


def build_argv(
    binary: str,
    model_id: str,
    prompt_path: Path,
    schema: str,
    effort: str | None = None,
) -> list[str]:
    argv = [
        binary,
        "--print",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model_id,
    ]
    if effort:
        argv.extend(["--effort", effort])
    argv.extend([
        "--system-prompt-file",
        str(prompt_path),
        "--json-schema",
        schema,
        "--tools",
        "",
        "--max-turns",
        "1",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--safe-mode",
        "--strict-mcp-config",
        "--restricted",
    ])
    return argv


def _media_type(path: Path, head: bytes) -> str:
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "image/webp"
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")


def build_stdin(request: GenerationRequest) -> bytes:
    blocks: list[dict] = []
    for frame in request.frames:
        raw = frame.path.read_bytes()
        blocks.append({"type": "text", "text": f"Frame timestamp {frame.timestamp:.3f} seconds."})
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _media_type(frame.path, raw[:16]),
                    "data": base64.b64encode(raw).decode("ascii"),
                },
            }
        )
    blocks.append({"type": "text", "text": request.user_prompt})
    message = {"type": "user", "message": {"role": "user", "content": blocks}}
    return (json.dumps(message) + "\n").encode()


def parse_cli_payload(stdout: str) -> dict:
    text = stdout.strip()
    if not text:
        raise ProviderError("Claude Code returned no output")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("type") == "result":
                payload = candidate
                break
        if payload is None:
            raise ProviderError(text[-800:])
    if not isinstance(payload, dict):
        raise ProviderError("Claude Code returned an unexpected payload")
    return payload


def response_from_payload(payload: dict, request: GenerationRequest, latency_ms: int, version: str) -> ProviderResponse:
    if payload.get("is_error") or payload.get("subtype") in {"error", "error_during_execution"}:
        detail = payload.get("result") or payload.get("error") or "Claude Code run failed"
        message = str(detail)[:2000]
        lowered = message.lower()
        if "not logged" in lowered or "login" in lowered or "authentication" in lowered:
            raise AuthenticationError(message)
        raise ProviderError(message)
    structured = payload.get("structured_output")
    if isinstance(structured, (dict, list)):
        text = json.dumps(structured)
    else:
        text = str(payload.get("result") or "")
    if not text.strip():
        raise ProviderError("Claude Code returned an empty analysis")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return ProviderResponse(
        text=text,
        raw={
            "session_id": str(payload.get("session_id") or ""),
            "model": str(payload.get("model") or request.model_id),
            "usage": usage,
            "stop_reason": str(payload.get("subtype") or ""),
            "billing": "claude_code_subscription",
        },
        usage=Usage(input_tokens, output_tokens, 0.0, False),
        request_id=str(payload.get("session_id") or ""),
        latency_ms=latency_ms,
        model_id=str(payload.get("model") or request.model_id),
        sdk_version=version,
    )


async def run_process(
    argv: list[str],
    stdin: bytes,
    env: dict[str, str],
    timeout: float,
    cwd: str,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ProviderError(f"Claude Code timed out after {timeout:.0f}s") from None
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def complete(request: GenerationRequest) -> ProviderResponse:
    settings = load_claude_code()
    resolved = resolve_binary(settings["binary"])
    if resolved is None:
        raise AuthenticationError(
            f"Claude Code executable not found: {settings['binary']}. "
            "Set the executable on the Providers page."
        )
    started = time.perf_counter()
    version = ""
    try:
        checked = await asyncio.to_thread(_run_text, [resolved, "--version"], 15)
        if checked.returncode == 0 and checked.stdout.strip():
            version = checked.stdout.strip().splitlines()[0][:80]
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    schema = json.dumps(request.json_schema or {"type": "object"})
    with tempfile.TemporaryDirectory(prefix="claude-code-") as directory:
        prompt_path = Path(directory) / "system.txt"
        prompt_path.write_text(request.system_prompt, encoding="utf-8")
        argv = build_argv(resolved, request.model_id, prompt_path, schema, request.reasoning_effort or None)
        try:
            code, out, err = await run_process(
                argv,
                build_stdin(request),
                generation_env(),
                request.timeout_s,
                directory,
            )
        except OSError as exc:
            raise ProviderError(str(exc)) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    if code != 0:
        try:
            payload = parse_cli_payload(out)
        except ProviderError:
            payload = None
        if isinstance(payload, dict):
            return response_from_payload(payload, request, latency_ms, version)
        detail = (err or out or "Claude Code failed").strip()
        lowered = detail.lower()
        if "not logged" in lowered or "/login" in lowered:
            raise AuthenticationError(detail[-800:])
        raise ProviderError(detail[-800:] or "Claude Code failed")
    return response_from_payload(parse_cli_payload(out), request, latency_ms, version)
