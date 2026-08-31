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
                 slug: str, structured: str = ""):
        self.name = name
        self.label = label
        # `name` is not unique — core yields three groups — so the tab rail,
        # the panel it reveals, and the accent all key off `slug` instead.
        self.slug = slug
        self.accent = ACCENTS.get(slug, DEFAULT_ACCENT)
        self.settings = [s for s in rows if not s.key.startswith("SCRIBEJAY_FEATURE_")]
        self.toggles = [s for s in rows if s.key.startswith("SCRIBEJAY_FEATURE_")]
        # Which structured settings section this panel edits, if any: "" for the
        # schema-driven groups, "calendar" for the colour table, "learnings" for
        # the exclusion lists. A structured section is a list, not a value, so
        # it has no schema rows and gets its own renderer.
        self.structured = structured
        # `(index, entry)` pairs from the calendar section. The index is the
        # position in the *stored* list, not in this one, so a malformed entry
        # that is not shown is still not overwritten on save.
        self.categories = calendar_rows() if structured == "calendar" else []
        # A structured panel borrows a feature's name so `groups()` still covers
        # exactly `schema.FEATURES`, but not its feature list: the Test buttons
        # belong on the tab that configures the source, and only there.
        self.features = ([] if structured else
                         [f for f in features.FEATURES if f.settings_group == name])

    def owns_error(self, errors) -> bool:
        """Does one of this group's fields explain one of these errors?

        Matched on the label rather than the key because that is what the
        message carries — `validate()` writes "{label}: ..." and the Keychain
        failure writes "Could not store {label} ...". A tab the user cannot see
        is exactly where a rejected value would otherwise hide.
        """
        labels = ([s.label for s in self.settings + self.toggles]
                  + [c["name"] for _, c in self.categories]
                  + ([label for _, label, _ in EXCLUSION_LISTS]
                     if self.structured == "learnings" else []))
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
    "exclusions": "#5F5E5A",
    "model": "#534AB7",
    "google": "#378ADD",
    "colors": "#8E24AA",
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


CATEGORY_FIELD = "cal_color_"


def calendar_rows() -> list[tuple[int, dict]]:
    """The categories worth showing, paired with their position in the stored
    list.

    Filtered the same way `config.calendar_categories()` filters — an entry
    without a name or a colorId is not something this form can edit — but the
    index is into the raw list, so saving cannot renumber or drop the entries
    that were skipped.
    """
    raw = config.section("calendar").get("categories", [])
    if not isinstance(raw, list):
        return []
    return [(i, c) for i, c in enumerate(raw)
            if isinstance(c, dict) and c.get("name") and c.get("color_id")]


def apply_categories(fields: dict[str, str]) -> tuple[dict | None, list[str]]:
    """(calendar section to stage, errors). None means nothing was submitted.

    Only the colour moves. `name`, `hint` and `role` are the classification
    prompt and the operational lookups — editing those from a dropdown is a
    different job, and one wrong `role` silently changes which colour Strava
    activities are logged with.

    `color_name` is written from the id rather than accepted from the form, so
    the two fields cannot disagree. They are what the colorizer shows the model
    and what Google actually paints; a category labelled Grape and painted
    Peacock trains the model on a lie.
    """
    import copy

    rows = calendar_rows()
    submitted = {i: fields[f"{CATEGORY_FIELD}{i}"] for i, _ in rows
                 if f"{CATEGORY_FIELD}{i}" in fields}
    if not submitted:
        return None, []

    errors = []
    for index, entry in rows:
        color_id = submitted.get(index, "").strip()
        if color_id and color_id not in schema.COLOR_NAMES:
            errors.append(f"{entry['name']}: '{color_id}' is not one of Google's "
                          f"eleven event colours.")
    if errors:
        return None, errors

    section = copy.deepcopy(config.section("calendar"))
    for index, _ in rows:
        color_id = submitted.get(index, "").strip()
        if not color_id:
            continue
        section["categories"][index]["color_id"] = color_id
        section["categories"][index]["color_name"] = schema.COLOR_NAMES[color_id]
    return section, []


# (config key, label, help). The two lists the daily reviews read, in
# `scribejay/activity.py`. Named here for presentation only — a third exclusion
# list would be a row in this tuple and nothing else.
EXCLUSION_LISTS = (
    ("excluded_keywords", "Excluded keywords",
     "Subject matter kept out of the reviews entirely, whatever it is hosted "
     "on. One per line. Matched as a case-insensitive substring against a "
     "page's title and its path — blunt on purpose, so pick distinctive "
     "terms; a short or common one will over-match."),
    ("excluded_domains", "Excluded domains",
     "Sites kept out of the reviews. One per line, domain only — no https:// "
     "and no path. A domain covers its subdomains, so sharepoint.com covers "
     "acme.sharepoint.com, but it is not a substring match: notsharepoint.com "
     "is still reviewed."),
)

EXCLUSION_FIELD = "learnings_"


def exclusion_lines(name: str) -> str:
    """The stored list as one entry per line. Non-strings are dropped the same
    way `activity.py` drops them, so what is shown is what is actually read."""
    stored = config.section("learnings").get(name, [])
    if not isinstance(stored, list):
        return ""
    return "\n".join(v for v in stored if isinstance(v, str) and v.strip())


def apply_exclusions(fields: dict[str, str]) -> tuple[dict | None, list[str]]:
    """(learnings section to stage, errors). None means nothing was submitted.

    A textarea rather than a row per entry: these are short strings with no
    fields of their own, and one per line gets adding, removing and reordering
    for free. Blank lines are dropped rather than rejected — a trailing newline
    is not a mistake worth an error message.
    """
    import copy

    submitted = {name: fields[f"{EXCLUSION_FIELD}{name}"]
                 for name, _, _ in EXCLUSION_LISTS
                 if f"{EXCLUSION_FIELD}{name}" in fields}
    if not submitted:
        return None, []

    errors = []
    cleaned = {}
    for name, label, _ in EXCLUSION_LISTS:
        if name not in submitted:
            continue
        values = [line.strip() for line in submitted[name].splitlines()]
        values = [v for v in values if v]
        if name == "excluded_domains":
            # Lowercased on the way in because `activity.py` lowercases before
            # matching anyway, and a stored "SharePoint.com" that silently
            # behaves as "sharepoint.com" is a difference nobody can see.
            values = [v.lower() for v in values]
            for v in values:
                if any(c in v for c in "/: @") or v.startswith("."):
                    errors.append(f"{label}: '{v}' is not a bare domain. Enter "
                                  f"just the host, e.g. sharepoint.com.")
        cleaned[name] = values

    if errors:
        return None, errors

    section = copy.deepcopy(config.section("learnings"))
    section.update(cleaned)
    return section, []


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
                if section == "output":
                    # Beside the journal folder, because these decide what
                    # never reaches it.
                    result.append(Group(name, "What to leave out", [],
                                        "exclusions", structured="learnings"))
            continue
        group_features = [f for f in features.FEATURES if f.settings_group == name]
        result.append(Group(name, _label_for(name, group_features), rows, name))
        if name == "google":
            # Beside the calendar it recolours, and only if there is a table to
            # show — a config with no categories gets no empty tab.
            if calendar_rows():
                result.append(Group("google", "Event colours", [], "colors",
                                    structured="calendar"))
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

    calendar, calendar_errors = apply_categories(fields)
    errors += calendar_errors

    learnings, learnings_errors = apply_exclusions(fields)
    errors += learnings_errors

    if errors:
        return [], errors

    for setting, value in staged:
        config.set_value(setting.key, value)
    if calendar is not None:
        config.set_preference("calendar", calendar)
    if learnings is not None:
        config.set_preference("learnings", learnings)
    config.flush()

    saved = [f"Saved {len(staged)} setting(s) to {config.config_path()}"]
    if calendar is not None:
        saved.append(f"Saved {len(calendar.get('categories', []))} event colour(s)")
    if learnings is not None:
        total = sum(len(learnings.get(n, [])) for n, _, _ in EXCLUSION_LISTS)
        saved.append(f"Saved {total} exclusion(s)")

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


def _category_row(index: int, entry: dict) -> str:
    key = f"{CATEGORY_FIELD}{index}"
    current = str(entry.get("color_id", ""))
    hint = entry.get("hint", "")
    role = entry.get("role", "")

    options = "".join(
        f'<option value="{cid}"{" selected" if cid == current else ""}>{name}</option>'
        for cid, name, _ in schema.GOOGLE_EVENT_COLORS)
    swatch = schema.COLOR_HEX.get(current, "#8888")
    note = html.escape(hint) if hint else ""
    if role:
        note += f' <code class="role">role: {html.escape(role)}</code>'

    return (f'<div class="cat"><span class="swatch" style="background:{swatch}"></span>'
            f'<div class="catname"><label for="{key}">{html.escape(entry["name"])}</label>'
            f'{f"<p class=\"help\">{note}</p>" if note else ""}</div>'
            f'<select name="{key}" id="{key}">{options}</select></div>')


def _categories_html(group: Group) -> str:
    rows = "".join(_category_row(i, c) for i, c in group.categories)
    return (f'<p class="help">The colour each kind of event is painted on your '
            f'calendar. Names and classification hints are not editable here — '
            f'they are the prompt the colorizer shows the model, and they live '
            f'in the settings file.</p>{rows}')


def _exclusions_html() -> str:
    blocks = ""
    for name, label, help_text in EXCLUSION_LISTS:
        key = f"{EXCLUSION_FIELD}{name}"
        value = html.escape(exclusion_lines(name))
        count = len([v for v in value.splitlines() if v])
        blocks += (f'<div class="row"><label for="{key}">{label} '
                   f'<span class="state not-set">{count}</span></label>'
                   f'<textarea name="{key}" id="{key}" rows="7" spellcheck="false"'
                   f' autocomplete="off">{value}</textarea>'
                   f'<p class="help">{html.escape(help_text)}</p>'
                   f'<code class="key">learnings.{name}</code></div>')
    return (f'<p class="help">What the daily reviews skip, so it never reaches '
            f'the journal folder. One entry per line; blank lines are ignored. '
            f'Takes effect on the next scheduled run.</p>{blocks}')


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
    if group.structured == "calendar":
        fields = _categories_html(group)
    elif group.structured == "learnings":
        fields = _exclusions_html()
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
input, select, textarea { width: 100%; padding: .4rem; font: inherit;
                border: 1px solid var(--line); border-radius: 5px;
                background: transparent; color: inherit; }
/* One entry per line, so a monospace face makes a stray space visible. */
textarea { font: .9rem/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
           resize: vertical; }
.help { margin: .25rem 0 0; font-size: .85rem; opacity: .75; }
.key { font-size: .75rem; opacity: .5; }
.cat { display: grid; grid-template-columns: 1rem minmax(0, 1fr) 10rem;
       gap: .6rem; align-items: center; margin: 0 0 .7rem; }
.cat .catname label { margin: 0; }
.cat .help { margin: 0; }
.cat select { width: 100%; }
.swatch { width: 1rem; height: 1rem; border-radius: 3px;
          border: 1px solid var(--line); }
.role { font-size: .7rem; opacity: .6; }
@media (max-width: 34rem) {
  .cat { grid-template-columns: 1rem minmax(0, 1fr); }
  .cat select { grid-column: 2; }
}
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
