"""Deploy the Worker through Cloudflare's REST API without exposing OAuth secrets."""

from __future__ import annotations

import json
import hashlib
import os
import secrets
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path


API = "https://api.cloudflare.com/client/v4"
SCRIPT_NAME = "fuelintel-ecuador"
D1_DATABASE_ID = "a0a6143e-8b44-4dbb-86ab-2b77435199a3"


def oauth_token() -> str:
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_file = config_root / ".wrangler" / "config" / "default.toml"
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))

    def find_token(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"oauth_token", "access_token"} and isinstance(item, str):
                    return item
            for item in value.values():
                found = find_token(item)
                if found:
                    return found
        return None

    token = find_token(data)
    if not token:
        raise RuntimeError(f"No se encontró la autorización de Wrangler en {config_file}")
    return token


def request(token: str, method: str, path: str, body=None, content_type="application/json"):
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "fuelintel-deployer/1.0"}
    if body is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"Cloudflare respondió HTTP {exc.code}: {detail}") from exc
    if not payload.get("success", False):
        raise RuntimeError(f"Cloudflare rechazó la operación: {payload.get('errors')}")
    return payload.get("result")


def multipart(worker_code: bytes, setup_key: str) -> tuple[bytes, str]:
    boundary = "----fuelintel" + secrets.token_hex(16)
    metadata = {
        "main_module": "worker.js",
        "compatibility_date": "2026-09-01",
        "bindings": [
            {"type": "d1", "name": "DB", "id": D1_DATABASE_ID},
            {"type": "plain_text", "name": "GITHUB_WORKFLOW", "text": "cloud-job.yml"},
            {"type": "plain_text", "name": "GITHUB_REF", "text": "main"},
            {"type": "plain_text", "name": "GITHUB_REPOSITORY", "text": "rodriguezm21446-cloud/fuel-intelligence-ecuador"},
            {"type": "secret_text", "name": "SETUP_KEY", "text": setup_key},
        ],
        "annotations": {"workers/message": "Despliegue inicial seguro con línea base"},
    }
    parts = []
    for name, filename, kind, value in (
        ("metadata", None, "application/json", json.dumps(metadata).encode("utf-8")),
        ("worker.js", "worker.js", "application/javascript+module", worker_code),
    ):
        disposition = f'form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        parts.append(
            f"--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {kind}\r\n\r\n".encode()
            + value
            + b"\r\n"
        )
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    token = oauth_token()
    accounts = request(token, "GET", "/accounts")
    selected = [item for item in accounts if item.get("id")]
    if len(selected) != 1:
        raise RuntimeError("Se necesita exactamente una cuenta Cloudflare autorizada")
    account_id = selected[0]["id"]

    code = (Path(__file__).parent / "src" / "worker.js").read_bytes()
    setup_key = hashlib.sha256(("fuelintel-setup:" + token).encode("utf-8")).hexdigest()
    body, content_type = multipart(code, setup_key)
    request(token, "PUT", f"/accounts/{account_id}/workers/scripts/{SCRIPT_NAME}", body, content_type)
    schedule = json.dumps([{"cron": "* * * * *"}]).encode("utf-8")
    request(token, "PUT", f"/accounts/{account_id}/workers/scripts/{SCRIPT_NAME}/schedules", schedule)

    public_url = None
    try:
        request(
            token,
            "POST",
            f"/accounts/{account_id}/workers/scripts/{SCRIPT_NAME}/subdomain",
            json.dumps({"enabled": True, "previews_enabled": True}).encode("utf-8"),
        )
        subdomain = request(token, "GET", f"/accounts/{account_id}/workers/subdomain")
        subdomain_name = subdomain.get("subdomain") if isinstance(subdomain, dict) else subdomain
        if subdomain_name:
            public_url = f"https://{SCRIPT_NAME}.{subdomain_name}.workers.dev/health"
    except RuntimeError:
        # The scheduled Worker does not depend on a public workers.dev route.
        pass

    schedules = request(token, "GET", f"/accounts/{account_id}/workers/scripts/{SCRIPT_NAME}/schedules")
    schedule_rows = schedules.get("schedules", []) if isinstance(schedules, dict) else schedules
    health = None
    if public_url:
        try:
            run_request = urllib.request.Request(
                public_url.replace("/health", "/admin/run"),
                data=b"",
                headers={"User-Agent": "Mozilla/5.0", "X-Setup-Key": setup_key},
                method="POST",
            )
            with urllib.request.urlopen(run_request, timeout=120) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            health = {"bootstrap_http": exc.code, "bootstrap_body": exc.read().decode("utf-8", errors="replace")[:1000]}
        try:
            health_request = urllib.request.Request(public_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(health_request, timeout=30) as response:
                health = {"http": response.status, "body": json.loads(response.read().decode("utf-8"))}
        except urllib.error.HTTPError as exc:
            health = {"http": exc.code, "body": exc.read().decode("utf-8", errors="replace")[:1000]}
    print(json.dumps({
        "deployed": True,
        "script": SCRIPT_NAME,
        "cron": [item.get("cron") if isinstance(item, dict) else item for item in schedule_rows],
        "health_url": public_url,
        "health": health,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
