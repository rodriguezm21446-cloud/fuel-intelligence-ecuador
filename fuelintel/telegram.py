from __future__ import annotations

import json
import mimetypes
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, enabled: bool, dry_run: bool):
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and not dry_run and bool(token and chat_id)
        self.dry_run = dry_run

    @property
    def api_base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def send_text(self, text: str) -> bool:
        if not self.enabled:
            print("\n--- ALERTA EN MODO SEGURO ---\n" + text + "\n--- FIN ALERTA ---\n")
            return False
        payload = urllib.parse.urlencode(
            {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"}
        ).encode("utf-8")
        request = urllib.request.Request(f"{self.api_base}/sendMessage", data=payload)
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
        if not result.get("ok"):
            raise RuntimeError(f"Telegram rechazó el mensaje: {result}")
        return True

    def send_photo(self, path: Path, caption: str) -> bool:
        if not self.enabled:
            print(f"[modo seguro] gráfico generado: {path}")
            return False
        boundary = f"----fuelintel-{uuid.uuid4().hex}"
        filename = path.name
        mime = mimetypes.guess_type(filename)[0] or "image/png"
        chunks = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{self.chat_id}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode("utf-8"),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        request = urllib.request.Request(
            f"{self.api_base}/sendPhoto",
            data=b"".join(chunks),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read())
        if not result.get("ok"):
            raise RuntimeError(f"Telegram rechazó el gráfico: {result}")
        return True

    def send_document_url(self, url: str, caption: str) -> bool:
        """Send Telegram the original remote document without downloading a copy."""
        if not self.enabled:
            print(f"[modo seguro] documento oficial: {url}")
            return False
        payload = urllib.parse.urlencode(
            {"chat_id": self.chat_id, "document": url, "caption": caption}
        ).encode("utf-8")
        request = urllib.request.Request(f"{self.api_base}/sendDocument", data=payload)
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read())
        if not result.get("ok"):
            raise RuntimeError(f"Telegram rechazó el documento: {result}")
        return True
