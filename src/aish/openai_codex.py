from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

OPENAI_CODEX_PROVIDER = "openai-codex"
OPENAI_CODEX_DEFAULT_MODEL = "gpt-5.4"
OPENAI_CODEX_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
OPENAI_CODEX_AUTH_ISSUER = "https://auth.openai.com"
OPENAI_CODEX_AUTHORIZE_URL = f"{OPENAI_CODEX_AUTH_ISSUER}/oauth/authorize"
OPENAI_CODEX_REFRESH_URL = f"{OPENAI_CODEX_AUTH_ISSUER}/oauth/token"
OPENAI_CODEX_DEVICE_AUTH_BASE_URL = f"{OPENAI_CODEX_AUTH_ISSUER}/api/accounts"
OPENAI_CODEX_DEVICE_VERIFICATION_URL = f"{OPENAI_CODEX_AUTH_ISSUER}/codex/device"
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_OAUTH_SCOPE = (
    "openid profile email offline_access api.connectors.read api.connectors.invoke"
)
OPENAI_CODEX_ORIGINATOR = "codex_cli_rs"
OPENAI_CODEX_USER_AGENT = (
    f"{OPENAI_CODEX_ORIGINATOR}/0.0.0 "
    f"(aish; Python/{platform.python_version()}; "
    f"{platform.system()} {platform.machine() or 'unknown'})"
)
OPENAI_CODEX_REFRESH_LEEWAY_SECONDS = 60
OPENAI_CODEX_DEFAULT_CALLBACK_PORT = 1455
OPENAI_CODEX_BROWSER_LOGIN_TIMEOUT_SECONDS = 300.0
OPENAI_CODEX_DEVICE_CODE_TIMEOUT_SECONDS = 900.0

_LOGIN_SUCCESS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>OpenAI Codex Login Complete</title>
  <style>
    body { font-family: sans-serif; margin: 3rem; color: #111; }
    code { background: #f3f4f6; padding: 0.1rem 0.3rem; }
  </style>
</head>
<body>
  <h1>Sign-in complete</h1>
  <p>You can close this tab and return to <code>aish</code>.</p>
</body>
</html>
"""

_LOGIN_ERROR_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>OpenAI Codex Login Failed</title>
  <style>
    body { font-family: sans-serif; margin: 3rem; color: #111; }
    pre { background: #f3f4f6; padding: 1rem; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>Sign-in failed</h1>
  <pre>{message}</pre>
</body>
</html>
"""


class OpenAICodexAuthError(RuntimeError):
    pass


@dataclass
class OpenAICodexAuthState:
    auth_path: Path
    access_token: str
    refresh_token: str | None
    account_id: str
    expires_at: int | None

    def needs_refresh(
        self, *, leeway_seconds: int = OPENAI_CODEX_REFRESH_LEEWAY_SECONDS
    ) -> bool:
        if self.expires_at is None:
            return False
        return int(time.time()) >= (self.expires_at - leeway_seconds)


@dataclass
class OpenAICodexPkceCodes:
    code_verifier: str
    code_challenge: str


@dataclass
class OpenAICodexOAuthTokens:
    id_token: str
    access_token: str
    refresh_token: str


@dataclass
class OpenAICodexDeviceCode:
    verification_url: str
    user_code: str
    device_auth_id: str
    interval: float


@dataclass
class OpenAICodexDeviceCodeAuthorization:
    authorization_code: str
    code_verifier: str
    code_challenge: str


@dataclass
class OpenAICodexBrowserCallbackResult:
    code: str | None = None
    error: str | None = None
    error_description: str | None = None


class _OpenAICodexCallbackServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], expected_state: str):
        super().__init__(server_address, _OpenAICodexCallbackHandler)
        self.expected_state = expected_state
        self.callback_event = threading.Event()
        self.callback_result: OpenAICodexBrowserCallbackResult | None = None

    def set_callback_result(self, result: OpenAICodexBrowserCallbackResult) -> None:
        if self.callback_event.is_set():
            return
        self.callback_result = result
        self.callback_event.set()


class _OpenAICodexCallbackHandler(BaseHTTPRequestHandler):
    server: _OpenAICodexCallbackServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/auth/callback":
            self._handle_auth_callback(parsed)
            return
        if parsed.path == "/cancel":
            self.server.set_callback_result(
                OpenAICodexBrowserCallbackResult(error="cancelled")
            )
            self._send_response(
                200,
                "text/plain; charset=utf-8",
                "Login cancelled.".encode("utf-8"),
            )
            self._shutdown_async()
            return
        self._send_response(404, "text/plain; charset=utf-8", b"Not Found")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_auth_callback(self, parsed) -> None:
        params = parse_qs(parsed.query, keep_blank_values=False)
        state = _first_query_value(params, "state")
        if state != self.server.expected_state:
            message = "State mismatch during OpenAI Codex OAuth callback."
            self.server.set_callback_result(
                OpenAICodexBrowserCallbackResult(
                    error="state_mismatch",
                    error_description=message,
                )
            )
            self._send_response(
                400,
                "text/html; charset=utf-8",
                _LOGIN_ERROR_HTML_TEMPLATE.format(message=message).encode("utf-8"),
            )
            self._shutdown_async()
            return

        error = _first_query_value(params, "error")
        error_description = _first_query_value(params, "error_description")
        if error:
            message = _format_oauth_callback_error(error, error_description)
            self.server.set_callback_result(
                OpenAICodexBrowserCallbackResult(
                    error=error,
                    error_description=error_description,
                )
            )
            self._send_response(
                200,
                "text/html; charset=utf-8",
                _LOGIN_ERROR_HTML_TEMPLATE.format(message=message).encode("utf-8"),
            )
            self._shutdown_async()
            return

        code = _first_query_value(params, "code")
        if not code:
            message = "Missing authorization code in OpenAI Codex OAuth callback."
            self.server.set_callback_result(
                OpenAICodexBrowserCallbackResult(
                    error="missing_authorization_code",
                    error_description=message,
                )
            )
            self._send_response(
                400,
                "text/html; charset=utf-8",
                _LOGIN_ERROR_HTML_TEMPLATE.format(message=message).encode("utf-8"),
            )
            self._shutdown_async()
            return

        self.server.set_callback_result(OpenAICodexBrowserCallbackResult(code=code))
        self._send_response(
            200,
            "text/html; charset=utf-8",
            _LOGIN_SUCCESS_HTML.encode("utf-8"),
        )
        self._shutdown_async()

    def _shutdown_async(self) -> None:
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _send_response(
        self, status_code: int, content_type: str, body: bytes
    ) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def is_openai_codex_model(model: str | None) -> bool:
    return bool(
        model and model.strip().lower().startswith(f"{OPENAI_CODEX_PROVIDER}/")
    )


def strip_openai_codex_prefix(model: str) -> str:
    if is_openai_codex_model(model):
        return model.split("/", 1)[1].strip()
    return model.strip()


def resolve_openai_codex_base_url(api_base: str | None) -> str:
    trimmed = (api_base or "").strip().rstrip("/")
    if not trimmed:
        return OPENAI_CODEX_DEFAULT_BASE_URL

    parsed = urlsplit(trimmed)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return OPENAI_CODEX_DEFAULT_BASE_URL

    host = parsed.netloc.lower()
    if host not in {"chatgpt.com", "chat.openai.com"}:
        return OPENAI_CODEX_DEFAULT_BASE_URL

    return f"{parsed.scheme}://{parsed.netloc}/backend-api/codex"


def resolve_openai_codex_auth_path(
    explicit_path: str | os.PathLike[str] | None = None,
) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()

    env_path = os.getenv("AISH_CODEX_AUTH_PATH")
    if env_path:
        return Path(env_path).expanduser()

    codex_home = os.getenv("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"

    return Path.home() / ".codex" / "auth.json"


def load_openai_codex_auth(
    auth_path: str | os.PathLike[str] | None = None,
) -> OpenAICodexAuthState:
    path = resolve_openai_codex_auth_path(auth_path)
    if not path.exists():
        raise OpenAICodexAuthError(
            "OpenAI Codex auth not found. Run `aish models auth login --provider openai-codex` first."
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise OpenAICodexAuthError(f"Failed to read Codex auth file: {path}") from exc

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        raise OpenAICodexAuthError(
            "Codex auth file does not contain ChatGPT OAuth tokens. Re-run "
            "`aish models auth login --provider openai-codex`."
        )

    access_token = _coerce_str(tokens.get("access_token"))
    refresh_token = _coerce_str(tokens.get("refresh_token")) or None
    access_token_claims = _decode_jwt_claims(access_token)
    id_token_claims = _coerce_id_token_claims(tokens.get("id_token"))

    account_id = (
        _coerce_str(tokens.get("account_id"))
        or _extract_account_id_from_claims(id_token_claims)
        or _extract_account_id_from_claims(access_token_claims)
    )
    if not access_token or not account_id:
        raise OpenAICodexAuthError(
            "Codex auth is incomplete. Re-run `aish models auth login --provider openai-codex`."
        )

    expires_at = _coerce_int(access_token_claims.get("exp")) or _coerce_int(
        id_token_claims.get("exp")
    )
    return OpenAICodexAuthState(
        auth_path=path,
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=account_id,
        expires_at=expires_at,
    )


def generate_openai_codex_pkce() -> OpenAICodexPkceCodes:
    verifier = base64.urlsafe_b64encode(os.urandom(64)).rstrip(b"=").decode("utf-8")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).rstrip(b"=").decode("utf-8")
    return OpenAICodexPkceCodes(
        code_verifier=verifier,
        code_challenge=challenge,
    )


def generate_openai_codex_state() -> str:
    return secrets.token_urlsafe(32)


def build_openai_codex_authorize_url(
    *,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    issuer: str = OPENAI_CODEX_AUTH_ISSUER,
    client_id: str = OPENAI_CODEX_CLIENT_ID,
    allowed_workspace_id: str | None = None,
) -> str:
    query: list[tuple[str, str]] = [
        ("response_type", "code"),
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("scope", OPENAI_CODEX_OAUTH_SCOPE),
        ("code_challenge", code_challenge),
        ("code_challenge_method", "S256"),
        ("id_token_add_organizations", "true"),
        ("codex_cli_simplified_flow", "true"),
        ("state", state),
        ("originator", OPENAI_CODEX_ORIGINATOR),
    ]
    if allowed_workspace_id:
        query.append(("allowed_workspace_id", allowed_workspace_id))
    return f"{issuer.rstrip('/')}/oauth/authorize?{urlencode(query)}"


def exchange_openai_codex_code_for_tokens(
    *,
    code: str,
    redirect_uri: str,
    pkce: OpenAICodexPkceCodes,
    issuer: str = OPENAI_CODEX_AUTH_ISSUER,
    client_id: str = OPENAI_CODEX_CLIENT_ID,
    client: httpx.Client | None = None,
) -> OpenAICodexOAuthTokens:
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)

    try:
        response = client.post(
            f"{issuer.rstrip('/')}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": pkce.code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except Exception as exc:
        raise OpenAICodexAuthError(
            f"OpenAI Codex token exchange failed: {exc}"
        ) from exc
    finally:
        if owns_client:
            client.close()

    if response.is_error:
        detail = _extract_http_error_message(response)
        raise OpenAICodexAuthError(
            f"OpenAI Codex token exchange failed: {response.status_code} {detail}"
        )

    try:
        payload = response.json()
    except Exception as exc:
        raise OpenAICodexAuthError(
            "OpenAI Codex token exchange returned invalid JSON."
        ) from exc

    id_token = _coerce_str(payload.get("id_token"))
    access_token = _coerce_str(payload.get("access_token"))
    refresh_token = _coerce_str(payload.get("refresh_token"))
    if not id_token or not access_token or not refresh_token:
        raise OpenAICodexAuthError(
            "OpenAI Codex token exchange returned incomplete credentials."
        )

    return OpenAICodexOAuthTokens(
        id_token=id_token,
        access_token=access_token,
        refresh_token=refresh_token,
    )


def request_openai_codex_device_code(
    *,
    issuer: str = OPENAI_CODEX_AUTH_ISSUER,
    client_id: str = OPENAI_CODEX_CLIENT_ID,
    client: httpx.Client | None = None,
) -> OpenAICodexDeviceCode:
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)

    try:
        response = client.post(
            f"{issuer.rstrip('/')}/api/accounts/deviceauth/usercode",
            json={"client_id": client_id},
            headers={"Content-Type": "application/json"},
        )
    except Exception as exc:
        raise OpenAICodexAuthError(
            f"OpenAI Codex device code request failed: {exc}"
        ) from exc
    finally:
        if owns_client:
            client.close()

    if response.status_code == httpx.codes.NOT_FOUND:
        raise OpenAICodexAuthError(
            "OpenAI Codex device-code login is not available on this auth server."
        )
    if response.is_error:
        detail = _extract_http_error_message(response)
        raise OpenAICodexAuthError(
            f"OpenAI Codex device code request failed: {response.status_code} {detail}"
        )

    try:
        payload = response.json()
    except Exception as exc:
        raise OpenAICodexAuthError(
            "OpenAI Codex device code request returned invalid JSON."
        ) from exc

    user_code = _coerce_str(payload.get("user_code") or payload.get("usercode"))
    device_auth_id = _coerce_str(payload.get("device_auth_id"))
    interval = _coerce_non_negative_float(payload.get("interval"), default=5.0)
    if not user_code or not device_auth_id:
        raise OpenAICodexAuthError(
            "OpenAI Codex device code request returned incomplete data."
        )

    return OpenAICodexDeviceCode(
        verification_url=f"{issuer.rstrip('/')}/codex/device",
        user_code=user_code,
        device_auth_id=device_auth_id,
        interval=interval,
    )


def poll_openai_codex_device_code_authorization(
    *,
    device_code: OpenAICodexDeviceCode,
    timeout: float = OPENAI_CODEX_DEVICE_CODE_TIMEOUT_SECONDS,
    issuer: str = OPENAI_CODEX_AUTH_ISSUER,
    client: httpx.Client | None = None,
) -> OpenAICodexDeviceCodeAuthorization:
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)

    deadline = time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() >= deadline:
                raise OpenAICodexAuthError(
                    "Timed out waiting for OpenAI Codex device-code approval."
                )

            try:
                response = client.post(
                    f"{issuer.rstrip('/')}/api/accounts/deviceauth/token",
                    json={
                        "device_auth_id": device_code.device_auth_id,
                        "user_code": device_code.user_code,
                    },
                    headers={"Content-Type": "application/json"},
                )
            except Exception as exc:
                raise OpenAICodexAuthError(
                    f"OpenAI Codex device-code polling failed: {exc}"
                ) from exc

            if response.is_success:
                try:
                    payload = response.json()
                except Exception as exc:
                    raise OpenAICodexAuthError(
                        "OpenAI Codex device-code polling returned invalid JSON."
                    ) from exc

                authorization_code = _coerce_str(payload.get("authorization_code"))
                code_verifier = _coerce_str(payload.get("code_verifier"))
                code_challenge = _coerce_str(payload.get("code_challenge"))
                if authorization_code and code_verifier and code_challenge:
                    return OpenAICodexDeviceCodeAuthorization(
                        authorization_code=authorization_code,
                        code_verifier=code_verifier,
                        code_challenge=code_challenge,
                    )
                raise OpenAICodexAuthError(
                    "OpenAI Codex device-code polling returned incomplete data."
                )

            if response.status_code not in {
                httpx.codes.FORBIDDEN,
                httpx.codes.NOT_FOUND,
            }:
                detail = _extract_http_error_message(response)
                raise OpenAICodexAuthError(
                    f"OpenAI Codex device-code polling failed: {response.status_code} {detail}"
                )

            sleep_for = min(device_code.interval, max(0.0, deadline - time.monotonic()))
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        if owns_client:
            client.close()


def login_openai_codex_with_browser(
    *,
    auth_path: str | os.PathLike[str] | None = None,
    issuer: str = OPENAI_CODEX_AUTH_ISSUER,
    client_id: str = OPENAI_CODEX_CLIENT_ID,
    callback_port: int = OPENAI_CODEX_DEFAULT_CALLBACK_PORT,
    timeout: float = OPENAI_CODEX_BROWSER_LOGIN_TIMEOUT_SECONDS,
    open_browser: bool = True,
    notify: Callable[[str], None] | None = None,
) -> OpenAICodexAuthState:
    notify = notify or print
    resolved_auth_path = resolve_openai_codex_auth_path(auth_path)
    pkce = generate_openai_codex_pkce()
    state = generate_openai_codex_state()
    server = _bind_openai_codex_callback_server(state, callback_port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    redirect_uri = (
        f"http://localhost:{server.server_address[1]}/auth/callback"
    )
    auth_url = build_openai_codex_authorize_url(
        redirect_uri=redirect_uri,
        code_challenge=pkce.code_challenge,
        state=state,
        issuer=issuer,
        client_id=client_id,
    )

    notify(f"Open this URL to sign in with OpenAI Codex:\n{auth_url}")
    if open_browser:
        try:
            opened = webbrowser.open(auth_url)
        except Exception as exc:
            opened = False
            notify(f"Could not open a browser automatically: {exc}")
        if not opened:
            notify("Browser auto-open failed. Open the URL above manually.")

    try:
        result = _wait_for_openai_codex_browser_callback(server, timeout=timeout)
        if result.error:
            raise OpenAICodexAuthError(
                _format_oauth_callback_error(result.error, result.error_description)
            )

        if not result.code:
            raise OpenAICodexAuthError(
                "OpenAI Codex browser login did not return an authorization code."
            )

        with httpx.Client(timeout=30.0) as client:
            tokens = exchange_openai_codex_code_for_tokens(
                code=result.code,
                redirect_uri=redirect_uri,
                pkce=pkce,
                issuer=issuer,
                client_id=client_id,
                client=client,
            )
            persist_openai_codex_tokens(
                resolved_auth_path,
                tokens=tokens,
            )
        return load_openai_codex_auth(resolved_auth_path)
    finally:
        _stop_openai_codex_callback_server(server, server_thread)


def login_openai_codex_with_device_code(
    *,
    auth_path: str | os.PathLike[str] | None = None,
    issuer: str = OPENAI_CODEX_AUTH_ISSUER,
    client_id: str = OPENAI_CODEX_CLIENT_ID,
    timeout: float = OPENAI_CODEX_DEVICE_CODE_TIMEOUT_SECONDS,
    notify: Callable[[str], None] | None = None,
) -> OpenAICodexAuthState:
    notify = notify or print
    resolved_auth_path = resolve_openai_codex_auth_path(auth_path)

    with httpx.Client(timeout=30.0) as client:
        device_code = request_openai_codex_device_code(
            issuer=issuer,
            client_id=client_id,
            client=client,
        )
        notify(
            "OpenAI Codex device-code login\n"
            f"1. Open: {device_code.verification_url}\n"
            f"2. Enter code: {device_code.user_code}\n"
            "3. Return here after approving access."
        )
        authorization = poll_openai_codex_device_code_authorization(
            device_code=device_code,
            timeout=timeout,
            issuer=issuer,
            client=client,
        )
        tokens = exchange_openai_codex_code_for_tokens(
            code=authorization.authorization_code,
            redirect_uri=f"{issuer.rstrip('/')}/deviceauth/callback",
            pkce=OpenAICodexPkceCodes(
                code_verifier=authorization.code_verifier,
                code_challenge=authorization.code_challenge,
            ),
            issuer=issuer,
            client_id=client_id,
            client=client,
        )

    persist_openai_codex_tokens(
        resolved_auth_path,
        tokens=tokens,
    )
    return load_openai_codex_auth(resolved_auth_path)


async def refresh_openai_codex_auth(
    auth: OpenAICodexAuthState,
    *,
    client: httpx.AsyncClient | None = None,
) -> OpenAICodexAuthState:
    if not auth.refresh_token:
        raise OpenAICodexAuthError(
            "OpenAI Codex session expired and no refresh token is available. "
            "Re-run `aish models auth login --provider openai-codex`."
        )

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)

    try:
        response = await client.post(
            OPENAI_CODEX_REFRESH_URL,
            json={
                "client_id": OPENAI_CODEX_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": auth.refresh_token,
            },
            headers={"Content-Type": "application/json"},
        )
    except Exception as exc:
        raise OpenAICodexAuthError(
            f"Failed to refresh OpenAI Codex session: {exc}"
        ) from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code == httpx.codes.UNAUTHORIZED:
        raise OpenAICodexAuthError(
            "OpenAI Codex session is no longer valid. Re-run "
            "`aish models auth login --provider openai-codex`."
        )
    if response.is_error:
        raise OpenAICodexAuthError(
            f"Failed to refresh OpenAI Codex session: {response.status_code} {response.text}"
        )

    try:
        refresh_payload = response.json()
    except Exception as exc:
        raise OpenAICodexAuthError(
            "OpenAI Codex refresh returned invalid JSON."
        ) from exc

    access_token = _coerce_str(refresh_payload.get("access_token"))
    if not access_token:
        raise OpenAICodexAuthError(
            "OpenAI Codex refresh did not return an access token."
        )

    refresh_token = _coerce_str(refresh_payload.get("refresh_token")) or auth.refresh_token
    id_token_claims = _coerce_id_token_claims(refresh_payload.get("id_token"))
    account_id = (
        _extract_account_id_from_claims(id_token_claims)
        or _extract_account_id_from_claims(_decode_jwt_claims(access_token))
        or auth.account_id
    )
    expires_at = _coerce_int(_decode_jwt_claims(access_token).get("exp"))
    _persist_openai_codex_auth(
        auth.auth_path,
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=account_id,
        id_token=refresh_payload.get("id_token"),
    )
    return OpenAICodexAuthState(
        auth_path=auth.auth_path,
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=account_id,
        expires_at=expires_at,
    )


def build_openai_codex_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str = "auto",
) -> dict[str, Any]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role == "system":
            content = _coerce_message_text(message.get("content"))
            if content:
                instructions.append(content)
            continue

        if role == "user":
            content = _coerce_message_text(message.get("content"))
            if content:
                input_items.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": content}],
                    }
                )
            continue

        if role == "assistant":
            content = _coerce_message_text(message.get("content"))
            if content:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    }
                )

            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") or {}
                if not isinstance(function, dict):
                    continue
                name = _coerce_str(function.get("name"))
                call_id = _coerce_str(tool_call.get("id"))
                arguments = function.get("arguments")
                if not name or not call_id:
                    continue
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments or {}, ensure_ascii=False)
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    }
                )
            continue

        if role == "tool":
            tool_call_id = _coerce_str(message.get("tool_call_id"))
            if not tool_call_id:
                continue
            content = _coerce_message_text(message.get("content"))
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": content,
                }
            )

    return {
        "model": strip_openai_codex_prefix(model),
        "instructions": "\n\n".join(part for part in instructions if part).strip(),
        "input": input_items,
        "tools": _convert_tools_for_openai_codex(tools or []),
        "tool_choice": tool_choice,
        "parallel_tool_calls": True,
        "store": False,
        "stream": True,
        "include": [],
    }


def convert_openai_codex_response_to_chat_completion(
    payload: dict[str, Any],
) -> dict[str, Any]:
    output = payload.get("output")
    if not isinstance(output, list):
        output = []

    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "message":
            text = _extract_response_message_text(item.get("content"))
            if text:
                content_parts.append(text)
            continue
        if item_type == "function_call":
            name = _coerce_str(item.get("name"))
            call_id = _coerce_str(item.get("call_id")) or _coerce_str(item.get("id"))
            arguments = item.get("arguments")
            if not name or not call_id:
                continue
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments or {}, ensure_ascii=False)
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": arguments,
                    },
                }
            )

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(part for part in content_parts if part) or "",
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "choices": [
            {
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ]
    }


async def create_openai_codex_chat_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    api_base: str | None = None,
    auth_path: str | os.PathLike[str] | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    url = f"{resolve_openai_codex_base_url(api_base)}/responses"
    request_body = build_openai_codex_request(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        auth = load_openai_codex_auth(auth_path)
        if auth.needs_refresh() and auth.refresh_token:
            auth = await refresh_openai_codex_auth(auth, client=client)

        for attempt in range(2):
            async with client.stream(
                "POST",
                url,
                json=request_body,
                headers=_build_headers(auth),
            ) as response:
                if (
                    response.status_code == httpx.codes.UNAUTHORIZED
                    and auth.refresh_token
                    and attempt == 0
                ):
                    auth = await refresh_openai_codex_auth(auth, client=client)
                    continue
                if response.is_error:
                    await response.aread()
                    detail = _extract_http_error_message(response)
                    raise OpenAICodexAuthError(
                        f"OpenAI Codex request failed: {response.status_code} {detail}"
                    )

                content_type = _coerce_str(response.headers.get("content-type")).lower()
                if not content_type or "text/event-stream" in content_type:
                    payload = await _collect_openai_codex_stream_response(response)
                else:
                    raw_body = await response.aread()
                    body_text = raw_body.decode("utf-8", errors="replace")
                    if _looks_like_sse_text(body_text):
                        payload = _collect_openai_codex_stream_text(body_text)
                        return convert_openai_codex_response_to_chat_completion(payload)
                    try:
                        payload = json.loads(body_text)
                    except Exception as exc:
                        raise OpenAICodexAuthError(
                            "OpenAI Codex returned invalid JSON."
                        ) from exc
                return convert_openai_codex_response_to_chat_completion(payload)

    raise OpenAICodexAuthError(
        "OpenAI Codex request failed after token refresh."
    )


def persist_openai_codex_tokens(
    auth_path: str | os.PathLike[str],
    *,
    tokens: OpenAICodexOAuthTokens,
) -> None:
    account_id = (
        _extract_account_id_from_claims(_decode_jwt_claims(tokens.id_token))
        or _extract_account_id_from_claims(_decode_jwt_claims(tokens.access_token))
    )
    if not account_id:
        raise OpenAICodexAuthError(
            "OpenAI Codex login succeeded, but the account id was missing from the returned tokens."
        )

    _persist_openai_codex_auth(
        Path(auth_path),
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        account_id=account_id,
        id_token=tokens.id_token,
    )


def _bind_openai_codex_callback_server(
    expected_state: str, callback_port: int
) -> _OpenAICodexCallbackServer:
    ports = [callback_port]
    if callback_port != 0:
        ports.append(0)

    last_error: OSError | None = None
    for port in ports:
        try:
            return _OpenAICodexCallbackServer(("127.0.0.1", port), expected_state)
        except OSError as exc:
            last_error = exc

    raise OpenAICodexAuthError(
        f"Failed to bind OpenAI Codex callback server: {last_error}"
    ) from last_error


def _wait_for_openai_codex_browser_callback(
    server: _OpenAICodexCallbackServer, *, timeout: float
) -> OpenAICodexBrowserCallbackResult:
    if not server.callback_event.wait(timeout):
        raise OpenAICodexAuthError(
            "Timed out waiting for the OpenAI Codex browser callback."
        )

    result = server.callback_result
    if result is None:
        raise OpenAICodexAuthError(
            "OpenAI Codex browser login ended without a callback result."
        )
    return result


def _stop_openai_codex_callback_server(
    server: _OpenAICodexCallbackServer, server_thread: threading.Thread
) -> None:
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.server_close()
    except Exception:
        pass
    server_thread.join(timeout=2.0)


def _extract_http_error_message(response: httpx.Response) -> str:
    content_type = _coerce_str(response.headers.get("content-type")).lower()
    text = response.text.strip()
    if _looks_like_html_error(content_type, text):
        detail = _summarize_html_error(text)
        return _append_http_error_metadata(detail, response)

    try:
        payload = response.json()
    except Exception:
        return _append_http_error_metadata(text or "unknown error", response)

    if isinstance(payload, dict):
        error_description = _coerce_str(payload.get("error_description"))
        if error_description:
            return error_description
        error_value = payload.get("error")
        if isinstance(error_value, dict):
            error_message = _coerce_str(error_value.get("message"))
            error_code = _coerce_str(error_value.get("code"))
            if error_message:
                return error_message
            if error_code:
                return error_code
        error_text = _coerce_str(error_value)
        if error_text:
            return error_text

    return _append_http_error_metadata(text or "unknown error", response)


async def _collect_openai_codex_stream_response(
    response: httpx.Response,
) -> dict[str, Any]:
    events: list[tuple[str, dict[str, Any]]] = []
    async for event_type, payload in _iter_openai_codex_sse_events(response):
        events.append((event_type, payload))
    return _build_openai_codex_stream_payload(events)


def _collect_openai_codex_stream_text(text: str) -> dict[str, Any]:
    event_type: str | None = None
    data_lines: list[str] = []
    events: list[tuple[str, dict[str, Any]]] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            parsed = _parse_openai_codex_sse_event(event_type, data_lines)
            if parsed is not None:
                events.append(parsed)
            event_type = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.lstrip()
        if field == "event":
            event_type = value
        elif field == "data":
            data_lines.append(value)

    parsed = _parse_openai_codex_sse_event(event_type, data_lines)
    if parsed is not None:
        events.append(parsed)

    return _build_openai_codex_stream_payload(events)


def _build_openai_codex_stream_payload(
    events: list[tuple[str, dict[str, Any]]]
) -> dict[str, Any]:
    output_items: list[dict[str, Any]] = []
    text_deltas: list[str] = []
    response_id: str | None = None
    usage: Any = None
    completed = False

    for event_type, payload in events:
        if event_type == "response.output_item.done":
            item = payload.get("item")
            if isinstance(item, dict):
                output_items.append(item)
            continue

        if event_type == "response.output_text.delta":
            delta = _coerce_str(payload.get("delta"))
            if delta:
                text_deltas.append(delta)
            continue

        if event_type == "response.failed":
            raise OpenAICodexAuthError(
                _extract_openai_codex_stream_failure_message(payload)
            )

        if event_type == "response.incomplete":
            reason = (
                payload.get("response", {})
                if isinstance(payload.get("response"), dict)
                else {}
            )
            incomplete_details = (
                reason.get("incomplete_details")
                if isinstance(reason.get("incomplete_details"), dict)
                else {}
            )
            incomplete_reason = _coerce_str(incomplete_details.get("reason")) or "unknown"
            raise OpenAICodexAuthError(
                f"OpenAI Codex returned an incomplete response: {incomplete_reason}"
            )

        if event_type == "response.completed":
            response_payload = payload.get("response")
            if isinstance(response_payload, dict):
                response_id = _coerce_str(response_payload.get("id")) or response_id
                usage = response_payload.get("usage")
                if not output_items:
                    completed_output = response_payload.get("output")
                    if isinstance(completed_output, list):
                        output_items = [
                            item for item in completed_output if isinstance(item, dict)
                        ]
            completed = True
            break

    if not completed:
        raise OpenAICodexAuthError(
            "OpenAI Codex stream ended before response.completed."
        )

    if not output_items and text_deltas:
        output_items.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "".join(text_deltas)},
                ],
            }
        )

    payload: dict[str, Any] = {"output": output_items}
    if response_id:
        payload["id"] = response_id
    if usage is not None:
        payload["usage"] = usage
    return payload


def _looks_like_sse_text(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("event: ") or "\nevent: " in stripped


async def _iter_openai_codex_sse_events(
    response: httpx.Response,
):
    event_type: str | None = None
    data_lines: list[str] = []

    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            parsed = _parse_openai_codex_sse_event(event_type, data_lines)
            if parsed is not None:
                yield parsed
            event_type = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.lstrip()
        if field == "event":
            event_type = value
        elif field == "data":
            data_lines.append(value)

    parsed = _parse_openai_codex_sse_event(event_type, data_lines)
    if parsed is not None:
        yield parsed


def _parse_openai_codex_sse_event(
    event_type: str | None, data_lines: list[str]
) -> tuple[str, dict[str, Any]] | None:
    if not data_lines:
        return None

    data = "\n".join(data_lines).strip()
    if not data or data == "[DONE]":
        return None

    try:
        payload = json.loads(data)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    resolved_type = _coerce_str(payload.get("type")) or _coerce_str(event_type)
    if not resolved_type:
        return None
    return resolved_type, payload


def _extract_openai_codex_stream_failure_message(payload: dict[str, Any]) -> str:
    response_payload = payload.get("response")
    if isinstance(response_payload, dict):
        error = response_payload.get("error")
        if isinstance(error, dict):
            message = _coerce_str(error.get("message"))
            if message:
                return message
            code = _coerce_str(error.get("code"))
            if code:
                return code
    return "OpenAI Codex stream failed."


def _format_oauth_callback_error(
    error_code: str, error_description: str | None
) -> str:
    if (
        error_code == "access_denied"
        and error_description
        and "missing_codex_entitlement" in error_description.lower()
    ):
        return (
            "Codex is not enabled for this workspace. Ask the workspace administrator "
            "to grant Codex access."
        )
    if error_description:
        return f"OpenAI Codex sign-in failed: {error_description}"
    return f"OpenAI Codex sign-in failed: {error_code}"


def _coerce_message_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunk for chunk in chunks if chunk)
    return str(value)


def _extract_response_message_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"output_text", "input_text"}:
            text = item.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
    return "\n".join(chunks)


def _convert_tools_for_openai_codex(
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = _coerce_str(function.get("name"))
        if not name:
            continue
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": _coerce_str(function.get("description")),
                "parameters": function.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return converted


def _build_headers(auth: OpenAICodexAuthState) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {auth.access_token}",
        "ChatGPT-Account-ID": auth.account_id,
        "Content-Type": "application/json",
        "User-Agent": OPENAI_CODEX_USER_AGENT,
        "originator": OPENAI_CODEX_ORIGINATOR,
    }


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload}{padding}".encode("utf-8"))
        claims = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return claims if isinstance(claims, dict) else {}


def _coerce_id_token_claims(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return _decode_jwt_claims(value)
    if isinstance(value, dict):
        return value
    return {}


def _extract_account_id_from_claims(claims: dict[str, Any]) -> str:
    auth_claims = claims.get("https://api.openai.com/auth")
    if not isinstance(auth_claims, dict):
        auth_claims = {}
    return (
        _coerce_str(claims.get("chatgpt_account_id"))
        or _coerce_str(claims.get("account_id"))
        or _coerce_str(auth_claims.get("chatgpt_account_id"))
        or _coerce_str(auth_claims.get("account_id"))
    )


def _looks_like_html_error(content_type: str, body: str) -> bool:
    if "text/html" in content_type:
        return True
    lowered = body.lstrip().lower()
    return lowered.startswith("<!doctype html") or lowered.startswith("<html")


def _summarize_html_error(body: str) -> str:
    lowered = body.lower()
    if (
        "cloudflare" in lowered
        or "enable javascript and cookies to continue" in lowered
        or "_cf_chl_opt" in lowered
        or "challenge-platform" in lowered
    ):
        return (
            "Cloudflare blocked the OpenAI Codex request. "
            "This usually means the wrong ChatGPT endpoint was used or "
            "this network/region requires an interactive challenge."
        )
    return "OpenAI Codex returned an HTML error page instead of JSON."


def _append_http_error_metadata(detail: str, response: httpx.Response) -> str:
    metadata: list[str] = []
    for header_name in ("cf-ray", "x-request-id", "request-id"):
        header_value = _coerce_str(response.headers.get(header_name))
        if header_value:
            metadata.append(f"{header_name}: {header_value}")
    if metadata:
        return f"{detail} ({', '.join(metadata)})"
    return detail


def _persist_openai_codex_auth(
    auth_path: Path,
    *,
    access_token: str,
    refresh_token: str | None,
    account_id: str,
    id_token: Any,
) -> None:
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}

    payload["auth_mode"] = "chatgpt"
    payload.pop("OPENAI_API_KEY", None)

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
        payload["tokens"] = tokens

    tokens["access_token"] = access_token
    if refresh_token:
        tokens["refresh_token"] = refresh_token
    tokens["account_id"] = account_id

    if isinstance(id_token, str) and id_token.strip():
        tokens["id_token"] = id_token.strip()
    elif id_token is not None:
        tokens["id_token"] = id_token

    payload["last_refresh"] = datetime.now(timezone.utc).isoformat()
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _first_query_value(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _coerce_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coerce_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_non_negative_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default
