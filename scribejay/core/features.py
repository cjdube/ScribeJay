"""What a user has switched on, and whether it could work if they did.

A **feature** is one source of material a user can decline. Declining it must
be free: the task that needs it stops running, and — the point of the whole
exercise — stops pushing a failure alert every morning. Before this module,
a user with no Strava account still got `local.scribejay.stravadownload`
installed and an alert at 5:50 every day for the rest of time.

Two questions, deliberately separate:

- `configured(name)` — could this work? Are the credentials present, does the
  folder exist? A fact about the machine, read fresh each call.
- `enabled(name)` — should this run? The user's answer, if they gave one.

`enabled()` falls back to `configured()` when the user has not answered, and
that fallback is what lets one default serve two very different people. A
stranger who just installed ScribeJay has no Strava keys, so Strava is off and
silent. Someone who has been running it for a year has keys in the Keychain, so
Strava stays on and nothing about their morning changes. Neither had to answer
a question, and either can override the guess from the settings screen.

The toggles resolve through `config.getenv` like every other setting, so the
environment still wins — `SCRIBEJAY_FEATURE_STRAVA=0` turns it off for one run
without touching the settings file.

`scribejay/core/registry.py` maps tasks to the features they need; this module
only answers about a feature.
"""

from dataclasses import dataclass

from scribejay.core import config

# Credential tiers, in the order the wizard should walk them. Tier 0 needs no
# account at all and is what ships on: a real journal — browsing, AI sessions,
# commits — from a machine and nothing else.
TIER_NONE = 0        # a permission at most (Full Disk Access)
TIER_GOOGLE = 1      # the user creates their own Google Cloud OAuth client
TIER_TOKEN = 2       # the user pastes one token


@dataclass(frozen=True)
class Feature:
    """One thing a user can decline.

    `settings_group` is the `feature` field on the schema rows that configure
    this — several features can share one group, because three Google
    capabilities are configured by one OAuth client but declined separately.
    Someone may well want the YouTube journal without ScribeJay recolouring
    their work calendar.

    `setup` is what the user must do to make `configured()` true. It is shown
    verbatim in the skip line a disabled task logs, so a quiet task always says
    why it was quiet.
    """

    name: str
    label: str
    settings_group: str
    tier: int
    setup: str


FEATURES: tuple[Feature, ...] = (
    Feature("chrome", "Chrome browsing history", "chrome", TIER_NONE,
            "Chrome is not installed, or its history database is missing"),
    Feature("transcripts", "AI chat sessions", "transcripts", TIER_NONE,
            "no Claude Code, Codex, or Gemini transcripts found on this Mac"),
    Feature("git", "Commits", "git", TIER_NONE,
            "the projects folder does not exist — set it in settings"),
    Feature("google_calendar", "Google Calendar", "google", TIER_GOOGLE,
            "Google is not set up — see docs/setup-google.md"),
    Feature("gmail", "Gmail sent mail", "google", TIER_GOOGLE,
            "Google is not set up — see docs/setup-google.md"),
    Feature("youtube", "YouTube Likes", "google", TIER_GOOGLE,
            "Google is not set up — see docs/setup-google.md"),
    Feature("strava", "Strava activities", "strava", TIER_TOKEN,
            "no Strava credentials — add them in settings"),
    Feature("clickup", "ClickUp closed tasks", "clickup", TIER_TOKEN,
            "no ClickUp token — add one in settings"),
    Feature("notify", "Failure push alerts", "notify", TIER_NONE,
            "no ntfy topic URL set"),
)

BY_NAME: dict[str, Feature] = {f.name: f for f in FEATURES}

NAMES: tuple[str, ...] = tuple(f.name for f in FEATURES)


def setting_key(name: str) -> str:
    """The env/settings key that carries a feature's on/off answer."""
    return f"SCRIBEJAY_FEATURE_{name.upper()}"


TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}


def _as_bool(raw: str) -> bool | None:
    """None for anything unrecognised, so a typo reads as 'no answer' and falls
    through to the configured() guess rather than silently meaning False."""
    value = raw.strip().lower()
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    return None


def _credentials_present(*keys: str) -> bool:
    # Imported here, not at module scope: core/http.py pulls requests, and this
    # module is imported by every task at startup.
    from scribejay.core.http import resolve_key

    return all(resolve_key(k) for k in keys)


def _path_setting_exists(key: str) -> bool:
    """config.resolve_path, not Path(value) — a relative setting means "beside
    the checkout, or under ~/.scribejay", never "relative to whatever directory
    launchd happened to start the job in". Probing it any other way would let
    the probe and the code that reads the file disagree about which file."""
    value = config.getenv(key)
    return bool(value) and config.resolve_path(value).exists()


def configured(name: str) -> bool:
    """Could this feature work right now, on this machine?

    Read fresh every call rather than cached: a user who pastes a token into
    the settings screen expects the next run to pick it up, and a cached
    "no" would keep the task silent until the process restarted.
    """
    if name == "chrome":
        # Reading it is Full Disk Access, a permission — but the file existing
        # is what says Chrome is installed at all, and that is the question
        # here. `doctor` covers the permission.
        from scribejay.sources.chrome import HISTORY_PATH

        return HISTORY_PATH.exists()

    if name == "transcripts":
        from scribejay.sources.transcripts import (
            claude_projects_dir, codex_sessions_dir, gemini_dir)

        return any(d.exists() for d in
                   (claude_projects_dir(), codex_sessions_dir(), gemini_dir()))

    if name == "git":
        return _path_setting_exists("PROJECTS_DIR")

    if name in ("google_calendar", "gmail", "youtube"):
        # The OAuth *client* file, not the token: the token only appears after
        # the first browser consent, and gating on it would mean a correctly
        # configured install could never reach the run that creates it.
        return _path_setting_exists("GOOGLE_CREDENTIALS_PATH")

    if name == "strava":
        return _credentials_present(
            "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN")

    if name == "clickup":
        return _credentials_present("CLICKUP_API_TOKEN")

    if name == "notify":
        return bool(config.getenv("NTFY_URL"))

    raise KeyError(f"unknown feature: {name}")


def state(name: str) -> tuple[bool, str]:
    """(enabled, reason). The reason is only meaningful when enabled is False,
    and is written to be read in a log by someone asking why nothing happened."""
    feature = BY_NAME.get(name)
    if feature is None:
        raise KeyError(f"unknown feature: {name}")

    raw = config.getenv(setting_key(name))
    answer = _as_bool(raw) if raw else None

    if answer is False:
        return False, f"{feature.label} is turned off in settings"
    if answer is True:
        # An explicit yes is honoured even when the probe disagrees. The user
        # may know something the probe does not, and a task that then fails
        # gets a real error naming the real cause — better than this module
        # overruling them and reporting a tidy skip.
        return True, ""
    if configured(name):
        return True, ""
    return False, f"{feature.label} is not set up: {feature.setup}"


def enabled(name: str) -> bool:
    return state(name)[0]


def by_tier(tier: int) -> list[Feature]:
    return [f for f in FEATURES if f.tier == tier]
