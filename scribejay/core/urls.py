"""URL guards shared by everything that handles an externally-sourced link.

Two jobs, not one. `safe_url` guards a url on its way INTO a file, so a
remote page cannot forge a second link out of the one it was given.
`is_private_host` guards a url on its way OUT, so a page the user visited
cannot point the fetcher back at this machine.

Started as a verbatim copy of LocalLLMAgent's tasks/_urls.py. It is no longer
one: the scheme check there is the whole function, and the delimiter encoding
below was added here after a review found that a scheme-clean url can still
close its own Markdown link and forge a second one.

**That gap does not reach LocalLLMAgent, checked 2026-09-15.** Its four
`safe_url` callers all render HTML — `morning_brief._game_html` and
`_starred_repos_html`, `opportunity_digest._item_html` and `_triage_footer` —
and each one writes the url as `html.escape(url)` inside a quoted `href`, which
closes the attribute-breakout the same way this table closes the Markdown one.
It builds no Markdown links at all; its only `](` is a regex in
`agent/tools/evaluate_app.py` that strips them out of fetched text. So the
scheme check really is the whole job there, and copying this table across would
be a fix for a renderer that repo does not have. What would change that is a
plain-text or Markdown emitter — a text alternative beside the digest's
`MIMEText(body, "html")` — because `safe_url` alone would not cover it.

The sibling of `scribejay/core/text.py`: `safe_url` guards the destination,
`safe_label` guards the words around it. Import from here rather than copying.
"""

import ipaddress
import re
import socket
from urllib.parse import urlparse

# The characters that end a Markdown link target. `[Title](url)` puts the url
# inside parentheses, so a `)` in the url closes the link early and everything
# after it is rendered as fresh Markdown chosen by whoever controls the url:
#
#     https://good.example/a) [Free gift](http://evil.example
#
# renders as the expected link followed by a SECOND live link, wearing a label
# the user never saw. `)` is a legal unencoded path character — Wikipedia uses
# it — so this is reachable from an ordinary browsing history, not only from a
# crafted one.
#
# Percent-encoded rather than neutralized the way text.py:safe_label
# neutralizes its own set. A label only has to read; a url still has to
# resolve, and %28/%29/%20 are equivalent to the raw characters for every http
# server. The result is a link that still works and can no longer escape its
# own parentheses.
# `[` and `]` are in the table for a different reason than the rest. They do
# not end a link target in CommonMark, so a renderer that follows the spec is
# already safe from them — but the vault is read in Obsidian, and betting the
# guard on one renderer's bracket handling buys nothing when encoding them
# costs nothing. It also means no bracket a remote page chose survives into the
# page at all, which is a property worth being able to state plainly.
_LINK_DELIMITERS = {
    "(": "%28",
    ")": "%29",
    " ": "%20",
    "<": "%3C",
    ">": "%3E",
    "[": "%5B",
    "]": "%5D",
    '"': "%22",
    "\\": "%5C",
}


def safe_url(url: str) -> str:
    """Return url only if it's an http(s) link, else "". Guards against
    javascript:/data: (or other) schemes in externally-sourced URLs —
    html.escape() alone does not neutralize a dangerous scheme, because the
    danger is in the scheme rather than in any character escaping would touch.

    The returned url also has its Markdown link delimiters percent-encoded, so
    it cannot break out of the `(...)` a caller renders it into. See
    `_LINK_DELIMITERS`.

    Returning "" rather than raising is what lets a caller degrade to unlinked
    text: a bad URL costs its link, not the whole page."""
    try:
        if urlparse(url).scheme not in ("http", "https"):
            return ""
    except (ValueError, AttributeError):
        return ""
    for char, encoded in _LINK_DELIMITERS.items():
        url = url.replace(char, encoded)
    return url


# Names that start like a private address. Not an address test — those go
# through `ipaddress` below — but a free catch for the rebinding services
# (`127.0.0.1.nip.io`) that spell the address they will resolve to.
_PRIVATE_HOST_PREFIXES = ("127.", "10.", "192.168.", "169.254.", "0.")

# IPv4 the way the resolver reads it, not the way people write it: up to four
# parts, each decimal, octal or hex. `2130706433`, `0x7f000001` and `127.1` are
# all 127.0.0.1 to `getaddrinfo`, and a redirect gets to pick the spelling.
_IPV4_SHORTHAND = re.compile(r"^(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+)){0,3}$")


def _ip_literal(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _IPV4_SHORTHAND.match(host):
        try:
            return ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def is_private_address(ip) -> bool:
    """True for any address that is not on the public internet.

    `is_global` is the whole rule, so the stdlib owns the list of ranges —
    loopback, RFC1918, link-local and the metadata address, CGNAT, IPv6
    unique-local and link-local — rather than a hand-kept one that misses the
    next range. Multicast is global by that definition and is not a web page.

    An IPv4-mapped IPv6 address is judged as the IPv4 address it carries. The
    3.12 this project's venv runs already does that inside `is_global`, so no
    test here can fail without the unwrap. It stays for older interpreters
    `requires-python` still admits, whose `ipaddress` judged the IPv6 wrapper
    and not the address inside it.
    """
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def is_private_host(host: str) -> bool:
    """True if this host is this machine or its own network.

    Lives here rather than beside its first caller because it has two, and
    they guard different moments: `activity.py:candidate_urls` screens the url
    the user VISITED, and `sources/web_fetch.py` screens every url a redirect
    points it at. A second copy would let the two drift, and the half that
    drifted would be the one nobody reads.

    An address is judged as an address, however it is spelled. A name is
    judged only by its shape here — whether it RESOLVES somewhere private is a
    DNS question, and `web_fetch` asks it before each request.

    An empty or unparseable host is private. That is the fail-closed answer,
    and it costs at most one page: a host nothing can identify is not one worth
    sending a request to, or reading a body from.
    """
    host = (host or "").lower().strip("[]").rstrip(".")
    if not host:
        return True
    ip = _ip_literal(host)
    if ip is None and host.count(":") == 1:  # "name:port", never IPv6
        host = host.split(":")[0]
        ip = _ip_literal(host)
    if ip is not None:
        return is_private_address(ip)
    return (host == "localhost" or host.endswith((".localhost", ".local"))
            or host.startswith(_PRIVATE_HOST_PREFIXES))
