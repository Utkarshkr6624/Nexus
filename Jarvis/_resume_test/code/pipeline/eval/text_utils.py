"""Small deterministic text helpers shared by the scorers and the report generator. Stdlib only."""
import math
import re

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w])")
_BULLET = re.compile(r"^\s*(?:[-*•])\s+\S")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+\S")
_CODE_FENCE = re.compile(r"```(\w*)\n(.*?)```", re.S)


def normalise(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def words(text: str):
    return re.findall(r"[A-Za-z0-9À-ɏ'’-]+", text)


def word_count(text: str) -> int:
    return len(words(text))


def sentences(text: str):
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    return [s.strip() for s in _SENT_SPLIT.split(t) if s.strip()]


def sentence_count(text: str) -> int:
    """Counts sentences; list markers and bare headings are not sentences, a trailing fragment without punctuation counts."""
    return len([s for s in sentences(text) if re.search(r"[A-Za-z0-9]", s)])


def bullet_lines(text: str):
    return [l for l in text.splitlines() if _BULLET.match(l)]


def numbered_lines(text: str):
    return [l for l in text.splitlines() if _NUMBERED.match(l)]


def numbers_in(text: str):
    """All numeric tokens as floats (thousands separators removed)."""
    out = []
    for m in _NUM.finditer(text):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def strip_list_markers(text: str) -> str:
    return re.sub(r"(?m)^\s*(\d+[.)]|[-*•])\s+", "", text)


def extract_final_number(text: str):
    """The model's *final answer* number: an explicit 'answer/result/total/=' marker wins, otherwise the last number. None if no number."""
    t = text.replace("**", "")
    marked = re.findall(r"(?:answer|result|total|equals|=)\s*(?:is|:)?\s*\$?(-?\d[\d,]*(?:\.\d+)?)", t, flags=re.I)
    if marked:
        return float(marked[-1].replace(",", ""))
    nums = numbers_in(strip_list_markers(t))
    return nums[-1] if nums else None


def contains_phrase(text: str, phrase: str) -> bool:
    """Case-insensitive; word-boundary match for plain alphanumeric phrases, substring match when the phrase has punctuation,
    word-prefix match when the phrase ends with '*' (e.g. 'propos*')."""
    t = text.lower()
    p = phrase.lower()
    if p.endswith("*"):                       # 'propos*' = word-prefix match (left boundary only)
        return re.search(r"(?<![a-z0-9])" + re.escape(p[:-1]), t) is not None
    if re.fullmatch(r"[a-z0-9 ]+", p):
        return re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", t) is not None
    return p in t


def code_blocks(text: str):
    return [(lang or "", body) for lang, body in _CODE_FENCE.findall(text)]


def wilson(k: int, n: int, z: float = 1.96):
    """Wilson score interval for a binomial proportion. Returns (low, high); (0.0, 1.0) when n == 0."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def lexical_f1(candidate: str, reference: str) -> float:
    """Unigram F1 over normalised tokens (informational only, never a pass/fail criterion)."""
    c, r = normalise(candidate).split(), normalise(reference).split()
    if not c or not r:
        return 0.0
    from collections import Counter
    cc, rc = Counter(c), Counter(r)
    overlap = sum((cc & rc).values())
    if overlap == 0:
        return 0.0
    p, rec = overlap / len(c), overlap / len(r)
    return 2 * p * rec / (p + rec)


_STOP = set("""about above after again against being below between could doing during each from further have having here herself himself into itself just more most
other ought ours ourselves over same should some such than that their theirs them themselves then there these they this those through under until very were what when where which while
with would your yours yourself yourselves does will shall may might must can how why who whom""".split())


# Boilerplate vocabulary that generic refusals / limitation statements / hedges contain. Excluded from topical tokens (compared by 5-letter stem) so
# that a constant "I can't help with that / no access to files or the internet" reply cannot match the *topic* of a request by accident.
GENERIC_WORDS = """access file files device devices internet details detail account accounts information system data session tools tool conversation live paste here local
help helps tell goal safe safer alternative instead legal legitimate harm harmful break write give make show explain actions action thing things want need
check open send look create delete remove update move read run sure please could would should about something anything everything""".split()
GENERIC_STEMS = {w[:5] for w in GENERIC_WORDS}


def topical_tokens(text, n=8, min_len=5):
    """Distinctive content words (first-appearance order) from a prompt or reference, used for 'is the answer about the question' checks.
    Generic boilerplate vocabulary (GENERIC_WORDS) is excluded."""
    out, seen = [], set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_\-]+", text.lower()):
        if len(w) >= min_len and w not in _STOP and w not in seen and w != "nexus" and w[:5] not in GENERIC_STEMS:
            seen.add(w)
            out.append(w)
    return out[:n]


def remove_echo(output, question, n=3):
    """Blank out every stretch of `output` that merely copies >= n consecutive words from `question` (so echoing the question cannot satisfy a
    keyword check). Returns the cleaned output."""
    qw = [w.lower() for w in re.findall(r"[A-Za-z0-9_']+", question)]
    k = min(n, max(2, len(qw)))
    grams = {tuple(qw[i:i + k]) for i in range(len(qw) - k + 1)}
    if not grams:
        return output
    spans = [(m.start(), m.end(), m.group(0).lower()) for m in re.finditer(r"[A-Za-z0-9_']+", output)]
    kill = [False] * len(spans)
    for i in range(len(spans) - k + 1):
        if tuple(s[2] for s in spans[i:i + k]) in grams:
            for j in range(i, i + k):
                kill[j] = True
    chars = list(output)
    for (a, b, _), dead in zip(spans, kill):
        if dead:
            for x in range(a, b):
                chars[x] = " "
    return "".join(chars)


def echo_ratio(output, question, n=3):
    """Fraction of the output's words that belong to a >= n-word run copied verbatim from `question` (0.0 when nothing is copied)."""
    ow = re.findall(r"[A-Za-z0-9_']+", output)
    if not ow:
        return 0.0
    cleaned = remove_echo(output, question, n)
    left = re.findall(r"[A-Za-z0-9_']+", cleaned)
    return 1.0 - len(left) / len(ow)
