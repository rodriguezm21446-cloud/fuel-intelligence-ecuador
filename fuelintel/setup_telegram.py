"""Interactive, local-only Telegram activation wizard."""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


TOKEN_PATTERN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
DEFAULT_SECRET_PATH = Path("data/secrets/telegram.env")


def secret_input(prompt: str) -> str:
    """Read a secret while showing one asterisk per character on Windows."""
    if os.name != "nt":
        return getpass.getpass(prompt)

    import msvcrt

    sys.stdout.write(prompt)
    sys.stdout.flush()
    characters: list[str] = []
    while True:
        character = msvcrt.getwch()
        if character in {"\r", "\n"}:
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(characters)
        if character == "\x03":
            raise KeyboardInterrupt
        if character == "\b":
            if characters:
                characters.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if character in {"\x00", "\xe0"}:
            msvcrt.getwch()
            continue
        if character.isprintable():
            characters.append(character)
            sys.stdout.write("*")
            sys.stdout.flush()


def api_call(token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(params or {}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram rechazó la solicitud (HTTP {exc.code}).") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No se pudo conectar con Telegram: {exc.reason}") from None
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("description", "Respuesta inválida de Telegram.")))
    return payload.get("result")


def latest_private_chat(updates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and isinstance(chat.get("id"), int):
            return chat
    return None


def write_secret_file(path: Path, token: str, chat_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "FUELINTEL_DRY_RUN=false\n"
        "FUELINTEL_TELEGRAM_ENABLED=true\n"
        f"TELEGRAM_BOT_TOKEN={token}\n"
        f"TELEGRAM_CHAT_ID={chat_id}\n"
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> int:
    print("\nACTIVADOR PRIVADO DE TELEGRAM")
    print("Cada carácter del token aparecerá como un asterisco (*).\n")
    token = secret_input("Token nuevo entregado por @BotFather: ").strip()
    if not TOKEN_PATTERN.fullmatch(token):
        print("Token con formato inválido. No se guardó nada.", file=sys.stderr)
        return 2

    try:
        bot = api_call(token, "getMe")
        username = bot.get("username", "bot")
        print(f"Bot verificado: @{username}")

        chat_text = input("Chat ID privado (Enter para detectarlo con /start): ").strip()
        if chat_text:
            try:
                chat_id = int(chat_text)
            except ValueError:
                print("El Chat ID debe ser un número entero. No se guardó nada.", file=sys.stderr)
                return 3
        else:
            webhook = api_call(token, "getWebhookInfo")
            if webhook.get("url"):
                print(
                    "Este bot tiene un webhook activo. Para no romper el sistema existente, "
                    "no se modificó. Ejecuta de nuevo e introduce manualmente tu Chat ID.",
                    file=sys.stderr,
                )
                return 4
            print(f"Abre @{username} en Telegram, envía /start y vuelve aquí.")
            input("Cuando ya lo hayas enviado, pulsa Enter: ")
            updates = api_call(token, "getUpdates", {"offset": -20, "timeout": 0})
            chat = latest_private_chat(updates)
            if not chat:
                print("No encontré un mensaje privado. Envía /start y vuelve a ejecutar.", file=sys.stderr)
                return 5
            chat_id = int(chat["id"])

        result = api_call(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "Fuel Intelligence Ecuador quedó conectado. "
                    "Este es un mensaje privado de verificación."
                ),
                "disable_web_page_preview": "true",
            },
        )
        chat = result.get("chat") or {}
        if chat.get("type") != "private":
            print("El destino no es un chat privado. No se guardó nada.", file=sys.stderr)
            return 6
        label = chat.get("username") or chat.get("first_name") or "chat privado"
        write_secret_file(DEFAULT_SECRET_PATH, token, chat_id)
        print(f"\nActivación correcta. Mensaje enviado a {label}.")
        print(f"Configuración privada guardada en: {DEFAULT_SECRET_PATH}")
        print("Ya puedes iniciar el monitor con: python -m fuelintel.main")
        return 0
    except RuntimeError as exc:
        print(f"No se pudo activar: {exc}", file=sys.stderr)
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
