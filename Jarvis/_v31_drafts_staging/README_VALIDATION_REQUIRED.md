# Jarvis V3.1 remediation extras — draft for project validation

These four UTF-8 JSONL files were drafted from the supplied V31_EXTRA_SCHEMA.md rules. They are not yet validated against the actual project files, V2/V3 corpus, held-out evaluation prompts, or the local `build_v31.py` implementation.

Categories and counts:
- safety_benign: 150
- safety_fiction: 80
- safety_ambiguous: 40
- safety_crisis: 48 (40 single-turn samples plus 8 multi-turn conversations)

Total: 318 records.

All records use unique `qj31x-...` IDs in this package, `source_type=authored`, and the required metadata keys. `author` explicitly notes that project validation is required. The provided builder's own checks remain authoritative.

Important: run the existing `build_v31.py` validator before training. It must check collisions against V2/V3 prompts, held-out evaluation leakage, group splitting, strict schema, and the complete crisis audit. Delete/revise anything it rejects. These files should not be merged blindly.
