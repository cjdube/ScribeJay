"""Send email via Gmail API — the vault-write fallback and the colorizer's
failure notice.

Mirrors just `send_email` from LocalLLMAgent's agent/tools/email.py. The
recipient is pinned to BRIEF_TO_EMAIL; this module never grows a `to`
parameter — ScribeJay has no reply-to-thread need, so the rest of that file
(reply_to_thread, gmail_read integration) is not carried over.
"""

import base64
import os
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

from scribejay.core.google import build_service

_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / "config" / ".env")


def send_email(subject: str, body: str, to: str = None, html: bool = False) -> dict:
    to = to or os.getenv("BRIEF_TO_EMAIL")
    if not to:
        return {"error": "BRIEF_TO_EMAIL not set in config/.env"}

    message = MIMEText(body, "html" if html else "plain")
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    try:
        sent = build_service("gmail", "v1").users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        return {"error": str(e)}

    return {"message_id": sent.get("id")}
