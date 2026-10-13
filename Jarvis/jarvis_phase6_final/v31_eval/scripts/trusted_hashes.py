"""Trusted expected hashes and the pre-generation integrity gates (pure stdlib, no model).

Provenance of every constant (none is computed from a Kaggle input during a run):
  * ADAPTERS: E:\\Nexo\\Jarvis\\CHECKPOINT_SHA256SUMS.txt == manifests/EVALUATION_MANIFEST.pre_step1.json `checkpoints.adapter_sha256`,
    each verified against its own checkpoint_meta.json (PHASE_B/PHASE_C/FINALIZATION reports; sha256sum -c 35/35 on 2026-10-11).
  * PACKAGE_MANIFEST_SHA256: PACKAGE_MANIFEST.json inside qwen_jarvis_kaggle_package.zip (zip sha256 944eab01..., equal to
    kaggle/dist/package_sha256.txt); the copy in Kaggle dataset utkarsh6624/qwen-jarvis-phase5-package was byte-identical on 2026-10-11.
selftest_phase6.py re-derives all of them from those local sources, so a typo here fails the self-test."""
import hashlib
import json
from pathlib import Path

STEPS = [800, 1100, 1200, 1300, 1400, 1500, 1596]
ADAPTERS = {   # step -> {file relative to checkpoint-<step>/adapter: sha256}
    800: {"adapter_model.safetensors": "41ee6d98ded59aec8ed641cd294bca0110ca3703358fbad003def9cd4eeed1fc",
          "adapter_config.json": "c9a0eda987ace5085658eaa80f253a0ccf31beb1c03f39be9545053b1f0e9d64"},
    1100: {"adapter_model.safetensors": "35b8707b6b2dfcbfa1862751d1ce56ea4499ce939bcec4f7345ea5b0e62beebe",
           "adapter_config.json": "c9a0eda987ace5085658eaa80f253a0ccf31beb1c03f39be9545053b1f0e9d64"},
    1200: {"adapter_model.safetensors": "8c5881d4d9a4202a85a257c1d64c30f4ef078a3fe9026e92e0fecd856243ea2b",
           "adapter_config.json": "c9a0eda987ace5085658eaa80f253a0ccf31beb1c03f39be9545053b1f0e9d64"},
    1300: {"adapter_model.safetensors": "d71f9a974837cdbaf4ade94e779ed15bfeee67bb215b77c14db9333926ef570c",
           "adapter_config.json": "f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0"},
    1400: {"adapter_model.safetensors": "8ea48e39cfc975fdad23ae8dc2bfc2ba19af9b574be0600b633460f4eb7e34fe",
           "adapter_config.json": "f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0"},
    1500: {"adapter_model.safetensors": "b9d16c934d689952da179c4b8b3dbdfb104172ce2e95a46ecad685d48c865e9a",
           "adapter_config.json": "f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0"},
    1596: {"adapter_model.safetensors": "162b97f6007e76a147833c126b11f5146034c1315c08024414464b1bd4564dc8",
           "adapter_config.json": "f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0"},
}
V3_STEP = 139           # filled after the V3.1 training run: the evaluated checkpoint step of run qwen3_8b_jarvis_v3_1_remediation
V3_ADAPTER = {"adapter_model.safetensors": "d754a3c6b77fb80bf24e67527d5b32f04606d8496e9e7c2b9d7cccef48f48fa8",
              "adapter_config.json": "d40a7049e7c3ce987cbe585190c45abfa0d9a60cfd850dc316de7b11b261efab"}  # {file: sha256} of that checkpoint's adapter, hashed locally from the downloaded training output
V3_RUN_DIR = "qwen3_8b_jarvis_v3_1_remediation"
V3_PREFIX = "v31_"
if V3_STEP is not None:
    STEPS = STEPS + [f"{V3_PREFIX}{V3_STEP}"]
    ADAPTERS[f"{V3_PREFIX}{V3_STEP}"] = V3_ADAPTER
PACKAGE_MANIFEST_SHA256 = "888b18ba9ea2f684d7eaa14a71c5500765d5dc34e0616ff931f0ebdf7c6005a0"


class IntegrityError(SystemExit):
    """Raised (exit) before any model is loaded or any token is generated."""


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def verify_package(code_dir):
    """Checks PACKAGE_MANIFEST.json against the trusted constant, then every file it lists (sha256 + bytes). Returns a report dict."""
    code_dir = Path(code_dir)
    mp = code_dir / "PACKAGE_MANIFEST.json"
    if not mp.is_file():
        raise IntegrityError(f"INTEGRITY ABORT: {mp} missing")
    got = sha256_file(mp)
    if got != PACKAGE_MANIFEST_SHA256:
        raise IntegrityError(f"INTEGRITY ABORT: PACKAGE_MANIFEST.json sha256 {got} != trusted {PACKAGE_MANIFEST_SHA256}")
    files = json.loads(mp.read_text(encoding="utf-8"))["files"]
    bad = []
    for f in files:
        p = code_dir / f["path"]
        if not p.is_file():
            bad.append(f"missing {f['path']}")
        elif p.stat().st_size != f["bytes"] or sha256_file(p) != f["sha256"]:
            bad.append(f"mismatch {f['path']}")
    if bad:
        raise IntegrityError(f"INTEGRITY ABORT: package files differ from the trusted manifest: {bad[:5]} ({len(bad)} total)")
    return {"package_manifest_sha256": got, "files_verified": len(files), "status": "PASS"}


def find_adapters(roots, steps=STEPS):
    """For each step, search every root for checkpoint-<step>/adapter whose files match the trusted hashes. A candidate with a different hash
    is never used; if no candidate matches, abort. checkpoint-1200 exists in two Kaggle datasets: any copy with the trusted hash is accepted."""
    roots = [Path(r) for r in roots]
    chosen, report = {}, {}
    for s in steps:
        if str(s).startswith(V3_PREFIX):    # the V3.1 adapter lives in the V3.1 training run directory only
            cands = sorted({d for r in roots for d in r.rglob(f"checkpoint-{str(s)[len(V3_PREFIX):]}") if (d / "adapter").is_dir() and d.parent.name == V3_RUN_DIR})
        else:
            cands = sorted({d for r in roots for d in r.rglob(f"checkpoint-{s}") if (d / "adapter").is_dir() and d.parent.name != V3_RUN_DIR})
        tried = []
        for d in cands:
            got = {f: sha256_file(d / "adapter" / f) if (d / "adapter" / f).is_file() else None for f in ADAPTERS[s]}
            ok = got == ADAPTERS[s]
            tried.append({"path": str(d), "match": ok, "sha256": got})
            if ok:
                chosen[s] = d / "adapter"
                break
        report[f"checkpoint_{s}"] = {"candidates": tried, "chosen": str(chosen.get(s)), "status": "PASS" if s in chosen else "FAIL"}
        if s not in chosen:
            raise IntegrityError(f"INTEGRITY ABORT: no checkpoint-{s}/adapter with the trusted sha256 under {[str(r) for r in roots]}: {tried}")
    return chosen, report
