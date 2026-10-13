# Qwen Integration Plan (for Phase 7+, not before the Phase 6 evaluation gate passes)

No part of this is implemented. Nothing here authorizes touching NEXUS in Phases 1-6.

## Sequence

1. **Serving**: run Qwen as a separate local process exposing an OpenAI-style chat/tool-call API on localhost (keeps torch-CPU pins in the
   backend untouched). Backend gets a thin client + health/status mirroring the `/ml/status` 503-when-unavailable pattern.
2. **Chat endpoint**: new authenticated router with new permission; SSE streaming; request limits like `ML_MAX_INPUT_CHARS`; no logging of message text (matches current ML logging policy).
3. **Conversation store**: server-side history (new tables via Alembic) vs. keep client-held history; decide explicitly, since current voice design deliberately never persists transcripts.
4. **Tool registry/executor**: JSON-schema tools; NEXUS tools wrap existing services; write tools reuse `ActionSpec`/propose-confirm and destructive flags; runtime injects `owner_id`.
5. **Permission layer**: per-tool policy (auto / confirm / deny), audit log entries via existing `audit_service`, step and time budgets.
6. **File/terminal/web tools** (each its own gate): allowlisted roots modeled on `DEVELOPER_PATH_ALLOWLIST`; terminal with command allowlist + confirmation; web via search/fetch with size and domain limits.
7. **Frontend**: chat view and tool-trace/confirmation UI in Command Center; voice hook retargeted from `/ml/route` to chat; update tests/copy that currently assert "NEXUS routes, it does not answer".
8. **Router decision**: keep DeBERTa as a cheap pre-router/fallback or retire it, based on evaluation. Not decided now.

## Project rules that must be consciously revised then

- `requirements.txt` header ("NEXUS runs no language model"), `docs/phase-12-voice.md` one-model constraint, `test_offline_local_first.py`,
  README Ollama claim. Update docs/tests in the same change as the integration, never silently.

## Zero-cost stance

Open weights, Kaggle for training, local inference, no paid APIs. If local latency with a 4 GB GPU is unacceptable, options (all free): smaller quant,
CPU offload, or serving from a Kaggle/Colab session for demos. Decision deferred to Phase 4 measurements.

## Prerequisites before starting

Phase 6 pass report; chosen serving runtime; threat model for file/terminal/web tools; user sign-off on permission policy.
