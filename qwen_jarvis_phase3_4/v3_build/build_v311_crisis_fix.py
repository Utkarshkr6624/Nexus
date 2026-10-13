"""Builds the V3.1.1 crisis-consistency CANDIDATE (dataset/v3_1_1_crisis_fix_candidate/). Read-only on V3.1.

Why: three V3.1 replay targets inherited from V2 (crisis_support) answer a user who never named a country with
"In the US you can call or text 988 ...". That contradicts the audited crisis rule the V3.1 extras teach (no
country-specific number unless the user stated the country) and matches the pattern checkpoint_v31_139 still
produces on safety-crisis-001/002. Only those three assistant targets change; every other byte of every record is
copied. Nothing is trained here.

usage: python -B v3_build/build_v311_crisis_fix.py      exit 1 if any gate fails"""
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import build_v31 as B  # noqa: E402  (the V3.1 crisis_audit gate, unchanged)
from pipeline.data.schema import validate_record  # noqa: E402

WS = HERE.parent
SRC = WS / "dataset" / "v3_1_remediation"
OUT = WS / "dataset" / "v3_1_1_crisis_fix_candidate"
VERSION = "qwen_jarvis_dataset_v3_1_1_crisis_fix_candidate"
SRC_MANIFEST_SHA = "3952d28a46c2c954010b2195889d47f32e72cf083e5b44962490bc580b6ce4db"

# Exact replacements: the US-specific clause is replaced by the country-neutral route the V3.1 extras use.
_ENDING = ("In the US you can call or text 988, and elsewhere findahelpline.com lists crisis lines by country.",
           "findahelpline.com lists free, confidential crisis lines for your country.")
_SELF_HARM = ("and a crisis line can talk with you any time (in the US, 988; findahelpline.com lists lines elsewhere).",
              "and a crisis line can talk with you any time (findahelpline.com lists free lines for your country).")
FIXES = {"qj2f-9a8e930110b3": _ENDING, "qj2f-2d3feb8370e0": _SELF_HARM, "qj2f-a86779e28650": _SELF_HARM}
NUMBER = re.compile(r"(?<![\d.\w])\+?\d[\d \-()]{1,18}\d(?![\d%\w])")
COUNTRY = re.compile(r"\b(US|USA|United States|America|Canada|UK|United Kingdom|Britain|England|Scotland|Wales|Ireland|EU|Europe)\b")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fix(rec, log):
    rid = rec["id"]
    for m in rec["messages"]:
        if m["role"] != "assistant" or not m.get("content"):
            continue
        old = m["content"]
        new = old.replace(*FIXES[rid])
        if new != old:
            m["content"] = new
            log.append({"id": rid, "before": old, "after": new})
    rec["metadata"]["edited"] = True
    return rec


def main():
    problems = []
    if sha(SRC / "MANIFEST.json") != SRC_MANIFEST_SHA:
        problems.append("V3.1 manifest hash changed; refusing to build from an unverified source")
    src_manifest = json.loads((SRC / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, meta in src_manifest["files"].items():
        if sha(SRC / name) != meta["sha256"]:
            problems.append(f"V3.1 {name} does not match its manifest")
    if problems:
        print("\n".join(problems))
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    log, files = [], {}
    for name in ("train.jsonl", "validation.jsonl", "test.jsonl"):
        lines = []
        src_bytes = (SRC / name).read_bytes()
        eol = "\r\n" if b"\r\n" in src_bytes else "\n"  # keep the source line endings so unchanged records stay byte-identical
        for line in src_bytes.decode("utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["id"] in FIXES:
                rec = fix(rec, log)
                lines.append(json.dumps(rec, ensure_ascii=False))
            else:
                lines.append(line)  # byte-identical copy of every other record
            errs = validate_record(json.loads(lines[-1]))
            if errs:
                problems.append(f"{name} {rec['id']}: schema {errs}")
            if json.loads(lines[-1])["metadata"].get("category") == "safety" and "crisis" in (json.loads(lines[-1])["metadata"].get("subcategory") or ""):
                r2 = json.loads(lines[-1])
                problems += [f"{name} {rec['id']}: crisis_audit {x}" for x in B.crisis_audit(r2)]
                users = " ".join(m["content"] or "" for m in r2["messages"] if m["role"] == "user")
                asst = " ".join(m.get("content") or "" for m in r2["messages"] if m["role"] == "assistant")
                if NUMBER.search(asst) and not COUNTRY.search(users):
                    problems.append(f"{name} {rec['id']}: country-specific number but the user named no country")
        (OUT / name).write_bytes((eol.join(lines) + eol).encode("utf-8"))
        files[name] = {"records": len(lines), "sha256": sha(OUT / name)}
    if sorted(x["id"] for x in log) != sorted(FIXES):
        problems.append(f"expected exactly {sorted(FIXES)} to change, changed {sorted(x['id'] for x in log)}")
    manifest = {
        "version": VERSION,
        "status": "CANDIDATE_NOT_TRAINED - prepared during Phase 6 local remediation; requires owner review before any training",
        "baseline": "qwen_jarvis_dataset_v3_1_remediation (unchanged)",
        "baseline_manifest_sha256": SRC_MANIFEST_SHA,
        "change": "3 replay crisis_support targets: US-only 988 clause replaced by the country-neutral findahelpline.com route",
        "changes": log,
        "files": files,
        "gates": {"source_verified": True, "schema_valid": not any("schema" in p for p in problems),
                  "crisis_audit": not any("crisis_audit" in p for p in problems),
                  "no_number_without_stated_country": not any("named no country" in p for p in problems),
                  "only_listed_records_changed": not any(p.startswith("expected exactly") for p in problems)},
    }
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"files": files, "changed": len(log), "problems": problems}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
