import json

filepath = "E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_03_action.jsonl"

# Read all lines
with open(filepath, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Remove duplicates, keeping first occurrence
seen_prompts = {}
unique_lines = []
duplicates_removed = 0
for line in lines:
    line = line.strip()
    if not line:
        continue
    obj = json.loads(line)
    prompt = obj["messages"][0]["content"]
    if prompt in seen_prompts:
        duplicates_removed += 1
        continue
    seen_prompts[prompt] = True
    unique_lines.append(line)

print(f"Removed {duplicates_removed} duplicates")
print(f"Remaining: {len(unique_lines)} lines")

# Write back
with open(filepath, "w", encoding="utf-8") as f:
    for line in unique_lines:
        f.write(line + "\n")
