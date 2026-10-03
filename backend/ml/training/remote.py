"""Drive Kaggle-hosted GPU training from a stdlib-only pipeline.

The backend's ``requirements.txt`` is pinned and carries no ``torch``, no
``transformers`` and no ``kaggle``. Training ``deberta-v3-base`` and the QLoRA
fine-tune of ``Qwen3-8B`` needs an accelerator, so those runs happen on Kaggle
kernels — and this module is the seam between the local pipeline and that
remote one. It shells out to the ``kaggle`` CLI with :mod:`subprocess` and
nothing else: no ``kaggle`` Python package, no ``requests``, no third-party
import of any kind. The reason is the same one that keeps ``torch`` out of the
repository: the contributor's clone and CI must stay runnable without a
multi-gigabyte download standing between them and a test run.

**Credentials are never read here.** Nothing in this module opens
``~/.kaggle/access_token`` or any other credential file, and nothing logs,
returns or embeds a credential *value*. Authentication is the CLI's job; this
module's job is to notice that it is absent. Username discovery runs
``kaggle kernels list -m`` and reads the ``author`` field the CLI already
returns, which is why :meth:`KaggleClient.probe` never has to touch a secret to
answer "am I signed in". When a subprocess does fail, the command line and the
exit code are surfaced — they are what makes a failure diagnosable — but every
string that goes into the exception passes through
:func:`ml.preprocessing.normalize.redact` first, because the Kaggle CLI is
perfectly capable of echoing a token back in a traceback.

**A GPU in the metadata is not a GPU.** This was verified against Kaggle CLI
2.2.4 and it is the reason :func:`verify_gpu_available` exists. A kernel
pushed with ``enable_gpu: true`` and ``gpu_type_option: "T4x2"`` comes back from
the API with ``machine_shape: "NvidiaTeslaT4"`` recorded in its metadata — and
the executed environment had **no** ``/dev/nvidia*`` device node and **no**
``nvidia-smi`` binary on ``PATH``. The metadata is a *request* echoed back, not
an observation. Anything that reports "GPU enabled" on the strength of kernel
metadata is reporting the request. :meth:`KaggleClient.probe` therefore returns
``gpu_quota_hours`` as a quota (an account property, honestly obtainable) and
never as a capability claim, and :func:`verify_gpu_available` refuses to answer
``True`` without a probe file written by the kernel *after* it actually ran.

The probe contract: the pushed script writes
:data:`GPU_PROBE_FILENAME` into its own output directory as a JSON object with
the keys ``nvidia_smi`` (bool), ``device_nodes`` (list of ``/dev/nvidia*`` paths
that really exist), ``torch_cuda_available`` (bool or null) and ``recorded_at``
(ISO-8601 UTC). :func:`verify_gpu_available` reads it, and treats the presence
of a device node or a working ``nvidia-smi`` as the only admissible evidence of
an accelerator.

Kernel naming follows :data:`KAGGLE_REF_PREFIX`: every dataset slug and kernel
slug this module creates begins ``nexo-phase10-``, so a Phase 10 run is
distinguishable in a Kaggle account that also hosts unrelated experiments.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ml.preprocessing.normalize import redact

#: Every slug this module creates or adopts begins with this. Phase 10 pushes
#: from whatever account the operator happens to have, which usually also hosts
#: unrelated experiments; a shared prefix is what lets :meth:`KaggleClient.
#: list_kernels` report Phase 10 kernels without reporting everything.
KAGGLE_REF_PREFIX: Final = "nexo-phase10"

#: Name of the file a pushed kernel writes to record what it actually found.
#: It lives in the kernel's own output directory, which is to say it is written
#: by the code that ran on the remote machine — the only party able to testify
#: about the machine it ran on.
GPU_PROBE_FILENAME: Final = "nexo_gpu_probe.json"

#: Normalised kernel states. A raw status string outside this set becomes
#: ``UNKNOWN`` rather than being passed through, so a caller switching on
#: ``status.state`` cannot be surprised by a CLI spelling it has never seen.
KERNEL_STATES: Final = frozenset(
    {"PENDING", "RUNNING", "COMPLETE", "ERROR", "CANCELLED", "UNKNOWN"}
)

#: States after which a kernel will not change on its own again.
TERMINAL_STATES: Final = frozenset({"COMPLETE", "ERROR", "CANCELLED"})

#: Synonyms the Kaggle CLI has used for the six normalised states, plus the
#: spellings its notebook backend returns. Matching is done on an upper-cased
#: string, so ``complete`` and ``COMPLETED`` are the same thing.
_STATE_ALIASES: Final = {
    "PENDING": "PENDING",
    "QUEUED": "PENDING",
    "SUBMITTED": "PENDING",
    "RUNNING": "RUNNING",
    "IN_PROGRESS": "RUNNING",
    "IN PROGRESS": "RUNNING",
    "EXECUTING": "RUNNING",
    "COMPLETE": "COMPLETE",
    "COMPLETED": "COMPLETE",
    "SUCCESS": "COMPLETE",
    "SUCCEEDED": "COMPLETE",
    "ERROR": "ERROR",
    "FAILED": "ERROR",
    "FAILURE": "ERROR",
    "CANCELLED": "CANCELLED",
    "CANCELED": "CANCELLED",
    "ABORTED": "CANCELLED",
}

#: Kaggle slug ceiling. A slug longer than this is rejected by the API, so
#: :func:`slugify` truncates rather than letting the push fail after the upload.
_SLUG_MAX_LENGTH: Final = 50

_SLUG_ILLEGAL: Final = re.compile(r"[^a-z0-9]+")
_SLUG_TRIM: Final = re.compile(r"-+")
_VERSION_IN_TEXT: Final = re.compile(r"\b(\d+\.\d+(?:\.\d+)*)\b")
_VERSION_URL: Final = re.compile(r"/version/(\d+)")
_KERNEL_VERSION_LINE: Final = re.compile(r"[Kk]ernel version (\d+)", re.ASCII)
_HOURS_VALUE: Final = re.compile(r"(\d+(?:\.\d+)?)\s*h", re.IGNORECASE)
_ALREADY_EXISTS: Final = re.compile(r"already exists", re.IGNORECASE)

#: ``kaggle kernels output`` copies the whole artefact out of the kernel; 300 s
#: is the client's general default and is not enough for a checkpoint.
_OUTPUT_TIMEOUT_SECONDS: Final = 1800

#: How old a probe file may be before :func:`verify_gpu_available` declines to
#: call it current. Override with ``NEXO_GPU_PROBE_MAX_AGE_SECONDS`` for
#: accounts that push more often than daily.
_PROBE_MAX_AGE_SECONDS: Final = 86400.0

#: Where :func:`verify_gpu_available` looks for the probe file. Overridable with
#: ``NEXO_REMOTE_ARTIFACT_DIR`` so a pipeline that already has an output
#: directory does not have to copy its own artefact directory.
_ARTIFACT_DIR_ENV: Final = "NEXO_REMOTE_ARTIFACT_DIR"
_PROBE_MAX_AGE_ENV: Final = "NEXO_GPU_PROBE_MAX_AGE_SECONDS"

#: Username environment variables consulted *only* when the kernel listing gives
#: nothing to read. Neither is a secret: they are the public half of a Kaggle
#: identity, and reading them reveals nothing the API did not already show.
_USERNAME_ENV_KEYS: Final = ("KAGGLE_USERNAME", "KAGGLE_USER")


@dataclass(frozen=True, slots=True)
class KaggleEnvironment:
    """What the CLI can tell us about this machine and this account.

    ``gpu_quota_hours`` is a *quota*, not a capability: it says how many GPU
    hours the account may still spend, which is worth knowing before a
    twelve-hour QLoRA job is queued and tells you nothing about whether any of
    those hours land on a real accelerator. ``None`` means the CLI did not
    report a figure we could parse — never ``0``, which would assert the quota
    is exhausted.

    ``username`` is the ``author`` string the API returned, verbatim and
    therefore possibly mixed-case. Kaggle kernel refs are lower-cased by the
    service, so a caller composing a ref should use ``username.lower()``.
    """

    username: str
    kaggle_cli_version: str | None
    gpu_quota_hours: float | None
    authenticated: bool


@dataclass(frozen=True, slots=True)
class KernelRef:
    """A pushed kernel, identified the way Kaggle identifies it.

    ``ref`` is the full ``<username>/<slug>`` form, which is the only form the
    CLI accepts for a status, output or delete call. ``version`` is the push
    number, because a kernel ref without one names the *latest* version and a
    training run that waits hours for a result needs to name its own.
    """

    ref: str
    version: int
    url: str


@dataclass(frozen=True, slots=True)
class KernelStatus:
    """One poll of a kernel, with the CLI's own wording kept alongside.

    ``state`` is normalised to one of :data:`KERNEL_STATES`. ``raw`` is the
    scrubbed text the CLI actually produced, because "why did my state become
    UNKNOWN" is unanswerable without it.
    """

    ref: str
    state: str
    raw: str


@dataclass(frozen=True, slots=True)
class DatasetRef:
    """A dataset slug and the version the upload produced.

    ``version`` is ``None`` when the CLI succeeded but did not print a version
    we could parse. A dataset exists in that case; what cannot be claimed is
    which revision it is, and a manifest that guesses would be wrong in the one
    place a manifest exists to be right.
    """

    slug: str
    version: int | None


@dataclass(frozen=True, slots=True)
class RemoteError(Exception):
    """A Kaggle CLI call failed.

    Carries the command line and exit code because a subprocess failure that
    does not say which command failed is not diagnosable.

    Both are scrubbed on construction, not merely at the call sites that happen
    to remember to scrub them. A credential-shaped run in a traceback would
    otherwise reach a log line, a test failure and a chat transcript intact,
    and the one place that has to be right cannot be left to every future
    caller's diligence.
    """

    command: str
    returncode: int | None = None
    stderr: str = ""

    def __post_init__(self) -> None:
        """Scrub the command line and the captured stderr.

        Runs on every construction, including the ones where a caller has
        already redacted; redacting twice is a no-op on already-scrubbed text.
        """
        object.__setattr__(self, "command", redact(self.command))
        object.__setattr__(self, "stderr", redact(self.stderr))

    def __str__(self) -> str:
        """Render the failure without any unscrubbed text.

        A ``None`` return code means the process never produced one — it was
        missing, or it was killed for running too long — so the wording stays
        open rather than asserting one of the two. ``stderr`` carries which.

        Returns:
            The command, the exit status where there was one, and the scrubbed
            stderr.
        """
        if self.returncode is None:
            head = f"`{self.command}` did not run to completion"
        else:
            head = f"`{self.command}` failed with exit code {self.returncode}"
        return f"{head}: {self.stderr}" if self.stderr else head


def slugify(title: str) -> str:
    """Reduce a human title to the slug Kaggle derives from it.

    Kaggle resolves a kernel's identity from its *title*, not from any field we
    would like to write, so the slug and the title have to agree or the push
    lands somewhere other than where the caller asked. Folding to ASCII, lower
    casing, collapsing every non-alphanumeric run to a single hyphen and
    truncating on a hyphen boundary is the normalisation Kaggle applies; doing
    it here means :func:`render_kernel_metadata` can check the agreement
    instead of discovering it from a warning in the middle of an upload.

    Args:
        title: A human title or an already-slugified string.

    Returns:
        A slug of at most :data:`_SLUG_MAX_LENGTH` characters, never empty and
        never leading or trailing with a hyphen.
    """
    folded = _SLUG_TRIM.sub("-", _SLUG_ILLEGAL.sub("-", title.casefold())).strip("-")
    if len(folded) <= _SLUG_MAX_LENGTH:
        return folded or "nexo-phase10"
    truncated = folded[:_SLUG_MAX_LENGTH]
    cut = truncated.rfind("-")
    return (truncated[:cut] if cut > 0 else truncated).strip("-") or "nexo-phase10"


def render_kernel_metadata(
    *,
    ref: str,
    title: str,
    code_file: str,
    dataset_sources: Iterable[str] = (),
    enable_gpu: bool = True,
    gpu_type_option: str = "T4x2",
) -> dict[str, Any]:
    """Build the ``kernel-metadata.json`` that Kaggle CLI 2.2.4 accepts.

    The field list below is not a style preference; each entry is load-bearing
    and the two marked ``verified`` were found the hard way on CLI 2.2.4.

    **``kernel_type`` must be present and equal to ``"notebook"``.** Omitting it
    does not fall back to a default: ``kaggle kernels push`` aborts with
    *"A valid kernel type must be specified in the metadata"* before it uploads
    anything.

    **``id`` must be the full ``<username>/<slug>`` ref**, and ``title`` must
    slugify to that same slug. When the two disagree Kaggle warns and then
    resolves the identity from the *title* — so a mismatched ``id`` does not
    fail loudly, it silently pushes to a different kernel than the caller named.

    ``enable_gpu`` and ``gpu_type_option`` are recorded by Kaggle as a
    ``machine_shape`` of ``NvidiaTeslaT4``, which is a record of what was
    *requested*. See the module docstring: the executed environment had no
    ``/dev/nvidia*`` and no ``nvidia-smi``. Ask
    :func:`verify_gpu_available` whether a GPU existed, not this mapping.

    Args:
        ref: The full ``<username>/<slug>`` kernel identity.
        title: The human title. Must slugify to the slug inside ``ref``.
        code_file: Filename of the code file, relative to the push directory.
        dataset_sources: Dataset slugs to mount, each ``<username>/<slug>``.
        enable_gpu: Whether to request an accelerator.
        gpu_type_option: The requested accelerator shape.

    Returns:
        A JSON-ready mapping ready to be written as ``kernel-metadata.json``.

    Raises:
        ValueError: ``ref`` is not ``<username>/<slug>``, or ``title`` slugifies
            to a different slug than ``ref`` names. Pushing anyway would push
            to the wrong kernel.
    """
    username, separator, slug_part = ref.partition("/")
    if not separator or not username or not slug_part.strip("/"):
        raise ValueError(f"ref must be '<username>/<slug>', got {ref!r}")
    slug = slugify(slug_part)
    title_slug = slugify(title)
    if title_slug != slug:
        raise ValueError(
            f"title {title!r} slugifies to {title_slug!r} but ref names {slug!r}; "
            "Kaggle resolves kernel identity from the title, so the push would "
            f"land on {title_slug!r} instead of {slug!r}"
        )
    return {
        "id": ref,
        "title": title_slug,
        "code_file": code_file,
        "language": "python",
        "kernel_type": "notebook",
        "enable_gpu": enable_gpu,
        "gpu_type_option": gpu_type_option,
        "enable_internet": True,
        "dataset_sources": list(dataset_sources),
        "model_sources": [],
        "keywords": [],
    }


def verify_gpu_available(force: bool = True) -> tuple[bool, str]:
    """Report whether a Phase 10 run has *evidence* of an accelerator.

    This pushes nothing and schedules nothing. It reads
    :data:`GPU_PROBE_FILENAME` out of the local artifact directory — a file
    the remote kernel wrote into its own output while it was running — and
    reports what that file says. The distinction is the whole point: kernel
    metadata records a ``machine_shape`` for the accelerator that was
    *requested*, and on the run examined here that request was recorded while
    the environment had no ``/dev/nvidia*`` node and no ``nvidia-smi`` binary.
    Only the kernel that actually executed can testify to its own machine, and
    only its testimony is accepted here.

    Args:
        force: When True (the default) a probe older than the freshness window
            is refused, because accelerator availability is a property of the
            machine rather than of the account and yesterday's T4 says nothing
            about today's. When False the newest probe is reported whatever its
            age, with its age in the message, for the case where a stale answer
            is still better than no answer.

    Returns:
        ``(True, reason)`` when a fresh probe recorded a device node or a
        working ``nvidia-smi``; otherwise ``(False, reason)`` saying why —
        including ``"not yet probed"`` when no kernel has written one yet. Never
        ``True`` without a probe behind it.
    """
    probe_path = _artifact_dir() / GPU_PROBE_FILENAME
    probe = _read_gpu_probe(probe_path)
    if probe is None:
        return False, f"not yet probed ({probe_path} absent or unreadable)"

    age = _probe_age_seconds(probe)
    max_age = _probe_max_age_seconds()
    if age is not None and age > max_age:
        detail = f"probe is {age / 3600:.1f}h old"
        if force:
            return False, f"stale probe: {detail}, limit is {max_age / 3600:.1f}h"
    else:
        detail = "probe is current"

    device_nodes = [str(node) for node in probe.get("device_nodes") or []]
    if device_nodes:
        return True, f"{detail}: device nodes {sorted(device_nodes)}"
    if bool(probe.get("nvidia_smi")):
        return True, f"{detail}: nvidia-smi reported a device"
    return False, f"{detail}: no /dev/nvidia* device and no working nvidia-smi"


class KaggleClient:
    """A thin, credential-blind driver over the ``kaggle`` CLI.

    Every call is a subprocess. Nothing is cached between calls except the
    environment overrides the constructor was given, because a cached status
    is a status that was true at some point other than now.
    """

    def __init__(
        self,
        *,
        executable: str = "kaggle",
        timeout: int = 300,
        kaggle_home: Path | None = None,
    ) -> None:
        self._executable = executable
        self._timeout = timeout
        self._env = dict(os.environ)
        if kaggle_home is not None:
            self._env["KAGGLE_CONFIG_DIR"] = str(kaggle_home)

    def probe(self) -> KaggleEnvironment:
        """Ask the CLI who we are, which version it is, and what the quota is.

        Username discovery goes through ``kaggle kernels list -m --format json``
        and reads the ``author`` field the API already returned. It does not go
        near ``~/.kaggle/access_token``: the presence of a working, credential-
        bearing config file is not evidence that it works, whereas a successful
        private listing is.

        A CLI that is not installed is not an error here — an unauthenticated
        run of the local pipeline is a legitimate state to be in — so every
        probe degrades to a field being ``None`` or empty. Only a subprocess
        that ran and failed to answer is swallowed; one that could not run at
        all is reported through the resulting ``authenticated=False``.

        Returns:
            What the CLI could tell us, with every unknown as ``None`` or
            ``False`` and never ``0``.
        """
        version = self._try_version()
        quota = self._try_quota_hours()
        username, listed = self._try_username()
        return KaggleEnvironment(
            username=username,
            kaggle_cli_version=version,
            gpu_quota_hours=quota,
            authenticated=listed,
        )

    def create_or_version_dataset(
        self,
        *,
        local_dir: Path,
        slug: str,
        description: str,
        dir_name: str,
    ) -> DatasetRef:
        """Publish a dataset, versioning it when it is already there.

        The first push of a Phase 10 dataset creates it; every push after that
        must be a *version*, because a Kaggle dataset that already exists
        cannot be silently overwritten and the identity of revision N is what a
        training run's manifest has to cite. Hence the two-step attempt rather
        than a version-first strategy that would fail on a first push.

        Both ``slug`` and ``dir_name`` are given the :data:`KAGGLE_REF_PREFIX`
        treatment when they lack it, so Phase 10's datasets stay greppable in an
        account that hosts other work and so the two cannot disagree.

        Args:
            local_dir: Directory whose contents become the dataset. Kaggle
                zips it as-is, so it must hold only what belongs in the dataset.
            slug: The bare dataset slug. Prefixed if needed.
            description: The dataset's description text.
            dir_name: The display name Kaggle slugifies into the dataset's own
                identity. Prefixed if needed, so it must resolve to the same
                slug as ``slug``.

        Returns:
            The slug as published and the revision it produced.

        Raises:
            ValueError: ``dir_name`` and ``slug`` do not resolve to the same
                slug, which would publish the contents under a name the caller
                did not ask for, or ``local_dir`` does not exist.
            RemoteError: The CLI is missing, or both create and version failed.
        """
        published = _prefixed(slug, f"{KAGGLE_REF_PREFIX}-datasets")
        if _tail(dir_name) != _tail(slug):
            raise ValueError(
                f"dir_name {dir_name!r} resolves to {_tail(dir_name)!r} but slug "
                f"{slug!r} resolves to {_tail(slug)!r}; Kaggle names a dataset after "
                "its title, so the two must agree on the name itself"
            )
        title_slug = published
        if not local_dir.is_dir():
            raise ValueError(f"dataset directory does not exist: {local_dir}")

        create = self._run(
            [
                "datasets",
                "create",
                "-p",
                str(local_dir),
                "-t",
                title_slug,
                "-d",
                description,
            ],
            check=False,
        )
        if create.returncode == 0:
            combined = f"{create.stdout}\n{create.stderr}"
            return DatasetRef(slug=published, version=_parse_version_number(combined) or 1)

        combined = redact(f"{create.stdout}\n{create.stderr}")
        if not _ALREADY_EXISTS.search(combined):
            raise RemoteError(
                command=_format_command([self._executable, "datasets", "create"]),
                returncode=create.returncode,
                stderr=combined,
            )

        version = self._run(
            ["datasets", "version", "-p", str(local_dir), "-d", description, "-t", title_slug]
        )
        return DatasetRef(
            slug=published, version=_parse_version_number(f"{version.stdout}\n{version.stderr}")
        )

    def push_kernel(self, workdir: Path) -> KernelRef:
        """Push a kernel directory and return what was pushed.

        Args:
            workdir: A directory holding ``kernel-metadata.json`` and the code
                file it names. Kaggle zips the whole directory.

        Returns:
            The kernel ref, the version created and its URL.

        Raises:
            ValueError: ``kernel-metadata.json`` is absent.
            RemoteError: The push failed.
        """
        metadata_path = workdir / "kernel-metadata.json"
        if not metadata_path.is_file():
            raise ValueError(f"no kernel-metadata.json in {workdir}")
        result = self._run(["kernels", "push", "-p", str(workdir)])
        combined = f"{result.stdout}\n{result.stderr}"
        return _kernel_ref_from_output(_read_metadata_id(metadata_path), combined)

    def kernel_status(self, ref: str) -> KernelStatus:
        """Poll a kernel once.

        Args:
            ref: The ``<username>/<slug>`` kernel ref.

        Returns:
            The status, with ``state`` normalised and the scrubbed CLI text
            kept in ``raw``.

        Raises:
            RemoteError: The status command itself failed.
        """
        result = self._run(["kernels", "status", ref])
        raw = result.stdout.strip() or result.stderr.strip()
        return KernelStatus(ref=ref, state=_normalise_state(raw), raw=redact(raw))

    def wait_for_kernel(
        self,
        ref: str,
        *,
        poll_seconds: int = 30,
        max_seconds: int = 43200,
        on_poll: Callable[[KernelStatus], None] | None = None,
    ) -> KernelStatus:
        """Block until a kernel reaches a terminal state.

        Twelve hours is the default ceiling because a QLoRA fine-tune of an 8B
        model on a two-T4 kernel legitimately runs for hours, and a ceiling set
        by what feels long is how a queue that never starts gets reported as a
        training run.

        The return value is whatever terminal state was observed, **including
        ``ERROR`` and ``CANCELLED``** — the caller asked what happened, and a
        raised exception would throw away the CLI's own wording. A caller that
        treats this as success without checking ``status.state`` is the bug;
        there is no way for this function to make that mistake for it.

        Args:
            ref: The ``<username>/<slug>`` kernel ref.
            poll_seconds: Delay between polls.
            max_seconds: Overall ceiling.
            on_poll: Called with each observed status, for progress logging.

        Returns:
            The terminal status.

        Raises:
            RemoteError: The ceiling was reached before a terminal state, or a
                poll failed.
        """
        deadline = time.monotonic() + max_seconds
        while True:
            status = self.kernel_status(ref)
            if on_poll is not None:
                on_poll(status)
            if status.state in TERMINAL_STATES:
                return status
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RemoteError(
                    command=_format_command([self._executable, "kernels", "status", ref]),
                    returncode=None,
                    stderr=(
                        f"kernel did not reach a terminal state within {max_seconds}s; "
                        f"last observed {status.state}: {status.raw}"
                    ),
                )
            time.sleep(min(poll_seconds, max(remaining, 0.0)))

    def kernel_output(self, ref: str, dest: Path) -> Path:
        """Download a kernel's output artefacts.

        Args:
            ref: The ``<username>/<slug>`` kernel ref.
            dest: Local directory to download into. Created if absent.

        Returns:
            ``dest``, so the caller can chain straight into a manifest.

        Raises:
            RemoteError: The download failed.
        """
        dest.mkdir(parents=True, exist_ok=True)
        self._run(
            ["kernels", "output", ref, "-p", str(dest)],
            timeout=_OUTPUT_TIMEOUT_SECONDS,
        )
        return dest

    def kernel_logs(self, ref: str, dest: Path) -> Path:
        """Download a kernel's log file.

        Args:
            ref: The ``<username>/<slug>`` kernel ref.
            dest: Local directory to download into. Created if absent.

        Returns:
            ``dest``.

        Raises:
            RemoteError: The download failed.
        """
        dest.mkdir(parents=True, exist_ok=True)
        self._run(
            ["kernels", "output", ref, "-p", str(dest), "--log"],
            timeout=_OUTPUT_TIMEOUT_SECONDS,
        )
        return dest

    def delete_kernel(self, ref: str) -> None:
        """Delete a kernel.

        Used for cleaning up after a failed push or an abandoned experiment.
        It is deliberately not wrapped in anything that retries: a delete that
        appears to fail because the network blipped has already succeeded on
        Kaggle's side, and retrying is how a slug gets reused.

        Args:
            ref: The ``<username>/<slug>`` kernel ref.

        Raises:
            RemoteError: The delete failed.
        """
        self._run(["kernels", "delete", ref])

    def list_kernels(self) -> tuple[KernelRef, ...]:
        """List this account's Phase 10 kernels.

        Args:
            None.

        Returns:
            One :class:`KernelRef` per kernel whose slug carries
            :data:`KAGGLE_REF_PREFIX`, newest-first as the CLI orders them.
            ``version`` is ``0`` when the listing did not report one — CLI
            2.2.4's ``kernels list --format json`` returns only ``ref``,
            ``title``, ``author``, ``lastRunTime`` and ``totalVotes``, so a
            version would otherwise have to be invented. Empty when nothing
            matches, or when the listing failed: "cannot see" and "nothing
            there" both report as empty, because this is a convenience listing
            and not a gate. It never raises for a failed listing: a client that
            cannot see its own kernels has nothing useful to say about them.

        """
        result = self._run(["kernels", "list", "-m", "--format", "json"], check=False)
        if result.returncode != 0:
            return ()
        refs: list[KernelRef] = []
        for row in _decode_json_rows(result.stdout):
            ref = str(row.get("ref") or "")
            _, _, slug = ref.partition("/")
            if not ref or "/" not in ref or not slug.startswith(KAGGLE_REF_PREFIX):
                continue
            refs.append(
                KernelRef(
                    ref=ref,
                    version=_parse_int(row, "lastVersionNumber", "lastVersion", "version") or 0,
                    url=str(row.get("url") or f"https://www.kaggle.com/code/{ref}"),
                )
            )
        return tuple(refs)

    def _run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the CLI, capturing stdout and stderr separately.

        Capturing separately is not tidiness: several calls parse stdout as
        JSON, and merging the streams would let a warning line corrupt the
        parse in a way that surfaces much later as a nonsensical error.

        ``check=False`` returns the completed process instead of raising, for
        the one call (:meth:`create_or_version_dataset`) that has to inspect a
        failure to tell "already exists" apart from a real error.

        Args:
            args: Arguments after the executable.
            check: Whether a non-zero exit raises.
            timeout: Override for the client's default, in seconds.

        Returns:
            The completed process with ``text=True`` decoding.

        Raises:
            RemoteError: The executable is missing, the run timed out, or it
                failed and ``check`` is set.
        """
        command = [self._executable, *args]
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv list, shell=False
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self._timeout,
                env=self._env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RemoteError(
                command=_format_command(command),
                returncode=None,
                stderr=f"executable not found on PATH: {exc.strerror}",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RemoteError(
                command=_format_command(command),
                returncode=None,
                stderr=f"timed out after {timeout or self._timeout}s",
            ) from exc

        if check and completed.returncode != 0:
            raise RemoteError(
                command=_format_command(command),
                returncode=completed.returncode,
                stderr=redact(completed.stderr.strip() or completed.stdout.strip()),
            )
        return completed

    def _try_version(self) -> str | None:
        """Return the CLI version, or None if it will not say.

        Returns:
            A dotted version string, or None.
        """
        try:
            result = self._run(["--version"], check=False)
        except RemoteError:
            return None
        return _parse_dotted_version(f"{result.stdout}\n{result.stderr}")

    def _try_quota_hours(self) -> float | None:
        """Return the account's remaining GPU quota in hours, or None.

        Returns:
            A non-negative float, or None when the CLI's table is unreadable.
            None is the right answer for "cannot tell" and a wrong answer for
            "none left", which is why the parser reads the table by column
            rather than taking the first number it sees.
        """
        try:
            result = self._run(["quota"], check=False)
        except RemoteError:
            return None
        if result.returncode != 0:
            return None
        return _parse_quota(f"{result.stdout}\n{result.stderr}")

    def _try_username(self) -> tuple[str, bool]:
        """Discover the username from the private kernel listing.

        Returns:
            ``(username, authenticated)``. ``authenticated`` is True only when
            the private listing succeeded — it is the one call here that an
            unauthenticated CLI cannot fake.
        """
        try:
            result = self._run(["kernels", "list", "-m", "--format", "json"], check=False)
        except RemoteError:
            return "", False
        if result.returncode != 0:
            return "", False

        rows = _decode_json_rows(result.stdout)
        for row in rows:
            author = str(row.get("author") or "").strip()
            if author:
                return author, True
            ref = str(row.get("ref") or "")
            prefix = ref.partition("/")[0].strip()
            if prefix:
                return prefix, True

        for key in _USERNAME_ENV_KEYS:
            value = self._env.get(key, "").strip()
            if value:
                return value, True
        return "", True


def _format_command(command: Sequence[str]) -> str:
    """Render an argv list for an error message.

    Args:
        command: The argument vector, executable first.

    Returns:
        A single-line rendering with spaces inside arguments quoted.
    """
    return " ".join(part if " " not in part else f'"{part}"' for part in command)


def _prefixed(slug: str, fallback_prefix: str) -> str:
    """Give a slug the Phase 10 prefix when it does not already have one.

    Args:
        slug: The bare slug.
        fallback_prefix: Prefix to use if the slug carries no prefix at all and
            ``KAGGLE_REF_PREFIX`` would not distinguish it.

    Returns:
        The slug to publish under.
    """
    candidate = slugify(slug)
    if candidate.startswith(KAGGLE_REF_PREFIX):
        return candidate
    return slugify(f"{fallback_prefix}-{candidate}")


def _tail(slug: str) -> str:
    """The part of a slug that follows whatever prefix it carries.

    Comparing tails rather than whole slugs is what lets a caller say
    ``slug="routing-intent"`` with ``dir_name="Nexo Phase10 Routing Intent"``:
    the two describe the same dataset once each has been prefixed, which is
    the only comparison that survives the prefixing being applied at all.

    Args:
        slug: A slug or display name.

    Returns:
        The slugified name with any known prefix removed.
    """
    cleaned = slugify(slug)
    for prefix in (f"{KAGGLE_REF_PREFIX}-datasets-", f"{KAGGLE_REF_PREFIX}-"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


def _normalise_state(raw: str) -> str:
    """Map the CLI's status wording onto :data:`KERNEL_STATES`.

    Args:
        raw: Whatever the status command printed.

    Returns:
        One of :data:`KERNEL_STATES`; an unrecognised string becomes
        ``UNKNOWN`` rather than being passed through, so a caller switching on
        ``state`` sees a closed set.
    """
    text = raw.strip().upper()
    if not text:
        return "UNKNOWN"
    if text in _STATE_ALIASES:
        return _STATE_ALIASES[text]
    for token in re.split(r"[\s:,-]+", text):
        if token in _STATE_ALIASES:
            return _STATE_ALIASES[token]
    if "COMPLETE" in text:
        return "COMPLETE"
    if "CANCEL" in text:
        return "CANCELLED"
    if "RUN" in text or "PROGRESS" in text or "EXECUT" in text:
        return "RUNNING"
    if "PEND" in text or "QUEUE" in text or "SUBMIT" in text:
        return "PENDING"
    if "ERROR" in text or "FAIL" in text:
        return "ERROR"
    return "UNKNOWN"


def _decode_json_rows(text: str) -> list[dict[str, Any]]:
    """Decode a JSON array that may be preceded by warning lines.

    The Kaggle CLI prints a deprecation banner ahead of ``--format json``
    output often enough that requiring byte-zero JSON would break the listing
    for a cosmetic reason.

    Args:
        text: The captured stdout.

    Returns:
        The decoded objects, or an empty list when nothing decodes.
    """
    start = text.find("[")
    if start < 0:
        return []
    try:
        decoded = json.loads(text[start:])
    except json.JSONDecodeError:
        return []
    return [row for row in decoded if isinstance(row, dict)]


def _parse_int(row: dict[str, Any], *keys: str) -> int | None:
    """Read the first integer-valued key present in a decoded row.

    Args:
        row: The decoded JSON object.
        keys: Candidate key names, in preference order. The CLI has renamed
            these between releases.

    Returns:
        The integer, or None when no key holds one.
    """
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _parse_dotted_version(text: str) -> str | None:
    """Extract a dotted version from CLI output.

    Args:
        text: The captured output of ``kaggle --version``.

    Returns:
        The version string, or None when the text carries no version.
    """
    match = _VERSION_IN_TEXT.search(text)
    return match.group(1) if match else None


def _parse_version_number(text: str) -> int | None:
    """Extract a published version number from CLI output.

    Kaggle prints the created dataset or kernel as a versioned URL, which is
    unambiguous; the "Kernel version N" line is the fallback.

    Args:
        text: The captured output.

    Returns:
        The version number, or None when neither form is present.
    """
    match = _VERSION_URL.search(text)
    if match:
        return int(match.group(1))
    match = _KERNEL_VERSION_LINE.search(text)
    return int(match.group(1)) if match else None


def _parse_quota(text: str, resource: str = "GPU") -> float | None:
    """Extract a GPU quota figure from ``kaggle quota`` output.

    ``kaggle quota`` on CLI 2.2.4 prints a **fixed-width table**, not JSON::

        resource  used   remaining  total   refreshAt
        GPU       0.00h  30.00h     30.00h  2026-10-10T00:00:00
        TPU       0.00h  20.00h     20.00h  2026-10-10T00:00:00

    Reading the first number out of that text yields ``0.00`` — the *used*
    column — and reporting it as a quota would assert the account has spent its
    whole allowance, which is the exact opposite of what the table says. So the
    header is used to locate the columns and the ``GPU`` row's ``remaining``
    column is read by position. A format this parser does not recognise yields
    None rather than a guess.

    Args:
        text: The captured output.
        resource: The row to read. ``GPU`` is the only accelerator Phase 10
            requests, so ``TPU`` is deliberately ignored.

    Returns:
        A non-negative number of hours, or None when nothing parsed.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        columns = _table_columns(line)
        names = [name for name, _, _ in columns]
        if "resource" not in names or "remaining" not in names:
            continue
        for row in lines[index + 1 :]:
            if not row.strip() or row.lstrip().startswith("-"):
                continue
            if row.strip().split()[0] != resource:
                continue
            return _hours(_cell(row, columns, "remaining") or _cell(row, columns, "total"))
        return None
    return None


def _table_columns(header: str) -> list[tuple[str, int, int]]:
    """Locate a fixed-width table's columns from its header row.

    Args:
        header: The header line.

    Returns:
        One ``(name, start, end)`` triple per column, in order.
    """
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", header)]


def _cell(row: str, columns: Sequence[tuple[str, int, int]], name: str) -> str:
    """Read one named cell out of a fixed-width table row.

    Args:
        row: The data row.
        columns: The column triples from :func:`_table_columns`.
        name: The column to read.

    Returns:
        The trimmed cell, or an empty string when the column is absent.
    """
    for column_name, start, end in columns:
        if column_name == name:
            return row[start:end].strip()
    return ""


def _hours(value: str) -> float | None:
    """Parse a ``30.00h`` style figure.

    Args:
        value: The cell text.

    Returns:
        The float, or None when the cell is not a number of hours.
    """
    match = _HOURS_VALUE.fullmatch(value.strip())
    return float(match.group(1)) if match else None


def _read_metadata_id(metadata_path: Path) -> str:
    """Read the ``id`` field from a ``kernel-metadata.json``.

    Args:
        metadata_path: Path to the metadata file.

    Returns:
        The declared kernel ref.

    Raises:
        RemoteError: The file is unreadable, is not JSON, or declares no
            ``<username>/<slug>`` id.
    """
    try:
        raw = metadata_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RemoteError(
            command=_format_command(["read", str(metadata_path)]),
            returncode=None,
            stderr=redact(f"cannot read kernel metadata: {exc}"),
        ) from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RemoteError(
            command=_format_command(["read", str(metadata_path)]),
            returncode=None,
            stderr=f"kernel metadata is not valid JSON: {exc}",
        ) from exc
    ref = str(decoded.get("id") or "") if isinstance(decoded, dict) else ""
    if "/" not in ref:
        raise RemoteError(
            command=_format_command(["read", str(metadata_path)]),
            returncode=None,
            stderr="kernel metadata declares no '<username>/<slug>' id",
        )
    return ref


def _kernel_ref_from_output(ref: str, combined: str) -> KernelRef:
    """Build a :class:`KernelRef` from a push's output.

    Args:
        ref: The declared ref from the metadata file.
        combined: Captured stdout and stderr of the push.

    Returns:
        The ref with the version the CLI reported, defaulting to 1 when it
        reported a success without a number.

    Raises:
        RemoteError: The push succeeded but named no version, which means the
            push and the ref in the metadata disagree about what happened.
    """
    version = _parse_version_number(combined)
    if version is None:
        if "success" not in combined.casefold():
            raise RemoteError(
                command=_format_command(["kaggle", "kernels", "push"]),
                returncode=0,
                stderr=f"push reported no version for {ref}: {redact(combined.strip())}",
            )
        version = 1
    url_match = re.search(r"https://www\.kaggle\.com/\S+", combined)
    url = url_match.group(0) if url_match else f"https://www.kaggle.com/code/{ref}"
    return KernelRef(ref=ref, version=version, url=url)


def _artifact_dir() -> Path:
    """Where downloaded kernel output is expected to live.

    Returns:
        The directory named by ``NEXO_REMOTE_ARTIFACT_DIR``, or ``artifacts/
        remote`` under the working directory.
    """
    configured = os.environ.get(_ARTIFACT_DIR_ENV, "").strip()
    return Path(configured) if configured else Path("artifacts") / "remote"


def _probe_max_age_seconds() -> float:
    """The freshness window for a GPU probe.

    Returns:
        ``NEXO_GPU_PROBE_MAX_AGE_SECONDS`` as a float, or the default window.
    """
    raw = os.environ.get(_PROBE_MAX_AGE_ENV, "").strip()
    if not raw:
        return _PROBE_MAX_AGE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _PROBE_MAX_AGE_SECONDS
    return value if value >= 0 else _PROBE_MAX_AGE_SECONDS


def _read_gpu_probe(path: Path) -> dict[str, Any] | None:
    """Read the probe file a remote kernel wrote.

    Args:
        path: Candidate probe file.

    Returns:
        The decoded object, or None when it is absent or unusable.
    """
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _probe_age_seconds(probe: dict[str, Any]) -> float | None:
    """How long ago a probe was recorded.

    Args:
        probe: The decoded probe object.

    Returns:
        Age in seconds, or None when ``recorded_at`` is missing or unparseable.
        A missing timestamp makes the probe unusable rather than infinitely old:
        without one there is no way to apply the freshness window honestly.
    """
    recorded = str(probe.get("recorded_at") or "").strip()
    if not recorded:
        return None
    try:
        stamp = datetime.strptime(recorded, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return max(0.0, (datetime.now(UTC) - stamp).total_seconds())


__all__ = [
    "GPU_PROBE_FILENAME",
    "KAGGLE_REF_PREFIX",
    "KERNEL_STATES",
    "TERMINAL_STATES",
    "DatasetRef",
    "KaggleClient",
    "KaggleEnvironment",
    "KernelRef",
    "KernelStatus",
    "RemoteError",
    "render_kernel_metadata",
    "slugify",
    "verify_gpu_available",
]
