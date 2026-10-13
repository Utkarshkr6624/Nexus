#!/usr/bin/env python3
"""Trim and validate category_06_debug_toolselect.jsonl."""

import json

INPUT = "E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_06_debug_toolselect.jsonl"
OUTPUT = "E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_06_debug_toolselect.jsonl"

# Read all examples
with open(INPUT, "r") as f:
    examples = [json.loads(line) for line in f if line.strip()]

print(f"Total examples read: {len(examples)}")

# Separate by category
debugging = [e for e in examples if e["metadata"]["category"] == "debugging"]
tool_selection = [e for e in examples if e["metadata"]["category"] == "tool_selection"]
print(f"Debugging: {len(debugging)}")
print(f"Tool selection: {len(tool_selection)}")

# Trim: keep first 25 debugging + first 30 tool_selection
trimmed = debugging[:25] + tool_selection[:30]
print(f"Trimmed: {len(trimmed)} (debugging={sum(1 for e in trimmed if e['metadata']['category']=='debugging')}, tool_selection={sum(1 for e in trimmed if e['metadata']['category']=='tool_selection')})")

# Write trimmed file
with open(OUTPUT, "w") as f:
    for ex in trimmed:
        f.write(json.dumps(ex, ensure_ascii=False) + "\n")
print(f"Written to {OUTPUT}")

# ===== VALIDATION =====
print("\n===== VALIDATION =====")
errors = []

# Load tool manifest for name qualification check
import os
manifest_path = "E:/Nexo/backend/tools/manifest.json"
if os.path.exists(manifest_path):
    with open(manifest_path) as f:
        manifest = json.load(f)
    tool_names = set(t["name"] for t in manifest.get("tools", []))
else:
    tool_names = set()
    print("WARNING: manifest not found, skipping tool name check")

for i, ex in enumerate(trimmed):
    # Check top-level keys
    if "messages" not in ex:
        errors.append(f"Line {i+1}: missing 'messages'")
        continue
    if "metadata" not in ex:
        errors.append(f"Line {i+1}: missing 'metadata'")
        continue

    messages = ex["messages"]
    metadata = ex["metadata"]

    # Check metadata fields
    for field in ["category", "difficulty", "tools_used", "multi_step", "requires_clarification", "requires_confirmation"]:
        if field not in metadata:
            errors.append(f"Line {i+1}: metadata missing '{field}'")

    # Check messages array
    if not isinstance(messages, list) or len(messages) == 0:
        errors.append(f"Line {i+1}: messages not a non-empty array")
        continue

    for j, msg in enumerate(messages):
        # Check role
        role = msg.get("role", "")
        if role not in ("user", "assistant", "tool"):
            errors.append(f"Line {i+1} msg {j}: invalid role '{role}'")
            continue

        # Check content
        content = msg.get("content")
        if content is None:
            errors.append(f"Line {i+1} msg {j}: content is null")
        elif role != "assistant" or not msg.get("tool_calls"):
            # Non-assistant or assistant without tool_calls: content must be non-empty string
            if not isinstance(content, str) or content == "":
                errors.append(f"Line {i+1} msg {j}: content empty/non-string for role={role}")

        # Assistant with tool_calls: content must be empty string
        if role == "assistant" and msg.get("tool_calls"):
            if content != "":
                errors.append(f"Line {i+1} msg {j}: assistant with tool_calls should have empty content, got: {repr(content[:50])}")

        # Tool role: check name field
        if role == "tool":
            name = msg.get("name", "")
            if not name:
                errors.append(f"Line {i+1} msg {j}: tool role missing 'name'")
            elif tool_names and name not in tool_names:
                errors.append(f"Line {i+1} msg {j}: tool name '{name}' not in manifest")

    # Check final assistant message has text content
    last_msg = messages[-1]
    if last_msg["role"] != "assistant":
        errors.append(f"Line {i+1}: last message role is {last_msg['role']}, expected assistant")
    elif not last_msg.get("content", ""):
        errors.append(f"Line {i+1}: final assistant message has empty content")

if errors:
    print(f"VALIDATION FAILED: {len(errors)} errors")
    for e in errors[:30]:
        print(f"  - {e}")
    if len(errors) > 30:
        print(f"  ... and {len(errors) - 30} more")
else:
    print("VALIDATION PASSED: all 55 examples comply with format rules")

# Category/difficulty summary
print("\n===== SUMMARY =====")
print(f"Total: {len(trimmed)} examples")
print(f"Debugging: {sum(1 for e in trimmed if e['metadata']['category'] == 'debugging')}")
print(f"Tool selection: {sum(1 for e in trimmed if e['metadata']['category'] == 'tool_selection')}")
print(f"Difficulty: {json.dumps({d: sum(1 for e in trimmed if e['metadata']['difficulty']==d) for d in ['easy','medium','hard']})}")
print(f"Multi-step: {sum(1 for e in trimmed if e['metadata']['multi_step'])}")
print(f"Errors: {len(errors)}")
