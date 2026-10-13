"""Builds the Kaggle upload package  kaggle/dist/qwen_jarvis_kaggle_package.zip  (Phase 3 B13).  Stdlib only.

* Deterministic: entries sorted, fixed timestamps (1980-01-01), fixed permissions, fixed compression level -> same bytes for the same inputs.
* Contains: pipeline/, configs/, dataset/v2_final/ (or dataset/v2_clean/ with --allow-placeholder while the final dataset is not frozen),
  tool_protocol/ (schemas, spec, converter), kaggle/ (README, requirements, notebook), selected docs/, PACKAGE_MANIFEST.json (sha256 of every file).
* Excludes: .venv, __pycache__, *.pyc, runs/, _hf_meta/ (tokenizer/config metadata is fetched with the model), reports/, tests/, any .env / key / token file.
* Secret scan: the build FAILS if any included text file matches a credential pattern or any forbidden filename is included.
* It never uploads anything and never contacts Kaggle or Hugging Face.

usage: python kaggle/build_package.py [--allow-placeholder] [--list] [--out PATH]
"""
import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
DIST = WS / "kaggle" / "dist"
ZIP_NAME = "qwen_jarvis_kaggle_package.zip"
FIXED_TIME = (1980, 1, 1, 0, 0, 0)

INCLUDE_DIRS = ["pipeline", "configs", "tool_protocol"]
INCLUDE_KAGGLE_FILES = ["kaggle/README.md", "kaggle/requirements_kaggle.txt", "kaggle/notebook/qwen_jarvis_phase5_training.ipynb"]
INCLUDE_DOCS = ["docs/CONTRACT.md", "docs/TRAINING_DESIGN.md", "docs/MEMORY_PLAN.md", "docs/TOOL_PROTOCOL.md"]     # included only if present
EXCLUDE_PARTS = {".venv", "__pycache__", "runs", "_hf_meta", ".git", ".pytest_cache", "tests", "reports", "dist", ".ipynb_checkpoints"}
# generated validation/report outputs are rewritten by other tools after packaging and would make the hash check fail against the workspace; they are not inputs
GENERATED_NAME_RX = re.compile(r"(?i)(protocol_validation|_validation|_report|_results|_audit)\.(json|md)$")
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".gguf", ".log"}
FORBIDDEN_NAMES = {".env", "kaggle.json", "id_rsa", "id_ed25519", "credentials", "credentials.json", "secrets.json", ".netrc"}
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
MAX_TEXT_BYTES = 40 * 1024 * 1024

SECRET_PATTERNS = [
    ("huggingface token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("openai/anthropic style key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{32,}\b")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("kaggle key assignment", re.compile(r"KAGGLE_KEY\s*[=:]\s*['\"]?[A-Za-z0-9]{16,}")),
    ("generic secret assignment", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[=:]\s*['\"][A-Za-z0-9_\-/+=]{20,}['\"]")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
]


def excluded(rel: Path) -> bool:
    if any(p in EXCLUDE_PARTS for p in rel.parts):
        return True
    return rel.suffix.lower() in EXCLUDE_SUFFIXES


def dataset_dir(allow_placeholder: bool, name_override=None):
    if name_override:                                       # an explicitly named frozen dataset (e.g. v3_final), packaged under its own name
        d = WS / "dataset" / name_override
        if not (d / "train.jsonl").exists():
            raise SystemExit(f"dataset/{name_override}/train.jsonl not found")
        return d, name_override
    # preference order: frozen final, then the final candidate, then the preflight-cleaned set (never the raw phase-2 files)
    for name in ("v2_final", "v2_final_candidate", "v2_clean"):
        d = WS / "dataset" / name
        if (d / "train.jsonl").exists():
            if name != "v2_final":
                print(f"WARNING: dataset/v2_final is not frozen yet; packaging dataset/{name} (it is mounted as dataset/v2_final inside the zip)", file=sys.stderr)
            return d, name
    raise SystemExit("no dataset found: expected dataset/v2_final (or v2_final_candidate / v2_clean) with train.jsonl")


def collect_files(allow_placeholder=False, dataset=None):
    """Returns sorted list of (arcname, Path). arcname is relative to the package root."""
    files = {}

    def add(path: Path, arc: str):
        files[arc.replace("\\", "/")] = path

    for d in INCLUDE_DIRS:
        base = WS / d
        if base.exists():
            for p in sorted(base.rglob("*")):
                if p.is_file() and not excluded(p.relative_to(WS)) and not GENERATED_NAME_RX.search(p.name):
                    add(p, str(p.relative_to(WS)))
    ds, label = dataset_dir(allow_placeholder, dataset)
    mount = dataset or "v2_final"
    for p in sorted(ds.glob("*.jsonl")):
        if p.stem.endswith("_messages_only"):
            continue
        add(p, f"dataset/{mount}/{p.name}")        # always mounted under the name the configs expect
    # The frozen dataset's integrity manifest must be shipped with EXACTLY the name train.py looks for (MANIFEST.json, case preserved):
    # Kaggle runs Linux, where a lower-case copy would silently disable the dataset integrity check.
    on_disk = {q.name for q in ds.iterdir()}                 # exact-case directory listing (Path.exists() is case-insensitive on Windows)
    if "MANIFEST.json" in on_disk:
        add(ds / "MANIFEST.json", f"dataset/{mount}/MANIFEST.json")
    for f in INCLUDE_KAGGLE_FILES + INCLUDE_DOCS:
        p = WS / f
        if p.exists():
            add(p, f)
    return sorted(files.items()), label


def scan(files):
    problems = []
    for arc, p in files:
        name = p.name.lower()
        if name in FORBIDDEN_NAMES or p.suffix.lower() in FORBIDDEN_SUFFIXES or name.startswith(".env"):
            problems.append((arc, "forbidden file name"))
            continue
        if p.stat().st_size > MAX_TEXT_BYTES:
            problems.append((arc, "file too large for a text package entry"))
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append((arc, "non-UTF-8 file (binary files are not allowed in the package)"))
            continue
        for label, rx in SECRET_PATTERNS:
            m = rx.search(text)
            if m:
                problems.append((arc, f"{label}: {m.group(0)[:6]}... (redacted)"))
    return problems


def build(out: Path, allow_placeholder: bool, dataset=None):
    files, label = collect_files(allow_placeholder, dataset)
    problems = scan(files)
    if problems:
        for arc, why in problems:
            print("SECRET-SCAN FAIL:", arc, why)
        raise SystemExit(f"package build refused: {len(problems)} problem(s)")
    blobs = {arc: p.read_bytes() for arc, p in files}      # each file is read exactly once: the manifest hash and the zipped bytes cannot disagree
    manifest = {"package": ZIP_NAME, "dataset_dir": label, "phase": "3/4 - Kaggle execution PENDING PHASE 5",
                "files": [{"path": arc, "bytes": len(blobs[arc]), "sha256": hashlib.sha256(blobs[arc]).hexdigest()} for arc, _ in files]}
    manifest_bytes = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        entries = list(blobs.items()) + [("PACKAGE_MANIFEST.json", manifest_bytes)]
        for arc, data in sorted(entries):
            zi = zipfile.ZipInfo(arc, FIXED_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zi.create_system = 3
            z.writestr(zi, data, compresslevel=9)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.parent / "package_sha256.txt").write_text(f"{sha}  {out.name}\n", encoding="utf-8")
    return out, sha, len(files) + 1, label


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--allow-placeholder", action="store_true", help="package dataset/v2_clean if v2_final does not exist yet (layout check only)")
    ap.add_argument("--list", action="store_true", help="print the package file list and exit")
    ap.add_argument("--out", default=str(DIST / ZIP_NAME))
    ap.add_argument("--dataset", default=None, help="package dataset/<NAME> under its own name instead of v2_final (e.g. v3_final)")
    a = ap.parse_args()
    if a.list:
        fl, lab = collect_files(a.allow_placeholder, a.dataset)
        print(f"dataset: {lab}")
        for arc, p in fl:
            print(f"{p.stat().st_size:>10}  {arc}")
        sys.exit(0)
    out, sha, n, lab = build(Path(a.out), a.allow_placeholder, a.dataset)
    print(f"built {out} ({out.stat().st_size / 1e6:.1f} MB, {n} entries, dataset={lab}) sha256={sha}")
