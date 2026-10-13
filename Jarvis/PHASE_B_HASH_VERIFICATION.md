# Phase B — SHA-256 Hash Verification

Generated: 2026-10-10
Method: Python `hashlib.sha256`, full-file streaming read (16 MiB blocks), read-only.
No files were downloaded, moved, renamed, overwritten or deleted. `trainer_state.pt` files were hashed as raw bytes only, never deserialized.
Errors: none. All 12 files opened and hashed.

Original-hash source: the `files` section of each checkpoint's `checkpoint_meta.json`.
- 800 / 1100 / 1200: local meta in `E:\Nexo\Jarvis\checkpoint-N\checkpoint_meta.json`
- 1300: staging copy `C:\Users\utkar\Downloads\JARVIS_Backup\checkpoint-1300\checkpoint_meta.json` (read-only)
- 1400 / 1500 / 1596: no meta available, so no original hash to compare against

## Checkpoint adapters

| Checkpoint | Local path | Bytes | Local SHA-256 | Meta SHA-256 | Size match | Result |
|---|---|---|---|---|---|---|
| 800 | checkpoint-800\adapter\adapter_model.safetensors | 174655536 | 41ee6d98ded59aec8ed641cd294bca0110ca3703358fbad003def9cd4eeed1fc | 41ee6d98ded59aec8ed641cd294bca0110ca3703358fbad003def9cd4eeed1fc | yes | PASS |
| 1100 | checkpoint-1100\adapter\adapter_model.safetensors | 174655536 | 35b8707b6b2dfcbfa1862751d1ce56ea4499ce939bcec4f7345ea5b0e62beebe | 35b8707b6b2dfcbfa1862751d1ce56ea4499ce939bcec4f7345ea5b0e62beebe | yes | PASS |
| 1200 | checkpoint-1200\adapter\adapter_model.safetensors | 174655536 | 8c5881d4d9a4202a85a257c1d64c30f4ef078a3fe9026e92e0fecd856243ea2b | 8c5881d4d9a4202a85a257c1d64c30f4ef078a3fe9026e92e0fecd856243ea2b | yes | PASS |
| 1300 | checkpoint-1300\adapter_model.safetensors (misplaced, root) | 174655536 | d71f9a974837cdbaf4ade94e779ed15bfeee67bb215b77c14db9333926ef570c | d71f9a974837cdbaf4ade94e779ed15bfeee67bb215b77c14db9333926ef570c (staging meta) | yes | PASS |
| 1400 | checkpoint-1400\adapter_model.safetensors (misplaced, root) | 174655536 | 8ea48e39cfc975fdad23ae8dc2bfc2ba19af9b574be0600b633460f4eb7e34fe | unavailable | n/a | UNVERIFIED |
| 1500 | checkpoint-1500\adapter_model.safetensors (misplaced, root) | 174655536 | b9d16c934d689952da179c4b8b3dbdfb104172ce2e95a46ecad685d48c865e9a | unavailable | n/a | UNVERIFIED |
| 1596 | checkpoint-1596\adapter_model.safetensors (misplaced, root) | 174655536 | 162b97f6007e76a147833c126b11f5146034c1315c08024414464b1bd4564dc8 | unavailable | n/a | UNVERIFIED |

Notes:
- 1400/1500/1596 hashes match the truncated prefixes from the earlier session (8EA48E39CFC975FDAD23AE8DC2BFC2BA19A…, B9D16C934D689952DA179C4B8B3DBDFB104…, 162B97F6007E76A147833C126B11F514603…). That shows the files are unchanged since then, not that they are authentic. They stay UNVERIFIED until their own `checkpoint_meta.json` is recovered.
- All seven adapter hashes are distinct, so no two checkpoints hold the same adapter.

## Extra adapters of unknown origin

| File | Bytes | SHA-256 | Matches | Result |
|---|---|---|---|---|
| E:\Nexo\Jarvis\adapter_model.safetensors | 174655536 | 8c5881d4d9a4202a85a257c1d64c30f4ef078a3fe9026e92e0fecd856243ea2b | checkpoint-1200 adapter (meta + local) | PASS (identified as checkpoint-1200 duplicate) |
| C:\Users\utkar\Downloads\JARVIS_Backup\adapter_model.safetensors | 174655536 | 8c5881d4d9a4202a85a257c1d64c30f4ef078a3fe9026e92e0fecd856243ea2b | checkpoint-1200 adapter (meta + local) | PASS (identified as checkpoint-1200 duplicate) |

Both extras are byte-identical duplicates of the checkpoint-1200 adapter. Neither was deleted (deletion not authorized in Phase B).

## Trainer states

| Checkpoint | Bytes | Local SHA-256 | Meta SHA-256 | Size match | Result |
|---|---|---|---|---|---|
| 800 | 349622034 | e57880b3011b03e2f6f81ee87db1c2d378b68ff4bc0e883ced6814973d848521 | e57880b3011b03e2f6f81ee87db1c2d378b68ff4bc0e883ced6814973d848521 | yes | PASS |
| 1100 | 349622034 | ab9d2be3a7423d9e38490ebf06966926e1e0e9686f7af1d37c552ab1ab7b7b0e | ab9d2be3a7423d9e38490ebf06966926e1e0e9686f7af1d37c552ab1ab7b7b0e | yes | PASS |
| 1200 | 349622034 | 67a4d6b7e2200f335524587ec3d685fe49eed4a065b45754b4500596a9e306e6 | 67a4d6b7e2200f335524587ec3d685fe49eed4a065b45754b4500596a9e306e6 | yes | PASS |
| 1300 | — | not hashed (staging copy truncated: 176160768 of 349622034 bytes) | 3de2eafb922bf2eb3f268e1c44617da3314666872e6bb3a1d4630d10500faeb5 | no | MISSING |
| 1400 | — | — | unavailable | — | MISSING |
| 1500 | — | — | unavailable | — | MISSING |
| 1596 | — | — | unavailable | — | MISSING |

## Not covered in Phase B (remain UNVERIFIED)

- `adapter_config.json` and `README.md` for 800/1100/1200 (outside the Phase B scope given).
- `checkpoint_meta.json` files themselves: they have no external hash. They are trusted only as internally consistent with the payloads they describe.
- 1300 meta authenticity: it is a staging download with no independent hash. Its adapter hash matching the local 1300 adapter supports both files, but is not independent proof.
- The per-checkpoint staging adapter copies (1200–1596) were not hashed. Not requested.

## Inconsistencies

1. The 1300 adapter is correct but misplaced (checkpoint root, not `adapter\`).
2. The 1400/1500/1596 adapters are misplaced and lack metadata.
3. The root adapter is a checkpoint-1200 duplicate, not a separate checkpoint.
4. Carried over from Phase A: `best.json` (800) and `latest.json` (1200) disagree with the historical best (1300) and final (1596) steps. The 1300 `adapter_config.json` meta hash differs from 800/1100/1200.
