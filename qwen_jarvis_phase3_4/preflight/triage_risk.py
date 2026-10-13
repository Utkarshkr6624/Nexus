"""A5/A6: high-risk factual triage over dataset v2. Prints / writes every flagged example for manual reading.
Classes: medical, legal, financial, security_instruction, certainty, capability_claim, dosage.  Output: preflight/risk_flags.json
({id: [classes]}).  This is a recall-oriented triage; every flagged item is then READ and given a decision in the patch files."""
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "qwen_jarvis_phase2" / "dataset" / "splits"

CLASSES = {
    "medical": r"\b(doctor|physician|symptom|diagnos|medicat|medicine|pill|tablet|vaccin|antibiotic|infection|disease|cancer|diabet|blood pressure|dementia|asthma|allerg|pregnan|dose|dosage|mg\b|ibuprofen|paracetamol|acetaminophen|aspirin|insulin|therapy|therapist|mental health|depress|anxiety|suicid|self-harm|overdose|poison|first aid|cpr|choking|fever|injur|pain)\b",
    "legal": r"\b(lawyer|attorney|legal|law\b|sue\b|lawsuit|contract|liabilit|copyright|trademark|patent|tenancy|landlord|eviction|visa|immigration|gdpr|court|arrest|police|rights|tax\b|taxes|irs)\b",
    "financial": r"\b(invest|stock|bond|crypto|bitcoin|retirement|ira\b|401k|loan|mortgage|interest rate|credit score|credit card|debt|savings|budget|insurance|etf|index fund|dividend|portfolio|forex|trading)\b",
    "security_instruction": r"\b(password|hack|exploit|malware|phishing|ransomware|inject|bypass|crack|brute.?force|keylogger|ddos|sql injection|xss|csrf|credential|2fa|two-factor|vpn)\b",
    "certainty": r"\b(always|never|guarantee[ds]?|definitely|certainly|100%|proven|cures?|impossible|no risk|risk-free|safe to|completely safe|will not)\b",
    "capability_claim": r"\b(gpt|chatgpt|claude|qwen|llama|llm|language model|ollama|whisper|deberta|kaggle|openai|anthropic|hugging ?face)\b",
    "dosage": r"\b\d+(\.\d+)?\s?(mg|mcg|ml|iu|g)\b",
}
RX = {k: re.compile(v, re.I) for k, v in CLASSES.items()}


def load():
    recs = []
    for s in ("train", "validation", "test"):
        for l in open(SRC / f"{s}.jsonl", encoding="utf-8"):
            r = json.loads(l)
            r["_split"] = s
            recs.append(r)
    return recs


if __name__ == "__main__":
    recs = load()
    flags = {}
    cnt = collections.Counter()
    for r in recs:
        a = " ".join(m["content"] for m in r["messages"] if m["role"] == "assistant")
        u = " ".join(m["content"] for m in r["messages"] if m["role"] == "user")
        cls = [k for k, rx in RX.items() if rx.search(a)]
        # certainty words are noise inside code/maths; only count them in prose categories
        if r["metadata"]["category"] in ("mathematics", "reasoning", "programming", "instruction_following") and "certainty" in cls:
            cls.remove("certainty")
        if cls:
            flags[r["id"]] = cls
            for c in cls:
                cnt[(c, r["metadata"]["category"])] += 1
    json.dump(flags, open(HERE / "risk_flags.json", "w"), indent=0)
    print(len(flags), "flagged of", len(recs))
    by_class = collections.Counter(c for v in flags.values() for c in v)
    print(dict(by_class))
    # the classes that need full manual reading: consequential advice
    core = {i for i, v in flags.items() if set(v) & {"medical", "legal", "financial", "dosage"}}
    print("consequential-advice candidates:", len(core))
