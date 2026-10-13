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

# === filesystem_actions examples 56-70 ===

# 56
lines.append(json.dumps(ex(
    "Create E:/temp/log1.txt with 'Entry 1', E:/temp/log2.txt with 'Entry 2', then list E:/temp.",
    [("filesystem.create_file", {"path": "E:/temp/log1.txt", "content": "Entry 1"}), ("filesystem.create_file", {"path": "E:/temp/log2.txt", "content": "Entry 2"}), ("filesystem.list_directory", {"path": "E:/temp"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/temp/log1.txt","created":true}'), ("filesystem.create_file", '{"success":true,"path":"E:/temp/log2.txt","created":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/temp","contents":["log1.txt","log2.txt"]}')],
    "Created E:/temp/log1.txt and E:/temp/log2.txt. Directory listing: log1.txt, log2.txt.",
    "filesystem_actions", "medium", ["filesystem.create_file", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 57
lines.append(json.dumps(ex(
    "Copy E:/source/data.json to E:/backup/data.json and verify the copy.",
    [("filesystem.copy_file", {"source": "E:/source/data.json", "destination": "E:/backup/data.json"}), ("filesystem.file_exists", {"path": "E:/backup/data.json"})],
    [("filesystem.copy_file", '{"success":true,"source":"E:/source/data.json","destination":"E:/backup/data.json","copied":true}'), ("filesystem.file_exists", '{"success":true,"path":"E:/backup/data.json","exists":true}')],
    "Copied E:/source/data.json to E:/backup/data.json and verified the copy exists.",
    "filesystem_actions", "medium", ["filesystem.copy_file", "filesystem.file_exists"], True, False, False
), ensure_ascii=False))

# 58
lines.append(json.dumps(ex(
    "Move E:/incoming/report.pdf to E:/completed/report.pdf and list E:/completed.",
    [("filesystem.move_file", {"source": "E:/incoming/report.pdf", "destination": "E:/completed/report.pdf"}), ("filesystem.list_directory", {"path": "E:/completed"})],
    [("filesystem.move_file", '{"success":true,"source":"E:/incoming/report.pdf","destination":"E:/completed/report.pdf","moved":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/completed","contents":["report.pdf"]}')],
    "Moved E:/incoming/report.pdf to E:/completed/report.pdf. E:/completed now contains: report.pdf.",
    "filesystem_actions", "medium", ["filesystem.move_file", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 59
lines.append(json.dumps(ex(
    "Search for config.json files anywhere and read the first match.",
    [("filesystem.search_files", {"pattern": "config.json"}), ("filesystem.read_file", {"path": "E:/data/config.json"})],
    [("filesystem.search_files", '{"success":true,"pattern":"config.json","results":["E:/data/config.json"]}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/config.json","content":"{\\"verbose\\":true}"}')],
    "Found E:/data/config.json. Content: {\"verbose\":true}.",
    "filesystem_actions", "medium", ["filesystem.search_files", "filesystem.read_file"], True, False, False
), ensure_ascii=False))

# 60
lines.append(json.dumps(ex(
    "Create E:/workspace/src/main.py and E:/workspace/src/utils.py, then list E:/workspace/src.",
    [("filesystem.create_file", {"path": "E:/workspace/src/main.py", "content": "# main"}), ("filesystem.create_file", {"path": "E:/workspace/src/utils.py", "content": "# utils"}), ("filesystem.list_directory", {"path": "E:/workspace/src"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/workspace/src/main.py","created":true}'), ("filesystem.create_file", '{"success":true,"path":"E:/workspace/src/utils.py","created":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/workspace/src","contents":["main.py","utils.py"]}')],
    "Created E:/workspace/src/main.py and E:/workspace/src/utils.py. Directory listing: main.py, utils.py.",
    "filesystem_actions", "medium", ["filesystem.create_file", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 61
lines.append(json.dumps(ex(
    "Delete E:/temp/old.txt and verify it's gone.",
    [("filesystem.delete_file", {"path": "E:/temp/old.txt"}), ("filesystem.file_exists", {"path": "E:/temp/old.txt"})],
    [("filesystem.delete_file", '{"success":true,"path":"E:/temp/old.txt","deleted":true}'), ("filesystem.file_exists", '{"success":true,"path":"E:/temp/old.txt","exists":false}')],
    "Deleted E:/temp/old.txt and confirmed it no longer exists.",
    "filesystem_actions", "medium", ["filesystem.delete_file", "filesystem.file_exists"], True, False, False
), ensure_ascii=False))

# 62
lines.append(json.dumps(ex(
    "Edit E:/notes/log.txt to add 'Error: none' at the end, read it, then search for log files.",
    [("filesystem.edit_file", {"path": "E:/notes/log.txt", "old_string": "", "new_string": "Error: none\n"}), ("filesystem.read_file", {"path": "E:/notes/log.txt"}), ("filesystem.search_files", {"pattern": "*.log", "path": "E:/notes"})],
    [("filesystem.edit_file", '{"success":true,"path":"E:/notes/log.txt","edits_applied":1}'), ("filesystem.read_file", '{"success":true,"path":"E:/notes/log.txt","content":"Error: none"}'), ("filesystem.search_files", '{"success":true,"pattern":"*.log","path":"E:/notes","results":["log.txt"]}')],
    "Added 'Error: none' to E:/notes/log.txt. Content verified. Found log files: log.txt.",
    "filesystem_actions", "hard", ["filesystem.edit_file", "filesystem.read_file", "filesystem.search_files"], True, False, False
), ensure_ascii=False))

# 63
lines.append(json.dumps(ex(
    "Write E:/output/result.txt with 'Success', read it, then delete it.",
    [("filesystem.write_file", {"path": "E:/output/result.txt", "content": "Success"}), ("filesystem.read_file", {"path": "E:/output/result.txt"}), ("filesystem.delete_file", {"path": "E:/output/result.txt"})],
    [("filesystem.write_file", '{"success":true,"path":"E:/output/result.txt","written":true}'), ("filesystem.read_file", '{"success":true,"path":"E:/output/result.txt","content":"Success"}'), ("filesystem.delete_file", '{"success":true,"path":"E:/output/result.txt","deleted":true}')],
    "Wrote 'Success' to E:/output/result.txt, verified content, then deleted the file.",
    "filesystem_actions", "medium", ["filesystem.write_file", "filesystem.read_file", "filesystem.delete_file"], True, False, False
), ensure_ascii=False))

# 64
lines.append(json.dumps(ex(
    "List E:/docs and read every .md file found.",
    [("filesystem.list_directory", {"path": "E:/docs"}), ("filesystem.search_files", {"pattern": "*.md", "path": "E:/docs"}), ("filesystem.read_file", {"path": "E:/docs/readme.md"}), ("filesystem.read_file", {"path": "E:/docs/notes.md"})],
    [("filesystem.list_directory", '{"success":true,"path":"E:/docs","contents":["readme.md","notes.md","data.csv"]}'), ("filesystem.search_files", '{"success":true,"pattern":"*.md","path":"E:/docs","results":["readme.md","notes.md"]}'), ("filesystem.read_file", '{"success":true,"path":"E:/docs/readme.md","content":"# NEXUS Documentation"}'), ("filesystem.read_file", '{"success":true,"path":"E:/docs/notes.md","content":"Meeting notes from yesterday"}')],
    "E:/docs contains: readme.md, notes.md, data.csv. Readme: '# NEXUS Documentation'. Notes: 'Meeting notes from yesterday'.",
    "filesystem_actions", "hard", ["filesystem.list_directory", "filesystem.search_files", "filesystem.read_file"], True, False, False
), ensure_ascii=False))

# 65
lines.append(json.dumps(ex(
    "Check if E:/archive exists. If yes, list it. If no, create it.",
    [("filesystem.file_exists", {"path": "E:/archive"}), ("filesystem.list_directory", {"path": "E:/archive"})],
    [("filesystem.file_exists", '{"success":true,"path":"E:/archive","exists":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/archive","contents":["data.csv"]}')],
    "E:/archive exists and contains: data.csv.",
    "filesystem_actions", "medium", ["filesystem.file_exists", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 66
lines.append(json.dumps(ex(
    "Search for config files, read the first one, then update a value in it.",
    [("filesystem.search_files", {"pattern": "config*"}), ("filesystem.read_file", {"path": "E:/data/config.json"}), ("filesystem.edit_file", {"path": "E:/data/config.json", "old_string": "\"debug\": false", "new_string": "\"debug\": true"})],
    [("filesystem.search_files", '{"success":true,"pattern":"config*","results":["E:/data/config.json"]}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/config.json","content":"{\\"verbose\\":true,\\"debug\\":false}"}'), ("filesystem.edit_file", '{"success":true,"path":"E:/data/config.json","edits_applied":1}')],
    "Found E:/data/config.json, read content, then changed debug from false to true.",
    "filesystem_actions", "hard", ["filesystem.search_files", "filesystem.read_file", "filesystem.edit_file"], True, False, False
), ensure_ascii=False))

# 67
lines.append(json.dumps(ex(
    "Create E:/backup/archive.zip with content 'backup data', then copy it to E:/backup/archive_backup.zip.",
    [("filesystem.create_file", {"path": "E:/backup/archive.zip", "content": "backup data"}), ("filesystem.copy_file", {"source": "E:/backup/archive.zip", "destination": "E:/backup/archive_backup.zip"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/backup/archive.zip","created":true}'), ("filesystem.copy_file", '{"success":true,"source":"E:/backup/archive.zip","destination":"E:/backup/archive_backup.zip","copied":true}')],
    "Created E:/backup/archive.zip and copied it to E:/backup/archive_backup.zip.",
    "filesystem_actions", "medium", ["filesystem.create_file", "filesystem.copy_file"], True, False, False
), ensure_ascii=False))

# 68
lines.append(json.dumps(ex(
    "Copy E:/data/file1.csv to E:/backup/file1.csv, then move E:/data/file1.csv to E:/archive/file1.csv.",
    [("filesystem.copy_file", {"source": "E:/data/file1.csv", "destination": "E:/backup/file1.csv"}), ("filesystem.move_file", {"source": "E:/data/file1.csv", "destination": "E:/archive/file1.csv"})],
    [("filesystem.copy_file", '{"success":true,"source":"E:/data/file1.csv","destination":"E:/backup/file1.csv","copied":true}'), ("filesystem.move_file", '{"success":true,"source":"E:/data/file1.csv","destination":"E:/archive/file1.csv","moved":true}')],
    "Copied E:/data/file1.csv to E:/backup/file1.csv and moved E:/data/file1.csv to E:/archive/file1.csv.",
    "filesystem_actions", "medium", ["filesystem.copy_file", "filesystem.move_file"], True, False, False
), ensure_ascii=False))

# 69
lines.append(json.dumps(ex(
    "Create E:/data/raw/file1.csv with 'col1,col2\n1,2', E:/data/raw/file2.csv with 'col1,col2\n3,4', E:/data/raw/file3.csv with 'col1,col2\n5,6', then list E:/data/raw.",
    [("filesystem.create_file", {"path": "E:/data/raw/file1.csv", "content": "col1,col2\n1,2"}), ("filesystem.create_file", {"path": "E:/data/raw/file2.csv", "content": "col1,col2\n3,4"}), ("filesystem.create_file", {"path": "E:/data/raw/file3.csv", "content": "col1,col2\n5,6"}), ("filesystem.list_directory", {"path": "E:/data/raw"})],
    [("filesystem.create_file", '{"success":true,"path":"E:/data/raw/file1.csv","created":true}'), ("filesystem.create_file", '{"success":true,"path":"E:/data/raw/file2.csv","created":true}'), ("filesystem.create_file", '{"success":true,"path":"E:/data/raw/file3.csv","created":true}'), ("filesystem.list_directory", '{"success":true,"path":"E:/data/raw","contents":["file1.csv","file2.csv","file3.csv"]}')],
    "Created file1.csv, file2.csv, file3.csv in E:/data/raw. Directory listing confirms all three.",
    "filesystem_actions", "medium", ["filesystem.create_file", "filesystem.list_directory"], True, False, False
), ensure_ascii=False))

# 70
lines.append(json.dumps(ex(
    "Search for all .csv files, then read the first one.",
    [("filesystem.search_files", {"pattern": "*.csv"}), ("filesystem.read_file", {"path": "E:/data/report.csv"})],
    [("filesystem.search_files", '{"success":true,"pattern":"*.csv","results":["E:/data/report.csv","E:/data/input.csv"]}'), ("filesystem.read_file", '{"success":true,"path":"E:/data/report.csv","content":"id,value\n1,100"}')],
    "Found .csv files: E:/data/report.csv, E:/data/input.csv. First file E:/data/report.csv contains: 'id,value\\n1,100'.",
    "filesystem_actions", "medium", ["filesystem.search_files", "filesystem.read_file"], True, False, False
), ensure_ascii=False))

with open("E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_03_action.jsonl", "a", encoding="utf-8") as f:
    for line in lines:
        f.write(line + "\n")

print(f"Added {len(lines)} filesystem_actions examples")
