"""Typed training configuration for the Phase 10 pipeline.

Every hyperparameter in `ml/configs` was derived from the size of Nexo's own
datasets and the accelerator actually available, which is precisely why it
lives in version-controlled TOML with the reasoning next to the number. That
makes the *validators* part of the deliverable: they are what turns an edited
TOML into a loud failure at load time rather than a CUDA OOM three hours into a
Kaggle run, or a silently wrong training run that nobody notices.

The rules being protected here are the ones a config edit breaks quietly:

* the routing head has exactly `len(INTENT_NAMES)` outputs — an intent added to
  the taxonomy without a head slot trains a model that cannot predict it;
* the 8B fine-tune stays 4-bit QLoRA, because full fine-tuning does not fit in
  24 GB of VRAM and the brief rules it out;
* the effective batch (micro-batch x accumulation) stays at or above 8, because
  a config that quietly drops to 2 trains, and trains badly, without saying so.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ml.datasets.taxonomy import INTENT_NAMES
from ml.training.config import (
    DEFAULT_SEED,
    LORA_QUANT_TYPES,
    MIN_EFFECTIVE_BATCH_SIZE,
    QWEN_METHOD,
    WEIGHTING_STRATEGIES,
    PipelineConfig,
    QwenConfig,
    SmallModelConfig,
    load_config,
)

#: The shipped configuration directory.
CONFIG_DIR = Path(__file__).resolve().parents[1] / "ml" / "configs"

#: The QLoRA memory arithmetic the Qwen config is sized against.
QWEN_BASE_MODEL = "Qwen/Qwen3-8B"
ROUTER_BASE_MODEL = "microsoft/deberta-v3-base"


@pytest.fixture(scope="module")
def config() -> PipelineConfig:
    return load_config(CONFIG_DIR)


def test_the_shipped_configuration_loads(config):
    assert isinstance(config, PipelineConfig)
    assert isinstance(config.small_model, SmallModelConfig)
    assert isinstance(config.qwen, QwenConfig)


def test_the_head_is_sized_from_the_taxonomy_not_from_a_literal(config):
    """Adding an intent without a head slot trains a model that cannot predict it."""
    assert config.small_model.num_labels == len(INTENT_NAMES)
    assert config.small_model.num_labels == 14


def test_the_router_checkpoint_matches_its_declared_size(config):
    assert config.small_model.base_model == ROUTER_BASE_MODEL
    low, high = config.small_model.parameter_count_band
    assert low <= config.small_model.parameter_count <= high
    assert low == 100_000_000
    assert high == 300_000_000


def test_the_sequence_length_is_bounded_for_an_encoder(config):
    assert 32 <= config.small_model.max_seq_length <= 512
    assert config.small_model.max_seq_length == 128


def test_the_reasoning_model_is_configured_as_four_bit_qlora(config):
    assert config.qwen.method == QWEN_METHOD
    assert config.qwen.load_in_4bit is True
    assert config.qwen.base_model == QWEN_BASE_MODEL
    assert config.qwen.bnb_quant_type in LORA_QUANT_TYPES
    assert config.qwen.gradient_checkpointing is True
    assert config.qwen.bnb_compute_dtype == "bfloat16"


def test_the_optimizer_sees_a_wide_enough_batch_to_learn_from(config):
    assert config.qwen.effective_batch_size == (
        config.qwen.per_device_train_batch_size * config.qwen.gradient_accumulation_steps
    )
    assert config.qwen.effective_batch_size >= MIN_EFFECTIVE_BATCH_SIZE
    assert config.qwen.per_device_train_batch_size == 1


def test_the_adapter_targets_every_projection_including_the_mlp(config):
    assert set(config.qwen.lora_target_modules) == {
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    }
    assert config.qwen.lora_alpha == config.qwen.lora_r * 2


def test_one_seed_is_shared_by_the_whole_pipeline(config):
    assert config.seed == DEFAULT_SEED
    assert config.small_model.seed == config.seed
    assert config.qwen.seed == config.seed


def test_the_pipeline_paths_default_to_the_documented_locations(config):
    assert config.datasets_dir == Path("ml/datasets")
    assert config.artifacts_dir == Path("ml/artifacts")
    assert config.reports_dir == Path("ml/reports")


def test_the_weighting_strategy_is_one_the_classifier_understands(config):
    assert config.small_model.weighting_strategy in WEIGHTING_STRATEGIES


def test_loading_is_deterministic(config):
    assert load_config(CONFIG_DIR).small_model == config.small_model
    assert load_config(CONFIG_DIR).qwen == config.qwen


def test_a_missing_configuration_directory_is_reported():
    with pytest.raises(OSError):
        load_config(CONFIG_DIR / "no-such-directory")


def test_an_out_of_band_parameter_count_is_refused():
    """A 3B router is not a router any more; a 50M one cannot hold the embedding table."""
    with pytest.raises(ValidationError, match="falls outside the required band"):
        SmallModelConfig(num_labels=14, parameter_count=3_000_000_000)

    with pytest.raises(ValidationError, match="falls outside the required band"):
        SmallModelConfig(num_labels=14, parameter_count=50_000_000)


def test_an_inverted_parameter_band_is_refused():
    with pytest.raises(ValidationError, match="parameter_count_band is inverted"):
        SmallModelConfig(
            num_labels=14, parameter_count=183_000_000, parameter_count_band=(300_000_000, 1)
        )


def test_a_single_label_head_is_refused():
    with pytest.raises(ValidationError, match="num_labels must be at least 2"):
        SmallModelConfig(num_labels=1)


@pytest.mark.parametrize("length", [16, 1024])
def test_a_sequence_length_outside_the_encoder_range_is_refused(length):
    with pytest.raises(ValidationError, match="max_seq_length must be between 32 and 512"):
        SmallModelConfig(num_labels=14, max_seq_length=length)


@pytest.mark.parametrize("rate", [0.0, -1e-5])
def test_a_non_positive_learning_rate_is_refused(rate):
    with pytest.raises(ValidationError, match="learning_rate must be positive"):
        SmallModelConfig(num_labels=14, learning_rate=rate)

    with pytest.raises(ValidationError, match="learning_rate must be positive"):
        QwenConfig(learning_rate=rate)


def test_an_unrecognised_weighting_strategy_is_refused():
    with pytest.raises(ValidationError, match="weighting_strategy must be one of"):
        SmallModelConfig(num_labels=14, weighting_strategy="inverse-sqrt")


def test_a_typo_in_a_config_key_is_refused_rather_than_defaulted():
    """`max_seq_lenght` would otherwise leave the default in place and misreport the run."""
    with pytest.raises(ValidationError):
        SmallModelConfig(num_labels=14, max_seq_lenght=256)


def test_full_fine_tuning_of_the_eight_billion_parameter_model_is_refused():
    with pytest.raises(ValidationError, match="method must be 'qlora'"):
        QwenConfig(method="full")


def test_turning_off_four_bit_quantisation_is_refused():
    """Unquantised 8B does not fit on the only GPU this project has."""
    with pytest.raises(ValidationError, match="load_in_4bit must be True"):
        QwenConfig(load_in_4bit=False)


def test_an_effective_batch_below_eight_is_refused():
    with pytest.raises(ValidationError, match="below the required minimum"):
        QwenConfig(per_device_train_batch_size=1, gradient_accumulation_steps=4)

    with pytest.raises(ValidationError, match="below the required minimum"):
        QwenConfig(per_device_train_batch_size=4, gradient_accumulation_steps=1)


def test_the_effective_batch_boundary_is_accepted():
    config = QwenConfig(per_device_train_batch_size=2, gradient_accumulation_steps=4)

    assert config.effective_batch_size == MIN_EFFECTIVE_BATCH_SIZE


@pytest.mark.parametrize("rank", [2, 256])
def test_a_lora_rank_outside_its_usable_range_is_refused(rank):
    with pytest.raises(ValidationError, match="lora_r must be between 4 and 128"):
        QwenConfig(lora_r=rank)


@pytest.mark.parametrize("length", [128, 16384])
def test_a_reasoning_context_outside_its_usable_range_is_refused(length):
    with pytest.raises(ValidationError, match="max_seq_length must be between 256 and 8192"):
        QwenConfig(max_seq_length=length)


def test_an_unknown_quantisation_type_is_refused():
    with pytest.raises(ValidationError, match="bnb_quant_type must be one of"):
        QwenConfig(bnb_quant_type="int8")


def test_a_non_positive_lora_alpha_is_refused():
    with pytest.raises(ValidationError, match="lora_alpha must be positive"):
        QwenConfig(lora_alpha=0)


def test_a_zero_segment_length_is_refused():
    """The field constraint catches it before the model validator ever runs."""
    with pytest.raises(ValidationError, match="segment_steps"):
        QwenConfig(segment_steps=0)


def test_a_batch_size_below_one_is_refused_by_the_field_constraint():
    with pytest.raises(ValidationError):
        SmallModelConfig(num_labels=14, per_device_train_batch_size=0)


def test_a_negative_seed_is_refused():
    with pytest.raises(ValidationError):
        PipelineConfig(
            small_model=SmallModelConfig(num_labels=14),
            qwen=QwenConfig(),
            seed=-1,
        )
