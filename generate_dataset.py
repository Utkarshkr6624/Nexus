#!/usr/bin/env python3
"""Generate v2_final dataset for debugging and tool_selection categories."""

import json

examples = []

# Helper to construct shell command strings with embedded Python code
# Avoids quote nesting issues by building strings separately
def py_cmd(code):
    """Build a shell command that runs Python code."""
    return 'cd E:/Nexo && python -c "' + code + '"'

# =============================================
# DEBUGGING EXAMPLES (25)
# =============================================

# 1. SyntaxError - missing colon
examples.append({
    "messages": [
        {"role": "user", "content": "I'm getting a SyntaxError when I run my Python script. It says invalid syntax on line 42. Can you help me figure out what is wrong?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/script.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "Line 40: def calculate_total(items):\nLine 41:     total = 0\nLine 42:     for item in items\nLine 43:         total += item['price']\nLine 44:     return total"},
        {"role": "assistant", "content": "I can see the issue. Line 42 is missing a colon at the end of the for loop. It should be for item in items: instead of for item in items. This is a common syntax error in Python. Fix line 42 and the script should run without the SyntaxError."}
    ],
    "metadata": {"category": "debugging", "difficulty": "easy", "tools_used": ["filesystem.read_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 2. pytest failures after commits
examples.append({
    "messages": [
        {"role": "user", "content": "My pytest suite was passing yesterday but now three tests are failing with AssertionError. Can you investigate?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "cd E:/Nexo && python -m pytest tests/ -v 2>&1 | tail -60"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "FAILED tests/test_auth.py::test_login_valid_user - AssertionError: assert active == inactive\nFAILED tests/test_tasks.py::test_task_creation - AssertionError: assert 5 == 3\nFAILED tests/test_analytics.py::test_completion_rate - AssertionError: assert 0.85 == 0.92"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "cd E:/Nexo && git log --oneline -5"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "a3f1b2c Update auth logic\n7d4e9f2 Refactor task model\nc12a8b3 Adjust analytics threshold\nf90d3e1 Previous stable commit\nb7e2c4d Initial commit"},
        {"role": "assistant", "content": "The three failing tests started failing after recent commits. test_login_valid_user fails because the auth logic now returns active instead of inactive. test_task_creation expects 3 items but gets 5. test_completion_rate expects 0.92 but gets 0.85. You should update the test assertions to match the new expected values, or revert those commits if the behavior change was unintentional."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 3. High CPU Node.js process
examples.append({
    "messages": [
        {"role": "user", "content": "The Node.js process on my machine is consuming 95% CPU and the fan is screaming. How do I find out what is going on?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.list_processes", "arguments": {}}}]},
        {"role": "tool", "name": "terminal.list_processes", "content": "PID 1234 node dist/server.js (CPU: 95%, MEM: 2.1GB)\nPID 5678 python ml/train.py (CPU: 12%, MEM: 800MB)\nPID 9012 chrome (CPU: 8%, MEM: 1.5GB)"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.inspect_process", "arguments": {"pid": 1234}}}]},
        {"role": "tool", "name": "terminal.inspect_process", "content": "PID: 1234\nName: node\nCommand: node dist/server.js\nCPU: 95%\nMemory: 2.1GB\nThreads: 12\nStatus: Running\nOpen files: 47"},
        {"role": "assistant", "content": "The Node.js process (PID 1234) running dist/server.js is consuming 95% CPU and 2.1GB of memory with 12 threads. This is likely a hot loop or a blocking operation in your server code. I recommend checking for infinite loops, unoptimized database queries, or middleware causing repeated re-renders."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["terminal.list_processes", "terminal.inspect_process"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 4. Merge conflicts
examples.append({
    "messages": [
        {"role": "user", "content": "I merged a branch into main and now the build is broken. There are merge conflicts in several files."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.status", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.status", "content": "On branch main\nYou have unmerged paths.\nUnmerged paths:\n  both modified: src/auth.py\n  both modified: src/tasks.py\n  both modified: tests/test_auth.py"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.diff", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.diff", "content": "diff --cc src/auth.py\n--- a/src/auth.py\n+++ b/src/auth.py\n@@ -10,5 -8,7 +10,11 @@\n  def login(user, password):\n+<<<<<<< HEAD\n+    return authenticate(user, password)\n+=======\n+    token = generate_token(user)\n+    return verify_token(token)\n+>>>>>>> feature/auth-tokens"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.log", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.log", "content": "commit a3f1b2c (HEAD -> main)\nMerge: 7d4e9f2 feature/auth-tokens\nAuthor: developer\nDate: 2025-07-28\n\n    Merge branch feature/auth-tokens into main"},
        {"role": "assistant", "content": "I found the merge conflicts. There are three files with conflicts: src/auth.py, src/tasks.py, and tests/test_auth.py. The conflict in src/auth.py is between the HEAD version (using authenticate) and the feature branch version (using token-based auth). To resolve: 1) Decide whether to keep the original authenticate approach or the new token-based approach, 2) Edit src/auth.py, 3) Resolve conflicts in src/tasks.py and tests/test_auth.py similarly, 4) Stage the resolved files with git add, 5) Complete the merge with git commit."}
    ],
    "metadata": {"category": "debugging", "difficulty": "hard", "tools_used": ["git.status", "git.diff", "git.log"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 5. Permission denied
examples.append({
    "messages": [
        {"role": "user", "content": "I cannot open a file I need for editing. It says permission denied. What should I do?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.file_exists", "arguments": {"path": "E:/Nexo/backend/app/config.py"}}}]},
        {"role": "tool", "name": "filesystem.file_exists", "content": "true"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/config.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "Error: Permission denied - unable to read E:/Nexo/backend/app/config.py"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "ls -la E:/Nexo/backend/app/config.py"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "-rw------- 1 root root 2048 Jul 28 10:30 E:/Nexo/backend/app/config.py"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "sudo chmod 644 E:/Nexo/backend/app/config.py"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "Permission changed: -rw-r--r-- 1 root root 2048 Jul 28 10:30 E:/Nexo/backend/app/config.py"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/config.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "# NEXUS Configuration\nDEBUG=False\nDATABASE_URL=postgresql://localhost/nexus\nSECRET_KEY=change-me-in-production"},
        {"role": "assistant", "content": "The permission issue was caused by restrictive file permissions (600, owned by root). I changed the permissions to 644 and was then able to read the file. The config shows DEBUG=False and a PostgreSQL database URL. Be cautious with the SECRET_KEY."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["filesystem.file_exists", "filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 6. CSS broken layout
examples.append({
    "messages": [
        {"role": "user", "content": "The web page is not rendering correctly. The layout is broken and buttons are not clickable. Can you check what is wrong?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "browser.open", "arguments": {"url": "http://localhost:5173"}}}]},
        {"role": "tool", "name": "browser.open", "content": "Page loaded. Title: NEXUS - Personal Intelligence Platform. Status: 200 OK."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "browser.extract", "arguments": {}}}]},
        {"role": "tool", "name": "browser.extract", "content": "HTML content: broken-layout... button disabled... stylesheet media=print"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "browser.navigate", "arguments": {"url": "http://localhost:5173/static/css/main.css"}}}]},
        {"role": "tool", "name": "browser.navigate", "content": "CSS file loaded. Contains: @media print { .container { display: none; } }"},
        {"role": "assistant", "content": "I found the issue. The CSS file is loaded with media=print, which means the styles only apply when printing, not on screen. This is why the layout is broken and the button is disabled. The fix is to change the stylesheet link from media=print to media=all."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["browser.open", "browser.extract", "browser.navigate"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 7. Web search returns nothing
examples.append({
    "messages": [
        {"role": "user", "content": "I ran a web search for NEXUS API documentation but got zero useful results. The search tool returned empty."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "web.search", "arguments": {"query": "NEXUS API documentation"}}}]},
        {"role": "tool", "name": "web.search", "content": "No results found for NEXUS API documentation. Try refining your query."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "web.search", "arguments": {"query": "NEXUS personal intelligence platform API"}}}]},
        {"role": "tool", "name": "web.search", "content": "No results found. NEXUS is a local-first application and does not have public API documentation online."},
        {"role": "assistant", "content": "The search returns no results because NEXUS is a local-first application. The API is documented locally in the project itself. Check the README.md in the NEXUS repository for API conventions, or look at the backend/app/api/ directory for endpoint definitions. The base URL is typically http://localhost:8000/api/v1."}
    ],
    "metadata": {"category": "debugging", "difficulty": "easy", "tools_used": ["web.search"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 8. ImportError - module not found
examples.append({
    "messages": [
        {"role": "user", "content": "I get an ImportError when trying to import the NEXUS auth module. The error says No module named auth. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.search_files", "arguments": {"pattern": "auth.py", "path": "E:/Nexo/backend"}}}]},
        {"role": "tool", "name": "filesystem.search_files", "content": "E:/Nexo/backend/app/api/auth.py\nE:/Nexo/backend/app/services/auth_service.py\nE:/Nexo/backend/tests/test_auth.py"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/api/auth.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "from fastapi import APIRouter\nfrom app.services.auth_service import AuthService\n\nrouter = APIRouter()\n\n@router.post(/login)\ndef login(): ..."},
        {"role": "assistant", "content": "The ImportError occurs because you are trying to import auth as a top-level module, but the auth module is located at app.api.auth within the backend package. The correct import should be from app.api.auth import router or from app.services.auth_service import AuthService. Make sure your PYTHONPATH includes the backend/ directory."}
    ],
    "metadata": {"category": "debugging", "difficulty": "easy", "tools_used": ["filesystem.search_files", "filesystem.read_file"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 9. Pydantic BaseSettings import error
examples.append({
    "messages": [
        {"role": "user", "content": "I typed a command in the terminal and it returned an error. How do I figure out what went wrong?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "cd E:/Nexo && python run.py 2>&1 | head -30"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "Traceback (most recent call last):\n  File run.py, line 8, in <module>\n    from app.core.config import settings\n  File E:/Nexo/backend/app/core/config.py, line 15, in <module>\n    from pydantic import BaseSettings\nImportError: cannot import name BaseSettings from pydantic\n\nDid you mean: BaseModel?"},
        {"role": "assistant", "content": "The error is clear: BaseSettings was removed from Pydantic v2. It was moved to pydantic-settings package. You have two options: 1) Install pydantic-settings and change the import to from pydantic_settings import BaseSettings, or 2) Downgrade to Pydantic v1. Option 1 is recommended since Pydantic v2 is the current version."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["terminal.run_command"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 10. Analytics completion rate mismatch
analytics_cmd = py_cmd("from app.services.analytics_service import calculate_completion_rate; tasks=[{'status':'done'}]*85+[{'status':'pending'}]*15; print(calculate_completion_rate(tasks))")
examples.append({
    "messages": [
        {"role": "user", "content": "I think there is a bug in the NEXUS analytics. The completion rate shows 85 percent but I expected 92 percent. Can you look into it?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/services/analytics_service.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "# Analytics Service\nCOMPLETION_WEIGHT = 0.30\nDEADLINE_WEIGHT = 0.25\nCONSISTENCY_WEIGHT = 0.20\nFOCUS_WEIGHT = 0.25\n\ndef calculate_completion_rate(tasks):\n    completed = sum(1 for t in tasks if t.status == done)\n    return completed / len(tasks) if tasks else 0"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": analytics_cmd}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "0.85"},
        {"role": "assistant", "content": "The analytics service calculates completion rate as completed divided by total_tasks. With 85 completed out of 100 tasks, the rate is 0.85 or 85 percent. The expected 92 percent would require 92 completed out of 100. The calculation itself is correct. You can also check the analytics configuration in the .env file."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 11. Buggy commit identification
examples.append({
    "messages": [
        {"role": "user", "content": "I committed some changes but now I realize the commit introduced a bug. Can you help me find which commit it was?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.log", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.log", "content": "commit a3f1b2c (HEAD -> main)\nAuthor: dev\nDate: 2025-07-28\n\n    Update auth logic\n\ncommit 7d4e9f2\nAuthor: dev\nDate: 2025-07-27\n\n    Refactor task model\n\ncommit c12a8b3\nAuthor: dev\nDate: 2025-07-26\n\n    Adjust analytics threshold\n\ncommit f90d3e1\nAuthor: dev\nDate: 2025-07-25\n\n    Previous stable commit"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.diff", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.diff", "content": "diff --git a/src/auth.py b/src/auth.py\n--- a/src/auth.py\n+++ b/src/auth.py\n@@ -10,3 +10,5 @@\n def login(user, password):\n+    return authenticate(user, password)\n+    # new login logic"},
        {"role": "assistant", "content": "Based on the git log and diff, the most recent commit a3f1b2c (Update auth logic) added new authentication logic with return authenticate(user, password). This is likely the commit that introduced the bug. You can verify by checking out the previous commit f90d3e1 and testing: git checkout f90d3e1 && python -m pytest tests/test_auth.py. To fix, either revert the commit with git revert a3f1b2c or fix the authenticate function."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["git.log", "git.diff"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 12. Database connection failure
examples.append({
    "messages": [
        {"role": "user", "content": "The NEXUS application will not start. It says the database connection failed. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/.env"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "DATABASE_URL=postgresql://localhost/nexus\nPOSTGRES_HOST=localhost\nPOSTGRES_PORT=5432\nPOSTGRES_USER=nexus_user\nPOSTGRES_PASSWORD=secret123"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "pg_isready -h localhost -p 5432"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "localhost:5432 - no response"},
        {"role": "assistant", "content": "The database connection is failing because PostgreSQL is not running or not responding on localhost:5432. Check: 1) Is PostgreSQL installed and running? Try sudo service postgresql start. 2) Is the port correct? Default is 5432. 3) Are the credentials in .env correct? 4) Try connecting manually with psql -h localhost -p 5432 -U nexus_user -d nexus."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 13. KeyError in dictionary access
examples.append({
    "messages": [
        {"role": "user", "content": "I keep getting a KeyError when accessing dictionary keys that might not exist. How should I handle this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "code.analyze", "arguments": {"code": "data = {'name': 'Alice', 'age': 30}\nvalue = data['email']"}}}]},
        {"role": "tool", "name": "code.analyze", "content": "{'issues': [{'type': 'correctness', 'message': 'KeyError when key does not exist in dictionary'}], 'suggestions': ['Use dict.get() with default value', 'Use try/except KeyError', 'Use collections.defaultdict for missing keys']}"},
        {"role": "assistant", "content": "The KeyError occurs when accessing a dictionary key that does not exist using dict[key] syntax. Solutions: 1) Use dict.get() with a default value: value = data.get('email', 'default@example.com'). 2) Use try/except KeyError for cases where the key should exist. 3) Use collections.defaultdict for consistent default behavior across many keys. Recommendation: use .get() for simple cases with a sensible default."}
    ],
    "metadata": {"category": "debugging", "difficulty": "easy", "tools_used": ["code.analyze"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 14. Failing API endpoint 500 error
examples.append({
    "messages": [
        {"role": "user", "content": "I need to debug a failing API endpoint that returns a 500 error."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/api/projects.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "@router.get('/projects/{project_id}')\nasync def get_project(project_id: int):\n    project = await project_repository.get_by_id(project_id)\n    if not project:\n        raise HTTPException(status_code=404, detail='Project not found')\n    return project"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "cd E:/Nexo && python -m pytest tests/test_projects.py -v 2>&1 | tail -20"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "FAILED tests/test_projects.py::test_get_project_missing_id - TypeError: get_by_id() missing 1 required positional argument: 'project_id'"},
        {"role": "assistant", "content": "The 500 error is caused by a TypeError in the test_get_project_missing_id test. The get_by_id() method is missing the project_id argument. The endpoint code looks correct, but the repository method signature may have changed. Check that project_repository.get_by_id() accepts project_id as a parameter and is being called correctly."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 15. Voice assistant not responding
voice_cmd = py_cmd("from app.services.voice_service import transcribe; print(transcribe.__doc__)")
examples.append({
    "messages": [
        {"role": "user", "content": "The NEXUS voice assistant is not responding to voice input. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/api/voice.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "@router.post('/voice/transcribe')\nasync def transcribe_audio(audio: AudioFile):\n    try:\n        return await voice_service.transcribe(audio)\n    except Exception as e:\n        logger.error(f'Transcription failed: {e}')\n        raise HTTPException(status_code=500, detail='Transcription failed')"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": voice_cmd}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "Transcribe audio file to text using Whisper model. Returns text string."},
        {"role": "assistant", "content": "The voice endpoint catches exceptions and logs them. The transcription service uses Whisper model. Check: 1) Is the Whisper model loaded? 2) Is the audio file format supported? 3) Are the logs showing transcription errors? Try testing the endpoint directly with a valid audio file to see the actual error message."}
    ],
    "metadata": {"category": "debugging", "difficulty": "hard", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 16. Test suite failing after refactor
examples.append({
    "messages": [
        {"role": "user", "content": "My test suite is failing after a recent refactor. How do I identify what broke?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "cd E:/Nexo && python -m pytest tests/ -v --tb=short 2>&1 | tail -30"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "PASSED tests/test_auth.py::test_login_valid_user\nPASSED tests/test_tasks.py::test_task_creation\nPASSED tests/test_analytics.py::test_completion_rate\nFAILED tests/test_projects.py::test_get_project - AssertionError: assert project.name == 'Test Project'\nFAILED tests/test_projects.py::test_delete_project - AssertionError: assert response.status_code == 200"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.diff", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.diff", "content": "diff --git a/src/projects.py b/src/projects.py\n--- a/src/projects.py\n+++ b/src/projects.py\n@@ -20,7 +20,7 @@\n def get_project(id):\n-    return db.query(Project).filter_by(id=id).first()\n+    return db.query(Project).filter_by(uid=id).first()"},
        {"role": "assistant", "content": "The test failures are caused by a recent change in projects.py: the filter_by argument changed from id=id to uid=id, which breaks the project lookup. The tests expect the original id column. Revert the change or update the tests to use uid instead of id."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["terminal.run_command", "git.diff"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 17. NEXUS API returns 500
config_cmd = py_cmd("from app.core.config import settings; print(settings.DATABASE_URL)")
examples.append({
    "messages": [
        {"role": "user", "content": "NEXUS API is returning 500 errors on all endpoints. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/core/config.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "# NEXUS Configuration\nDEBUG=False\nDATABASE_URL=postgresql://localhost/nexus\nSECRET_KEY=change-me-in-production\nML_ENABLED=true"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": config_cmd}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "postgresql://localhost/nexus"},
        {"role": "assistant", "content": "The 500 errors on all endpoints suggest a common dependency failure. Check: 1) Is PostgreSQL running? 2) Are the DATABASE_URL credentials correct? 3) Is ML_ENABLED=true but the ML model not loaded? Start by verifying the database connection, then check the ML model loading, and finally review the application logs for the specific 500 error details."}
    ],
    "metadata": {"category": "debugging", "difficulty": "hard", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 18. Incorrect ML model predictions
ml_cmd = py_cmd("from app.ml.intent_classifier import IntentClassifier; c = IntentClassifier(); print(c.predict('show me my tasks'))")
examples.append({
    "messages": [
        {"role": "user", "content": "The NEXUS ML model is returning incorrect intent predictions. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/ml/intent_classifier.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "class IntentClassifier:\n    def __init__(self):\n        self.model = load_model('microsoft/deberta-v3-base')\n    \n    def predict(self, text):\n        return self.model.predict(text)"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": ml_cmd}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "predicted_intent: show_tasks\nconfidence: 0.87\nmodel: microsoft/deberta-v3-base"},
        {"role": "assistant", "content": "The ML model is predicting 'show_tasks' with confidence 0.87 for the input 'show me my tasks'. If this prediction is incorrect, check: 1) The ML_CONFIDENCE_THRESHOLD setting (currently 0.90) - the model confidence 0.87 is below threshold, 2) The training data distribution, 3) Whether the model checkpoint is up to date. Consider lowering the threshold or retraining with more diverse examples."}
    ],
    "metadata": {"category": "debugging", "difficulty": "hard", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 19. Frontend build error
examples.append({
    "messages": [
        {"role": "user", "content": "The frontend build is failing with a TypeScript error. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "cd E:/Nexo/frontend && npm run build 2>&1 | tail -30"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "src/app/page.tsx:45:12 - error TS2322: Type 'string' is not assignable to type 'number'.\n  Type 'string' is not assignable to type 'number'.\n\nFound 1 error in src/app/page.tsx:45"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/frontend/src/app/page.tsx"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "const page = () => {\n  const count = '5';\n  return <div>{count}</div>;\n}"},
        {"role": "assistant", "content": "The TypeScript error is at line 45: count is declared as a string ('5') but the JSX expects a number. Fix: change const count = '5' to const count = 5 or use parseInt to convert the string to a number."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["terminal.run_command", "filesystem.read_file"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 20. CORS error
cors_cmd = py_cmd("from app.core.config import settings; print(settings.CORS_ORIGINS)")
examples.append({
    "messages": [
        {"role": "user", "content": "I get a CORS error when the frontend tries to call the backend API. How do I fix this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/core/config.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "# CORS Configuration\nCORS_ORIGINS=http://localhost:5173,http://localhost:3000"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": cors_cmd}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "['http://localhost:5173', 'http://localhost:3000']"},
        {"role": "assistant", "content": "The CORS error occurs because the frontend origin is not in the CORS_ORIGINS list. The config shows http://localhost:5173 and http://localhost:3000 are allowed. If your frontend is running on a different port or origin, add it to CORS_ORIGINS in the .env file. Also check that the backend is reading the CORS_ORIGINS env variable correctly."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 21. Authentication token expired
token_cmd = py_cmd("from datetime import datetime, timedelta; print('Access token expires:', datetime.now() + timedelta(minutes=30))")
examples.append({
    "messages": [
        {"role": "user", "content": "My authentication token keeps expiring. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/.env"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "ACCESS_TOKEN_EXPIRE_MINUTES=30\nREFRESH_TOKEN_EXPIRE_DAYS=30\nJWT_ALGORITHM=HS256"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": token_cmd}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "Access token expires: 2025-07-29 11:30:00"},
        {"role": "assistant", "content": "The access token expires in 30 minutes based on ACCESS_TOKEN_EXPIRE_MINUTES=30 in the .env file. This is a short expiry by design for security. Use the refresh token to get a new access token when it expires. If you need longer sessions, increase ACCESS_TOKEN_EXPIRE_MINUTES in the .env file."}
    ],
    "metadata": {"category": "debugging", "difficulty": "easy", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 22. Rate limiting blocking requests
rate_cmd = py_cmd("from app.core.config import settings; print(f'Rate limit: {settings.RATE_LIMIT_GENERAL_MAX_REQUESTS} per {settings.RATE_LIMIT_WINDOW_SECONDS}s')")
examples.append({
    "messages": [
        {"role": "user", "content": "I keep getting 429 Too Many Requests errors when calling the API. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/.env"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "RATE_LIMIT_ENABLED=true\nRATE_LIMIT_WINDOW_SECONDS=60\nRATE_LIMIT_GENERAL_MAX_REQUESTS=600\nRATE_LIMIT_CREDENTIAL_MAX_REQUESTS=120"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": rate_cmd}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "Rate limit: 600 per 60s"},
        {"role": "assistant", "content": "The 429 errors are caused by rate limiting. The current limit is 600 requests per 60 seconds for general endpoints and 120 for credential endpoints. If you are making more than 600 requests per minute, you will be rate limited. Options: 1) Reduce request frequency, 2) Increase RATE_LIMIT_GENERAL_MAX_REQUESTS in .env, 3) Implement request batching, 4) Use exponential backoff for retries."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 23. Database migration conflict
examples.append({
    "messages": [
        {"role": "user", "content": "Alembic migration is failing with a conflict. How do I resolve this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "cd E:/Nexo/backend && alembic upgrade head 2>&1"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) FATAL:  database 'nexus' does not exist\n\nCREATE DATABASE"},
        {"role": "assistant", "content": "The migration is failing because the 'nexus' database does not exist. Create the database first: createdb -h localhost -U nexus_user nexus, then run the migration again: alembic upgrade head."}
    ],
    "metadata": {"category": "debugging", "difficulty": "medium", "tools_used": ["terminal.run_command"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 24. WebSocket connection drops
examples.append({
    "messages": [
        {"role": "user", "content": "The WebSocket connection keeps dropping. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.list_processes", "arguments": {}}}]},
        {"role": "tool", "name": "terminal.list_processes", "content": "PID 1234 node dist/server.js (CPU: 45%, MEM: 1.2GB)\nPID 5678 python ml/train.py (CPU: 5%, MEM: 400MB)"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.inspect_process", "arguments": {"pid": 1234}}}]},
        {"role": "tool", "name": "terminal.inspect_process", "content": "PID: 1234\nName: node\nCommand: node dist/server.js\nCPU: 45%\nMemory: 1.2GB\nThreads: 8\nStatus: Running\nOpen files: 23\nNetwork: 150 connections"},
        {"role": "assistant", "content": "The Node.js server has 150 WebSocket connections open. The CPU usage is 45% which is elevated but not critical. The connection drops may be caused by: 1) Keep-alive timeout settings, 2) Network instability, 3) Memory pressure (1.2GB). Check the WebSocket heartbeat/ping interval and increase the timeout if needed."}
    ],
    "metadata": {"category": "debugging", "difficulty": "hard", "tools_used": ["terminal.list_processes", "terminal.inspect_process"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 25. Cache invalidation issue
cache_cmd = py_cmd("from app.core.cache import CACHE_TTL_SECONDS; print(f'Cache TTL: {CACHE_TTL_SECONDS}s = {CACHE_TTL_SECONDS/60} minutes')")
examples.append({
    "messages": [
        {"role": "user", "content": "The cached data in NEXUS is stale and not updating. How do I debug this?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/core/cache.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "CACHE_TTL_SECONDS=300\nCACHE_MAX_SIZE=1000\n\ndef get_cache(key):\n    return cache.get(key)\n\ndef set_cache(key, value):\n    cache.set(key, value, ttl=CACHE_TTL_SECONDS)"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": cache_cmd}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "Cache TTL: 300s = 5.0 minutes"},
        {"role": "assistant", "content": "The cache TTL is 300 seconds (5 minutes). Data older than 5 minutes is considered stale. If you need fresher data, reduce CACHE_TTL_SECONDS in the cache config. Also check if the cache invalidation logic is being triggered when data changes."}
    ],
    "metadata": {"category": "debugging", "difficulty": "easy", "tools_used": ["filesystem.read_file", "terminal.run_command"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

print(f"Debugging examples: {sum(1 for e in examples if e['metadata']['category'] == 'debugging')}")

# =============================================
# TOOL_SELECTION EXAMPLES (30)
# =============================================

# 26. Best tool to read file
examples.append({
    "messages": [
        {"role": "user", "content": "What is the best tool to read the contents of a file?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/README.md"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "# NEXUS\n\n### Personal Intelligence & Decision Platform\n\nNEXUS is a full-stack personal intelligence platform..."},
        {"role": "assistant", "content": "The best tool for reading a file contents is filesystem.read_file. It takes a path argument and returns the file contents. For example, filesystem.read_file(path=E:/Nexo/README.md) reads the README file. Always verify the file exists first with filesystem.file_exists if you are unsure of the path."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.read_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 27. Web search for info
examples.append({
    "messages": [
        {"role": "user", "content": "I need to find information on the internet about the latest Python version."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "web.search", "arguments": {"query": "latest Python version 2025"}}}]},
        {"role": "tool", "name": "web.search", "content": "Python 3.13 released in October 2024. Python 3.12 is the previous stable release. Key new features in 3.13: JIT compiler, improved error messages, new interactive interpreter."},
        {"role": "assistant", "content": "The best tool for finding information on the internet is web.search. It takes a query parameter and returns relevant search results. For Python version info, web.search(query=latest Python version 2025) returned that Python 3.13 was released in October 2024."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["web.search"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 28. Code inspection
examples.append({
    "messages": [
        {"role": "user", "content": "I want to examine some source code to understand how a function works."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "code.inspect", "arguments": {"file": "E:/Nexo/backend/app/services/auth_service.py"}}}]},
        {"role": "tool", "name": "code.inspect", "content": "def authenticate(user, password):\n    \"\"\"Authenticate a user with username and password.\"\"\"\n    if not user or not password:\n        return False\n    return user.check_password(password)"},
        {"role": "assistant", "content": "The best tool for examining source code is code.inspect. It takes a file path and returns the file contents with syntax-aware formatting. For understanding how a function works, use code.inspect(file=path/to/file.py) to read and highlight the code structure."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["code.inspect"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 29. Git status
examples.append({
    "messages": [
        {"role": "user", "content": "I need to check the status of my git repository to see what files have changed."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.status", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.status", "content": "On branch main\nYour branch is up to date with origin/main.\n\nChanges not staged for commit:\n  modified: src/auth.py\n  modified: tests/test_auth.py\n\nUntracked files:\n  new_feature.py"},
        {"role": "assistant", "content": "The best tool for checking git repository status is git.status. It takes a repo path and returns the current branch, staged/unstaged changes, and untracked files. In this case, src/auth.py and tests/test_auth.py are modified but not staged, and new_feature.py is a new untracked file."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["git.status"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 30. Browser interaction
examples.append({
    "messages": [
        {"role": "user", "content": "I want to interact with a website - click a button and fill out a form."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "browser.click", "arguments": {"selector": "button.submit-btn"}}}]},
        {"role": "tool", "name": "browser.click", "content": "Clicked button.submit-btn successfully. Page navigated to /form page."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "browser.type", "arguments": {"selector": "input#username", "text": "testuser"}}}]},
        {"role": "tool", "name": "browser.type", "content": "Typed testuser into input#username successfully."},
        {"role": "assistant", "content": "The best tools for interacting with a website are browser.click and browser.type. browser.click(selector) clicks an element identified by a CSS selector, and browser.type(selector, text) types text into an input field. Together they allow form interaction."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["browser.click", "browser.type"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 31. Run shell command
examples.append({
    "messages": [
        {"role": "user", "content": "I need to run a shell command to check disk usage on my system."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.run_command", "arguments": {"cmd": "df -h"}}}]},
        {"role": "tool", "name": "terminal.run_command", "content": "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1        50G   32G   18G  64% /\n/dev/sda2        100G   45G   55G  45% /home"},
        {"role": "assistant", "content": "The best tool for running shell commands is terminal.run_command. It takes a cmd parameter and returns the command output. For disk usage, terminal.run_command(cmd=df -h) shows filesystem sizes and usage percentages."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["terminal.run_command"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 32. Create file
examples.append({
    "messages": [
        {"role": "user", "content": "I need to create a new file in the project directory."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.create_file", "arguments": {"path": "E:/Nexo/new_module.py", "content": "# New module\n\ndef hello():\n    return 'Hello from NEXUS'\n"}}}]},
        {"role": "tool", "name": "filesystem.create_file", "content": "{\"success\": true, \"path\": \"E:/Nexo/new_module.py\"}"},
        {"role": "assistant", "content": "The best tool for creating a new file is filesystem.create_file. It takes path and content arguments. After creation, verify with filesystem.file_exists to confirm the file was created successfully."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.create_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 33. Code analysis
examples.append({
    "messages": [
        {"role": "user", "content": "I want to analyze code to find potential bugs before running tests."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "code.analyze", "arguments": {"code": "def divide(a, b):\n    return a / b"}}}]},
        {"role": "tool", "name": "code.analyze", "content": "Potential issues found:\n1. Division by zero: no check for b == 0\n2. No type hints for parameters\n3. No docstring\n4. No error handling for non-numeric inputs"},
        {"role": "assistant", "content": "The best tool for code analysis is code.analyze. It takes a code string and returns potential issues like bugs, missing error handling, and style problems. Use it before running tests to catch obvious issues early. For the example function, it correctly identified the division-by-zero risk and missing type hints."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["code.analyze"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 34. Search for NEXUS projects
examples.append({
    "messages": [
        {"role": "user", "content": "Can you look up the projects I have in NEXUS?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.search_files", "arguments": {"pattern": "project*", "path": "E:/Nexo"}}}]},
        {"role": "tool", "name": "filesystem.search_files", "content": "E:/Nexo/backend/app/models/project.py\nE:/Nexo/backend/app/api/projects.py\nE:/Nexo/frontend/src/features/projects\nE:/Nexo/docs/specifications/phase-3-projects-tasks.md"},
        {"role": "assistant", "content": "For NEXUS project queries, the relevant files are in backend/app/models/project.py and backend/app/api/projects.py. The frontend project features are in frontend/src/features/projects. The project management functionality was built in Phase 3 of NEXUS. To see your actual projects, you would need to query the NEXUS API at GET /api/v1/projects or check the database directly."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "medium", "tools_used": ["filesystem.search_files"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 35. Check NEXUS tasks
examples.append({
    "messages": [
        {"role": "user", "content": "I need to check my tasks in NEXUS and see what needs attention."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/api/tasks.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "@router.get('/tasks')\nasync def get_tasks(skip: int = 0, limit: int = 100):\n    tasks = await task_repository.get_all(skip=skip, limit=limit)\n    return tasks\n\n@router.post('/tasks')\nasync def create_task(task: TaskCreate):\n    return await task_repository.create(task)"},
        {"role": "assistant", "content": "For NEXUS task management, the API endpoints are in backend/app/api/tasks.py. You can: 1) Get tasks with GET /api/v1/tasks (supports pagination with skip/limit), 2) Create tasks with POST /api/v1/tasks. The tasks are associated with projects and can be prioritized, scheduled, and updated. Check the Command Center for a unified view of tasks needing attention."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "medium", "tools_used": ["filesystem.read_file"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 36. Search all NEXUS data
examples.append({
    "messages": [
        {"role": "user", "content": "I want to search across all my NEXUS data - projects, tasks, notes, everything."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.search_files", "arguments": {"pattern": "*", "path": "E:/Nexo"}}}]},
        {"role": "tool", "name": "filesystem.search_files", "content": "E:/Nexo/backend/app/api/projects.py\nE:/Nexo/backend/app/api/tasks.py\nE:/Nexo/backend/app/api/notes.py\nE:/Nexo/backend/app/api/resources.py\nE:/Nexo/backend/app/api/concepts.py\nE:/Nexo/backend/app/api/analytics.py\nE:/Nexo/backend/app/api/risks.py\nE:/Nexo/backend/app/api/recommendations.py\nE:/Nexo/backend/app/api/developer.py\nE:/Nexo/backend/app/api/learning.py\nE:/Nexo/backend/app/api/career.py\nE:/Nexo/backend/app/api/voice.py\nE:/Nexo/backend/app/api/command_center.py"},
        {"role": "assistant", "content": "NEXUS has a Global Search feature (Phase 13) that searches across projects, tasks, notes, resources, concepts, repositories, learning goals, skills, calendar information, risks, and recommendations. The search results are scoped to your own data. The API endpoints are in backend/app/api/ - each module has its own file. Individual module APIs include: projects, tasks, notes, resources, concepts, analytics, risks, recommendations, developer, learning, career, voice, and command-center."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "hard", "tools_used": ["filesystem.search_files"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 37. Learning and career tracking
examples.append({
    "messages": [
        {"role": "user", "content": "I want to track my learning progress and career goals in NEXUS."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/api/learning.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "@router.get('/learning/goals')\nasync def get_learning_goals():\n    goals = await learning_repository.get_all()\n    return goals\n\n@router.post('/learning/goals')\nasync def create_learning_goal(goal: LearningGoalCreate):\n    return await learning_repository.create(goal)"},
        {"role": "assistant", "content": "For NEXUS learning and career tracking, use the learning API at GET /api/v1/learning/goals and POST /api/v1/learning/goals for learning goals and skills tracking. Use the career API at GET /api/v1/career/profile and POST /api/v1/career/records for career records and portfolio evidence. These were built in Phase 9 (Learning & Career)."},
        {"role": "assistant", "content": "The learning system tracks goals, skills, activities, skill gaps, and progress. The career system tracks profile, records, and portfolio evidence."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "medium", "tools_used": ["filesystem.read_file"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 38. Voice features
examples.append({
    "messages": [
        {"role": "user", "content": "Can you check what voice features are available in NEXUS?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/api/voice.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "@router.post('/voice/transcribe')\nasync def transcribe_audio(audio: AudioFile):\n    return await voice_service.transcribe(audio)\n\n@router.post('/voice/synthesize')\nasync def synthesize_text(text: TextRequest):\n    return await voice_service.synthesize(text)"},
        {"role": "assistant", "content": "NEXUS voice features (Phase 12) include: 1) Voice transcription - POST /api/v1/voice/transcribe converts audio to text, 2) Text-to-speech - POST /api/v1/voice/synthesize converts text to audio, 3) Voice request routing - voice inputs are routed to the appropriate NEXUS service, 4) Error handling for voice failures. Voice is an interface to NEXUS rather than a separate system, integrated with the Ollama-based assistant."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.read_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 39. ML model setup
examples.append({
    "messages": [
        {"role": "user", "content": "I need to understand the ML model setup in NEXUS for intent classification."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/ml"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "Error: E:/Nexo/backend/app/ml is a directory, not a file."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.list_directory", "arguments": {"path": "E:/Nexo/backend/app/ml"}}}]},
        {"role": "tool", "name": "filesystem.list_directory", "content": "intent_classifier.py\nmodel_loader.py\nprediction.py\nrouter.py\nschemas.py"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/ml/intent_classifier.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "class IntentClassifier:\n    def __init__(self):\n        self.model = load_model('microsoft/deberta-v3-base')\n    \n    def predict(self, text):\n        return self.model.predict(text)"},
        {"role": "assistant", "content": "NEXUS uses a DeBERTa-v3-base model for intent classification (Phase 10/11). The ML module is at backend/app/ml/ with files for intent classification, model loading, prediction, routing, and schemas. The model acts as a router - it classifies user intent and routes to the appropriate NEXUS service. Configuration: ML_ENABLED=true, ML_MODEL_PATH (relative to repo), ML_CONFIDENCE_THRESHOLD=0.90, ML_DEVICE=auto. The model does not directly control the database - it only classifies intent."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "hard", "tools_used": ["filesystem.read_file", "filesystem.list_directory"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 40. Command Center
examples.append({
    "messages": [
        {"role": "user", "content": "I want to check what the Command Center shows in NEXUS."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/api/command_center.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "@router.get('/command-center')\nasync def get_command_center():\n    return {\n        'priorities': await get_priorities(),\n        'attention_signals': await get_attention_signals(),\n        'recommendations': await get_recommendations(),\n        'activity': await get_activity(),\n        'quick_actions': await get_quick_actions()\n    }"},
        {"role": "assistant", "content": "NEXUS Command Center (Phase 13) provides a centralized intelligence surface. It answers three main questions: What is happening?, What needs my attention?, and What should I work on next?. The endpoint GET /api/v1/command-center returns: priorities, attention signals, recommendations, activity information, and quick actions. It surfaces cross-module information bringing projects, tasks, knowledge, analytics, risks, and recommendations into one place."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.read_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 41. Browser research workflow
examples.append({
    "messages": [
        {"role": "user", "content": "I need to research the latest trends in web development."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "browser.open", "arguments": {"url": "https://developer.mozilla.org/en-US/docs/Web"}}}]},
        {"role": "tool", "name": "browser.open", "content": "Page loaded. Title: MDN Web Docs. Status: 200 OK."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "browser.extract", "arguments": {}}}]},
        {"role": "tool", "name": "browser.extract", "content": "HTML content: MDN Web Docs - Web technology docs... Web APIs... HTML... CSS... JavaScript..."},
        {"role": "assistant", "content": "Use browser.open to load a web page, then browser.extract to get the page content. For deeper research, combine with web.search for broader coverage."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "medium", "tools_used": ["browser.open", "browser.extract"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 42. Code test execution
examples.append({
    "messages": [
        {"role": "user", "content": "I want to test a function in my codebase."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "code.test", "arguments": {"command": "cd E:/Nexo && python -m pytest tests/test_auth.py -v"}}}]},
        {"role": "tool", "name": "code.test", "content": "PASSED tests/test_auth.py::test_login_valid_user\nFAILED tests/test_auth.py::test_login_invalid_user - AssertionError: assert active == inactive\nFAILED tests/test_auth.py::test_logout - AssertionError: assert redirected == true"},
        {"role": "assistant", "content": "Use code.test to run tests. It takes an optional command argument. In this example, running the auth tests shows 1 passed and 2 failed. The failed tests indicate the login logic may have changed unexpectedly."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["code.test"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 43. Code formatting
examples.append({
    "messages": [
        {"role": "user", "content": "I need to format my Python code according to style guidelines."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "code.format", "arguments": {"file": "E:/Nexo/backend/app/services/auth_service.py"}}}]},
        {"role": "tool", "name": "code.format", "content": "{\"success\": true, \"file\": \"E:/Nexo/backend/app/services/auth_service.py\", \"changes\": 5}"},
        {"role": "assistant", "content": "Use code.format to format code according to style guidelines. It takes a file argument and returns the number of changes made. After formatting, review the changes with git.diff to ensure the formatting did not introduce unintended modifications."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["code.format"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 44. Code modification
examples.append({
    "messages": [
        {"role": "user", "content": "I want to modify a function in my codebase."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "code.modify", "arguments": {"file": "E:/Nexo/backend/app/services/auth_service.py", "changes": "Add password hashing before authentication"}}}]},
        {"role": "tool", "name": "code.modify", "content": "{\"success\": true, \"file\": \"E:/Nexo/backend/app/services/auth_service.py\", \"changes_applied\": 1}"},
        {"role": "assistant", "content": "Use code.modify to modify code. It takes file and changes arguments. After modification, use code.test to run relevant tests to verify the changes did not break existing functionality."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["code.modify"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 45. NEXUS knowledge search
examples.append({
    "messages": [
        {"role": "user", "content": "I need to search NEXUS knowledge base for information about focus sessions."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.knowledge.search", "arguments": {"query": "focus sessions"}}}]},
        {"role": "tool", "name": "nexus.knowledge.search", "content": "{\"query\": \"focus sessions\", \"results\": [{\"title\": \"Focus Sessions Guide\", \"content\": \"Focus sessions are time-blocked work periods. They can be scheduled in the planner and tracked for productivity analytics.\"}, {\"title\": \"Planner Integration\", \"content\": \"Focus sessions integrate with weekly planning and availability settings.\"}], \"total\": 2}"},
        {"role": "assistant", "content": "Use nexus.knowledge.search to search the NEXUS knowledge base. It takes a query argument and returns relevant knowledge entries. For focus sessions, it found 2 entries: a guide on time-blocked work periods and planner integration information."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.knowledge.search"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 46. NEXUS analytics view
examples.append({
    "messages": [
        {"role": "user", "content": "I want to view my NEXUS analytics dashboard."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.analytics.view", "arguments": {"range": "7d"}}}]},
        {"role": "tool", "name": "nexus.analytics.view", "content": "{\"range\": \"7d\", \"productivity\": 85, \"completion_rate\": 0.92, \"focus_score\": 78, \"activity_level\": \"high\"}"},
        {"role": "assistant", "content": "Use nexus.analytics.view to view analytics. It takes an optional range argument. For the last 7 days, productivity is 85, completion rate is 92%, focus score is 78, and activity level is high."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.analytics.view"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 47. NEXUS learning status
examples.append({
    "messages": [
        {"role": "user", "content": "I want to check my learning status in NEXUS."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.learning.status", "arguments": {}}}]},
        {"role": "tool", "name": "nexus.learning.status", "content": "{\"goals_completed\": 5, \"skills\": [\"Python\", \"TypeScript\", \"FastAPI\"], \"current_goal\": \"Master backend development\"}"},
        {"role": "assistant", "content": "Use nexus.learning.status to view your learning progress. It takes no required arguments. You have completed 5 goals, are learning Python, TypeScript, and FastAPI, with a current goal of mastering backend development."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.learning.status"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 48. NEXUS career view
examples.append({
    "messages": [
        {"role": "user", "content": "I want to view my career information in NEXUS."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.career.view", "arguments": {}}}]},
        {"role": "tool", "name": "nexus.career.view", "content": "{\"profile\": \"Backend Developer\", \"experience_years\": 3, \"portfolio_evidence\": [\"Project Alpha\", \"Project Beta\"]}"},
        {"role": "assistant", "content": "Use nexus.career.view to view career information. It takes no required arguments. Your profile is Backend Developer with 3 years of experience and portfolio evidence from Project Alpha and Project Beta."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.career.view"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 49. NEXUS task list
examples.append({
    "messages": [
        {"role": "user", "content": "I need to list all my NEXUS tasks."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.task.list", "arguments": {}}}]},
        {"role": "tool", "name": "nexus.task.list", "content": "2 tasks due today: Submit expense report, Review pull request #42"},
        {"role": "assistant", "content": "Use nexus.task.list to list tasks. It takes optional project and status arguments. Without filters, it returns all tasks including 2 due today: Submit expense report and Review pull request #42."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.task.list"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 50. NEXUS task create
examples.append({
    "messages": [
        {"role": "user", "content": "I want to create a new task in NEXUS."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.task.create", "arguments": {"title": "Review pull request #42", "project": "NEXUS", "priority": "high"}}}]},
        {"role": "tool", "name": "nexus.task.create", "content": "{\"success\": true, \"task_id\": \"task-123\", \"title\": \"Review pull request #42\", \"status\": \"pending\"}"},
        {"role": "assistant", "content": "Use nexus.task.create to create a new task. It takes optional title, project, and priority arguments. Task task-123 was created successfully with title Review pull request #42 and high priority."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.task.create"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 51. NEXUS task update
examples.append({
    "messages": [
        {"role": "user", "content": "I want to update an existing task in NEXUS."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.task.update", "arguments": {"id": "task-123", "status": "completed"}}}]},
        {"role": "tool", "name": "nexus.task.update", "content": "{\"success\": true, \"task_id\": \"task-123\", \"status\": \"completed\"}"},
        {"role": "assistant", "content": "Use nexus.task.update to update a task. It requires the id argument and optionally accepts title, status, and priority. Task task-123 was updated to completed status."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.task.update"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 52. File write
examples.append({
    "messages": [
        {"role": "user", "content": "I want to write content to a file. Which tool should I use?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.write_file", "arguments": {"path": "E:/Nexo/new_file.txt", "content": "Hello from NEXUS"}}}]},
        {"role": "tool", "name": "filesystem.write_file", "content": "{\"success\": true, \"path\": \"E:/Nexo/new_file.txt\"}"},
        {"role": "assistant", "content": "Use filesystem.write_file to write content to a file. It takes path and content arguments. In this example, filesystem.write_file(path=E:/Nexo/new_file.txt, content=Hello from NEXUS) successfully created the file."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.write_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 53. File edit
examples.append({
    "messages": [
        {"role": "user", "content": "I need to edit a specific line in a file precisely."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.edit_file", "arguments": {"path": "E:/Nexo/README.md", "old_string": "version: 1.0", "new_string": "version: 2.0"}}}]},
        {"role": "tool", "name": "filesystem.edit_file", "content": "{\"success\": true, \"path\": \"E:/Nexo/README.md\", \"edits_applied\": 1}"},
        {"role": "assistant", "content": "Use filesystem.edit_file for precise edits. It takes path, old_string, and new_string arguments. It replaces only the exact old_string with new_string, making it safer than rewriting the entire file."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.edit_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 54. File move
examples.append({
    "messages": [
        {"role": "user", "content": "I need to move a file from one directory to another."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.move_file", "arguments": {"source": "E:/Nexo/old_location.py", "destination": "E:/Nexo/new_location.py"}}}]},
        {"role": "tool", "name": "filesystem.move_file", "content": "{\"success\": true, \"source\": \"E:/Nexo/old_location.py\", \"destination\": \"E:/Nexo/new_location.py\"}"},
        {"role": "assistant", "content": "Use filesystem.move_file to move or rename a file. It takes source and destination arguments. Use this instead of copy_file followed by delete_file when you want to relocate a file."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.move_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 55. File copy
examples.append({
    "messages": [
        {"role": "user", "content": "I need to copy a file to a backup location."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.copy_file", "arguments": {"source": "E:/Nexo/config.py", "destination": "E:/Nexo/config.py.backup"}}}]},
        {"role": "tool", "name": "filesystem.copy_file", "content": "{\"success\": true, \"source\": \"E:/Nexo/config.py\", \"destination\": \"E:/Nexo/config.py.backup\"}"},
        {"role": "assistant", "content": "Use filesystem.copy_file to create a copy of a file. It takes source and destination arguments. After copying, verify with filesystem.file_exists to confirm the backup was created."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.copy_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 56. File delete
examples.append({
    "messages": [
        {"role": "user", "content": "I want to delete a file that is no longer needed."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.delete_file", "arguments": {"path": "E:/Nexo/temp_file.py"}}}]},
        {"role": "tool", "name": "filesystem.delete_file", "content": "{\"success\": true, \"path\": \"E:/Nexo/temp_file.py\"}"},
        {"role": "assistant", "content": "Use filesystem.delete_file to delete a file. It takes a path argument. Be careful with this operation as deleted files may not be recoverable. Consider using filesystem.copy_file to create a backup first if the file might be needed later."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.delete_file"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 57. Git log
examples.append({
    "messages": [
        {"role": "user", "content": "I need to check the git history to understand what changed."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.log", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.log", "content": "commit a3f1b2c (HEAD -> main)\nAuthor: dev\nDate: 2025-07-28\n\n    Update auth logic\n\ncommit 7d4e9f2\nAuthor: dev\nDate: 2025-07-27\n\n    Refactor task model\n\ncommit c12a8b3\nAuthor: dev\nDate: 2025-07-26\n\n    Adjust analytics threshold\n\ncommit f90d3e1\nAuthor: dev\nDate: 2025-07-25\n\n    Previous stable commit"},
        {"role": "assistant", "content": "Use git.log to view the commit history. It takes a repo argument and returns commit hashes, authors, dates, and messages. For the NEXUS repo, the most recent commit is a3f1b2c (Update auth logic) on July 28, followed by 7d4e9f2 (Refactor task model) on July 27."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["git.log"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 58. Git diff
examples.append({
    "messages": [
        {"role": "user", "content": "I need to view the differences between commits."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.diff", "arguments": {"repo": "E:/Nexo"}}}]},
        {"role": "tool", "name": "git.diff", "content": "diff --git a/src/auth.py b/src/auth.py\n--- a/src/auth.py\n+++ b/src/auth.py\n@@ -10,3 +10,5 @@\n def login(user, password):\n+    return authenticate(user, password)\n+    # new login logic"},
        {"role": "assistant", "content": "Use git.diff to view the differences between commits. It takes a repo argument and returns the unified diff of uncommitted changes or the diff between commits. In this case, it shows that a new authenticate call was added to src/auth.py."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["git.diff"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 59. Git branch
examples.append({
    "messages": [
        {"role": "user", "content": "I need to create a new git branch for a feature."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.branch", "arguments": {"repo": "E:/Nexo", "name": "feature/new-feature"}}}]},
        {"role": "tool", "name": "git.branch", "content": "{\"success\": true, \"branch\": \"feature/new-feature\", \"repo\": \"E:/Nexo\"}"},
        {"role": "assistant", "content": "Use git.branch to create a new branch. It takes repo and name arguments. After creating the branch, use git.status to verify the branch was created successfully."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["git.branch"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 60. Git add
examples.append({
    "messages": [
        {"role": "user", "content": "I need to stage my changes before committing."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.add", "arguments": {"repo": "E:/Nexo", "files": ["src/auth.py", "tests/test_auth.py"]}}}]},
        {"role": "tool", "name": "git.add", "content": "{\"success\": true, \"staged_files\": [\"src/auth.py\", \"tests/test_auth.py\"]}"},
        {"role": "assistant", "content": "Use git.add to stage files for commit. It takes repo and files arguments. After staging, use git.commit to create the commit with a descriptive message."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["git.add"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 61. Git commit
examples.append({
    "messages": [
        {"role": "user", "content": "I need to commit my changes with a message."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "git.commit", "arguments": {"repo": "E:/Nexo", "message": "Fix auth logic and update tests"}}}]},
        {"role": "tool", "name": "git.commit", "content": "{\"success\": true, \"commit\": \"a3f1b2c\", \"message\": \"Fix auth logic and update tests\"}"},
        {"role": "assistant", "content": "Use git.commit to create a commit. It takes repo and message arguments. The commit a3f1b2c was created successfully with the message Fix auth logic and update tests."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["git.commit"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 62. Web research
examples.append({
    "messages": [
        {"role": "user", "content": "I want to search the web for the latest AI research papers."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "web.research", "arguments": {"topic": "AI research papers 2025"}}}]},
        {"role": "tool", "name": "web.research", "content": "{\"results\": [{\"title\": \"Attention Is All You Need Revisited\", \"url\": \"https://arxiv.org/abs/2501.12345\"}, {\"title\": \"Scaling Laws for Neural Networks\", \"url\": \"https://arxiv.org/abs/2502.67890\"}], \"total\": 2}"},
        {"role": "assistant", "content": "Use web.research for comprehensive multi-source research. It takes a topic argument and returns results from multiple sources. For AI research papers, web.research(topic=AI research papers 2025) found two relevant papers on attention mechanisms and scaling laws."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["web.research"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 63. Web fetch
examples.append({
    "messages": [
        {"role": "user", "content": "I want to fetch a specific web page for its raw content."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "web.fetch", "arguments": {"url": "https://example.com/api-docs"}}}]},
        {"role": "tool", "name": "web.fetch", "content": "{\"url\": \"https://example.com/api-docs\", \"content\": \"API Documentation v2.0...\"}"},
        {"role": "assistant", "content": "Use web.fetch to retrieve the raw content of a specific URL. It takes a url argument and returns the page content. For structured search results, use web.search instead."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["web.fetch"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 64. List directory
examples.append({
    "messages": [
        {"role": "user", "content": "How do I list the contents of a directory?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.list_directory", "arguments": {"path": "E:/Nexo/backend"}}}]},
        {"role": "tool", "name": "filesystem.list_directory", "content": "{\"entries\": [\"app\", \"ml\", \"tests\", \"migrations\", \"alembic.ini\", \"Dockerfile\", \"requirements.txt\", \"run.py\"]}"},
        {"role": "assistant", "content": "Use filesystem.list_directory to list directory contents. It takes a path argument and returns a list of entries. For the backend directory, it shows app, ml, tests, migrations, alembic.ini, Dockerfile, requirements.txt, and run.py."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.list_directory"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 65. File exists
examples.append({
    "messages": [
        {"role": "user", "content": "How do I check whether a file exists before trying to read it?"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.file_exists", "arguments": {"path": "E:/Nexo/backend/app/config.py"}}}]},
        {"role": "tool", "name": "filesystem.file_exists", "content": "true"},
        {"role": "assistant", "content": "Use filesystem.file_exists to check whether a file exists before reading it. It takes a path argument and returns true or false. In this case, E:/Nexo/backend/app/config.py exists, so it is safe to proceed with filesystem.read_file."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.file_exists"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 66. Search files
examples.append({
    "messages": [
        {"role": "user", "content": "I am not sure which tool I should use for searching files in a directory."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.search_files", "arguments": {"pattern": "*.py", "path": "E:/Nexo/backend"}}}]},
        {"role": "tool", "name": "filesystem.search_files", "content": "E:/Nexo/backend/app/api/auth.py\nE:/Nexo/backend/app/api/projects.py\nE:/Nexo/backend/app/api/tasks.py\nE:/Nexo/backend/app/services/auth_service.py\nE:/Nexo/backend/app/services/project_service.py"},
        {"role": "assistant", "content": "The best tool for searching files by pattern is filesystem.search_files. It takes a pattern (glob) and an optional path. For Python files in the backend directory, use filesystem.search_files(pattern=*.py, path=E:/Nexo/backend). It returns all matching file paths."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["filesystem.search_files"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 67. Web search with follow-up
examples.append({
    "messages": [
        {"role": "user", "content": "I need to look up information about the NEXUS planner feature."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "web.search", "arguments": {"query": "NEXUS planner feature documentation"}}}]},
        {"role": "tool", "name": "web.search", "content": "No results found for NEXUS planner feature documentation."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/backend/app/api/planner.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "@router.get('/planner/week')\nasync def get_week_plan():\n    return await planner_service.get_current_week()\n\n@router.post('/planner/schedule')\nasync def schedule_task(task: TaskSchedule):\n    return await planner_service.schedule(task)"},
        {"role": "assistant", "content": "The NEXUS planner feature (Phase 4) connects work with time. The API endpoints are: GET /api/v1/planner/week for weekly planning, POST /api/v1/planner/schedule for task scheduling. The planner integrates with availability settings and focus sessions."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "medium", "tools_used": ["web.search", "filesystem.read_file"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

# 68. Account info
examples.append({
    "messages": [
        {"role": "user", "content": "I want to view my NEXUS account information."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.account.info", "arguments": {}}}]},
        {"role": "tool", "name": "nexus.account.info", "content": "{\"username\": \"utkar\", \"email\": \"utkar@example.com\", \"role\": \"admin\", \"mfa_enabled\": true}"},
        {"role": "assistant", "content": "Use nexus.account.info to view account information. It takes no required arguments. Your account shows username utkar, email utkar@example.com, role admin, and MFA enabled."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.account.info"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 69. Planner view
examples.append({
    "messages": [
        {"role": "user", "content": "I want to check my NEXUS planner for the current week."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "nexus.planner.view", "arguments": {"week": "current"}}}]},
        {"role": "tool", "name": "nexus.planner.view", "content": "{\"week\": \"2025-W31\", \"days\": [{\"date\": \"2025-07-28\", \"blocks\": [{\"start\": \"09:00\", \"end\": \"11:00\", \"task\": \"Dataset work\"}]}, {\"date\": \"2025-07-29\", \"blocks\": []}]}"},
        {"role": "assistant", "content": "Use nexus.planner.view to view your planner. It takes an optional week argument. For the current week (W31), you have one time block: Dataset work from 09:00 to 11:00 on July 28th. July 29th is currently empty."}
    ],
    "metadata": {"category": "tool_selection", "difficulty": "easy", "tools_used": ["nexus.planner.view"], "multi_step": False, "requires_clarification": False, "requires_confirmation": False}
})

# 70. Multi-step: inspect process then read config
examples.append({
    "messages": [
        {"role": "user", "content": "A process is consuming too much memory. I need to check what it is and then review its config."},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.list_processes", "arguments": {}}}]},
        {"role": "tool", "name": "terminal.list_processes", "content": "PID 1234 python3 data_pipeline.py (CPU: 25%, MEM: 4.2GB)\nPID 5678 node server.js (CPU: 8%, MEM: 500MB)"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "terminal.inspect_process", "arguments": {"pid": 1234}}}]},
        {"role": "tool", "name": "terminal.inspect_process", "content": "PID: 1234\nName: python3\nCommand: python3 data_pipeline.py\nCPU: 25%\nMemory: 4.2GB\nThreads: 4\nStatus: Running"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "filesystem.read_file", "arguments": {"path": "E:/Nexo/config.py"}}}]},
        {"role": "tool", "name": "filesystem.read_file", "content": "# Configuration\nDATA_BATCH_SIZE=1000\nDATA_TIMEOUT=30\nLOG_LEVEL=INFO"},
        {"role": "assistant", "content": "Process 1234 is python3 running data_pipeline.py consuming 4.2GB memory. The config shows DATA_BATCH_SIZE=1000 and DATA_TIMEOUT=30. The high memory usage may be due to a large batch size or a memory leak in the data pipeline. Consider reducing DATA_BATCH_SIZE or adding memory monitoring."}
    ],
    "metadata": {"category": "debugging", "difficulty": "hard", "tools_used": ["terminal.list_processes", "terminal.inspect_process", "filesystem.read_file"], "multi_step": True, "requires_clarification": False, "requires_confirmation": False}
})

print(f"Total examples: {len(examples)}")
print(f"Debugging: {sum(1 for e in examples if e['metadata']['category'] == 'debugging')}")
print(f"Tool selection: {sum(1 for e in examples if e['metadata']['category'] == 'tool_selection')}")

# Write JSONL
with open('E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_06_debug_toolselect.jsonl', 'w') as f:
    for ex in examples:
        f.write(json.dumps(ex, ensure_ascii=False) + '\n')

print("Written to category_06_debug_toolselect.jsonl")