"""Production lock (B7), safe modes and CLI exit codes (C5, C11). Nothing here may load or train Qwen3-8B."""
import subprocess
import sys

import pytest

from test_train_common import PROD_CFG, SMOKE_CFG, WORKSPACE, C, T


def _run_cli(*args, env_extra=None):
    import os

    env = dict(os.environ)
    env.pop(T.UNLOCK_ENV, None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(WORKSPACE / "pipeline" / "train" / "train.py"), *args], capture_output=True, text=True, env=env, timeout=300)


def test_import_has_no_side_effects():
    code = ("import sys; sys.path.insert(0, r'%s'); import pipeline.train.train as t; "
            "print('bitsandbytes' in sys.modules, 'transformers' in sys.modules)" % WORKSPACE)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[-2:] == ["False", "False"], r.stdout          # importing loads no model library at all


def test_production_refused_by_default(capsys):
    rc = T.main(["--config", str(PROD_CFG)])
    err = capsys.readouterr().err
    assert rc == T.EXIT["locked"]
    assert "LOCKED" in err and T.UNLOCK_ENV in err and "--confirm-production" in err and "production_locked: false" in err


def test_each_unlock_condition_is_required(monkeypatch, capsys):
    cfg = C.load_config(PROD_CFG, ["production_locked=false"])
    good_env = {T.UNLOCK_ENV: T.UNLOCK_VALUE}
    T.check_production_unlock(cfg, True, good_env)                          # all three satisfied -> no exception (check only; nothing is started)
    with pytest.raises(T.ProductionLocked, match="--confirm-production"):
        T.check_production_unlock(cfg, False, good_env)
    with pytest.raises(T.ProductionLocked, match=T.UNLOCK_ENV):
        T.check_production_unlock(cfg, True, {})
    with pytest.raises(T.ProductionLocked, match=T.UNLOCK_ENV):
        T.check_production_unlock(cfg, True, {T.UNLOCK_ENV: "yes"})
    with pytest.raises(T.ProductionLocked, match="production_locked"):
        T.check_production_unlock(C.load_config(PROD_CFG), True, good_env)
    with pytest.raises(T.ProductionLocked, match="mode: production"):
        T.check_production_unlock(C.load_config(SMOKE_CFG, ["production_locked=false"]), True, good_env)


def test_cli_refuses_even_with_partial_unlock(monkeypatch):
    monkeypatch.delenv(T.UNLOCK_ENV, raising=False)
    assert T.main(["--config", str(PROD_CFG), "--confirm-production"]) == T.EXIT["locked"]
    assert T.main(["--config", str(PROD_CFG), "--confirm-production", "--set", "production_locked=false"]) == T.EXIT["locked"]
    monkeypatch.setenv(T.UNLOCK_ENV, T.UNLOCK_VALUE)
    assert T.main(["--config", str(PROD_CFG)]) == T.EXIT["locked"]                       # env alone is not enough
    assert T.main(["--config", str(PROD_CFG), "--confirm-production"]) == T.EXIT["locked"]   # config still locked


def test_production_model_loader_requires_unlock():
    cfg = C.load_config(PROD_CFG)
    with pytest.raises(T.ProductionLocked):
        T.load_production_model(cfg, unlocked=False)


def test_validate_config_cli_codes(tmp_path):
    r = _run_cli("--config", str(SMOKE_CFG), "--validate-config")
    assert r.returncode == 0 and "CONFIG VALID" in r.stdout and "NOT QWEN PRODUCTION TRAINING" in r.stdout
    bad = tmp_path / "bad.yaml"
    bad.write_text("mode: [", encoding="utf-8")
    r = _run_cli("--config", str(bad), "--validate-config")
    assert r.returncode == T.EXIT["config"] and "CONFIG ERROR" in r.stderr
    r = _run_cli("--config", str(SMOKE_CFG), "--validate-config", "--set", "train.learning_rate=-3")
    assert r.returncode == T.EXIT["config"] and "learning_rate" in r.stderr
    r = _run_cli("--config", str(tmp_path / "missing.yaml"), "--validate-config")
    assert r.returncode == T.EXIT["config"]


def test_cli_production_locked_subprocess():
    r = _run_cli("--config", str(PROD_CFG))
    assert r.returncode == T.EXIT["locked"] and "REFUSED" in r.stderr


def test_safe_modes_are_mutually_exclusive():
    r = _run_cli("--dry-run", "--smoke-test")
    assert r.returncode != 0


def test_dry_run_never_loads_a_model(monkeypatch):
    import transformers

    def boom(*a, **k):
        raise AssertionError("model construction attempted during dry-run")

    monkeypatch.setattr(transformers.AutoModelForCausalLM, "from_pretrained", boom)
    monkeypatch.setattr(transformers.AutoModelForCausalLM, "from_config", boom)
    monkeypatch.setattr(T, "build_tiny_model", boom)
    monkeypatch.setattr(T, "load_production_model", boom)
    cfg = C.load_config(SMOKE_CFG)
    out = T.cmd_dry_run(cfg, sample=24)
    assert out["model_loaded"] is False and out["dataset"]["train_samples_used"] > 0
    assert out["batch_shape"][0] == cfg.train.per_device_batch
    assert "ESTIMATED" in " ".join(out) .upper() or any("ESTIMATED" in k for k in out)


def test_dry_run_reports_missing_dataset(tmp_path, capsys):
    rc = T.main(["--config", str(SMOKE_CFG), "--dry-run", "--set", f"data.train_path={tmp_path / 'gone.jsonl'}"])
    assert rc == T.EXIT["config"] and "train_path not found" in capsys.readouterr().err


def test_smoke_refuses_production_config(capsys):
    rc = T.main(["--smoke-test", "--config", str(PROD_CFG)])
    assert rc == T.EXIT["config"]
    assert "smoke" in capsys.readouterr().err.lower()
