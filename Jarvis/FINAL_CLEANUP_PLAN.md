# Final Cleanup Plan — Verified Duplicates Only

Written: 2026-10-10, before any deletion, after audit steps A–E were complete.
Scope: files inside `E:\Nexo\Jarvis` only. The staging folder `C:\Users\utkar\Downloads\JARVIS_Backup` is outside scope and will not be touched.

## Eligibility rules (every file below meets all of them)

1. The file is inside `E:\Nexo\Jarvis`.
2. Its full SHA-256 and byte size equal the canonical file's (computed this session, full-file streaming read).
3. The canonical file stays in place and is itself verified against that checkpoint's `checkpoint_meta.json` (adapters, trainer states, configs, READMEs). For `checkpoint_meta.json` the comparison is byte identity only.
4. No script in `E:\Nexo\Jarvis` references it. The workspace contains 0 `.py/.ps1/.sh/.bat/.ipynb` files. Earlier reports mention these paths only as history.
5. Deleting it does not remove the only copy of anything.

## Execution safeguards

Immediately before each deletion:
- Re-hash both the duplicate and the canonical file.
- Require identical size and SHA-256.
- Delete the single file only. No directory deletion.

Any mismatch means skip and report. After cleanup, all 35 canonical files will be re-verified.

## Proposed deletions (21 files)

| # | Duplicate path (relative to E:\Nexo\Jarvis) | Bytes | SHA-256 (identical for duplicate and canonical) | Canonical file kept | Why it is unnecessary |
|---|---|---|---|---|---|
| 1 | `checkpoint-1300/adapter_model.safetensors` | 174655536 | d71f9a974837cdbaf4ade94e779ed15bfeee67bb215b77c14db9333926ef570c | `checkpoint-1300/adapter/adapter_model.safetensors` | Adapter left in the wrong folder before Phase C; the canonical copy is now in adapter/ |
| 2 | `checkpoint-1400/adapter_model.safetensors` | 174655536 | 8ea48e39cfc975fdad23ae8dc2bfc2ba19af9b574be0600b633460f4eb7e34fe | `checkpoint-1400/adapter/adapter_model.safetensors` | Adapter left in the wrong folder before Phase C; the canonical copy is now in adapter/ |
| 3 | `checkpoint-1500/adapter_model.safetensors` | 174655536 | b9d16c934d689952da179c4b8b3dbdfb104172ce2e95a46ecad685d48c865e9a | `checkpoint-1500/adapter/adapter_model.safetensors` | Adapter left in the wrong folder before Phase C; the canonical copy is now in adapter/ |
| 4 | `checkpoint-1596/adapter_model.safetensors` | 174655536 | 162b97f6007e76a147833c126b11f5146034c1315c08024414464b1bd4564dc8 | `checkpoint-1596/adapter/adapter_model.safetensors` | Adapter left in the wrong folder before Phase C; the canonical copy is now in adapter/ |
| 5 | `adapter_model.safetensors` | 174655536 | 8c5881d4d9a4202a85a257c1d64c30f4ef078a3fe9026e92e0fecd856243ea2b | `checkpoint-1200/adapter/adapter_model.safetensors` | Stray duplicate of the checkpoint-1200 adapter at the workspace root (another copy also exists in staging) |
| 6 | `_phase_c_downloads/checkpoint-1300/checkpoint_meta.json` | 1498 | 3e2739a301f6b580d2c015f18781af2473217e96401f4b0a68c9e468bee4ccec | `checkpoint-1300/checkpoint_meta.json` | Phase C temporary download, already copied into place |
| 7 | `_phase_c_downloads/checkpoint-1300/trainer_state.pt` | 349622034 | 3de2eafb922bf2eb3f268e1c44617da3314666872e6bb3a1d4630d10500faeb5 | `checkpoint-1300/trainer_state.pt` | Phase C temporary download, already copied into place |
| 8 | `_phase_c_downloads/checkpoint-1300/adapter/adapter_config.json` | 1146 | f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0 | `checkpoint-1300/adapter/adapter_config.json` | Phase C temporary download, already copied into place |
| 9 | `_phase_c_downloads/checkpoint-1300/adapter/README.md` | 5180 | 85e2b3a3fae33eab459538a9f1ce2d9ad6bd9fb7c6b97f9ee97e07923ad3e57a | `checkpoint-1300/adapter/README.md` | Phase C temporary download, already copied into place |
| 10 | `_phase_c_downloads/checkpoint-1400/checkpoint_meta.json` | 1498 | eaaee64f044ad947d6ed22f21d676c4c416f5f79de2f74357b0abc22b996186b | `checkpoint-1400/checkpoint_meta.json` | Phase C temporary download, already copied into place |
| 11 | `_phase_c_downloads/checkpoint-1400/trainer_state.pt` | 349622034 | 81d314ae6f3a193fee1f484e79d9d4122fbc294545aae9458017dd8df980384d | `checkpoint-1400/trainer_state.pt` | Phase C temporary download, already copied into place |
| 12 | `_phase_c_downloads/checkpoint-1400/adapter/adapter_config.json` | 1146 | f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0 | `checkpoint-1400/adapter/adapter_config.json` | Phase C temporary download, already copied into place |
| 13 | `_phase_c_downloads/checkpoint-1400/adapter/README.md` | 5180 | 85e2b3a3fae33eab459538a9f1ce2d9ad6bd9fb7c6b97f9ee97e07923ad3e57a | `checkpoint-1400/adapter/README.md` | Phase C temporary download, already copied into place |
| 14 | `_phase_c_downloads/checkpoint-1500/checkpoint_meta.json` | 1499 | c4354c5ab816daaf319fffb08649b8a2309e28d8890aa626b137569ac5d07ec3 | `checkpoint-1500/checkpoint_meta.json` | Phase C temporary download, already copied into place |
| 15 | `_phase_c_downloads/checkpoint-1500/trainer_state.pt` | 349622034 | 81f36e901bdde386ac12df440e6f27749b1bd093ed5ac0ab141f2b882fe861dc | `checkpoint-1500/trainer_state.pt` | Phase C temporary download, already copied into place |
| 16 | `_phase_c_downloads/checkpoint-1500/adapter/adapter_config.json` | 1146 | f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0 | `checkpoint-1500/adapter/adapter_config.json` | Phase C temporary download, already copied into place |
| 17 | `_phase_c_downloads/checkpoint-1500/adapter/README.md` | 5180 | 85e2b3a3fae33eab459538a9f1ce2d9ad6bd9fb7c6b97f9ee97e07923ad3e57a | `checkpoint-1500/adapter/README.md` | Phase C temporary download, already copied into place |
| 18 | `_phase_c_downloads/checkpoint-1596/checkpoint_meta.json` | 1499 | a8bef0e232fe367d308ff9c551115a29a8370af01411916beea6ca715a8a7478 | `checkpoint-1596/checkpoint_meta.json` | Phase C temporary download, already copied into place |
| 19 | `_phase_c_downloads/checkpoint-1596/trainer_state.pt` | 349622034 | 29c19b9c8cf9143167e4df44b25a929192a2448ade14bd6aa8c4c6fbbb38178b | `checkpoint-1596/trainer_state.pt` | Phase C temporary download, already copied into place |
| 20 | `_phase_c_downloads/checkpoint-1596/adapter/adapter_config.json` | 1146 | f5f0c671d8840894ceb384bdaa765c09adbc235678e57433954498477acb86f0 | `checkpoint-1596/adapter/adapter_config.json` | Phase C temporary download, already copied into place |
| 21 | `_phase_c_downloads/checkpoint-1596/adapter/README.md` | 5180 | 85e2b3a3fae33eab459538a9f1ce2d9ad6bd9fb7c6b97f9ee97e07923ad3e57a | `checkpoint-1596/adapter/README.md` | Phase C temporary download, already copied into place |

**Expected space recovered: 2271797114 bytes (~2.12 GiB).** The earlier "~1.4 GB" estimate left out the 4 Phase C trainer-state copies (1398488136 bytes) and counted the adapters only partially.

## Explicitly preserved (not deleted)

| Path | Reason |
|---|---|
| `_phase_c_downloads/dl_1300.log`, `dl_1400.log`, `dl_1500.log`, `dl_1596.log`, `trainer_downloads.log` | The only record of the Phase C download exit codes and sizes (audit evidence). No canonical copy exists. |
| `_phase_c_downloads/` and its now-empty subfolders | Hold the logs above. Empty subfolders are left as they are rather than removing whole directories. |
| `_finalization_downloads/primary/training_results.json` | The only local copy of the run results. |
| `_finalization_downloads/primary/checkpoint-1200/checkpoint_meta.json` | Evidence that the primary (final-run) dataset holds a checkpoint-1200 byte-identical to the local one (run-continuity link). 1499 bytes. |
| `_finalization_downloads/older/best.json`, `latest.json` | Evidence that the original pointer files came from the older dataset. Byte-identical to the backups, but kept for provenance (325 bytes). |
| `_finalization_backups/best.json`, `latest.json` | Required byte-identical backups of the pointers that were replaced. |
| All reports, the manifest, `best.json`, `latest.json`, and all 35 canonical checkpoint files | Required artifacts. |
| `C:\Users\utkar\Downloads\JARVIS_Backup\*` | Outside the workspace and never eligible. Its partial 1300 `trainer_state.pt` still exists there; you can remove it manually if you want. |
