"""The settings form: what it shows, what it accepts, and what a Test does.

Every field on the page comes from `scribejay/core/schema.py` and every group
header from `scribejay/core/features.py`. Nothing here knows the name of a
single setting. That is the whole point of the two tables: adding an
integration is a row, not an edit in five places, and the form cannot drift
out of step with what the code actually reads.

Kept apart from `settings_server.py` so the decisions — what a field is worth,
whether a path is safe to write, what a Test says — are plain functions over
plain data, testable without opening a socket.

**Secrets are write-only here.** A secret field renders as `set` or `not set`
and never as a value; a blank one means "leave it alone", not "clear it".
`config.set_value()` refuses a secret key outright, so the only way one reaches
storage is the deliberate `core/secrets.py` call in `apply()`.
"""

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scribejay.core import config, features, schema, secrets
from scribejay.core.dates import local_timezone, prior_day

# Path settings that name a FILE rather than a folder. Listed rather than
# guessed from the key, so a new path row is classified by someone who knows
# which it is instead of by a suffix rule that will eventually be wrong.
FILE_PATH_KEYS = frozenset({"GOOGLE_CREDENTIALS_PATH", "GOOGLE_TOKEN_PATH"})

# Directories a journal must never be pointed at, checked after symlinks are
# resolved. A path setting decides where an unattended job writes files; the
# realistic abuse is not a malicious user typing one of these but a symlink
# quietly resolving into one, which is why the check happens on the resolved
# path and the resolved path is what gets stored.
FORBIDDEN_ROOTS = (
    Path.home() / "Library" / "LaunchAgents",
    Path.home() / ".ssh",
    Path("/System"), Path("/Library"), Path("/usr"), Path("/bin"),
    Path("/sbin"), Path("/etc"), Path("/private/etc"), Path("/var"),
)


class Group:
    """One box on the page: a feature's settings, plus the features it governs.

    A group and a feature are not one-to-one, which is why this exists.
    `google` is one OAuth client configured once, and three separate things a
    user may accept or decline — somebody may well want the YouTube journal
    without ScribeJay recolouring their work calendar.
    """

    def __init__(self, name: str, label: str, rows: list[schema.Setting],
                 slug: str):
        self.name = name
        self.label = label
        # `name` is not unique — core yields three groups — so the tab rail,
        # the panel it reveals, and the accent all key off `slug` instead.
        self.slug = slug
        self.accent = ACCENTS.get(slug, DEFAULT_ACCENT)
        self.settings = [s for s in rows if not s.key.startswith("SCRIBEJAY_FEATURE_")]
        self.toggles = [s for s in rows if s.key.startswith("SCRIBEJAY_FEATURE_")]
        self.features = [f for f in features.FEATURES if f.settings_group == name]

    def owns_error(self, errors) -> bool:
        """Does one of this group's fields explain one of these errors?

        Matched on the label rather than the key because that is what the
        message carries — `validate()` writes "{label}: ..." and the Keychain
        failure writes "Could not store {label} ...". A tab the user cannot see
        is exactly where a rejected value would otherwise hide.
        """
        labels = [s.label for s in self.settings + self.toggles]
        return any(label in text for text in errors for label in labels)


# Everything that is not one of the eight declinable sources shares the "core"
# feature, which would put timezone, folders, and four model backends under one
# heading. They already carry a settings-file section each, so the page splits
# on that rather than inventing a second grouping nobody else reads.
CORE_SECTION_LABELS = {
    "core": "Core",
    "output": "Where pages are written",
    "model": "Model",
}

# Groups that govern more than one feature, and so cannot take a feature's own
# label. One OAuth client, three things a user declines separately.
GROUP_LABELS = {"google": "Google (calendar, sent mail, YouTube)"}

# One accent per tab, so the rail reads as ten places rather than ten words.
# Mid-ramp values, chosen to hold their contrast against both a light and a
# dark Canvas — text never sits on them, only a dot, a rule and an 8% wash, so
# one set covers both colour schemes. Keyed by slug; a group without a row here
# falls back to the hairline grey rather than failing to render.
DEFAULT_ACCENT = "#888780"
ACCENTS = {
    "core": "#888780",
    "output": "#1D9E75",
    "model": "#534AB7",
    "google": "#378ADD",
    "chrome": "#EF9F27",
    "transcripts": "#D85A30",
    "git": "#D4537E",
    "strava": "#639922",
    "clickup": "#0F6E56",
    "notify": "#E24B4A",
}


def _label_for(name: str, group_features: list) -> str:
    if name in GROUP_LABELS:
        return GROUP_LABELS[name]
    if len(group_features) == 1:
        return group_features[0].label
    return name.title()


def groups() -> list[Group]:
    """Every group with something to show, in schema declaration order — which
    is the order the wizard asks its questions in too."""
    result: list[Group] = []
    for name in schema.FEATURES:
        rows = schema.by_feature(name)
        if not rows:
            continue
        if name == "core":
            for section, label in CORE_SECTION_LABELS.items():
                in_section = [r for r in rows if r.section == section]
                if in_section:
                    result.append(Group(name, label, in_section, section))
            continue
        group_features = [f for f in features.FEATURES if f.settings_group == name]
        result.append(Group(name, _label_for(name, group_features), rows, name))
    return result


# ---- reading the current value ----------------------------------------------

def current_value(setting: schema.Setting) -> str:
    """What the form should pre-fill. Never a secret."""
    if setting.secret:
        return ""
    return config.getenv(setting.key) or ""


def secret_state(key: str) -> str:
    return "set" if secrets.is_set(key) else "not set"


# ---- validation --------------------------------------------------------------

def validate_path(setting: schema.Setting, value: str,
                  create: bool = False) -> tuple[str, str]:
    """(stored value, error). An empty value is fine and means "use the default".

    Symlinks are resolved and the *resolved* path is what gets stored. A path
    setting decides where an unattended job writes, and an indirection the user
    cannot see in the form is exactly the thing that should not survive into
    the settings file.

    `create=True` is the setup wizard, and only the setup wizard. It creates a
    missing *folder* — never a file, and never before the forbidden-root check
    below, so "make me a journal folder" can never become "make me a folder in
    /System". The form itself passes False: a folder that vanished after setup
    is a renamed vault, and silently recreating it would file every later page
    somewhere the user is not looking.
    """
    if not value.strip():
        return "", ""

    path = config.resolve_path(value.strip())
    try:
        resolved = path.resolve()
    except OSError as e:
        return "", f"{setting.label}: {e}"

    for root in FORBIDDEN_ROOTS:
        if resolved == root or root in resolved.parents:
            return "", (f"{setting.label}: {resolved} is inside {root}, which "
                        f"ScribeJay will not write to.")

    if setting.key in FILE_PATH_KEYS:
        if not resolved.parent.is_dir():
            return "", f"{setting.label}: {resolved.parent} does not exist."
        if resolved.exists() and not resolved.is_file():
            return "", f"{setting.label}: {resolved} is not a file."
        return str(resolved), ""

    if not resolved.is_dir():
        # Not created here unless the caller is the wizard. sinks/vault.py has
        # the same rule, for the same reason: a folder that does not exist
        # means the path is wrong, and filing pages where nobody reads them is
        # worse than failing.
        if not create:
            return "", (f"{setting.label}: {resolved} is not a folder that exists. "
                        f"Create it first, or fix the path.")
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return "", f"{setting.label}: could not create {resolved} ({e})."
    return str(resolved), ""


def validate(setting: schema.Setting, value: str,
             create: bool = False) -> tuple[str, str]:
    """(stored value, error) for any non-secret row. `create` is the wizard's
    folder-making flag; see validate_path."""
    value = value.strip()
    if not value:
        return "", ""
    if setting.type == "path":
        return validate_path(setting, value, create=create)
    if setting.type in ("int", "float"):
        try:
            (int if setting.type == "int" else float)(value)
        except ValueError:
            return "", f"{setting.label}: '{value}' is not a number."
    if setting.type == "choice" and value not in setting.choices:
        return "", f"{setting.label}: '{value}' is not one of {', '.join(setting.choices)}."
    if setting.type == "bool" and value not in ("0", "1"):
        return "", f"{setting.label}: '{value}' is not yes or no."
    if setting.key == "TIMEZONE":
        try:
            ZoneInfo(value)
        except Exception:
            return "", f"{setting.label}: '{value}' is not an IANA timezone name."
    return value, ""


# ---- writing -----------------------------------------------------------------

def apply(fields: dict[str, str]) -> tuple[list[str], list[str]]:
    """Save the submitted form. Returns (saved messages, errors).

    Nothing is written unless every field validates. A half-applied form is the
    worst outcome available here: the user sees an error, assumes nothing
    happened, and a scheduled job at 4:30 runs against a setting they thought
    they had abandoned.
    """
    staged: list[tuple[schema.Setting, str]] = []
    errors: list[str] = []

    for setting in schema.SETTINGS:
        if setting.secret or setting.key not in fields:
            continue
        value, error = validate(setting, fields[setting.key])
        if error:
            errors.append(error)
        else:
            staged.append((setting, value))

    if errors:
        return [], errors

    for setting, value in staged:
        config.set_value(setting.key, value)
    config.flush()

    saved = [f"Saved {len(staged)} setting(s) to {config.config_path()}"]

    # Credentials last, and only after the settings file is on disk: a Keychain
    # write that fails should not also lose the twenty non-secret fields the
    # user filled in on the same visit.
    for setting in schema.SETTINGS:
        if not setting.secret:
            continue
        value = fields.get(setting.key, "").strip()
        if not value:
            # Blank means "leave it alone". It cannot mean "clear it": the
            # field renders empty on every visit, so clearing on blank would
            # delete a working credential whenever anyone saved the page.
            continue
        if secrets.set(setting.key, value):
            saved.append(f"Stored {setting.label} in the Keychain")
        else:
            errors.append(f"Could not store {setting.label} in the Keychain")

    return saved, errors


# ---- the Test button ---------------------------------------------------------

def _yesterday():
    return prior_day(datetime.now(ZoneInfo(local_timezone())))


def _rows(result: dict, *names: str) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    for name in names:
        if name in result:
            return f"{len(result[name])} row(s) for yesterday"
    return "ok"


def test_feature(name: str) -> str:
    """Call the real fetcher for one feature and describe what came back.

    Deliberately the same function the scheduled task calls, not a lighter
    imitation: the question a user is asking by pressing Test is "will the 4:30
    run work", and only the real call answers it. Every source returns
    `{"error": ...}` rather than raising, so a broken one reports here the same
    way it would in a log.
    """
    start, end, day = _yesterday()

    if name == "chrome":
        from scribejay.sources.chrome import fetch_chrome_history
        return _rows(fetch_chrome_history(days_ago=1, max_sites=None), "sites")

    if name == "transcripts":
        from scribejay.sources.transcripts import fetch_session_activity
        return f"{len(fetch_session_activity(start, end))} session(s) for yesterday"

    if name == "git":
        from scribejay.sources.git import collect_commits
        return _rows(collect_commits(start, end), "commits")

    if name == "google_calendar":
        from scribejay.sources.calendar import get_events_in_range
        return _rows(get_events_in_range(start.isoformat(), end.isoformat()), "events")

    if name == "gmail":
        from scribejay.sources.gmail_sent import fetch_sent_metadata
        return _rows(fetch_sent_metadata(start, end), "messages")

    if name == "youtube":
        from scribejay.sources.youtube import fetch_liked_videos
        return _rows(fetch_liked_videos(str(day), str(day)), "videos")

    if name == "strava":
        from scribejay.sources.strava import fetch_strava
        return _rows(fetch_strava(str(day)), "activities")

    if name == "clickup":
        from scribejay.sources.clickup import closed_tasks
        return _rows(closed_tasks(day), "items")

    if name == "notify":
        from scribejay.core.notify import notify
        result = notify(message="ScribeJay settings test", title="ScribeJay")
        return f"error: {result['error']}" if "error" in result else "push sent"

    return f"no test for {name}"


# ---- rendering ----------------------------------------------------------------

def _field(setting: schema.Setting) -> str:
    key = html.escape(setting.key)
    label = html.escape(setting.label)
    help_text = html.escape(setting.help)

    if setting.secret:
        state = secret_state(setting.key)
        control = (f'<input type="password" name="{key}" autocomplete="off" '
                   f'placeholder="{state} — leave blank to keep it">')
        note = f'<span class="state {state.replace(" ", "-")}">{state}</span>'
    else:
        value = html.escape(current_value(setting))
        note = ""
        if setting.type == "choice":
            options = "".join(
                f'<option value="{html.escape(c)}"'
                f'{" selected" if c == value else ""}>{html.escape(c)}</option>'
                for c in setting.choices)
            control = (f'<select name="{key}"><option value="">(unset)</option>'
                       f'{options}</select>')
        elif setting.type == "bool":
            options = "".join(
                f'<option value="{v}"{" selected" if v == value else ""}>{t}</option>'
                for v, t in (("", "decide from what is set up"), ("1", "yes"), ("0", "no")))
            control = f'<select name="{key}">{options}</select>'
        elif setting.type in ("int", "float"):
            step = "1" if setting.type == "int" else "any"
            control = f'<input type="number" step="{step}" name="{key}" value="{value}">'
        else:
            control = f'<input type="text" name="{key}" value="{value}" autocomplete="off">'

    return (f'<div class="row"><label for="{key}">{label} {note}</label>'
            f'{control}<p class="help">{help_text}</p>'
            f'<code class="key">{key}</code></div>')


def _group_html(group: Group) -> str:
    states = ""
    for feature in group.features:
        on, why = features.state(feature.name)
        detail = html.escape(why) if why else "on"
        states += (f'<li><span class="state {"set" if on else "not-set"}">'
                   f'{"on" if on else "off"}</span> {html.escape(feature.label)} '
                   f'— {detail} '
                   f'<button type="button" class="test" data-feature="'
                   f'{html.escape(feature.name)}">Test</button>'
                   f'<span class="result" id="r-{html.escape(feature.name)}"></span>'
                   f'</li>')

    fields = "".join(_field(s) for s in group.settings + group.toggles)
    feature_list = f'<ul class="features">{states}</ul>' if states else ""
    # Every panel is rendered, always. The inactive ones are hidden in CSS
    # rather than left out, because this is one form and one POST: a field the
    # server did not send is a field the browser cannot send back, and the save
    # would quietly drop every setting the user was not looking at.
    return (f'<section id="p-{group.slug}" style="--accent:{group.accent}">'
            f'<h2>{html.escape(group.label)}</h2>'
            f'{feature_list}{fields}</section>')


def load_logo() -> str:
    """ScribeJay's mark, inlined into the page.

    Read from the packaged file rather than pasted into this module so there is
    one copy of the artwork, and inlined rather than linked because the server
    has no static route and may be running with no network at all. Missing art
    is a cosmetic problem, not a reason to fail to render a settings screen, so
    a missing file is an empty string — the same bargain `load_persona()`
    makes in `core/model.py`.
    """
    try:
        path = Path(__file__).resolve().parent.parent / "assets" / "scribejay.svg"
        return path.read_text().strip()
    except OSError:
        return ""


LOGO = load_logo()


def _tab_css(group: Group) -> str:
    """The three rules that make one radio reveal one panel.

    Generated per group rather than written out, for the same reason the fields
    are: a group that appears in `groups()` and not here would render a tab that
    does nothing.
    """
    tab = f"#t-{group.slug}"
    return (f'{tab}:checked ~ .panels #p-{group.slug} {{ display: block; }}'
            f'{tab}:checked ~ .rail label[for="t-{group.slug}"] '
            f'{{ border-left-color: var(--accent); border-bottom-color: var(--accent); '
            f'font-weight: 600; '
            f'background: color-mix(in srgb, var(--accent) 10%, transparent); }}'
            f'{tab}:focus-visible ~ .rail label[for="t-{group.slug}"] '
            f'{{ outline: 2px solid var(--accent); outline-offset: -2px; }}')


def render(token: str, messages: list[str] = (), errors: list[str] = ()) -> str:
    """The whole page. One form, one POST, no framework and no CDN — this
    server is up for a couple of minutes on a loopback port and must work with
    no network at all."""
    banner = ""
    for text in errors:
        banner += f'<div class="banner error">{html.escape(text)}</div>'
    for text in messages:
        banner += f'<div class="banner ok">{html.escape(text)}</div>'

    shown = groups()
    flagged = {g.slug for g in shown if g.owns_error(errors)}
    # Open on the tab that failed, not on the first one. A banner naming a
    # field three tabs away is a banner the user cannot act on.
    active = next((g.slug for g in shown if g.slug in flagged),
                  shown[0].slug if shown else "")

    radios = "".join(
        f'<input type="radio" name="tab" class="tabradio" id="t-{g.slug}"'
        f'{" checked" if g.slug == active else ""}>' for g in shown)
    rail = ""
    for g in shown:
        mark = '<span class="flag">!</span>' if g.slug in flagged else ""
        rail += (f'<label for="t-{g.slug}" style="--accent:{g.accent}">'
                 f'<span class="dot"></span>{html.escape(g.label)}{mark}</label>')
    panels = "".join(_group_html(g) for g in shown)
    body = (f'<div class="tabs">{radios}'
            f'<nav class="rail" aria-label="Settings sections">{rail}</nav>'
            f'<div class="panels">{panels}</div></div>')
    tab_css = "".join(_tab_css(g) for g in shown)
    token_attr = html.escape(token)
    token_js = json.dumps(token)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ScribeJay settings</title>
<style>{_CSS}{tab_css}</style></head>
<body>
<header>
<div class="brand">{LOGO}<h1>ScribeJay settings</h1></div>
<p>Stored in <code>{html.escape(str(config.config_path()))}</code>. Credentials
go to the macOS Keychain, never to that file.</p></header>
{banner}
<form method="post" action="/save">
<input type="hidden" name="csrf" value="{token_attr}">
{body}
<div class="actions">
  <button type="submit">Save</button>
  <button type="submit" formaction="/done" class="secondary">Save and close</button>
</div>
</form>
<script>
const CSRF = {token_js};
document.querySelectorAll('button.test').forEach(function (b) {{
  b.addEventListener('click', async function () {{
    const out = document.getElementById('r-' + b.dataset.feature);
    out.textContent = 'testing…';
    const body = new URLSearchParams({{csrf: CSRF, feature: b.dataset.feature}});
    const res = await fetch('/test', {{method: 'POST', body: body}});
    out.textContent = await res.text();
  }});
}});
</script>
</body></html>
"""


_CSS = """
:root { color-scheme: light dark; --line: #8883; }
body { font: 15px/1.5 -apple-system, system-ui, sans-serif; max-width: 62rem;
       margin: 2rem auto; padding: 0 1rem 1rem; background: Canvas;
       color: CanvasText; }
h1 { font-size: 1.4rem; margin: 0; }
/* The mark is inlined SVG, so its own width/height attributes are overridden
   here rather than edited into the artwork. */
.brand { display: flex; align-items: center; gap: .6rem; margin: 0 0 .3rem; }
.brand svg { width: 2.1rem; height: 2.1rem; border-radius: 6px; flex: none; }
h2 { font-size: 1.05rem; margin: 0 0 .8rem; padding-bottom: .3rem;
     border-bottom: 2px solid var(--accent, var(--line)); }
header p { opacity: .75; margin: 0 0 1rem; }
/* The tab rail. No JavaScript: one radio per group, every radio ahead of both
   the rail and the panels so a sibling selector can reach them. Switching
   sections is something the page must still do with scripting off — and real
   radios bring arrow-key navigation and a focus ring along for free. */
.tabs { display: grid; grid-template-columns: 13rem minmax(0, 1fr);
        gap: 1.5rem; align-items: start; margin-top: 1rem; }
.tabradio { position: absolute; width: 1px; height: 1px; opacity: 0;
            pointer-events: none; }
.rail { grid-column: 1; display: flex; flex-direction: column; gap: 1px; }
.rail label { display: flex; align-items: center; gap: .5rem; font-weight: 400;
              margin: 0; padding: .35rem .5rem; cursor: pointer;
              border-left: 3px solid transparent; font-size: .92rem; }
.rail label:hover { background: #8881; }
.rail .dot { width: .5rem; height: .5rem; border-radius: 50%; flex: none;
             background: var(--accent); }
.rail .flag { margin-left: auto; color: #cf222e; font-weight: 600; }
.panels { grid-column: 2; }
.panels > section { display: none; }
@media (max-width: 46rem) {
  .tabs { grid-template-columns: 1fr; gap: .75rem; }
  .rail { grid-column: 1; flex-direction: row; flex-wrap: wrap; }
  .panels { grid-column: 1; }
  .rail label { border-left: 0; border-bottom: 3px solid transparent; }
}
.row { margin: 0 0 1.1rem; }
label { display: block; font-weight: 600; margin-bottom: .2rem; }
input, select { width: 100%; padding: .4rem; font: inherit;
                border: 1px solid var(--line); border-radius: 5px;
                background: transparent; color: inherit; }
.help { margin: .25rem 0 0; font-size: .85rem; opacity: .75; }
.key { font-size: .75rem; opacity: .5; }
.features { list-style: none; padding: 0; margin: 0 0 1rem; }
.features li { padding: .25rem 0; }
.state { font-size: .75rem; padding: .05rem .4rem; border-radius: 4px;
         font-weight: 600; }
.state.set { background: #1a7f3722; color: #1a7f37; }
.state.not-set { background: #88888822; }
.banner { padding: .6rem .8rem; border-radius: 6px; margin: .5rem 0; }
.banner.ok { background: #1a7f3722; }
.banner.error { background: #cf222e22; }
/* Sticky, so Save is reachable from anywhere on a long form — and opaque,
   because a translucent bar over the next section's heading reads as a
   rendering fault. */
.actions { position: sticky; bottom: 0; padding: 1rem 0;
           background: Canvas; border-top: 1px solid var(--line);
           display: flex; gap: .5rem; }
button { width: auto; padding: .45rem 1rem; cursor: pointer; border-radius: 5px;
         border: 1px solid var(--line); background: transparent; color: inherit;
         font: inherit; }
button.test { padding: .1rem .5rem; font-size: .75rem; }
.result { font-size: .8rem; opacity: .8; margin-left: .4rem; }
"""
