"""python -m pipeline.data.cli {stats,validate,show} ...   (run from E:\\Nexo\\qwen_jarvis_phase3_4)"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dataset import DataError, load_split, read_jsonl
from .render import IGNORE, load_tokenizer, render_samples
from .schema import validate_all, validate_record
from .stats import compute_stats, to_markdown

WS = Path(__file__).resolve().parents[2]


def resolve_data(data_dir, split):
    """Prefer the frozen Phase 3 dataset; fall back to the Phase 2 v2 splits (old format)."""
    if data_dir:
        d = Path(data_dir)
        mode = "strict" if (d / "MODE_STRICT").exists() or "v2_final" in str(d) else "phase2"
        return d / f"{split}.jsonl", mode
    final = WS / "dataset" / "v2_final" / f"{split}.jsonl"
    if final.is_file():
        return final, "strict"
    return WS.parent / "qwen_jarvis_phase2" / "dataset" / "splits" / f"{split}.jsonl", "phase2"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipeline.data.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("stats", "validate", "show"):
        p = sub.add_parser(name)
        p.add_argument("--split", default="train", choices=["train", "validation", "test"])
        p.add_argument("--data-dir")
        p.add_argument("--mode", choices=["strict", "phase2"], help="override schema mode")
        p.add_argument("--max-len", type=int, default=2048)
        p.add_argument("--train-on", default="per_turn", choices=["per_turn", "last_assistant", "all_assistant"])
        p.add_argument("--tokenizer-dir")
        p.add_argument("--limit", type=int)
        if name == "show":
            p.add_argument("--index", type=int, default=0)
        if name == "stats":
            p.add_argument("--out-dir", default=str(WS / "reports"))
            p.add_argument("--tag", default="", help="suffix for output file names, e.g. candidate")
            p.add_argument("--skip-invalid", action="store_true", help="drop (and report) records that fail validation instead of aborting")
    a = ap.parse_args(argv)
    path, mode = resolve_data(a.data_dir, a.split)
    mode = a.mode or mode
    try:
        if a.cmd == "validate":
            recs = read_jsonl(path)[: a.limit] if a.limit else read_jsonl(path)
            rep = validate_all(recs, mode)
            print(json.dumps({"path": str(path), "mode": mode, "records": rep.n, "ok": rep.ok, "bad": rep.bad, "first_problems": rep.problems[:10]}, indent=1))
            return 0 if rep.bad == 0 else 1
        tok = load_tokenizer(a.tokenizer_dir)
        if a.cmd == "stats":
            recs = read_jsonl(path)
            if a.limit:
                recs = recs[: a.limit]
            if a.skip_invalid:
                recs = [r for r in recs if not validate_record(r, mode)]
            data = load_split(path, tok, a.max_len, train_on=a.train_on, validate="skip" if a.skip_invalid else mode, limit=a.limit)
            st = compute_stats(recs, data, tok, data.report, a.max_len)
            st["source"] = {"path": str(path), "mode": mode, "train_on": a.train_on}
            out = Path(a.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            stem = f"data_stats_{a.split}" + (f"_{a.tag}" if a.tag else "")
            (out / f"{stem}.json").write_text(json.dumps(st, indent=1), encoding="utf-8")
            (out / f"{stem}.md").write_text(to_markdown(a.split, st), encoding="utf-8")
            print(to_markdown(a.split, st))
            return 0
        if a.cmd == "show":
            recs = read_jsonl(path)
            rec = recs[a.index]
            samples, dropped = render_samples(rec, tok, a.max_len, a.train_on)
            for s in samples:
                print(f"--- sample turn_index={s['turn_index']} tokens={s['n_tokens']} labelled={s['n_label_tokens']} truncated={s['truncated']}")
                out, cur, lab = [], [], None
                for tid, l in zip(s["input_ids"], s["labels"]):
                    flag = l != IGNORE
                    if lab is None or flag == lab:
                        cur.append(tid)
                    else:
                        out.append((lab, tok.decode(cur)))
                        cur = [tid]
                    lab = flag
                if cur:
                    out.append((lab, tok.decode(cur)))
                for flag, text in out:
                    print(("[LOSS] " if flag else "[ctx ] ") + repr(text))
            return 0
    except DataError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
