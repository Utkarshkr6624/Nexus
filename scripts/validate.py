import json

filepath = "E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_03_action.jsonl"

with open(filepath, "r", encoding="utf-8") as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")

errors = []
answer_vs_action_count = 0
filesystem_actions_count = 0

for i, line in enumerate(lines, 1):
    line = line.strip()
    if not line:
        continue

    # 1. Valid JSON
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        errors.append(f"Line {i}: Invalid JSON: {e}")
        continue

    # 2. Has "messages" array with >= 2 entries
    if "messages" not in obj:
        errors.append(f"Line {i}: Missing 'messages' key")
        continue
    if len(obj["messages"]) < 2:
        errors.append(f"Line {i}: messages array has < 2 entries")
        continue

    # 3. First message is "user"
    if obj["messages"][0]["role"] != "user":
        errors.append(f"Line {i}: First message role is not 'user'")

    # 4. Last message is "assistant" (no tool_calls)
    last_msg = obj["messages"][-1]
    if last_msg["role"] != "assistant":
        errors.append(f"Line {i}: Last message role is not 'assistant'")
    if "tool_calls" in last_msg:
        errors.append(f"Line {i}: Last message has tool_calls")

    # 5. No empty content fields
    for j, msg in enumerate(obj["messages"]):
        if "content" not in msg or msg["content"] is None:
            errors.append(f"Line {i}, msg {j}: Missing or null content")

    # 6. Check tool names
    tools_in_example = []
    for j, msg in enumerate(obj["messages"]):
        if msg["role"] == "assistant" and "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                tool_name = tc["function"]["name"]
                tools_in_example.append(tool_name)
                # Check fully qualified
                if "." not in tool_name:
                    errors.append(f"Line {i}, msg {j}: Tool name not fully qualified: {tool_name}")
        if msg["role"] == "tool":
            tool_name = msg.get("name", "")
            if "." not in tool_name:
                errors.append(f"Line {i}, msg {j}: Tool result name not fully qualified: {tool_name}")

    # 7. Check tool results reference tools that were called
    called_tools = set(tools_in_example)
    for msg in obj["messages"]:
        if msg["role"] == "tool":
            if msg.get("name") not in called_tools:
                errors.append(f"Line {i}: Tool result for {msg.get('name')} not in called tools")

    # 8. Count categories
    cat = obj.get("metadata", {}).get("category", "")
    if cat == "answer_vs_action":
        answer_vs_action_count += 1
    elif cat == "filesystem_actions":
        filesystem_actions_count += 1

    # 9. Check metadata fields
    meta = obj.get("metadata", {})
    for field in ["category", "difficulty", "tools_used", "multi_step", "requires_clarification", "requires_confirmation"]:
        if field not in meta:
            errors.append(f"Line {i}: Missing metadata field '{field}'")

    # 10. Check difficulty
    diff = meta.get("difficulty", "")
    if diff not in ["easy", "medium", "hard"]:
        errors.append(f"Line {i}: Invalid difficulty '{diff}'")

    # 11. Check tools_used matches actual tools
    actual_tools = set(tools_in_example)
    declared_tools = set(meta.get("tools_used", []))
    if actual_tools != declared_tools:
        errors.append(f"Line {i}: tools_used mismatch: declared={declared_tools}, actual={actual_tools}")

print(f"\nValidation results:")
print(f"  answer_vs_action examples: {answer_vs_action_count}")
print(f"  filesystem_actions examples: {filesystem_actions_count}")
print(f"  Total: {answer_vs_action_count + filesystem_actions_count}")
print(f"  Errors: {len(errors)}")
for e in errors:
    print(f"  - {e}")
