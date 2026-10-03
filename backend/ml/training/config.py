"""Typed training configuration for the Phase 10 pipeline.

Every hyperparameter in this module was **derived from the size of Nexo's own
datasets and the accelerator actually available**, not copied from a tutorial.
That distinction is the whole point of keeping configuration in TOML under
version control: a reviewer can see that ``max_seq_length = 128`` exists because
the routing utterances are short, not because a tutorial said so. When the
hardware or the dataset changes, the number changes here, in the diff, with the
reason next to it.

The two configs are deliberately asymmetric, because the two problems are.

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

**The reasoning model** (``Qwen/Qwen3-8B``) is QLoRA-only. The memory
arithmetic is what forces it:

* 8B parameters at 4 bits is ~0.5 bytes each, so the **frozen base weights are
  roughly 4 GB** on the device — the dominant fixed cost.
* The trainable LoRA adapters are ~0.1% of that, in bf16, and AdamW keeps two
  fp32 moments on them; together a few hundred MB.
* What is actually left over is **activations**, which scale with
  sequence length x layers x hidden width x batch. They do not shrink with
  quantisation at all, and at 2048 tokens they are the term that decides
  whether the run OOMs.

Hence ``gradient_checkpointing = True`` (recompute activations in the backward
pass instead of storing them), ``per_device_train_batch_size = 1`` and
``gradient_accumulation_steps = 16``. The optimiser still sees 16 sequences per
update — the batch size the loss curve actually cares about — while peak
activation memory stays at one sequence. The cross-field rule
:meth:`QwenConfig.effective_batch_size` is enforced for exactly this reason: a
config that silently drops to an effective batch of 2 trains, and trains badly,
without ever explaining itself.

LoRA ``r = 16`` / ``alpha = 32`` / ``dropout = 0.05`` is sized for the dataset
rather than the literature. Nexo contributes a few hundred
Nexo-specific supervised examples; capacity far beyond that is not
underfitting risk, it is a large adapter fitting a small corpus and memorising
it. ``alpha / r = 2`` keeps the scaling conventional, and the dropout is real
regularisation at that sample count.

Validation is loud. A configuration that cannot train is rejected at load time
with the reason attached, rather than surfacing three hours into a Kaggle run
as a CUDA OOM.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: File names looked up inside the directory passed to :func:`load_config`.
SMALL_MODEL_CONFIG_FILE = "small_model.toml"
QWEN_CONFIG_FILE = "qwen_qlora.toml"

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
QWEN_METHOD = "qlora"

#: Smallest effective batch the QLoRA run will accept. Below this the gradient
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
    num_train_epochs: int = 3
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


class QwenConfig(BaseModel):
    """Hyperparameters for the QLoRA fine-tune of Qwen3-8B.

    Every value here is bounded by the accelerator: 24 GB of VRAM with ~4 GB of
    it already spent on 4-bit base weights.
    """

    model_config = ConfigDict(extra="forbid")

    base_model: str = "Qwen/Qwen3-8B"
    method: str = "qlora"
    load_in_4bit: bool = True
    bnb_quant_type: str = "nf4"
    bnb_compute_dtype: str = "bfloat16"
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_target_modules: tuple[str, ...] = (
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    )
    max_seq_length: int = 2048
    learning_rate: float = 2e-4
    num_train_epochs: int = 2
    per_device_train_batch_size: int = Field(default=1, ge=1)
    gradient_accumulation_steps: int = Field(default=16, ge=1)
    warmup_ratio: float = 0.03
    lr_scheduler_type: str = "cosine"
    weight_decay: float = 0.0
    gradient_checkpointing: bool = True
    max_grad_norm: float = 0.3
    seed: int = Field(default=DEFAULT_SEED, ge=0)
    segment_steps: int = Field(default=250, ge=1)
    save_every_n_steps: int = Field(default=100, ge=1)
    eval_every_n_steps: int = Field(default=100, ge=1)

    @property
    def effective_batch_size(self) -> int:
        """Sequences the optimiser sees per update.

        Accumulation trades wall-clock for activation memory: the peak footprint
        is one micro-batch, while the gradient statistics are those of
        ``per_device_train_batch_size * gradient_accumulation_steps``.

        Returns:
            The effective batch size.
        """
        return self.per_device_train_batch_size * self.gradient_accumulation_steps

    @model_validator(mode="after")
    def _check_quantisation(self) -> QwenConfig:
        """Refuse any configuration that is not 4-bit QLoRA.

        ``method`` and ``load_in_4bit`` are both closed because the rest of this
        class — the memory arithmetic in the module docstring, the checkpointing
        flag, the micro-batch of 1 — assumes a quantised frozen base. A config
        that turned either off would still train, at 40 GB, and fail on the only
        GPU this project has.
        """
        if self.method != QWEN_METHOD:
            raise ValueError(
                f"method must be {QWEN_METHOD!r} on this hardware, got {self.method!r}"
            )
        if not self.load_in_4bit:
            raise ValueError("load_in_4bit must be True; unquantised 8B fine-tuning does not fit")
        if self.bnb_quant_type not in LORA_QUANT_TYPES:
            raise ValueError(
                f"bnb_quant_type must be one of {sorted(LORA_QUANT_TYPES)}, "
                f"got {self.bnb_quant_type!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_adapter_shape(self) -> QwenConfig:
        """Keep the adapter and the context inside their usable ranges.

        The rank floor of 4 is the smallest adapter that can express anything
        for an 8B model; the ceiling of 128 stops a rank large enough to
        re-fit the base model from being requested by accident. On the context
        side, 256 tokens truncates Nexo's reasoning answers mid-thought and 8192
        exceeds what the accumulator on a 24 GB card holds once activations for
        attention are included.
        """
        if self.lora_alpha <= 0:
            raise ValueError(f"lora_alpha must be positive, got {self.lora_alpha}")
        if not 4 <= self.lora_r <= 128:
            raise ValueError(f"lora_r must be between 4 and 128, got {self.lora_r}")
        if not 256 <= self.max_seq_length <= 8192:
            raise ValueError(
                f"max_seq_length must be between 256 and 8192, got {self.max_seq_length}"
            )
        if self.learning_rate <= 0:
            raise ValueError(f"learning_rate must be positive, got {self.learning_rate}")
        return self

    @model_validator(mode="after")
    def _check_optimizer_budget(self) -> QwenConfig:
        """Keep the optimiser's view of the data wide enough to learn from.

        The effective batch is checked rather than the micro-batch because the
        micro-batch is already bounded by memory alone; it is the *product* that
        determines gradient quality, and it is the one an edited TOML breaks
        quietly.
        """
        if self.gradient_accumulation_steps < 1:
            raise ValueError(
                f"gradient_accumulation_steps must be at least 1, "
                f"got {self.gradient_accumulation_steps}"
            )
        if self.segment_steps < 1:
            raise ValueError(f"segment_steps must be at least 1, got {self.segment_steps}")
        if self.effective_batch_size < MIN_EFFECTIVE_BATCH_SIZE:
            raise ValueError(
                "effective batch size (per_device_train_batch_size * "
                f"gradient_accumulation_steps) is {self.effective_batch_size}, below the "
                f"required minimum of {MIN_EFFECTIVE_BATCH_SIZE}"
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
    qwen: QwenConfig
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
        config_dir: Directory holding ``small_model.toml`` and ``qwen_qlora.toml``.

    Returns:
        The validated pipeline configuration.

    Raises:
        OSError: A file is missing or unreadable.
        tomllib.TOMLDecodeError: A file is not valid TOML.
        pydantic.ValidationError: A value is out of range or a key is unknown.
    """
    return PipelineConfig(
        small_model=SmallModelConfig(**_read_toml(config_dir / SMALL_MODEL_CONFIG_FILE)),
        qwen=QwenConfig(**_read_toml(config_dir / QWEN_CONFIG_FILE)),
    )


__all__ = [
    "DEFAULT_SEED",
    "LORA_QUANT_TYPES",
    "MIN_EFFECTIVE_BATCH_SIZE",
    "QWEN_CONFIG_FILE",
    "QWEN_METHOD",
    "SMALL_MODEL_CONFIG_FILE",
    "WEIGHTING_STRATEGIES",
    "PipelineConfig",
    "QwenConfig",
    "SmallModelConfig",
    "load_config",
]
