import json

def ex(user_content, tool_calls, tool_results, assistant_content, category, difficulty, tools_used, multi_step, req_clarify, req_confirm):
    msgs = [{"role": "user", "content": user_content}]
    for i, (name, args) in enumerate(tool_calls):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": name, "arguments": args}}]})
        if i < len(tool_results):
            msgs.append({"role": "tool", "name": tool_results[i][0], "content": tool_results[i][1]})
    msgs.append({"role": "assistant", "content": assistant_content})
    return {"messages": msgs, "metadata": {"category": category, "difficulty": difficulty, "tools_used": tools_used, "multi_step": multi_step, "requires_clarification": req_clarify, "requires_confirmation": req_confirm}}

lines = []

# === 22 new filesystem_actions examples (71-92) ===

# 71 - create + read + edit + read (4 steps)
lines.append(json.dumps(ex(
    "Create E:/data/output.log, write 'Starting process', read it, then change 'Starting' to 'Running', and verify the final content.",
    [("filesystem.create_file", {"path": "E:/data/output.log", "content": "Starting process"}), ("filesystem.read_file", {"path": "E:/data/output.log"}), ("filesystem.edit_file", {"path": "E:/data/output.log", "old_string": "Starting process", "new_string": "Running process"}), ("filesystem.read_file", {"path": "E:/data/output.log"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/data/output.log","created":true}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/output.log","content":"Starting process"}'), ("filesystem.edit_file", '{"success":true,"path":"E:/data/output.log","edits_applied":1}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/output.log","content":"Running process"}')],
    "Full workflow complete: Created E:/data/output.log, wrote 'Starting process', verified, changed to 'Running process', and confirmed final content is 'Running process'.",
    "filesystem_actions", "hard", ["filesystem.create_file", "filesystem.read_file", "filesystem.edit_file"], True, False, False
), ensure_ascii=False))

# 72 - 3x create_file
lines.append(json.dumps(ex(
    "Create three files: E:/data/a.txt with 'Alpha', E:/data/b.txt with 'Beta', and E:/data/c.txt with 'Gamma'.",
    [("filesystem.create_file", {"path": "E:/data/a.txt", "content": "Alpha"}), ("filesystem.create_file", {"path": "E:/data/b.txt", "content": "Beta"}), ("filesystem.create_file", {"path": "E:/data/c.txt", "content": "Gamma"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/data/a.txt","created":true}'), ("filesystem.create_file", '{"success":true,"path":"E:/data/b.txt","created":true}'), ("filesystem.create_file", '{"success":true,"path":"E:/data/c.txt","created":true}')],
    "Created all three files: E:/data/a.txt (Alpha), E:/data/b.txt (Beta), E:/data/c.txt (Gamma).",
    "filesystem_actions", "medium", ["filesystem.create_file"], True, False, False
), ensure_ascii=False))

# 73 - copy + move
lines.append(json.dumps(ex(
    "Copy E:/source/data.json to E:/backup/data.json, then move E:/source/data.json to E:/archive/data.json.",
    [("filesystem.copy_file", {"source": "E:/source/data.json", "destination": "E:/backup/data.json"}), ("filesystem.move_file", {"source": "E:/source/data.json", "destination": "E:/archive/data.json"})],
    [("filesystem.copy_file", '{"success":true,"source":"E:/source/data.json","destination":"E:/backup/data.json","copied":true}'), ("filesystem.move_file", '{"success":true,"source":"E:/source/data.json","destination":"E:/archive/data.json","moved":true}')],
    "Copied E:/source/data.json to E:/backup/data.json and moved E:/source/data.json to E:/archive/data.json.",
    "filesystem_actions", "medium", ["filesystem.copy_file", "filesystem.move_file"], True, False, False
), ensure_ascii=False))

# 74 - list + search
lines.append(json.dumps(ex(
    "List E:/projects and find all .py files within it.",
    [("filesystem.list_directory", {"path": "E:/projects"}), ("filesystem.search_files", {"pattern": "*.py", "path": "E:/projects"})],
    [("filesystem.list_directory", '{"success":true,"path":"E:/projects","contents":["main.py","utils.py","README.md"]}'), ("filesystem.search_files", '{"success":true,"pattern":"*.py","path":"E:/projects","results":["main.py","utils.py"]}')],
    "E:/projects contains: main.py, utils.py, README.md. Python files found: main.py, utils.py.",
    "filesystem_actions", "medium", ["filesystem.list_directory", "filesystem.search_files"], True, False, False
), ensure_ascii=False))

# 75 - write + file_exists
lines.append(json.dumps(ex(
    "Write 'Test content' to E:/test/verify.txt and confirm it exists.",
    [("filesystem.write_file", {"path": "E:/test/verify.txt", "content": "Test content"}), ("filesystem.file_exists", {"path": "E:/test/verify.txt"})],
    [("filesystem.write_file", '{"success":true,"path":"E:/test/verify.txt","written":true}'), ("filesystem.file_exists", '{"success":true,"path":"E:/test/verify.txt","exists":true}')],
    "Wrote 'Test content' to E:/test/verify.txt and confirmed it exists.",
    "filesystem_actions", "easy", ["filesystem.write_file", "filesystem.file_exists"], True, False, False
), ensure_ascii=False))

# 76 - read + edit
lines.append(json.dumps(ex(
    "Read E:/data/config.json, then edit it to change 'debug' to 'verbose'.",
    [("filesystem.read_file", {"path": "E:/data/config.json"}), ("filesystem.edit_file", {"path": "E:/data/config.json", "old_string": "debug", "new_string": "verbose"})],
    [("filesystem.read_file", '{"success":true,"path":"E:/data/config.json","content":"{\\"debug\\":true}"}'), ("filesystem.edit_file", '{"success":true,"path":"E:/data/config.json","edits_applied":1}')],
    "Edited E:/data/config.json: changed 'debug' to 'verbose'. New content: {\"verbose\": true}.",
    "filesystem_actions", "medium", ["filesystem.read_file", "filesystem.edit_file"], True, False, False
), ensure_ascii=False))

# 77 - move + list
lines.append(json.dumps(ex(
    "Move E:/incoming/report.pdf to E:/completed/report.pdf and list E:/completed.",
    [("filesystem.move_file", {"source": "E:/incoming/report.pdf", "destination": "E:/completed/report.pdf"}), ("filesystem.list_directory", {"path": "E:/completed"})],
    [("filesystem.move_file", '{"success":true,"source":"E:/incoming/report.pdf","destination":"E:/completed/report.pdf","moved":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/completed","contents":["report.pdf"]}')],
    "Moved E:/incoming/report.pdf to E:/completed/report.pdf. E:/completed now contains: report.pdf.",
    "filesystem_actions", "medium", ["filesystem.move_file", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 78 - search + read
lines.append(json.dumps(ex(
    "Search for config.json files anywhere and read the first match.",
    [("filesystem.search_files", {"pattern": "config.json"}), ("filesystem.read_file", {"path": "E:/data/config.json"})],
    [("filesystem.search_files", '{"success":true,"pattern":"config.json","results":["E:/data/config.json"]}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/config.json","content":"{\\"verbose\\":true}"}')],
    "Found E:/data/config.json. Content: {\"verbose\":true}.",
    "filesystem_actions", "medium", ["filesystem.search_files", "filesystem.read_file"], True, False, False
), ensure_ascii=False))

# 79 - 2x create_file + list
lines.append(json.dumps(ex(
    "Create E:/workspace/src/main.py and E:/workspace/src/utils.py, then list E:/workspace/src.",
    [("filesystem.create_file", {"path": "E:/workspace/src/main.py", "content": "# main"}), ("filesystem.create_file", {"path": "E:/workspace/src/utils.py", "content": "# utils"}), ("filesystem.list_directory", {"path": "E:/workspace/src"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/workspace/src/main.py","created":true}'), ("filesystem.create_file", '{"success":true,"path":"E:/workspace/src/utils.py","created":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/workspace/src","contents":["main.py","utils.py"]}')],
    "Created E:/workspace/src/main.py and E:/workspace/src/utils.py. Directory listing: main.py, utils.py.",
    "filesystem_actions", "medium", ["filesystem.create_file", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 80 - delete + file_exists
lines.append(json.dumps(ex(
    "Delete E:/temp/old.txt and verify it's gone.",
    [("filesystem.delete_file", {"path": "E:/temp/old.txt"}), ("filesystem.file_exists", {"path": "E:/temp/old.txt"})],
    [("filesystem.delete_file", '{"success":true,"path":"E:/temp/old.txt","deleted":true}'), ("filesystem.file_exists", '{"success":true,"path":"E:/temp/old.txt","exists":false}')],
    "Deleted E:/temp/old.txt and confirmed it no longer exists.",
    "filesystem_actions", "medium", ["filesystem.delete_file", "filesystem.file_exists"], True, False, False
), ensure_ascii=False))

# 81 - edit + read + search
lines.append(json.dumps(ex(
    "Edit E:/notes/log.txt to add 'Error: none' at the end, read it, then search for log files.",
    [("filesystem.edit_file", {"path": "E:/notes/log.txt", "old_string": "", "new_string": "Error: none\n"}), ("filesystem.read_file", {"path": "E:/notes/log.txt"}), ("filesystem.search_files", {"pattern": "*.log", "path": "E:/notes"})],
    [("filesystem.edit_file", '{"success":true,"path":"E:/notes/log.txt","edits_applied":1}'), ("filesystem.read_file", '{"success":true,"path":"E:/notes/log.txt","content":"Error: none"}'), ("filesystem.search_files", '{"success":true,"pattern":"*.log","path":"E:/notes","results":["log.txt"]}')],
    "Added 'Error: none' to E:/notes/log.txt. Content verified. Found log files: log.txt.",
    "filesystem_actions", "hard", ["filesystem.edit_file", "filesystem.read_file", "filesystem.search_files"], True, False, False
), ensure_ascii=False))

# 82 - 3x write_file + list
lines.append(json.dumps(ex(
    "Write E:/store/items/a.txt with 'Apple', E:/store/items/b.txt with 'Banana', E:/store/items/c.txt with 'Cherry', then list E:/store/items.",
    [("filesystem.write_file", {"path": "E:/store/items/a.txt", "content": "Apple"}), ("filesystem.write_file", {"path": "E:/store/items/b.txt", "content": "Banana"}), ("filesystem.write_file", {"path": "E:/store/items/c.txt", "content": "Cherry"}), ("filesystem.list_directory", {"path": "E:/store/items"})],
    [("filesystem.write_file", '{"success":true,"path":"E:/store/items/a.txt","written":true}'), ("filesystem.write_file", '{"success":true,"path":"E:/store/items/b.txt","written":true}'), ("filesystem.write_file", '{"success":true,"path":"E:/store/items/c.txt","written":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/store/items","contents":["a.txt","b.txt","c.txt"]}')],
    "Wrote a.txt (Apple), b.txt (Banana), c.txt (Cherry). E:/store/items contains: a.txt, b.txt, c.txt.",
    "filesystem_actions", "medium", ["filesystem.write_file", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 83 - search + read (empty results)
lines.append(json.dumps(ex(
    "Find all .exe files in E:/docs.",
    [("filesystem.search_files", {"pattern": "*.exe", "path": "E:/docs"})],
    [("filesystem.search_files", '{"success":true,"pattern":"*.exe","path":"E:/docs","results":[]}')],
    "No .exe files found in E:/docs.",
    "filesystem_actions", "easy", ["filesystem.search_files"], False, False, False
), ensure_ascii=False))

# 84 - file_exists (false)
lines.append(json.dumps(ex(
    "Does E:/nonexistent.txt exist?",
    [("filesystem.file_exists", {"path": "E:/nonexistent.txt"})],
    [("filesystem.file_exists", '{"success":true,"path":"E:/nonexistent.txt","exists":false}')],
    "No, E:/nonexistent.txt does not exist.",
    "filesystem_actions", "easy", ["filesystem.file_exists"], False, False, False
), ensure_ascii=False))

# 85 - 4-step workflow
lines.append(json.dumps(ex(
    "Create E:/test/output.log, write 'Starting process', read it, then change 'Starting' to 'Running', and verify the final content.",
    [("filesystem.create_file", {"path": "E:/test/output.log", "content": "Starting process"}), ("filesystem.read_file", {"path": "E:/test/output.log"}), ("filesystem.edit_file", {"path": "E:/test/output.log", "old_string": "Starting process", "new_string": "Running process"}), ("filesystem.read_file", {"path": "E:/test/output.log"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/test/output.log","created":true}'), ("filesystem.read_file", '{"success":true,"path":"E:/test/output.log","content":"Starting process"}'), ("filesystem.edit_file", '{"success":true,"path":"E:/test/output.log","edits_applied":1}'), ("filesystem.read_file", '{"success":true,"path":"E:/test/output.log","content":"Running process"}')],
    "Full workflow complete: Created E:/test/output.log, wrote 'Starting process', verified, changed to 'Running process', and confirmed final content is 'Running process'.",
    "filesystem_actions", "hard", ["filesystem.create_file", "filesystem.read_file", "filesystem.edit_file"], True, False, False
), ensure_ascii=False))

# 86 - 3x write_file + list
lines.append(json.dumps(ex(
    "Write E:/store/items/a.txt with 'Apple', E:/store/items/b.txt with 'Banana', E:/store/items/c.txt with 'Cherry', then list E:/store/items.",
    [("filesystem.write_file", {"path": "E:/store/items/a.txt", "content": "Apple"}), ("filesystem.write_file", {"path": "E:/store/items/b.txt", "content": "Banana"}), ("filesystem.write_file", {"path": "E:/store/items/c.txt", "content": "Cherry"}), ("filesystem.list_directory", {"path": "E:/store/items"})],
    [("filesystem.write_file", '{"success":true,"path":"E:/store/items/a.txt","written":true}'), ("filesystem.write_file", '{"success":true,"path":"E:/store/items/b.txt","written":true}'), ("filesystem.write_file", '{"success":true,"path":"E:/store/items/c.txt","written":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/store/items","contents":["a.txt","b.txt","c.txt"]}')],
    "Wrote a.txt (Apple), b.txt (Banana), c.txt (Cherry). E:/store/items contains: a.txt, b.txt, c.txt.",
    "filesystem_actions", "medium", ["filesystem.write_file", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 87 - copy + move + file_exists (3 steps)
lines.append(json.dumps(ex(
    "Copy E:/data/file1.csv to E:/backup/file1.csv, move E:/data/file1.csv to E:/archive/file1.csv, then verify E:/archive/file1.csv exists.",
    [("filesystem.copy_file", {"source": "E:/data/file1.csv", "destination": "E:/backup/file1.csv"}), ("filesystem.move_file", {"source": "E:/data/file1.csv", "destination": "E:/archive/file1.csv"}), ("filesystem.file_exists", {"path": "E:/archive/file1.csv"})],
    [("filesystem.copy_file", '{"success":true,"source":"E:/data/file1.csv","destination":"E:/backup/file1.csv","copied":true}'), ("filesystem.move_file", '{"success":true,"source":"E:/data/file1.csv","destination":"E:/archive/file1.csv","moved":true}'), ("filesystem.file_exists", '{"success":true,"path":"E:/archive/file1.csv","exists":true}')],
    "Copied E:/data/file1.csv to E:/backup/file1.csv, moved E:/data/file1.csv to E:/archive/file1.csv, and verified it exists.",
    "filesystem_actions", "hard", ["filesystem.copy_file", "filesystem.move_file", "filesystem.file_exists"], True, False, False
), ensure_ascii=False))

# 88 - search_files (empty results)
lines.append(json.dumps(ex(
    "Search for all .xyz files in E:/projects.",
    [("filesystem.search_files", {"pattern": "*.xyz", "path": "E:/projects"})],
    [("filesystem.search_files", '{"success":true,"pattern":"*.xyz","path":"E:/projects","results":[]}')],
    "No .xyz files found in E:/projects.",
    "filesystem_actions", "easy", ["filesystem.search_files"], False, False, False
), ensure_ascii=False))

# 89 - read_file (failure)
lines.append(json.dumps(ex(
    "Read E:/missing/config.yaml.",
    [("filesystem.read_file", {"path": "E:/missing/config.yaml"})],
    [("filesystem.read_file", '{"success":false,"path":"E:/missing/config.yaml","error":"File not found"}')],
    "Could not read E:/missing/config.yaml: file not found.",
    "filesystem_actions", "easy", ["filesystem.read_file"], False, False, False
), ensure_ascii=False))

# 90 - list_directory (failure)
lines.append(json.dumps(ex(
    "List the contents of E:/nonexistent_dir.",
    [("filesystem.list_directory", {"path": "E:/nonexistent_dir"})],
    [("filesystem.list_directory", '{"success":false,"path":"E:/nonexistent_dir","error":"Directory not found"}')],
    "Could not list E:/nonexistent_dir: directory not found.",
    "filesystem_actions", "easy", ["filesystem.list_directory"], False, False, False
), ensure_ascii=False))

# 91 - create_file (failure)
lines.append(json.dumps(ex(
    "Create E:/notes/todo.txt with content 'New content'.",
    [("filesystem.create_file", {"path": "E:/notes/todo.txt", "content": "New content"})],
    [("filesystem.create_file", '{"success":false,"path":"E:/notes/todo.txt","error":"File already exists"}')],
    "Could not create E:/notes/todo.txt: file already exists. Use write_file to overwrite it.",
    "filesystem_actions", "easy", ["filesystem.create_file"], False, False, False
), ensure_ascii=False))

# 92 - delete_file (failure)
lines.append(json.dumps(ex(
    "Delete E:/nonexistent/file.txt.",
    [("filesystem.delete_file", {"path": "E:/nonexistent/file.txt"})],
    [("filesystem.delete_file", '{"success":false,"path":"E:/nonexistent/file.txt","error":"File not found"}')],
    "Failed to delete E:/nonexistent/file.txt: file not found.",
    "filesystem_actions", "easy", ["filesystem.delete_file"], False, False, False
), ensure_ascii=False))

with open("E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_03_action.jsonl", "a", encoding="utf-8") as f:
    for line in lines:
        f.write(line + "\n")

print(f"Added {len(lines)} filesystem_actions examples")
