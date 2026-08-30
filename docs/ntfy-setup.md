# ntfy push server — setup

ScribeJay's one outbound push channel. A scheduled run that fails pushes a
one-line alert to your phone through [ntfy](https://ntfy.sh); nothing else in
this pipeline can reach out, and an emailed failure notice gets buried.

**Optional.** Leave `NTFY_URL` unset and push is simply off — an unset URL
means "switched off", not "delivery failed", so nothing warns about it.

Code: [scribejay/core/notify.py](../scribejay/core/notify.py) (`notify`), called
by `notify_failure` in [scribejay/core/logs.py](../scribejay/core/logs.py). A
push that does not send falls back to email, because this alert fires once and
nothing retries it.

## Two ways to run it

- **A private topic on ntfy.sh.** Nothing to install. Set `NTFY_URL` to
  `https://ntfy.sh/<a-long-unguessable-topic>` and stop reading here. The topic
  name is the only secret, so make it long.
- **A self-hosted server**, the rest of this page. It keeps alert text off the
  public internet, and `auth-default-access: deny-all` plus a publish token
  means nobody else can inject a fake notification.

The self-hosted setup below runs on a Mac behind Tailscale. TLS is unnecessary
there: the link rides Tailscale's encrypted tunnel.

## Why a container

**ntfy has no native macOS server.** `brew install ntfy` gives a *client-only*
binary (no `serve`), and even the official `darwin` release is client-only. So
on macOS the server runs as the Linux container in a lightweight VM — colima, no
Docker Desktop needed:

```bash
brew install colima docker
```

## Keeping colima up — not with `brew services`

**Don't use `brew services start colima`.** Homebrew's plist sets
`KeepAlive.SuccessfulExit=true`, which means *relaunch only after a clean exit* —
so a colima start that **fails** is never retried.

That is exactly what happened on 2026-07-11: the Mac rebooted, colima's VM died
mid-shutdown leaving stale state, the boot-time start failed with `vz driver is
running but host agent is not` (exit 1), launchd gave up, and the push channel
was down for four days. Nobody noticed, because nothing happened to need pushing.

Write your own agent instead — `KeepAlive=true` (retry on failure too) plus a
wrapper that clears the stale state a crash leaves behind, which a bare retry
cannot do. Put it in `launchd/infra/local.scribejay.colima.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.scribejay.colima</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>/opt/homebrew/bin/colima delete --force 2&gt;/dev/null; exec /opt/homebrew/bin/colima start --foreground</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/colima.log</string>
  <key>StandardErrorPath</key><string>/tmp/colima.log</string>
</dict>
</plist>
```

Then hand off from Homebrew and install it:

```bash
brew services stop colima
./launchd/install.sh launchd/infra/local.scribejay.colima.plist
```

It lives in `launchd/infra/`, so a bare `./launchd/install.sh` skips it — that
script globs `launchd/*.plist` only, and colima is optional infrastructure
rather than a ScribeJay job.

To stop colima deliberately, boot it out; `colima stop` alone won't stick, since
launchd immediately brings it back:

```bash
launchctl bootout gui/$(id -u)/local.scribejay.colima
```

That outage is also the reason to check this channel on purpose rather than
wait for it: a dead push channel is invisible until something tries to use it,
and by then the alert is the thing being lost. `scribejay doctor` reports the
push settings, and the smoke test below proves delivery end to end.

## The server

Create config + data dirs under your home (colima mounts `$HOME` into the VM, so
the container can read them):

```bash
mkdir -p ~/ntfy-server/{etc,lib,cache}
```

`~/ntfy-server/etc/server.yml`:

```yaml
base-url: "http://<mac-tailscale-name>:2586"
listen-http: ":2586"
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"      # no anonymous read OR publish
cache-file: "/var/cache/ntfy/cache.db"
upstream-base-url: "https://ntfy.sh" # iOS only: relays a *contentless*
                                     # wakeup ping so push is instant; the
                                     # message body is still fetched from
                                     # this server, never ntfy.sh.
```

Run the container (`--restart always` brings it back when colima restarts at
login):

```bash
docker run -d --name ntfy --restart always -p 2586:2586 \
  -v ~/ntfy-server/etc:/etc/ntfy \
  -v ~/ntfy-server/lib:/var/lib/ntfy \
  -v ~/ntfy-server/cache:/var/cache/ntfy \
  binwiederhier/ntfy:v2.26.0 serve
```

## Users, topic ACL, and the publish token

Create the publisher + subscriber, lock the topic down, and mint a publish token
(`NTFY_PASSWORD=...` sets passwords non-interactively):

```bash
docker exec -e NTFY_PASSWORD='<scribejay-pw>' ntfy ntfy user add scribejay
docker exec ntfy ntfy access scribejay scribejay-alerts write
docker exec -e NTFY_PASSWORD='<owner-pw>' ntfy ntfy user add owner
docker exec ntfy ntfy access owner scribejay-alerts read
docker exec ntfy ntfy token add scribejay   # -> tk_...  set as NTFY_TOKEN
```

ScribeJay publishes, the phone subscribes — separate accounts, so a leaked
publish token can't read the alert history.

## Wiring it up

Set both in `scribejay settings`, under **notify**:

| Setting | Value |
|---|---|
| `NTFY_URL` | `http://<mac-tailscale-name>:2586/scribejay-alerts` |
| `NTFY_TOKEN` | `tk_...` |

`NTFY_TOKEN` is a credential, so it goes to the macOS Keychain rather than the
settings file ([configuration.md](configuration.md)).

On the iPhone, install the ntfy app → add a custom server pointing at the same
Tailscale URL → log in as `owner` (its password) → subscribe to
`scribejay-alerts`.

Smoke-test end to end:

```bash
.venv/bin/python -c 'from scribejay.core.notify import notify; print(notify("hello from ScribeJay", title="ScribeJay"))'
```

`{"ok": True}` means the phone has it. Anything else is the exact error the
scheduled run would hit.

## Related

- [configuration.md](configuration.md) — where `NTFY_TOKEN` is stored
- [features.md](features.md) — the `notify` feature toggle
- [logs.md](logs.md) — where a failed run writes before it pushes
