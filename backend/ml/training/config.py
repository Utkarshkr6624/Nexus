"""Typed training configuration for the Phase 10 pipeline.

Every hyperparameter in this module was **derived from the size of Nexo's own
datasets and the accelerator actually available**, not copied from a tutorial.
That distinction is the whole point of keeping configuration in TOML under
version control: a reviewer can see that ``max_seq_length = 128`` exists because
the routing utterances are short, not because a tutorial said so. When the
hardware or the dataset changes, the number changes here, in the diff, with the
reason next to it.

NEXUS runs exactly one trained model, and this file is its configuration.

**The routing classifier** (``deberta-v3-base``, ~183M parameters) learns one
thing: which of the ``RecommendationType`` members a short utterance implies.
Every member of that enum names an action *a person takes* — NEXUS routes and
advises, it never auto-executes — so the label set is small and the inputs are
utterances like *"I am drowning in work this week"*. ``max_seq_length = 128``
covers that comfortably (the longest templates in the intent taxonomy run to
roughly 40 subword tokens); anything beyond it is padding that buys no signal
and costs quadratic attention. 183M parameters sits inside the brief's
100M-300M band, and that band is enforced rather than asserted: see
``parameter_count_band``.

"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: File names looked up inside the directory passed to :func:`load_config`.
SMALL_MODEL_CONFIG_FILE = "small_model.toml"

#: Loss-weighting strategies the classifier understands. ``balanced`` reweights
#: the loss by inverse class frequency so a rare-but-important intent such as
#: ``complete_blocked_task`` is not drowned out by ``reschedule_task``;
#: ``none`` is the honest choice when the dataset is already balanced by
#: construction, and there is no third option because an unrecognised spelling
#: would otherwise be silently treated as ``none``.
WEIGHTING_STRATEGIES = frozenset({"none", "balanced"})

#: bitsandbytes quantisation types. ``nf4`` is the normal-float variant, which
#: is measurably better than plain ``fp4`` at this model size; ``fp4`` exists
#: for a kernel that lacks NF4 support.
LORA_QUANT_TYPES = frozenset({"nf4", "fp4"})

#: The only fine-tuning method in scope. Full fine-tuning of 8B parameters needs
#: far more than 24 GB of VRAM than the 4-bit base weights alone consume, and
#: the brief rules it out, so the validator rejects anything else rather than
#: letting a run start and die in the allocator.

#: Smallest effective batch accepted. Below this the gradient
#: noise from a few hundred Nexo-specific examples dominates the signal.
MIN_EFFECTIVE_BATCH_SIZE = 8

#: One seed for the whole pipeline, shared by both models and by every split.
#: It is spelled out rather than left to a library default so a run can be
#: reproduced from the manifest alone.
DEFAULT_SEED = 20260101


class SmallModelConfig(BaseModel):
    """Hyperparameters for the routing-intent classifier.

    Bounds such as ``parameter_count_band`` are part of the model rather than a
    note in the TOML, because the brief's 100M-300M target is a constraint on
    *which checkpoint is allowed to serve traffic*, not a description of the
    one that happens to be configured today.
    """

    model_config = ConfigDict(extra="forbid")

    base_model: str = "microsoft/deberta-v3-base"
    num_labels: int
    max_seq_length: int = 128
    learning_rate: float = 2e-5
    weight_decay: float = 0.01
    num_train_epochs: int = 5
    per_device_train_batch_size: int = Field(default=16, ge=1)
    per_device_eval_batch_size: int = Field(default=32, ge=1)
    warmup_ratio: float = 0.1
    weighting_strategy: str = "balanced"
    seed: int = Field(default=DEFAULT_SEED, ge=0)
    gradient_checkpointing: bool = False
    save_every_n_steps: int = Field(default=100, ge=1)
    eval_every_n_steps: int = Field(default=100, ge=1)
    parameter_count: int = 183_000_000
    parameter_count_band: tuple[int, int] = (100_000_000, 300_000_000)

    @model_validator(mode="after")
    def _check_classifier_shape(self) -> SmallModelConfig:
        """Reject shapes the encoder cannot be fitted to.

        ``num_labels`` must be at least 2 because a one-class classifier has
        nothing to learn and would report a meaningless 100% accuracy.
        ``max_seq_length`` is capped at 512, the longest context this encoder
        family serves usefully; beyond that, inputs are silently truncated at
        inference and the training and serving contexts stop agreeing.
        """
        if not self.base_model.strip():
            raise ValueError("base_model must name a checkpoint, not an empty string")
        if self.num_labels < 2:
            raise ValueError(f"num_labels must be at least 2, got {self.num_labels}")
        if not 32 <= self.max_seq_length <= 512:
            raise ValueError(
                "max_seq_length must be between 32 and 512 for an encoder classifier; "
                f"got {self.max_seq_length}"
            )
        if self.learning_rate <= 0:
            raise ValueError(f"learning_rate must be positive, got {self.learning_rate}")
        return self

    @model_validator(mode="after")
    def _check_capacity_band(self) -> SmallModelConfig:
        """Keep the checkpoint inside the brief's size band.

        A router small enough to run in-process and large enough to hold
        ``microsoft/deberta-v3-base``'s 128k-row embedding table in memory. The
        band is data, not a comment, so a swap to a 3B router fails at config
        load rather than after a download.
        """
        low, high = self.parameter_count_band
        if low > high:
            raise ValueError(f"parameter_count_band is inverted: low {low} exceeds high {high}")
        if not low <= self.parameter_count <= high:
            raise ValueError(
                f"parameter_count {self.parameter_count} falls outside the required band "
                f"[{low}, {high}]"
            )
        if self.weighting_strategy not in WEIGHTING_STRATEGIES:
            raise ValueError(
                f"weighting_strategy must be one of {sorted(WEIGHTING_STRATEGIES)}, "
                f"got {self.weighting_strategy!r}"
            )
        return self


class PipelineConfig(BaseModel):
    """A complete Phase 10 run: both models plus the pipeline's own paths.

    The directory fields default to locations relative to ``backend/``, which
    is the working directory every documented command in the project runs from.
    They live here rather than in the TOML files because the TOMLs describe
    *hyperparameters*, and a checkpoint's destination is a property of the
    machine running the pipeline, not of the model.
    """

    model_config = ConfigDict(extra="forbid")

    small_model: SmallModelConfig
    seed: int = Field(default=DEFAULT_SEED, ge=0)
    datasets_dir: Path = Path("ml/datasets")
    artifacts_dir: Path = Path("ml/artifacts")
    reports_dir: Path = Path("ml/reports")


def _read_toml(path: Path) -> dict[str, Any]:
    """Decode one flat TOML table.

    Args:
        path: The file to read.

    Returns:
        The decoded key/value pairs.

    Raises:
        OSError: The file is missing or unreadable.
        tomllib.TOMLDecodeError: The file is not valid TOML.
    """
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(config_dir: Path) -> PipelineConfig:
    """Load both model configs from a directory of TOML files.

    Each file is a **flat** key/value table matching its model exactly. Unknown
    keys are refused rather than ignored: a typo such as ``max_seq_lenght``
    would otherwise leave the default in place and produce a run that trains on
    the wrong context window while the manifest reports a deliberate value.

    Args:
        config_dir: Directory holding ``small_model.toml``.

    Returns:
        The validated pipeline configuration.

    Raises:
        OSError: A file is missing or unreadable.
        tomllib.TOMLDecodeError: A file is not valid TOML.
        pydantic.ValidationError: A value is out of range or a key is unknown.
    """
    return PipelineConfig(
        small_model=SmallModelConfig(**_read_toml(config_dir / SMALL_MODEL_CONFIG_FILE)),
    )


__all__ = [
    "DEFAULT_SEED",
    "LORA_QUANT_TYPES",
    "MIN_EFFECTIVE_BATCH_SIZE",
    "SMALL_MODEL_CONFIG_FILE",
    "WEIGHTING_STRATEGIES",
    "PipelineConfig",
    "SmallModelConfig",
    "load_config",
]
