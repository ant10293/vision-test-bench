"""Cursor CLI transport.

Analysis calls go through the local `agent` executable. Ask mode can read the
frames and is not given permission to edit files. Linked-account usage is the
Cursor plan, so those calls are recorded unpriced. An API key is a separate
transport and is passed only in the child environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.providers.claude_code import login_url_from, mask_account, subscription_env
from app.providers.types import (
    AuthenticationError,
    GenerationRequest,
    ProviderError,
    ProviderResponse,
    UnsupportedMode,
    Usage,
)
from app.services.storage import data_path, ensure_data_dirs

TRANSPORTS = ("login", "api_key")
_SETTINGS = "cursor_cli.json"
_SIGNED_OUT = (
    "not authenticated",
    "not logged",
    "no authentication",
    "please log in",
    "run agent login",
    "run `agent login`",
)
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_MODEL_LINE = re.compile(
    r"^(?P<id>[A-Za-z0-9][A-Za-z0-9._:-]{0,80})(?:\s+-\s+(?P<label>.+))?$"
)
_MODEL_HEADER = {"model", "models", "id", "available"}
_MODEL_CACHE_S = 60.0
_model_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}
# Image analysis with tool reads often outlasts the shared 180s request timeout.
HARD_TIMEOUT_S = 600
_PROMPT = (
    "Read instructions.md and every frame file it names. "
    "Return only the JSON object requested there."
)
_login_proc: asyncio.subprocess.Process | None = None
_login_task: asyncio.Task[None] | None = None
_login_url = ""
_login_lock = asyncio.Lock()


def load_cursor_cli() -> dict[str, str]:
    ensure_data_dirs()
    path = data_path(_SETTINGS)
    binary = "agent"
    transport = "login"
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


def save_cursor_cli(binary: str, transport: str) -> dict[str, str]:
    binary = (binary or "").strip() or "agent"
    if transport not in TRANSPORTS:
        raise ValueError("transport must be login or api_key")
    if any(char in binary for char in "\n\r\0"):
        raise ValueError("executable path is invalid")
    resolved = resolve_binary(binary)
    if resolved is None:
        raise ValueError(f"Cursor CLI executable not found: {binary}")
    ensure_data_dirs()
    payload = {"binary": binary, "transport": transport}
    data_path(_SETTINGS).write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def resolve_binary(binary: str) -> str | None:
    candidate = Path(binary).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    return shutil.which(binary)


def hard_timeout(requested: float) -> float:
    return max(float(requested), HARD_TIMEOUT_S)


def child_env(transport: str, secret: str = "") -> dict[str, str]:
    """Use the plan login, or the stored key, and drop the editor sandbox proxy."""
    env = subscription_env()
    env.pop("CURSOR_API_KEY", None)
    if transport == "api_key" and secret:
        env["CURSOR_API_KEY"] = secret
    return env


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


def _run_text(argv: list[str], env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _account_from(text: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    if not match:
        return ""
    return mask_account(match.group(0))


def _subscription_tier(stdout: str) -> str:
    """Read the plan name from `agent about --format json`."""
    try:
        payload = json.loads(stdout or "")
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return str(payload.get("subscriptionTier") or "").strip()
    for line in (stdout or "").splitlines():
        if line.lower().startswith("subscription tier"):
            _, _, value = line.partition("Tier")
            return value.strip()
    return ""


def _signed_out(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _SIGNED_OUT)


def probe_sync(secret: str = "") -> dict:
    settings = load_cursor_cli()
    binary = settings["binary"]
    transport = settings["transport"]
    resolved = resolve_binary(binary)
    if resolved is None:
        return _empty_status(binary, transport, f"Cursor CLI executable not found: {binary}")
    if transport == "api_key" and not secret:
        status = _empty_status(binary, transport, "No API key is configured for Cursor.")
        status["installed"] = True
        return status
    env = child_env(transport, secret)
    try:
        version = _run_text([resolved, "--version"], env, 15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _empty_status(binary, transport, str(exc))
    version_text = (version.stdout or version.stderr or "").strip().splitlines()
    version_line = version_text[0][:80] if version_text else ""
    if version.returncode != 0:
        detail = (version.stderr or version.stdout or "agent --version failed").strip()
        return _empty_status(binary, transport, detail[:500])
    try:
        auth = _run_text([resolved, "status"], env, 20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        status = _empty_status(binary, transport, str(exc))
        status["installed"] = True
        status["version"] = version_line
        return status
    text = "\n".join(part for part in (auth.stdout, auth.stderr) if part)
    logged_in = auth.returncode == 0 and not _signed_out(text)
    error = ""
    if not logged_in:
        error = "Cursor CLI is installed and not signed in."
        if transport == "api_key":
            error = "Cursor CLI rejected the API key."
    subscription = ""
    if logged_in:
        try:
            about = _run_text([resolved, "about", "--format", "json"], env, 20)
        except (OSError, subprocess.TimeoutExpired):
            about = None
        if about is not None and about.returncode == 0:
            subscription = _subscription_tier(about.stdout or "")
    return {
        "binary": binary,
        "transport": transport,
        "installed": True,
        "version": version_line,
        "logged_in": logged_in,
        "auth_method": transport if logged_in else "",
        "subscription": subscription,
        "account": _account_from(text) if logged_in else "",
        "error": error,
        "login_started": False,
        "login_url": "",
    }


async def probe(secret: str = "") -> dict:
    return await asyncio.to_thread(probe_sync, secret)


def _clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", label).strip()


def parse_model_records(stdout: str) -> list[tuple[str, str]]:
    """Read `agent --list-models` output.

    Current builds print ``id - Display name``. A bare id, or a JSON list, is
    accepted as well. The header line is not a model.
    """
    text = stdout.strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            return _records_from_json(payload)
        if isinstance(payload, dict):
            raw = payload.get("models") or payload.get("data") or []
            if isinstance(raw, list):
                return _records_from_json(raw)
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = _MODEL_LINE.match(line.strip())
        if not match:
            continue
        token = match.group("id")
        label = _clean_label(match.group("label") or "")
        if not label and token.lower() in _MODEL_HEADER:
            continue
        if token in seen:
            continue
        seen.add(token)
        found.append((token, label))
    return found


def parse_model_list(stdout: str) -> list[str]:
    return [model_id for model_id, _label in parse_model_records(stdout)]


def _records_from_json(raw: list) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        label = ""
        if isinstance(item, str):
            token = item.strip()
        elif isinstance(item, dict):
            token = str(item.get("id") or item.get("model") or "").strip()
            label = str(item.get("label") or item.get("name") or item.get("display_name") or "")
        else:
            continue
        if token and _MODEL_ID.match(token) and token not in seen:
            seen.add(token)
            found.append((token, _clean_label(label)))
    return found


def _fetch_model_records(secret: str = "") -> list[tuple[str, str]]:
    settings = load_cursor_cli()
    resolved = resolve_binary(settings["binary"])
    if resolved is None:
        raise ProviderError(f"Cursor CLI executable not found: {settings['binary']}")
    if settings["transport"] == "api_key" and not secret:
        raise AuthenticationError("No API key is configured for Cursor.")
    cache_key = f"{resolved}|{settings['transport']}|{secret[-4:] if secret else ''}"
    cached = _model_cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        completed = _run_text(
            [resolved, "--list-models"],
            child_env(settings["transport"], secret),
            30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "agent --list-models failed").strip()
        if _signed_out(detail):
            raise AuthenticationError(detail[:500])
        raise ProviderError(detail[:500])
    records = parse_model_records(completed.stdout)
    _model_cache[cache_key] = (now + _MODEL_CACHE_S, records)
    return records


def list_models_sync(secret: str = "") -> list[str]:
    return [model_id for model_id, _label in _fetch_model_records(secret)]


def account_model_options(catalog: list[dict], secret: str = "") -> list[dict]:
    """Replace the static catalog with the models this Cursor account can run.

    The catalog stays in place when the CLI is missing or not signed in.
    """
    from app.providers.catalog import lookup

    try:
        records = _fetch_model_records(secret)
    except (ProviderError, OSError):
        return catalog
    if not records:
        return catalog
    by_id = {str(item.get("model_id")): item for item in catalog}
    options: list[dict] = []
    for model_id, label in records:
        base = dict(by_id.get(model_id) or lookup("cursor", model_id).as_dict())
        if label:
            base["label"] = label
        options.append(base)
    return options


async def stop_login() -> None:
    global _login_proc, _login_task, _login_url
    proc = _login_proc
    task = _login_task
    _login_proc = None
    _login_task = None
    _login_url = ""
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _collect_login(proc: asyncio.subprocess.Process, bucket: bytearray) -> None:
    async def pump(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            bucket.extend(chunk)

    await asyncio.gather(pump(proc.stdout), pump(proc.stderr))
    if proc.returncode is None:
        await proc.wait()


async def begin_login(force: bool = False) -> dict:
    """Start `agent login` and keep it alive until the browser finishes."""
    global _login_proc, _login_task, _login_url
    status = await probe()
    if not status["installed"]:
        raise ProviderError(status["error"] or "Cursor CLI executable not found")
    if status["transport"] != "login":
        raise ProviderError("Switch Cursor runs to the Cursor account before linking.")
    if status["logged_in"] and not force:
        return status
    async with _login_lock:
        if not force and _login_proc is not None and _login_proc.returncode is None and _login_url:
            status["login_started"] = True
            status["login_url"] = _login_url
            status["logged_in"] = False
            status["error"] = ""
            return status
        await stop_login()
        resolved = resolve_binary(status["binary"])
        if resolved is None:
            raise ProviderError(status["error"] or "Cursor CLI executable not found")
        env = child_env("login")
        env["NO_OPEN_BROWSER"] = "1"
        if force:
            try:
                await asyncio.to_thread(_run_text, [resolved, "logout"], env, 20)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ProviderError(str(exc)) from exc
        try:
            proc = await asyncio.create_subprocess_exec(
                resolved,
                "login",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise ProviderError(str(exc)) from exc
        bucket = bytearray()
        _login_proc = proc
        _login_task = asyncio.create_task(_collect_login(proc, bucket))
        deadline = time.monotonic() + 15
        url = ""
        while time.monotonic() < deadline:
            url = login_url_from(bucket.decode(errors="replace"))
            if url:
                break
            if proc.returncode is not None:
                await asyncio.sleep(0.05)
                url = login_url_from(bucket.decode(errors="replace"))
                break
            await asyncio.sleep(0.05)
        _login_url = url
        if proc.returncode is not None and not url:
            detail = bucket.decode(errors="replace").strip()
            raise ProviderError(detail[-500:] or "Cursor CLI login exited before a sign-in link was shown")
        status["logged_in"] = False
        status["login_started"] = True
        status["login_url"] = url
        status["error"] = "" if url else "Cursor CLI did not show a sign-in link."
        return status


def build_argv(binary: str, model_id: str, workspace: Path) -> list[str]:
    return [
        binary,
        "--print",
        "--output-format",
        "json",
        "--mode",
        "ask",
        "--sandbox",
        "enabled",
        "--trust",
        "--workspace",
        str(workspace),
        "--model",
        model_id,
        _PROMPT,
    ]


def write_instructions(directory: Path, request: GenerationRequest, frames: list[tuple[float, str]]) -> None:
    schema = json.dumps(request.json_schema or {"type": "object"}, indent=2)
    lines = [
        request.system_prompt.strip(),
        "",
        "Return one JSON object and no other text. Do not create, edit, or delete files.",
        "The object must match this JSON schema:",
        schema,
        "",
    ]
    if frames:
        lines.append("Frames. Each path is an image. The number is seconds from the start of the clip.")
        for timestamp, name in frames:
            lines.append(f"- {timestamp:.3f}s {name}")
    else:
        lines.append("This call has no frames.")
    lines.extend(["", request.user_prompt.strip(), ""])
    (directory / "instructions.md").write_text("\n".join(lines), encoding="utf-8")


def parse_result(stdout: str) -> dict:
    text = stdout.strip()
    if not text:
        raise ProviderError("Cursor CLI returned no output")
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
            if isinstance(candidate, dict) and (candidate.get("type") == "result" or "result" in candidate):
                payload = candidate
                break
        if payload is None:
            raise ProviderError(text[-800:])
    if not isinstance(payload, dict):
        raise ProviderError("Cursor CLI returned an unexpected payload")
    return payload


def response_from_payload(
    payload: dict,
    request: GenerationRequest,
    latency_ms: int,
    version: str,
) -> ProviderResponse:
    if payload.get("is_error") or payload.get("subtype") in {"error", "error_during_execution"}:
        detail = str(payload.get("result") or payload.get("error") or "Cursor CLI failed")
        raise ProviderError(detail[:800])
    text = payload.get("result")
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("Cursor CLI returned an empty result")
    return ProviderResponse(
        text=text,
        raw={
            "session_id": str(payload.get("session_id") or ""),
            "request_id": str(payload.get("request_id") or ""),
            "model": request.model_id,
            "duration_ms": payload.get("duration_ms"),
            "billing": "cursor_subscription" if _transport(request) == "login" else "cursor_api_key",
        },
        usage=Usage(0, 0, 0.0, False),
        request_id=str(payload.get("request_id") or payload.get("session_id") or ""),
        latency_ms=latency_ms,
        model_id=request.model_id,
        sdk_version=version,
    )


def _transport(request: GenerationRequest) -> str:
    raw = ""
    if isinstance(request.metadata, dict):
        raw = str(request.metadata.get("transport") or "")
    if raw in TRANSPORTS:
        return raw
    return load_cursor_cli()["transport"]


async def _execute(argv: list[str], env: dict[str, str], cwd: str, timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise ProviderError(f"Cursor CLI timed out after {timeout:.0f}s") from None
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def complete(request: GenerationRequest, secret: str = "") -> ProviderResponse:
    settings = load_cursor_cli()
    resolved = resolve_binary(settings["binary"])
    if resolved is None:
        raise AuthenticationError(
            f"Cursor CLI executable not found: {settings['binary']}. "
            "Set the executable on the Providers page."
        )
    transport = _transport(request)
    if request.modality == "native_video" or (request.video_path and not request.frames):
        raise UnsupportedMode(
            "The Cursor CLI is asked to read frames. Native video was not substituted for a video file."
        )
    if request.temperature is not None:
        raise UnsupportedMode("The Cursor CLI does not accept temperature. Leave it unset.")
    if request.reasoning_effort:
        raise UnsupportedMode("The Cursor CLI does not accept a reasoning effort. Leave it unset.")
    if any(char in request.model_id for char in "\n\r\0"):
        raise ProviderError("model id is invalid")
    if transport == "api_key" and not secret:
        raise AuthenticationError("No API key is configured for Cursor.")
    if transport == "api_key" and any(char in secret for char in "\n\r\0"):
        raise ProviderError("API key is invalid")
    started = time.perf_counter()
    env = child_env(transport, secret)
    version = ""
    try:
        checked = await asyncio.to_thread(_run_text, [resolved, "--version"], env, 15)
        if checked.returncode == 0 and checked.stdout.strip():
            version = checked.stdout.strip().splitlines()[0][:80]
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    with tempfile.TemporaryDirectory(prefix="cursor-cli-") as directory:
        root = Path(directory)
        frame_dir = root / "frames"
        frame_dir.mkdir()
        listed: list[tuple[float, str]] = []
        for index, frame in enumerate(request.frames):
            if not frame.path.is_file():
                raise ProviderError(f"frame file is missing: {frame.path.name}")
            name = f"frames/{index:03d}_{frame.timestamp:.3f}{frame.path.suffix.lower() or '.jpg'}"
            target = root / name
            shutil.copyfile(frame.path, target)
            listed.append((frame.timestamp, name))
        write_instructions(root, request, listed)
        argv = build_argv(resolved, request.model_id, root)
        try:
            code, out, err = await _execute(argv, env, directory, hard_timeout(request.timeout_s))
        except OSError as exc:
            raise ProviderError(str(exc)) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    if code != 0:
        detail = (err or out or "Cursor CLI failed").strip()
        if _signed_out(detail):
            raise AuthenticationError(detail[-800:])
        try:
            payload = parse_result(out)
        except ProviderError:
            raise ProviderError(detail[-800:] or "Cursor CLI failed") from None
        return response_from_payload(payload, request, latency_ms, version)
    return response_from_payload(parse_result(out), request, latency_ms, version)
