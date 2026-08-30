"""Send email via Gmail API — the vault-write fallback and the colorizer's
failure notice.

Mirrors just `send_email` from LocalLLMAgent's agent/tools/email.py. The
recipient is pinned to BRIEF_TO_EMAIL; this module never grows a `to`
parameter — ScribeJay has no reply-to-thread need, so the rest of that file
(reply_to_thread, gmail_read integration) is not carried over.
"""

import base64
from email.mime.text import MIMEText

from scribejay.core import config
from scribejay.core.google import build_service


def send_email(subject: str, body: str, to: str = None, html: bool = False) -> dict:
    to = to or config.getenv("BRIEF_TO_EMAIL")
    if not to:
        return {"error": "BRIEF_TO_EMAIL not set"}

    message = MIMEText(body, "html" if html else "plain")
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    try:
        sent = build_service("gmail", "v1").users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        return {"error": str(e)}

    return {"message_id": sent.get("id")}
