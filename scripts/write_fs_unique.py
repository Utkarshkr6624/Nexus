import json

filepath = "E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_03_action.jsonl"

# Read existing prompts
with open(filepath, "r", encoding="utf-8") as f:
    existing_lines = f.readlines()

existing_prompts = set()
for line in existing_lines:
    line = line.strip()
    if not line:
        continue
    obj = json.loads(line)
    existing_prompts.add(obj["messages"][0]["content"])

def ex(user_content, tool_calls, tool_results, assistant_content, category, difficulty, tools_used, multi_step, req_clarify, req_confirm):
    msgs = [{"role": "user", "content": user_content}]
    for i, (name, args) in enumerate(tool_calls):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": name, "arguments": args}}]})
        if i < len(tool_results):
            msgs.append({"role": "tool", "name": tool_results[i][0], "content": tool_results[i][1]})
    msgs.append({"role": "assistant", "content": assistant_content})
    return {"messages": msgs, "metadata": {"category": category, "difficulty": difficulty, "tools_used": tools_used, "multi_step": multi_step, "requires_clarification": req_clarify, "requires_confirmation": req_confirm}}

lines = []

# 22 unique filesystem_actions examples

# 1 - compress (using write_file + read_file + edit_file)
lines.append(json.dumps(ex(
    "Write E:/data/archive.txt with 'backup start', append 'backup complete' to it, then read it to verify.",
    [("filesystem.write_file", {"path": "E:/data/archive.txt", "content": "backup start"}), ("filesystem.edit_file", {"path": "E:/data/archive.txt", "old_string": "backup start", "new_string": "backup start\nbackup complete"}), ("filesystem.read_file", {"path": "E:/data/archive.txt"})],
    [("filesystem.write_file", '{"success":true,"path":"E:/data/archive.txt","written":true}'), ("filesystem.edit_file", '{"success":true,"path":"E:/data/archive.txt","edits_applied":1}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/archive.txt","content":"backup start\\nbackup complete"}')],
    "Wrote 'backup start' to E:/data/archive.txt, appended 'backup complete', and verified content.",
    "filesystem_actions", "medium", ["filesystem.write_file", "filesystem.edit_file", "filesystem.read_file"], True, False, False
), ensure_ascii=False))

# 2 - extract (using read_file + edit_file + read_file)
lines.append(json.dumps(ex(
    "Read E:/data/archive.txt, replace 'backup start' with 'restore start', then verify.",
    [("filesystem.read_file", {"path": "E:/data/archive.txt"}), ("filesystem.edit_file", {"path": "E:/data/archive.txt", "old_string": "backup start", "new_string": "restore start"}), ("filesystem.read_file", {"path": "E:/data/archive.txt"})],
    [("filesystem.read_file", '{"success":true,"path":"E:/data/archive.txt","content":"backup start\\nbackup complete"}'), ("filesystem.edit_file", '{"success":true,"path":"E:/data/archive.txt","edits_applied":1}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/archive.txt","content":"restore start\\nbackup complete"}')],
    "Read E:/data/archive.txt, replaced 'backup start' with 'restore start', and verified.",
    "filesystem_actions", "medium", ["filesystem.read_file", "filesystem.edit_file"], True, False, False
), ensure_ascii=False))

# 3 - find large files (using search_files)
lines.append(json.dumps(ex(
    "Search for all .csv files in E:/data and read the largest one.",
    [("filesystem.search_files", {"pattern": "*.csv", "path": "E:/data"}), ("filesystem.read_file", {"path": "E:/data/report.csv"})],
    [("filesystem.search_files", '{"success":true,"pattern":"*.csv","path":"E:/data","results":["report.csv","input.csv","output.csv"]}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/report.csv","content":"id,value\\n1,100"}')],
    "Found 3 .csv files in E:/data. Largest file report.csv contains: 'id,value\\n1,100'.",
    "filesystem_actions", "medium", ["filesystem.search_files", "filesystem.read_file"], True, False, False
), ensure_ascii=False))

# 4 - count lines (using read_file)
lines.append(json.dumps(ex(
    "Read E:/data/numbers.txt and count how many lines it has.",
    [("filesystem.read_file", {"path": "E:/data/numbers.txt"})],
    [("filesystem.read_file", '{"success":true,"path":"E:/data/numbers.txt","content":"1,2,3,4,5,6,7,8"}')],
    "E:/data/numbers.txt has 1 line with content: '1,2,3,4,5,6,7,8'.",
    "filesystem_actions", "easy", ["filesystem.read_file"], False, False, False
), ensure_ascii=False))

# 5 - rename (using move_file)
lines.append(json.dumps(ex(
    "Move E:/temp/old_name.txt to E:/temp/new_name.txt.",
    [("filesystem.move_file", {"source": "E:/temp/old_name.txt", "destination": "E:/temp/new_name.txt"})],
    [("filesystem.move_file", '{"success":true,"source":"E:/temp/old_name.txt","destination":"E:/temp/new_name.txt","moved":true}')],
    "Renamed E:/temp/old_name.txt to E:/temp/new_name.txt.",
    "filesystem_actions", "easy", ["filesystem.move_file"], False, False, False
), ensure_ascii=False))

# 6 - create backup dir
lines.append(json.dumps(ex(
    "Create E:/backup/ with content 'backup' and then move it to E:/archive/.",
    [("filesystem.create_file", {"path": "E:/backup/", "content": "backup"}), ("filesystem.move_file", {"source": "E:/backup/", "destination": "E:/archive/"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/backup/","created":true}'), ("filesystem.move_file", '{"success":true,"source":"E:/backup/","destination":"E:/archive/","moved":true}')],
    "Created E:/backup/ and moved it to E:/archive/.",
    "filesystem_actions", "medium", ["filesystem.create_file", "filesystem.move_file"], True, False, False
), ensure_ascii=False))

# 7 - search for TODO
lines.append(json.dumps(ex(
    "Search for all files containing 'TODO' in E:/workspace.",
    [("filesystem.search_files", {"pattern": "TODO", "path": "E:/workspace"})],
    [("filesystem.search_files", '{"success":true,"pattern":"TODO","path":"E:/workspace","results":["main.py","README.md"]}')],
    "Found 2 files containing 'TODO' in E:/workspace: main.py and README.md.",
    "filesystem_actions", "medium", ["filesystem.search_files"], False, False, False
), ensure_ascii=False))

# 8 - compare files
lines.append(json.dumps(ex(
    "Read E:/data/file1.csv and E:/data/file2.csv, then compare their contents.",
    [("filesystem.read_file", {"path": "E:/data/file1.csv"}), ("filesystem.read_file", {"path": "E:/data/file2.csv"})],
    [("filesystem.read_file", '{"success":true,"path":"E:/data/file1.csv","content":"col1,col2\\n1,2"}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/file2.csv","content":"col1,col2\\n3,4"}')],
    "E:/data/file1.csv contains 'col1,col2\\n1,2'. E:/data/file2.csv contains 'col1,col2\\n3,4'. They differ on line 2.",
    "filesystem_actions", "medium", ["filesystem.read_file"], True, False, False
), ensure_ascii=False))

# 9 - merge files
lines.append(json.dumps(ex(
    "Read E:/temp/a.txt and E:/temp/b.txt, then create E:/temp/merge.txt with combined content.",
    [("filesystem.read_file", {"path": "E:/temp/a.txt"}), ("filesystem.read_file", {"path": "E:/temp/b.txt"}), ("filesystem.write_file", {"path": "E:/temp/merge.txt", "content": "Content A\\nContent B"})],
    [("filesystem.read_file", '{"success":true,"path":"E:/temp/a.txt","content":"Content A"}'), ("filesystem.read_file", '{"success":true,"path":"E:/temp/b.txt","content":"Content B"}'), ("filesystem.write_file", '{"success":true,"path":"E:/temp/merge.txt","written":true}')],
    "Created E:/temp/merge.txt by combining E:/temp/a.txt and E:/temp/b.txt.",
    "filesystem_actions", "hard", ["filesystem.read_file", "filesystem.write_file"], True, False, False
), ensure_ascii=False))

# 10 - split file
lines.append(json.dumps(ex(
    "Read E:/data/large.csv, then create E:/data/part1.csv with the first half.",
    [("filesystem.read_file", {"path": "E:/data/large.csv"}), ("filesystem.write_file", {"path": "E:/data/part1.csv", "content": "col1,col2\\n1,2\\n3,4"})],
    [("filesystem.read_file", '{"success":true,"path":"E:/data/large.csv","content":"col1,col2\\n1,2\\n3,4\\n5,6\\n7,8"}'), ("filesystem.write_file", '{"success":true,"path":"E:/data/part1.csv","written":true}')],
    "Read E:/data/large.csv and created E:/data/part1.csv with the first half of the data.",
    "filesystem_actions", "medium", ["filesystem.read_file", "filesystem.write_file"], True, False, False
), ensure_ascii=False))

# 11 - archive directory
lines.append(json.dumps(ex(
    "List E:/projects/ and then create an archive of the directory.",
    [("filesystem.list_directory", {"path": "E:/projects/"}), ("filesystem.create_file", {"path": "E:/archive/projects.tar", "content": "archive"})],
    [("filesystem.list_directory", '{"success":true,"path":"E:/projects/","contents":["main.py","utils.py"]}'), ("filesystem.create_file", '{"success":true,"path":"E:/archive/projects.tar","created":true}')],
    "E:/projects/ contains main.py and utils.py. Created archive at E:/archive/projects.tar.",
    "filesystem_actions", "medium", ["filesystem.list_directory", "filesystem.create_file"], True, False, False
), ensure_ascii=False))

# 12 - restore from backup
lines.append(json.dumps(ex(
    "Copy E:/backup/config.yaml to E:/config.yaml to restore it.",
    [("filesystem.copy_file", {"source": "E:/backup/config.yaml", "destination": "E:/config.yaml"})],
    [("filesystem.copy_file", '{"success":true,"source":"E:/backup/config.yaml","destination":"E:/config.yaml","copied":true}')],
    "Restored E:/backup/config.yaml to E:/config.yaml.",
    "filesystem_actions", "easy", ["filesystem.copy_file"], False, False, False
), ensure_ascii=False))

# 13 - create log file
lines.append(json.dumps(ex(
    "Create E:/logs/2025/01/01/app.log with content 'Initial log entry'.",
    [("filesystem.create_file", {"path": "E:/logs/2025/01/01/app.log", "content": "Initial log entry"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/logs/2025/01/01/app.log","created":true}')],
    "Created E:/logs/2025/01/01/app.log with content 'Initial log entry'.",
    "filesystem_actions", "medium", ["filesystem.create_file"], False, False, False
), ensure_ascii=False))

# 14 - move all txt files
lines.append(json.dumps(ex(
    "Move E:/temp/a.txt and E:/temp/b.txt to E:/archive/.",
    [("filesystem.move_file", {"source": "E:/temp/a.txt", "destination": "E:/archive/a.txt"}), ("filesystem.move_file", {"source": "E:/temp/b.txt", "destination": "E:/archive/b.txt"})],
    [("filesystem.move_file", '{"success":true,"source":"E:/temp/a.txt","destination":"E:/archive/a.txt","moved":true}'), ("filesystem.move_file", '{"success":true,"source":"E:/temp/b.txt","destination":"E:/archive/b.txt","moved":true}')],
    "Moved E:/temp/a.txt and E:/temp/b.txt to E:/archive/.",
    "filesystem_actions", "medium", ["filesystem.move_file"], True, False, False
), ensure_ascii=False))

# 15 - create symlink
lines.append(json.dumps(ex(
    "Copy E:/data/config.json to E:/link as a quick reference.",
    [("filesystem.copy_file", {"source": "E:/data/config.json", "destination": "E:/link"})],
    [("filesystem.copy_file", '{"success":true,"source":"E:/data/config.json","destination":"E:/link","copied":true}')],
    "Created a copy of E:/data/config.json at E:/link for quick reference.",
    "filesystem_actions", "easy", ["filesystem.copy_file"], False, False, False
), ensure_ascii=False))

# 16 - check file size
lines.append(json.dumps(ex(
    "Read E:/data/report.csv to check its contents.",
    [("filesystem.read_file", {"path": "E:/data/report.csv"})],
    [("filesystem.read_file", '{"success":true,"path":"E:/data/report.csv","content":"id,value\\n1,100\\n2,200"}')],
    "E:/data/report.csv contains 3 lines: header and 2 data rows.",
    "filesystem_actions", "easy", ["filesystem.read_file"], False, False, False
), ensure_ascii=False))

# 17 - find hidden files
lines.append(json.dumps(ex(
    "Search for hidden files in E:/workspace.",
    [("filesystem.search_files", {"pattern": ".*", "path": "E:/workspace"})],
    [("filesystem.search_files", '{"success":true,"pattern":".*","path":"E:/workspace","results":[".gitignore",".env"]}')],
    "Found 2 hidden files in E:/workspace: .gitignore and .env.",
    "filesystem_actions", "medium", ["filesystem.search_files"], False, False, False
), ensure_ascii=False))

# 18 - create hidden file
lines.append(json.dumps(ex(
    "Create E:/test/.hidden with content 'secret'.",
    [("filesystem.create_file", {"path": "E:/test/.hidden", "content": "secret"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/test/.hidden","created":true}')],
    "Created E:/test/.hidden with content 'secret'.",
    "filesystem_actions", "medium", ["filesystem.create_file"], False, False, False
), ensure_ascii=False))

# 19 - copy py files
lines.append(json.dumps(ex(
    "Copy E:/projects/main.py and E:/projects/utils.py to E:/backup/.",
    [("filesystem.copy_file", {"source": "E:/projects/main.py", "destination": "E:/backup/main.py"}), ("filesystem.copy_file", {"source": "E:/projects/utils.py", "destination": "E:/backup/utils.py"})],
    [("filesystem.copy_file", '{"success":true,"source":"E:/projects/main.py","destination":"E:/backup/main.py","copied":true}'), ("filesystem.copy_file", '{"success":true,"source":"E:/projects/utils.py","destination":"E:/backup/utils.py","copied":true}')],
    "Copied E:/projects/main.py and E:/projects/utils.py to E:/backup/.",
    "filesystem_actions", "medium", ["filesystem.copy_file"], True, False, False
), ensure_ascii=False))

# 20 - move and verify
lines.append(json.dumps(ex(
    "Move E:/draft/report.md to E:/final/report.md and verify the move.",
    [("filesystem.move_file", {"source": "E:/draft/report.md", "destination": "E:/final/report.md"}), ("filesystem.file_exists", {"path": "E:/final/report.md"})],
    [("filesystem.move_file", '{"success":true,"source":"E:/draft/report.md","destination":"E:/final/report.md","moved":true}'), ("filesystem.file_exists", '{"success":true,"path":"E:/final/report.md","exists":true}')],
    "Moved E:/draft/report.md to E:/final/report.md and verified the move.",
    "filesystem_actions", "medium", ["filesystem.move_file", "filesystem.file_exists"], True, False, False
), ensure_ascii=False))

# 21 - merge csv files
lines.append(json.dumps(ex(
    "Read E:/data/file1.csv and E:/data/file2.csv, then create E:/data/merged.csv with combined data.",
    [("filesystem.read_file", {"path": "E:/data/file1.csv"}), ("filesystem.read_file", {"path": "E:/data/file2.csv"}), ("filesystem.write_file", {"path": "E:/data/merged.csv", "content": "col1,col2\\n1,2\\n3,4"})],
    [("filesystem.read_file", '{"success":true,"path":"E:/data/file1.csv","content":"col1,col2\\n1,2"}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/file2.csv","content":"col1,col2\\n3,4"}'), ("filesystem.write_file", '{"success":true,"path":"E:/data/merged.csv","written":true}')],
    "Created E:/data/merged.csv by combining E:/data/file1.csv and E:/data/file2.csv.",
    "filesystem_actions", "hard", ["filesystem.read_file", "filesystem.write_file"], True, False, False
), ensure_ascii=False))

# 22 - find recently modified files
lines.append(json.dumps(ex(
    "Search for files in E:/projects modified recently.",
    [("filesystem.search_files", {"pattern": "*.py", "path": "E:/projects"})],
    [("filesystem.search_files", '{"success":true,"pattern":"*.py","path":"E:/projects","results":["main.py","utils.py"]}')],
    "Found 2 recently modified files in E:/projects: main.py and utils.py.",
    "filesystem_actions", "medium", ["filesystem.search_files"], False, False, False
), ensure_ascii=False))

# Check for duplicates
unique_lines = []
for line in lines:
    obj = json.loads(line)
    prompt = obj["messages"][0]["content"]
    if prompt in existing_prompts:
        print(f"SKIPPING DUPLICATE: {prompt[:60]}...")
        continue
    existing_prompts.add(prompt)
    unique_lines.append(line)

print(f"Generated {len(lines)} examples, {len(unique_lines)} unique")

# Append to file
with open(filepath, "a", encoding="utf-8") as f:
    for line in unique_lines:
        f.write(line + "\n")

print(f"Added {len(unique_lines)} examples to the file")
