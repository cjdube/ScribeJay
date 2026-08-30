"""Every ScribeJay setting, described once.

This table is the single source of truth for four consumers that would
otherwise drift apart:

1. `scribejay/core/config.py` — resolves a value and supplies the default.
2. `scribejay/migrate.py` — knows which keys are secrets and must go to the
   Keychain rather than into `~/.scribejay/config.json`.
3. The setup wizard (Phase 6) — asks the questions in `feature` order.
4. The web settings screen (Phase 5) — renders a form from `label`, `help`,
   `type` and `choices`, and shows secrets as write-only fields.

Adding a setting means adding one row here. `tests/test_schema.py` asserts the
reverse direction too: every literal key `config.getenv()` reads in the source
tree has a row, so a new call site cannot quietly acquire an undocumented
setting with an inline default.

**Secrets never land in the settings file.** A row with `secret=True` is resolved
through `scribejay/core/secrets.py` (the macOS Keychain) by
`scribejay/core/http.py:resolve_key`, and the settings file holds no copy —
which is what makes `~/.scribejay/config.json` safe to back up or copy between
machines.

`default` is stored as the string an environment variable would carry, not as
a typed value, because the environment layer is a string layer and one
representation is easier to reason about than two. Callers coerce at the point
of use, exactly as they did when they called `os.getenv` directly.
"""

from dataclasses import dataclass
from pathlib import Path

# Feature keys. A feature is a capability a user can switch off; Phase 3's
# task registry maps each task to the features it needs. Listed here because
# the schema is what the wizard and the settings screen group by.
FEATURES = (
    "core",         # always on: timezone, logs, model routing, output location
    "google",       # Google OAuth: calendar, gmail, youtube
    "chrome",
    "transcripts",  # Claude Code / Codex / Gemini chat sessions
    "git",
    "strava",
    "clickup",
    "notify",       # ntfy push alerts
)


@dataclass(frozen=True)
class Setting:
    """One configurable value.

    `key` is the environment-variable name and the identifier every consumer
    uses. `section` and `name` are where it lives in `~/.scribejay/config.json`
    — spelled out rather than derived from `key`, because the mechanical
    derivations all produce at least one ugly or ambiguous name
    (`SCRIBEJAY_LLM_BACKEND` would become `scribejay_llm_backend` under a
    lowercase rule, inside a `[model]` table that already says so).
    """

    key: str
    section: str
    name: str
    label: str
    help: str
    type: str = "str"          # str | int | float | bool | path | choice
    default: str | None = None
    feature: str = "core"
    secret: bool = False
    choices: tuple[str, ...] = ()


# Defaults that are computed from the running machine rather than written out,
# so the schema and the module that used to own the constant cannot disagree.
_HOME = Path.home()
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


SETTINGS: tuple[Setting, ...] = (
    # ---- core ---------------------------------------------------------------
    Setting(
        key="TIMEZONE", section="core", name="timezone",
        label="Timezone",
        help="IANA timezone name, e.g. America/New_York. Leave empty to read "
             "the Mac's own timezone. Sources report UTC; day windows are local.",
        default=None,
    ),
    Setting(
        key="SCRIBEJAY_LOGS_DIR", section="core", name="logs_dir",
        label="Log directory", type="path",
        help="Where each task writes its run log. One file per task.",
        default=str(_REPO_ROOT / "logs"),
    ),
    Setting(
        key="LEARNINGS_DIR", section="output", name="learnings_dir", type="path",
        label="Journal folder",
        help="Where the daily pages are written — a plain folder or an "
             "Obsidian vault. ScribeJay will not create it: a missing folder "
             "means the path is wrong, and writing pages somewhere nobody "
             "reads is worse than failing.",
        default=str(_HOME / "Documents" / "ScribeJay"),
    ),
    Setting(
        key="CORRESPONDENCE_DIR", section="output", name="correspondence_dir", type="path",
        label="Correspondence folder",
        help="Where the daily sent-mail page is written. Kept out of the "
             "journal folder on purpose so it is not swept into an ingest queue.",
        default=str(_HOME / "Documents" / "ScribeJay" / "correspondence"),
    ),
    Setting(
        key="BRIEF_TO_EMAIL", section="output", name="fallback_email",
        label="Fallback email address",
        help="If writing to the journal folder fails, the draft is emailed "
             "here instead so a day's work is never lost. Needs Google set up.",
        default=None,
    ),

    # ---- model routing ------------------------------------------------------
    # No default on purpose, unlike every other row here. core/model.py treats
    # unset as "no opinion" and logs `from unset` — which is how a run says out
    # loud that nobody chose a backend, the failure mode docs/llm-backend.md
    # exists to make visible. A schema default of "ollama" would resolve to the
    # same model while reporting that someone had picked it. The effective
    # fallback lives in model._resolve_backend, and stays there.
    Setting(
        key="SCRIBEJAY_LLM_BACKEND", section="model", name="backend", type="choice",
        label="Model backend", choices=("ollama", "gemini", "openrouter"),
        help="Which model writes the summaries. Unset means local Ollama: free, "
             "and nothing leaves the machine. Both cloud backends are billed "
             "per token by the provider — 'gemini' sends that task's gathered "
             "input to Google, 'openrouter' sends it to OpenRouter and on to "
             "whichever model you pick there. Overridable per task under "
             "[model.per_task].",
        default=None,
    ),
    Setting(
        key="OLLAMA_HOST", section="model", name="ollama_host",
        label="Ollama host", help="Base URL of the local Ollama server.",
        default="http://localhost:11434",
    ),
    Setting(
        key="OLLAMA_MODEL", section="model", name="ollama_model",
        label="Ollama model", help="Model tag to run, e.g. gemma4.",
        default="gemma4",
    ),
    Setting(
        key="OLLAMA_NUM_CTX", section="model", name="ollama_num_ctx", type="int",
        label="Context window (tokens)",
        help="How much input the model accepts in one call.",
        default="8192",
    ),
    Setting(
        key="OLLAMA_NUM_PREDICT", section="model", name="ollama_num_predict", type="int",
        label="Output budget (tokens)",
        help="Ceiling on one reply. Thinking tokens share this budget, which "
             "is why template-filling calls pass think=False.",
        default="3072",
    ),
    Setting(
        key="OLLAMA_TIMEOUT", section="model", name="ollama_timeout", type="float",
        label="Model timeout (seconds)", help="Per-call timeout for a completion.",
        default="300",
    ),
    Setting(
        key="OLLAMA_WARM_TIMEOUT", section="model", name="ollama_warm_timeout", type="float",
        label="Warm-up timeout (seconds)",
        help="Longer than the call timeout because a cold model has to be "
             "read off disk first.",
        default="600",
    ),
    Setting(
        key="OLLAMA_KEEP_ALIVE", section="model", name="ollama_keep_alive",
        label="Keep model loaded for",
        help="How long Ollama holds the model in memory after a call, e.g. 30m.",
        default="30m",
    ),
    Setting(
        key="SCRIBEJAY_GEMINI_MODEL", section="model", name="gemini_model",
        label="Gemini model", help="Used only when the backend is 'gemini'.",
        default="gemini-2.5-flash",
    ),
    Setting(
        key="SCRIBEJAY_GEMINI_MAX_OUTPUT_TOKENS", section="model",
        name="gemini_max_output_tokens", type="int",
        label="Gemini output budget (tokens)", help="Ceiling on one Gemini reply.",
        default="8192",
    ),
    Setting(
        key="SCRIBEJAY_GEMINI_THINKING_BUDGET", section="model",
        name="gemini_thinking_budget", type="int",
        label="Gemini thinking budget (tokens)",
        help="Thinking shares the output budget, so a large value here can "
             "empty a template-filling reply. 0 disables thinking; 128 is the "
             "portable floor for a model that refuses to turn it off.",
        default="0",
    ),
    Setting(
        key="GEMINI_API_KEY", section="model", name="gemini_api_key", secret=True,
        label="Gemini API key",
        help="Only needed if you pick the 'gemini' backend. From "
             "https://aistudio.google.com/apikey.",
    ),
    Setting(
        key="OPENROUTER_MODEL", section="model", name="openrouter_model",
        label="OpenRouter model",
        help="Used only when the backend is 'openrouter'. A slug from "
             "https://openrouter.ai/models, e.g. anthropic/claude-sonnet-5 "
             "(the default, and a paid frontier model), or an @preset/ slug. "
             "The models page prices every slug; a ':free' one costs nothing.",
        default="anthropic/claude-sonnet-5",
    ),
    Setting(
        key="OPENROUTER_MAX_OUTPUT_TOKENS", section="model",
        name="openrouter_max_output_tokens", type="int",
        label="OpenRouter output budget (tokens)",
        help="Ceiling on one OpenRouter reply. On a reasoning model the "
             "reasoning is billed and can be drawn from this budget, which is "
             "why template-filling calls pass think=False.",
        default="8192",
    ),
    Setting(
        key="OPENROUTER_API_KEY", section="model", name="openrouter_api_key",
        secret=True, label="OpenRouter API key",
        help="Only needed if you pick the 'openrouter' backend. From "
             "https://openrouter.ai/keys. Every run on this backend spends "
             "credit on that key.",
    ),

    # ---- google -------------------------------------------------------------
    Setting(
        key="GOOGLE_CREDENTIALS_PATH", section="google", name="credentials_path",
        type="path", feature="google",
        label="OAuth client file",
        help="The client-secret JSON downloaded from Google Cloud Console. "
             "Relative paths are resolved against the ScribeJay install.",
        default="config/google_credentials.json",
    ),
    Setting(
        key="GOOGLE_TOKEN_PATH", section="google", name="token_path",
        type="path", feature="google",
        label="OAuth token cache",
        help="Where the token is stored after the one-time browser consent. "
             "Written by Google's own library, so it is not a Keychain item.",
        default="config/google_token.json",
    ),
    Setting(
        key="GOOGLE_CALENDAR_ID", section="google", name="calendar_id", feature="google",
        label="Calendar", help="Which calendar to read and write. 'primary' is yours.",
        default="primary",
    ),
    Setting(
        key="GOOGLE_HTTP_TIMEOUT_S", section="google", name="http_timeout_s",
        type="int", feature="google",
        label="Google API timeout (seconds)",
        help="Without this the Google client has no timeout at all, and a "
             "stalled call could run past the next scheduled start.",
        default="30",
    ),
    Setting(
        key="GOOGLE_OAUTH_PORT", section="google", name="oauth_port",
        type="int", feature="google",
        label="Consent callback port",
        help="Local port the browser redirects to during the one-time "
             "consent. 0 picks any free port, which is right unless you "
             "registered a fixed redirect URI.",
        default="0",
    ),

    # ---- chrome -------------------------------------------------------------
    # Chrome's history database path is not configurable today: chrome.py reads
    # the standard profile location. It needs Full Disk Access, which is a
    # permission, not a setting — the wizard walks it, doctor verifies it.

    # ---- transcripts --------------------------------------------------------
    Setting(
        key="CLAUDE_CONFIG_DIR", section="transcripts", name="claude_dir",
        type="path", feature="transcripts",
        label="Claude Code folder",
        help="Where Claude Code keeps its session transcripts.",
        default=str(_HOME / ".claude"),
    ),
    Setting(
        key="CODEX_HOME", section="transcripts", name="codex_dir",
        type="path", feature="transcripts",
        label="Codex folder",
        help="Where Codex keeps its session transcripts.",
        default=str(_HOME / ".codex"),
    ),
    Setting(
        key="SCRIBEJAY_GEMINI_CHATS_DIR", section="transcripts", name="gemini_chats_dir",
        type="path", feature="transcripts",
        label="Gemini chat drop folder",
        help="Gemini has no local transcript store, so exported chats are "
             "read from a folder you drop them into.",
        default=str(_HOME / "Documents" / "ScribeJay" / "gemini_inbox"),
    ),
    Setting(
        key="AI_CHAT_LEARNINGS_MAX_CHARS", section="transcripts", name="max_chars",
        type="int", feature="transcripts",
        label="Transcript input cap (characters)",
        help="How much conversation text is compacted into one prompt. "
             "Bounds the prompt so a long day cannot overflow the context.",
        default="12000",
    ),
    Setting(
        key="SCRIBEJAY_SESSION_BLOCK_GAP_MINUTES", section="transcripts", name="block_gap_minutes",
        type="int", feature="transcripts",
        label="Session gap (minutes)",
        help="A pause longer than this splits one coding session into two "
             "calendar blocks.",
        default="20",
    ),
    Setting(
        key="SCRIBEJAY_SESSION_BLOCK_MIN_MINUTES", section="transcripts", name="block_min_minutes",
        type="int", feature="transcripts",
        label="Shortest session (minutes)",
        help="Sessions shorter than this are dropped rather than logged.",
        default="10",
    ),
    Setting(
        key="SCRIBEJAY_SESSION_BLOCK_MAX_CHARS", section="transcripts", name="block_max_chars",
        type="int", feature="transcripts",
        label="Session summary input cap (characters)",
        help="How much of a session is shown to the model when it writes the "
             "block's one-line description.",
        default="6000",
    ),

    # ---- git ----------------------------------------------------------------
    Setting(
        key="PROJECTS_DIR", section="git", name="projects_dir",
        type="path", feature="git",
        label="Projects folder",
        help="Every git checkout directly under this folder is scanned for "
             "yesterday's commits.",
        default=str(_HOME / "Projects"),
    ),
    Setting(
        key="SCRIBEJAY_GIT_AUTHOR", section="git", name="author", feature="git",
        label="Your git author name",
        help="Only commits by this author are counted, so a shared repo does "
             "not fill your journal with someone else's work. Leave empty to "
             "use the machine's git config.",
        default=None,
    ),

    # ---- strava -------------------------------------------------------------
    # All three are secrets: they are one credential set, they are entered
    # together, and the client id is only meaningful next to the secret.
    Setting(
        key="STRAVA_CLIENT_ID", section="strava", name="client_id",
        secret=True, feature="strava",
        label="Strava client ID",
        help="From https://www.strava.com/settings/api, after creating an app.",
    ),
    Setting(
        key="STRAVA_CLIENT_SECRET", section="strava", name="client_secret",
        secret=True, feature="strava",
        label="Strava client secret", help="From the same Strava API settings page.",
    ),
    Setting(
        key="STRAVA_REFRESH_TOKEN", section="strava", name="refresh_token",
        secret=True, feature="strava",
        label="Strava refresh token",
        help="Minted once by `python -m scribejay.sources.strava --authorize`, "
             "which needs the client ID and secret above to be set first.",
    ),

    # ---- clickup ------------------------------------------------------------
    Setting(
        key="CLICKUP_API_TOKEN", section="clickup", name="api_token",
        secret=True, feature="clickup",
        label="ClickUp API token",
        help="A personal token from ClickUp Settings -> Apps. Used to record "
             "work that closes a task but leaves no commit behind.",
    ),

    # ---- notify -------------------------------------------------------------
    Setting(
        key="NTFY_URL", section="notify", name="url", feature="notify",
        label="ntfy topic URL",
        help="Where a failed run pushes an alert, e.g. "
             "https://ntfy.sh/your-private-topic. Leave empty to run with "
             "push off — an unset URL is 'switched off', not 'delivery failed'.",
        default=None,
    ),
    Setting(
        key="NTFY_TOKEN", section="notify", name="token",
        secret=True, feature="notify",
        label="ntfy access token", help="Only needed for a protected topic.",
    ),

    # ---- feature toggles ----------------------------------------------------
    # No defaults, on purpose. An unanswered toggle is not "off" — it means the
    # user has not said, and scribejay/core/features.py answers by asking the
    # machine whether the feature is even set up. That one rule serves a fresh
    # install (nothing configured, everything quiet) and a long-running one
    # (credentials present, nothing changes) without asking either of them a
    # question. A default here would collapse "not said" into "no" and take
    # that away.
    Setting(
        key="SCRIBEJAY_FEATURE_CHROME", section="features", name="chrome",
        type="bool", feature="chrome", default=None,
        label="Read Chrome browsing history",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
    Setting(
        key="SCRIBEJAY_FEATURE_TRANSCRIPTS", section="features", name="transcripts",
        type="bool", feature="transcripts", default=None,
        label="Read Claude Code, Codex and Gemini chat sessions",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
    Setting(
        key="SCRIBEJAY_FEATURE_GIT", section="features", name="git",
        type="bool", feature="git", default=None,
        label="Read commits from your projects folder",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
    Setting(
        key="SCRIBEJAY_FEATURE_GOOGLE_CALENDAR", section="features", name="google_calendar",
        type="bool", feature="google", default=None,
        label="Read and colour your Google Calendar",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
    Setting(
        key="SCRIBEJAY_FEATURE_GMAIL", section="features", name="gmail",
        type="bool", feature="google", default=None,
        label="Read who you sent mail to",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
    Setting(
        key="SCRIBEJAY_FEATURE_YOUTUBE", section="features", name="youtube",
        type="bool", feature="google", default=None,
        label="Read your YouTube Likes",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
    Setting(
        key="SCRIBEJAY_FEATURE_STRAVA", section="features", name="strava",
        type="bool", feature="strava", default=None,
        label="Log Strava activities onto the calendar",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
    Setting(
        key="SCRIBEJAY_FEATURE_CLICKUP", section="features", name="clickup",
        type="bool", feature="clickup", default=None,
        label="Include ClickUp tasks you closed",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
    Setting(
        key="SCRIBEJAY_FEATURE_NOTIFY", section="features", name="notify",
        type="bool", feature="notify", default=None,
        label="Push an alert when a run fails",
        help="Leave unanswered to let ScribeJay decide from whether this is "
             "set up. Answer to override that guess in either direction.",
    ),
)


BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}

# GOOGLE_API_KEY is read as an alias for GEMINI_API_KEY by the Gemini backend.
# It gets no row of its own — two rows for one value would give the settings
# screen two fields that silently shadow each other — but it is a legitimate
# secret name, so the Keychain and the migration must both recognise it.
SECRET_ALIASES: dict[str, str] = {"GOOGLE_API_KEY": "GEMINI_API_KEY"}

SECRET_KEYS: frozenset[str] = frozenset(
    [s.key for s in SETTINGS if s.secret] + list(SECRET_ALIASES)
)


def get(key: str) -> Setting | None:
    return BY_KEY.get(key)


def default_for(key: str) -> str | None:
    setting = BY_KEY.get(key)
    return setting.default if setting else None


def is_secret(key: str) -> bool:
    return key in SECRET_KEYS


def by_feature(feature: str) -> list[Setting]:
    """Every setting belonging to one feature, in declaration order — the
    order the wizard asks its questions and the settings screen draws its
    groups."""
    return [s for s in SETTINGS if s.feature == feature]


def sections() -> list[str]:
    """Settings-file section names in declaration order, de-duplicated."""
    seen: list[str] = []
    for s in SETTINGS:
        if not s.secret and s.section not in seen:
            seen.append(s.section)
    return seen
