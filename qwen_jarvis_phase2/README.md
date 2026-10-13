# Qwen/JARVIS — Phase 2: Dataset Engineering

Isolated workspace for the supervised fine-tuning (SFT) dataset `qwen_jarvis_dataset_v1`.
**Nothing here modifies, imports from, or depends on the NEXUS application.** No model is trained or downloaded.

- Current version: **qwen_jarvis_dataset_v2** (Phase 2.1 correction pass): **11,332 examples** (train 10,296 / validation 518 / test 518), **1,465 multi-turn**. Every category meets its minimum. Details: [`reports/PHASE_2_1_QUALITY_REPORT.md`](reports/PHASE_2_1_QUALITY_REPORT.md)
- The Phase 2 dataset (v1: 10,500 examples) is preserved unchanged in `baseline_v1/`; `DATASET_GENERATION_REPORT.md` describes v1.
- Format: chat JSONL with `system` / `user` / `assistant` roles only (Qwen3 chat-template compatible)
- Full report: [`DATASET_GENERATION_REPORT.md`](DATASET_GENERATION_REPORT.md) · schema and rules: [`DATASET_SPECIFICATION.md`](DATASET_SPECIFICATION.md)

## Layout

```text
qwen_jarvis_phase2/
├── README.md · DATASET_SPECIFICATION.md · DATASET_GENERATION_REPORT.md
├── dataset/
│   ├── generated/          28 raw generator outputs (input to the build; regenerable)
│   ├── validated/          qwen_jarvis_dataset_v2_all.jsonl  (all accepted examples, with metadata)
│   ├── splits/             {train,validation,test}.jsonl            (with metadata)
│   │                       {train,validation,test}_messages_only.jsonl   <- use these for SFT tooling
│   └── dataset_manifest.json   file hashes, counts, formats
├── baseline_v1/            untouched copy of the Phase 2 (v1) outputs
├── scripts/                generators (gen_*.py), py_bank*.py, nexus_facts.py (fact base), build.py, run_all.py
├── validation/             checks.py (per-example checks), validate_final.py (independent audit of the final files)
└── reports/                build_stats.json, dataset_statistics.json, final_validation.json, rejected.jsonl, code_verification_multi.json
```

## Rebuild / re-validate

```bash
python scripts/run_all.py            # regenerate -> build -> validate -> manifest (about one minute)
python validation/validate_final.py  # audit the files on disk; exit code 1 on any hard failure
```

Requirements: Python 3.13 standard library only. The code-verification generators also call `node`, `javac`/`java`, `g++`, `gcc`
(set `P2_SCRATCH` to a temp directory for their scratch files). Two consecutive full rebuilds produce byte-identical outputs.

## Use

Feed `dataset/splits/*_messages_only.jsonl` to an SFT trainer that applies the Qwen3 chat template. Keep `*.jsonl` (with metadata)
for analysis and per-category evaluation. Do **not** train from this repository yet: Phase 3 (tool manifest) comes first, and Phase 4 should
mix in open-licensed general-purpose replay data (see the report's concerns).

## Not part of this workspace

The 20 untracked dataset scripts that existed in the repository before this session (`generate_dataset.py`, `scripts/write_*.py`, …)
were not read for reuse, modified, moved or executed.
