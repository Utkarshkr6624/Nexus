"""Application configuration.

Every runtime setting is read from environment variables (optionally sourced
from a local ``.env`` file). This module is the single source of truth; no
other module reads ``os.environ`` directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

#: Placeholder used when no SECRET_KEY is supplied. The validator below refuses
#: to start a production process with this value.
INSECURE_DEV_SECRET_KEY = "dev-insecure-change-me"

#: The backend package root — the directory that contains ``app/`` — derived from
#: this file's own location rather than from ``__file__`` at the call site. Every
#: on-disk default resolves against it, so a checkout moved to another machine
#: or another drive still finds its own artifacts and no absolute path from the
#: developer's workstation is ever baked in.
BACKEND_ROOT = Path(__file__).resolve().parents[2]

#: Where Phase 10 writes the trained intent classifier. Relative to
#: :data:`BACKEND_ROOT` because ``backend/ml/artifacts`` is gitignored: the
#: checkpoint is produced by a training run on the machine that will serve it and
#: is never part of the repository.
DEFAULT_MODEL_PATH = BACKEND_ROOT / "ml" / "artifacts" / "small-model" / "final"

#: The only device names the runtime accepts. ``auto`` resolves to CUDA when a
#: CUDA build of torch sees a GPU and to CPU otherwise, which is what makes the
#: same configuration correct on a workstation and in a CPU-only container.
ML_DEVICES = ("auto", "cpu", "cuda")

#: The trained context length, in subword tokens. Recorded here because it is the
#: fact that bounds everything else about submitted text: the tokeniser truncates
#: here, so anything past it cannot change the prediction, only the latency.
ML_MAX_SEQUENCE_LENGTH = 128


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application ---------------------------------------------------------
    app_name: str = "NEXUS"
    app_version: str = "0.1.0"
    app_description: str = (
        "NEXUS — Personal Intelligence & Decision Platform. "
        "A local-first system for projects, planning, knowledge, analytics and ML."
    )
    environment: Environment = "development"
    debug: bool = True

    openapi_url: str = "/openapi.json"
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"
    api_v1_prefix: str = "/api/v1"

    # -- Security ------------------------------------------------------------
    secret_key: str = INSECURE_DEV_SECRET_KEY
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    # Shortest password the API will store. Enforced at the schema layer, so a
    # weak password is rejected before it ever reaches the database.
    password_min_length: int = 8
    # Lifetime of a password-reset token; short because the token is a bearer
    # credential for a full account takeover.
    password_reset_expire_minutes: int = 30
    # Whether `POST /auth/password/forgot` may hand the raw reset token back in
    # its response. Off by default: the endpoint answers identically for a
    # registered and an unregistered address so it cannot enumerate accounts,
    # and returning the token for one of them defeats that immediately — it
    # turns a "forgot my password" call into an unauthenticated takeover for
    # anyone who knows the address. A local install with no mail transport can
    # turn it on to finish the flow by hand.
    dev_expose_reset_token: bool = False
    # Hard ceiling on the age of a session row, independent of token rotation.
    # Without it a user who never logs out could keep rotating refresh tokens
    # indefinitely, so a session would outlive any actual sign-in event.
    session_absolute_lifetime_days: int = 30
    # Concurrent live sessions allowed per user. On login beyond the cap the
    # oldest session is revoked, so a stolen credential cannot be used to
    # accumulate access.
    max_active_sessions: int = 20
    # How long audit rows are kept. Enforcement is NOT part of this phase — no
    # pruning job exists yet, so this states the policy and gives the value
    # something to display, but nothing is currently deleting rows.
    audit_log_retention_days: int = 400

    # -- Rate limiting --------------------------------------------------------
    #: Master switch for the in-process limiter in ``app.core.middleware``.
    #: On by default because the credential endpoints it protects
    #: (``/auth/login``, ``/auth/password/forgot``) are otherwise an open
    #: password-guessing oracle: bcrypt costs an attacker ~250 ms a try, which
    #: slows them down but never stops them.
    rate_limit_enabled: bool = True
    #: Length of the fixed window every counter is measured over. A minute is
    #: the unit an operator thinks in ("20 a minute"), and it is short enough
    #: that a burst is forgiven quickly.
    rate_limit_window_seconds: int = 60
    #: Requests one client address may make to one route inside a window, for
    #: everything except the credential routes below. Generous on purpose: this
    #: bucket is a runaway-client backstop, not a quota, and the whole test
    #: suite talks to the API from one address.
    rate_limit_general_max_requests: int = 600
    #: Requests one client address may make to ``/auth/login`` and
    #: ``/auth/password/forgot`` inside a window — a shared, more aggressive
    #: budget than the general one, because both routes are unauthenticated
    #: credential oracles. 180 a minute is three attempts a second, which is
    #: below the ~4/s a single bcrypt-12 verification already permits, so it
    #: lengthens nobody's timeline; what it stops is a *parallelised* guess flood
    #: and a reset-request flood, and what it buys back is a caller that is told
    #: to stop rather than left to guess. It is deliberately far above what the
    #: densest minute of the test suite produces (62, measured from 127.0.0.1
    #: across the nine heaviest files), because every suite shares one address.
    rate_limit_credential_max_requests: int = 180
    #: Ceiling on tracked client/route pairs. The store is swept and evicted
    #: long before this, so it is a backstop against a client that can reach the
    #: limiter with unbounded distinct keys — a hard guarantee that the limiter
    #: cannot become the memory leak it exists to prevent.
    rate_limit_max_entries: int = 10_000
    #: Whether to take the client address from ``X-Forwarded-For``. Off by
    #: default, and deliberately so: that header is attacker-controlled on any
    #: path that does not terminate in a proxy we control, so honouring it would
    #: let a caller mint a fresh bucket per request by rotating the header.
    #: Turn it on when the app really does sit behind a trusted reverse proxy —
    #: then, and only then, every client shares the proxy's address.
    rate_limit_trust_forwarded_for: bool = False

    # -- CORS ----------------------------------------------------------------
    # Stored as a comma-separated string so it is pleasant to set in .env.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # -- Database ------------------------------------------------------------
    # An explicit URL wins; otherwise it is assembled from the POSTGRES_* parts.
    database_url: str | None = None
    test_database_url: str | None = None
    postgres_user: str = "nexus"
    postgres_password: str = "nexus"
    postgres_db: str = "nexus"
    # 127.0.0.1 rather than "localhost": on Windows the latter resolves to ::1
    # first and psycopg's async driver only connects over IPv4, so the app would
    # hang instead of failing fast.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    # Wall-clock budget for the health probe. db_pool_timeout only caps the
    # wait for a free connection, not the TCP/TLS handshake that follows, so
    # this is the only bound that keeps a filtered port from hanging the probe
    # until the OS gives up.
    db_probe_timeout_seconds: float = 3.0
    # Wall-clock budget for the TCP/TLS handshake itself, applied to every
    # connection the application opens. Without it psycopg substitutes its own
    # default of 130 seconds, so a database that is down — or a port that is
    # filtered rather than refused — turned every request that touches it into
    # a two-minute stall instead of a prompt failure. `pool_timeout` cannot
    # cover this: it bounds the wait for a free pool slot, and a new physical
    # connection is opened outside that wait.
    db_connect_timeout_seconds: int = 5

    # -- Planner (Phase 4) ---------------------------------------------------
    # IANA zone used when a request does not pass `tz`. Every stored instant is
    # UTC; this only decides which *day boundaries* a planner view spans.
    planner_default_timezone: str = "UTC"
    # Fallback working window for a user with no availability rules, in hours
    # local to that zone. 08:00-20:00 is a daytime window, not a working-hours
    # claim: it is what the scheduler assumes when it has been told nothing.
    planner_day_start_hour: int = 8
    planner_day_end_hour: int = 20
    # Shortest block the scheduler will propose. Below this a "session" is a
    # rounding error that costs a context switch for no useful work.
    planner_min_session_minutes: int = 15
    # Longest single block. Past this the scheduler splits the work instead of
    # proposing one block nobody will sit through.
    planner_max_session_minutes: int = 240
    # Cap per task, so one large task cannot fill the entire horizon ahead of a
    # task that is due tomorrow.
    planner_max_suggestions_per_task: int = 3
    # How far forward the scheduler will search. Bounded so a request over a
    # large backlog stays a bounded walk of availability rather than a scan.
    planner_lookahead_days: int = 30

    # -- Analytics (Phase 6) ------------------------------------------------
    # The productivity weights. They live here, as one block with one validator
    # below, rather than as literals inside the scoring functions: a score whose
    # formula is spread across the code cannot be argued with — changing it means
    # finding every literal, and nothing records that the total must stay 100.
    # `_validate_productivity_weights` is what turns "the weights must sum to
    # 100" from a comment into an invariant the process cannot start without.
    analytics_productivity_weight_completion: float = 30.0
    analytics_productivity_weight_deadline: float = 25.0
    analytics_productivity_weight_consistency: float = 20.0
    analytics_productivity_weight_focus: float = 25.0
    # Period lengths the API offers for period-over-period comparison. The
    # analytics endpoints derive each comparison period from the range the
    # caller asked for, so this is not read anywhere; it is kept only so an
    # existing .env carrying it does not fail validation.
    analytics_comparison_windows: str = "7,30,90"
    # Default window when a request names no dates. A week is the shortest span
    # that can distinguish a habit from a one-off.
    analytics_default_range_days: int = 7
    # Hard ceiling on any requested range, in days. A range query with no bound
    # is the one shape this system's indexes cannot serve: every aggregate below
    # scans the owner's whole history. 366 is a leap year, which is the longest
    # span a "compare my years" view could legitimately want.
    analytics_max_range_days: int = 366
    # Ceiling on `POST /analytics/rebuild`, which is the *write* path: it
    # recomputes each day from the underlying rows and upserts. Half a year is
    # already the most a synchronously-served request should ever be asked for;
    # anything longer belongs to the worker this phase only prepares for.
    analytics_rebuild_max_days: int = 180

    # -- Developer intelligence (Phase 8) ------------------------------------
    #: Wall-clock budget for one `git` invocation. A repository that hangs --
    #: a network-mounted work tree, a filter process waiting on a prompt -- must
    #: come back as an ERROR scan row with a human sentence, not as a request
    #: that never returns. The subprocess is killed when this elapses.
    developer_git_timeout_seconds: int = 30
    #: Upper bound on the commits one scan will transfer. The scan reads history
    #: incrementally, so this is a backstop against a repository whose entire log
    #: is new to us, not the expected volume.
    developer_max_commits_per_scan: int = 2000
    #: How many repositories one account may register. Each registered path is a
    #: directory the server will read on demand; an unbounded list is an
    #: unbounded bill for a list page that renders all of them.
    developer_max_repositories: int = 100
    #: Window used when a request names no dates. A month, not a week: a week of
    #: commits is too few to distinguish a habit from an off week.
    developer_default_window_days: int = 30
    #: Hard ceiling on any requested window, in days. The same argument as
    #: `analytics_max_range_days`: every windowed aggregate here scans the
    #: owner's whole commit history, so an unbounded range is the one query
    #: shape these indexes cannot serve.
    developer_max_window_days: int = 366
    #: Bucket size for the activity series when a request names none. One of
    #: `day`, `week` or `month` -- it is validated where it is read rather than
    #: here, because an unknown bucket size is a request the caller can be told
    #: about, whereas a process that refuses to start takes the whole app down
    #: over one analytics preference.
    developer_activity_granularity_default: str = "day"
    #: Comma-separated roots under which a repository may be registered. Empty
    #: means any readable absolute path that validates as a git work tree,
    #: which is the right default for a local-first application. Set it in a
    #: shared deployment to stop the server reading an arbitrary path at all.
    developer_path_allowlist: str = ""

    # -- Learning and career (Phase 9) --------------------------------------
    #: Window used when a request names no dates. A month, for the reason
    #: `developer_default_window_days` is a month: a shorter window cannot
    #: distinguish a habit from an off week, and a skill level is only ever
    #: described alongside how much was recorded inside it.
    learning_default_window_days: int = 30
    #: Hard ceiling on any requested window, in days. Every windowed aggregate
    #: here scans the owner's whole activity history, so an unbounded range is
    #: the one query shape these indexes cannot serve.
    learning_max_window_days: int = 366
    #: How many learning goals one account may keep. Each goal renders as a row
    #: with its own progress and deadline, so an unbounded list is an unbounded
    #: page — and archived goals still count toward it, because deleting them
    #: would delete the record the user kept them for.
    learning_max_goals: int = 200
    #: How many skills one account may keep. The skills list is the input to
    #: every gap calculation, so a cap here is also what bounds that computation
    #: per request.
    learning_max_skills: int = 100
    #: Below this many activities for a skill, NEXUS offers no level estimate at
    #: all. This is the cold-start guard from the brief, and it is a refusal
    #: rather than a default: a thin sample shown with a low confidence badge
    #: still reads as a claim, whereas a stated refusal does not.
    learning_min_evidence_for_estimate: int = 3
    #: Ceiling on rows in one career-evidence list. Everything on a profile is
    #: user-supplied or user-approved, so the bound here is a rendering bound
    #: rather than a correctness one.
    career_max_evidence: int = 500
    #: After this many days with no recorded activity, a target skill counts as
    #: dormant and is eligible for a nudge. Three weeks is roughly one review
    #: cycle: long enough that someone deep in a project does not get nagged,
    #: short enough that a habit has visibly lapsed.
    career_stale_inactive_days: int = 21

    # -- ML integration (Phase 11) -------------------------------------------
    #: Master switch for the intent classifier. Off means NEXUS never loads a
    #: model: the ML endpoints answer 503 with the reason ``disabled`` and every
    #: deterministic router is untouched. It exists because the checkpoint is a
    #: 703 MiB local artifact and a deployment without one — a fresh clone, a CI
    #: container, another machine — must still serve every other surface rather
    #: than failing to boot over a feature it does not have.
    ml_enabled: bool = True
    #: Whether a failed load stops the process. Off by default, because the
    #: degraded runtime degrades *cleanly*: the app boots, the routers work, and
    #: the ML endpoints answer 503 with a machine-readable reason. A deployment
    #: that cannot serve its own contract at all (a host whose production config
    #: points at a checkpoint that is not there) should turn this on so the
    #: failure is a boot failure instead of a silent feature that quietly 503s.
    ml_fail_fast: bool = False
    #: Directory holding the Phase 10 ``final/`` checkpoint. EMPTY means "use the
    #: default below", which is resolved relative to the repository rather than
    #: hard-coded, so a moved checkout keeps working. Note that
    #: ``backend/ml/artifacts`` is gitignored — this path names something a
    #: training run produces locally, never something the repository ships.
    ml_model_path: str = ""
    #: ``auto`` | ``cpu`` | ``cuda``. ``auto`` means "use a GPU when this build of
    #: torch sees one, CPU otherwise", which is what keeps one configuration
    #: correct on both a workstation and the CPU-only container this runs in.
    ml_device: str = "auto"
    #: Confidence below which the classifier's answer is treated as a refusal to
    #: name a router, rather than as a router to call.
    #:
    #: **This number is an integration threshold, not a calibrated probability.**
    #: It was chosen by re-running the Phase 10 checkpoint over the 420-row
    #: held-out test split and tabulating precision against coverage:
    #:
    #:   threshold | kept | precision | coverage | errors remaining
    #:   ----------+------+-----------+----------+----------------
    #:      0.00   | 420  |  0.9738   |  100.0%  | 11
    #:      0.60   | 416  |  0.9784   |   99.0%  |  9
    #:      0.80   | 407  |  0.9853   |   96.9%  |  6
    #:    * 0.90 * | 400  | *0.9900*  | * 95.2%* |  4
    #:      0.95   | 396  |  0.9924   |   94.3%  |  3
    #:      0.98   | 377  |  0.9947   |   89.8%  |  2
    #:
    #: 0.90 keeps 95.2% of utterances while lifting precision on accepted
    #: requests from 0.9738 to 0.9900 and rejecting 7 of the 11 errors. The rows
    #: above it trade badly for a router whose failure mode is refusing a
    #: legitimate request: 0.95 buys one error for a point of coverage, 0.98 buys
    #: one more for five.
    #:
    #: **That corpus is synthetic and template-generated.** These are not
    #: measurements of real user text, and this is not a claim about real-world
    #: accuracy. Real utterances are messier than the templates, so expect both a
    #: lower mean confidence and a worse hit rate than the table above — which
    #: makes 0.90 conservative in the right direction (more refusals, fewer wrong
    #: writes) and simultaneously an over-estimate of how often NEXUS will
    #: answer at all. Re-derive it against real traffic before trusting it.
    ml_confidence_threshold: float = 0.90
    #: Hard ceiling on the length of text submitted for classification, applied
    #: before inference. The trained context is 128 subword tokens, so a few
    #: thousand characters is already pure truncation — past that point extra
    #: characters cannot change the prediction, they only cost tokenisation time
    #: and make the request body a place to park something the router will never
    #: read. Two thousand characters is comfortably more than any utterance the
    #: training corpus contains.
    ml_max_input_chars: int = 2000
    #: Whether incoming text is screened for credential-shaped content before it
    #: reaches the classifier. On by default because a user utterance is exactly
    #: the untrusted free text that
    #: :func:`ml.preprocessing.normalize.find_credential` was written to scan, and
    #: because nothing downstream of here may echo what was submitted. The refusal
    #: names the *kind* of credential and never the matched value.
    ml_reject_credentials: bool = True

    # -- Logging -------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    log_file: str | None = None
    log_request_body: bool = False
    slow_request_ms: int = 1000

    # -- Derived values ------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_database_uri(self) -> str:
        """SQLAlchemy async URL for the primary database."""
        if self.database_url:
            return self.database_url
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def test_sqlalchemy_database_uri(self) -> str:
        """SQLAlchemy async URL for the pytest database."""
        if self.test_database_url:
            return self.test_database_url
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}_test"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def analytics_productivity_weights(self) -> dict[str, float]:
        """The four productivity weights, as one map.

        ``computed_field`` rather than a plain field so there is exactly one
        place the numbers are written and the scoring functions receive a
        mapping instead of four positional arguments that can be swapped in
        silence. The sum-to-100 rule is enforced by
        :meth:`_validate_productivity_weights` below, which runs before this can
        ever be read.
        """
        return {
            "completion": self.analytics_productivity_weight_completion,
            "deadline": self.analytics_productivity_weight_deadline,
            "consistency": self.analytics_productivity_weight_consistency,
            "focus": self.analytics_productivity_weight_focus,
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed CORS origins, empty strings removed."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment == "test"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ml_resolved_model_path(self) -> Path:
        """The absolute checkpoint directory, configured or defaulted.

        ``computed_field`` rather than a value assigned in a validator so there
        is exactly one answer to "where would we load from", and every caller —
        the runtime, the health check, an operator reading ``/health`` — gets the
        same one. It deliberately does **not** touch the filesystem: a deployment
        that has not run training yet must still be able to construct its
        settings, report that the checkpoint is absent, and serve every non-ML surface.

        An explicitly configured relative path resolves against the process's
        working directory — which is where an operator writing one into a unit
        file means it to point. Only the *default* is anchored to the repository.
        """
        configured = self.ml_model_path.strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return DEFAULT_MODEL_PATH

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ml_checkpoint_exists(self) -> bool:
        """Whether the resolved checkpoint directory is present on this machine.

        ``artifacts/`` is gitignored, so this is False on any checkout that has
        not run Phase 10. That is a supported state, not a broken install: the
        app boots without it and the ML endpoints answer 503.
        """
        return self.ml_resolved_model_path.is_dir()

    # -- Validation ----------------------------------------------------------

    @model_validator(mode="after")
    def _validate_ml_settings(self) -> Settings:
        """Refuse ML settings that would misroute rather than merely fail.

        Each rule below stops a configuration whose *failure* is silent. An
        out-of-range threshold does not raise at request time — it accepts every
        utterance, or refuses every utterance, and both look like a working
        router. A mistyped device name does not raise either; it falls back to
        CPU and the operator learns about it from a queue instead of from here.
        A character ceiling of zero refuses every request with a message that
        names a length the caller cannot satisfy. So like
        :meth:`_validate_productivity_weights`, this **raises**: ``Settings`` is
        constructed once per process via :func:`get_settings`, so the refusal
        happens at boot where an operator will see it.
        """
        if not 0.0 < self.ml_confidence_threshold <= 1.0:
            raise ValueError(
                "ML_CONFIDENCE_THRESHOLD must be in (0, 1]; "
                f"it is {self.ml_confidence_threshold}. Zero would accept every "
                "utterance whatever the model said, and a value above one would "
                "refuse every request as unconfident."
            )
        if self.ml_device not in ML_DEVICES:
            raise ValueError(
                f"ML_DEVICE must be one of {ML_DEVICES}; it is {self.ml_device!r}. "
                "An unrecognised device would silently fall back to CPU, which "
                "turns a configuration typo into a latency regression nobody "
                "attributed to the typo."
            )
        if self.ml_max_input_chars <= 0:
            raise ValueError(
                f"ML_MAX_INPUT_CHARS must be positive; it is {self.ml_max_input_chars}. "
                "A ceiling of zero or less refuses every classification with an "
                "error the caller has no way to satisfy."
            )
        if self.ml_max_input_chars > 10_000:
            raise ValueError(
                "ML_MAX_INPUT_CHARS must not exceed 10000; "
                f"it is {self.ml_max_input_chars}. The trained context is "
                f"{ML_MAX_SEQUENCE_LENGTH} subword tokens, so text this long is "
                "truncated before the model sees it: the bound would accept a "
                "large request body and charge for it while changing nothing."
            )
        return self

    @model_validator(mode="after")
    def _reject_insecure_production_secrets(self) -> Settings:
        if self.is_production:
            if self.secret_key == INSECURE_DEV_SECRET_KEY:
                raise ValueError(
                    "SECRET_KEY must be set to a strong random value when ENVIRONMENT=production."
                )
            if "*" in self.cors_origin_list:
                raise ValueError(
                    "CORS_ORIGINS must not contain '*' when ENVIRONMENT=production. "
                    "Credentials are allowed on every API response, so a wildcard "
                    "lets any site on the internet make authenticated, "
                    "cookie-bearing requests against this API."
                )
            if self.debug:
                raise ValueError("DEBUG must be false when ENVIRONMENT=production.")
        return self

    @model_validator(mode="after")
    def _validate_productivity_weights(self) -> Settings:
        """Refuse weights that do not describe a 0-100 score.

        The productivity score is presented as a percentage, so its weights are
        the denominators of that percentage: a set summing to 90 would report a
        "80/100" that is really "80/90", and one summing to 120 would report a
        score of 100 having awarded 120 points. Neither is a tuning choice, both
        are a broken scale, and a silently-normalised third option — rescale them
        here — would hide that the configured numbers were wrong.

        So this **raises**, and the failure is loud: ``Settings`` is constructed
        once at import time via :func:`get_settings`, so the process refuses to
        start rather than serving analytics whose formula does not add up. The
        operator sees the offending values and the required total.
        """
        total = sum(self.analytics_productivity_weights.values())
        if abs(total - 100.0) > 1e-6:
            raise ValueError(
                "The analytics productivity weights must sum to 100; "
                f"they sum to {total} "
                f"(analytics_productivity_weight_completion="
                f"{self.analytics_productivity_weight_completion}, "
                f"..._deadline={self.analytics_productivity_weight_deadline}, "
                f"..._consistency={self.analytics_productivity_weight_consistency}, "
                f"..._focus={self.analytics_productivity_weight_focus})."
            )
        if any(value < 0 for value in self.analytics_productivity_weights.values()):
            raise ValueError("The analytics productivity weights must not be negative.")
        return self

    @model_validator(mode="after")
    def _normalise_log_file(self) -> Settings:
        if self.log_file is not None and not self.log_file.strip():
            self.log_file = None
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
