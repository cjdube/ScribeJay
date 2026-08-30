"""`scribejay settings` — a settings page, on 127.0.0.1, for as long as it takes.

On-demand, not a daemon. The server binds an ephemeral loopback port, opens the
browser at it, and stops when the user clicks "Save and close" or stops touching
it. A journaling agent that left a web server listening all day would be a
strange thing to install.

## Why a localhost port is not automatically safe

Any web page the user has open can issue requests to `http://127.0.0.1:<port>`.
It cannot *read* the response without CORS, but it can fire a POST, and a POST
here rewrites the settings a scheduled job runs against. Worse, a hostile DNS
name can be pointed at 127.0.0.1 so a page's *own* origin becomes this server —
DNS rebinding, which defeats the same-origin protection entirely.

So, four rules, none of them optional:

1. **Bind 127.0.0.1 only**, on port 0 (the OS picks a free one).
2. **A per-launch token**, in the opened URL and then in a `SameSite=Strict`
   cookie. No token, no response — not even the login page. There is nothing to
   guess and nothing to phish; the token exists only in this process and the
   browser the user is sitting at.
3. **`Host` must be `127.0.0.1:<port>`.** This is the DNS-rebinding defence: a
   rebound name arrives with its own hostname in the Host header, and is
   refused before anything reads a cookie.
4. **`Origin` must match on every POST**, which is CSRF cover on top of the
   form's own token.

Single-threaded on purpose — `handle_request()` in a loop rather than
`serve_forever()` in a thread. One person is filling in one form, so
concurrency buys nothing, and the idle timeout becomes a loop condition instead
of a background timer that has to be cancelled correctly on every exit path.
"""

import http.server
import secrets as pysecrets
import sys
import time
import urllib.parse
import webbrowser

from scribejay.cli import settings_form

BIND_HOST = "127.0.0.1"

# How long the loop waits for a request before checking the clock. Also how
# long "Save and close" takes to actually exit, so keep it short.
POLL_SECONDS = 0.5

# The whole form is a few kilobytes of text fields. Anything larger is not this
# page, and reading it into memory before the guard runs would be the one thing
# an unauthenticated caller could make this process do.
MAX_BODY_BYTES = 1_000_000


class Session:
    """One launch: the token, the deadline, and whether we are done."""

    def __init__(self, idle_timeout: int):
        self.token = pysecrets.token_urlsafe(32)
        self.idle_timeout = idle_timeout
        self.deadline = time.monotonic() + idle_timeout
        self.done = False
        # Filled in once the socket is bound and the OS has chosen a port. The
        # handler reads it from here rather than closing over it, because the
        # handler class has to exist before the port does.
        self.expected_host = ""

    def touch(self) -> None:
        self.deadline = time.monotonic() + self.idle_timeout

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.deadline


def check_request(method: str, headers, query: dict, form: dict,
                  session: Session) -> tuple[int, str]:
    """(status, reason) for one request. 200 means "go ahead".

    Pure, and separated from the handler so the rules above can be tested as
    rules rather than through a socket. The order matters: Host is checked
    before the token, so a rebound request is refused without the server ever
    looking at a cookie it might have been tricked into sending.
    """
    host = (headers.get("Host") or "").strip()
    if host != session.expected_host:
        return 403, f"unexpected Host: {host!r}"

    if method == "POST":
        origin = (headers.get("Origin") or "").strip()
        if origin and origin != f"http://{session.expected_host}":
            return 403, f"unexpected Origin: {origin!r}"
        if not pysecrets.compare_digest(form.get("csrf", ""), session.token):
            return 403, "missing or wrong CSRF token"
        return 200, ""

    supplied = query.get("t", "") or _cookie_token(headers)
    if not pysecrets.compare_digest(supplied, session.token):
        return 401, "missing or wrong token"
    return 200, ""


def _cookie_token(headers) -> str:
    for part in (headers.get("Cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "scribejay":
            return value
    return ""


def _one_valued(pairs: dict[str, list[str]]) -> dict[str, str]:
    """Last value wins, matching how a browser form actually behaves and how
    every other form parser reads it."""
    return {k: v[-1] for k, v in pairs.items()}


def make_handler(session: Session):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "ScribeJay"
        # Suppress the default stderr access log. It would print the request
        # line — which for a GET carries the token in the query string — into
        # whatever terminal the user launched from.
        def log_message(self, *args):
            pass

        def _send(self, status: int, body: str, content_type="text/html; charset=utf-8",
                  set_cookie: bool = False):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            # Nothing here should ever be cached, framed, or sniffed: the page
            # renders which credentials exist and the form rewrites settings.
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if set_cookie:
                self.send_header(
                    "Set-Cookie",
                    f"scribejay={session.token}; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(payload)

        def _guard(self, method: str, query: dict, form: dict) -> bool:
            status, reason = check_request(method, self.headers, query, form,
                                           session)
            if status == 200:
                session.touch()
                return True
            # The reason goes to the client, never to stdout: a refusal is not
            # interesting enough to risk printing a header a caller chose.
            self._send(status, f"<h1>{status}</h1><p>{reason}</p>")
            return False

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = _one_valued(urllib.parse.parse_qs(parsed.query))
            if not self._guard("GET", query, {}):
                return
            if parsed.path != "/":
                self._send(404, "<h1>404</h1>")
                return
            # The cookie is (re)set on every page load so a reload without the
            # ?t= query string still works — the token leaves the address bar
            # after the first visit.
            self._send(200, settings_form.render(session.token), set_cookie=True)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send(413, "<h1>413</h1>")
                return
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            form = _one_valued(urllib.parse.parse_qs(raw, keep_blank_values=True))
            if not self._guard("POST", {}, form):
                return

            path = urllib.parse.urlparse(self.path).path
            if path == "/test":
                self._send(200, settings_form.test_feature(form.get("feature", "")),
                           content_type="text/plain; charset=utf-8")
                return
            if path not in ("/save", "/done"):
                self._send(404, "<h1>404</h1>")
                return

            messages, errors = settings_form.apply(form)
            if path == "/done" and not errors:
                session.done = True
                self._send(200, "<h1>Saved.</h1><p>You can close this tab.</p>")
                return
            self._send(200, settings_form.render(session.token, messages, errors))

    return Handler


def serve(open_browser: bool = True, idle_timeout: int = 900) -> int:
    session = Session(idle_timeout)

    # Port 0: the OS hands back a free one, so two settings screens can never
    # collide and nothing is left listening on a predictable number.
    server = http.server.HTTPServer((BIND_HOST, 0), make_handler(session))
    server.timeout = POLL_SECONDS
    session.expected_host = f"{BIND_HOST}:{server.server_port}"

    url = f"http://{session.expected_host}/?t={session.token}"
    print(f"Settings: {url}")
    print(f"Stops on 'Save and close', or after {idle_timeout}s idle. "
          f"Ctrl-C also stops it.")
    if open_browser:
        webbrowser.open(url)

    try:
        while not session.done and not session.expired:
            server.handle_request()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        server.server_close()

    if session.done:
        print("Settings saved.")
    elif session.expired:
        print(f"No activity for {idle_timeout}s — server stopped.")
    return 0
