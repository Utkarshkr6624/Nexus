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

# === answer_vs_action examples 61-80 ===

# 1
lines.append(json.dumps(ex(
    "Create a task called 'Update documentation' in the Docs project with low priority.",
    [("nexus.task.create", {"title": "Update documentation", "project": "Docs", "priority": "low"})],
    [("nexus.task.create", '{"success":true,"id":"task-8001","title":"Update documentation","project":"Docs","priority":"low"}')],
    "Created task 'Update documentation' (task-8001) in the Docs project with low priority.",
    "answer_vs_action", "easy", ["nexus.task.create"], False, False, False
), ensure_ascii=False))

# 2
lines.append(json.dumps(ex(
    "Search my knowledge base for Docker best practices.",
    [("nexus.knowledge.search", {"query": "Docker best practices"})],
    [("nexus.knowledge.search", '{"success":true,"results":[{"title":"Docker Setup","content":"Install Docker Desktop and configure volumes."},{"title":"Docker Compose","content":"Use docker-compose for multi-container apps."}]}')],
    "Found 2 Docker-related entries: 'Docker Setup' and 'Docker Compose'.",
    "answer_vs_action", "easy", ["nexus.knowledge.search"], False, False, False
), ensure_ascii=False))

# 3
lines.append(json.dumps(ex(
    "Show me my career profile and learning status.",
    [("nexus.career.view", {}), ("nexus.learning.status", {})],
    [("nexus.career.view", '{"success":true,"profile":{"role":"Developer","experience_years":5,"goals":["Senior Dev"],"skills":["Python"]}}'), ("nexus.learning.status", '{"success":true,"goals":[{"title":"Learn Python","progress":0.75}],"skills":["Python"]}')],
    "Career profile: Developer with 5 years experience, goal: Senior Dev, skill: Python. Learning status: 'Learn Python' at 75%.",
    "answer_vs_action", "medium", ["nexus.career.view", "nexus.learning.status"], True, False, False
), ensure_ascii=False))

# 4
lines.append(json.dumps(ex(
    "Research the latest trends in AI and search the web for AI news.",
    [("web.research", {"topic": "AI trends 2025"}), ("web.search", {"query": "AI news latest"})],
    [("web.research", '{"success":true,"summary":"Key AI trends: agentic AI, small language models, multimodal systems."}'), ("web.search", '{"success":true,"results":[{"title":"AI Breakthrough","url":"https://example.com/ai"}]}')],
    "AI trends: agentic AI, small language models, multimodal systems. Latest news: 'AI Breakthrough'.",
    "answer_vs_action", "hard", ["web.research", "web.search"], True, False, False
), ensure_ascii=False))

# 5
lines.append(json.dumps(ex(
    "What is the difference between project.create and project.list?",
    [], [],
    "project.create creates a new project (modifies data, requires confirmation), while project.list retrieves all existing projects (read-only).",
    "answer_vs_action", "easy", [], False, False, False
), ensure_ascii=False))

# 6
lines.append(json.dumps(ex(
    "I want to create a project but need to check what exists first.",
    [("nexus.project.list", {})],
    [("nexus.project.list", '{"success":true,"projects":[{"id":"p1","name":"Alpha"},{"id":"p2","name":"Beta"}]}')],
    "Existing projects: Alpha and Beta. What name and description for the new project?",
    "answer_vs_action", "medium", ["nexus.project.list"], False, True, False
), ensure_ascii=False))

# 7
lines.append(json.dumps(ex(
    "Update task task-8001 to high priority.",
    [("nexus.task.update", {"id": "task-8001", "priority": "high"})],
    [("nexus.task.update", '{"success":true,"id":"task-8001","priority":"high"}')],
    "Task task-8001 priority updated to high.",
    "answer_vs_action", "easy", ["nexus.task.update"], False, False, False
), ensure_ascii=False))

# 8
lines.append(json.dumps(ex(
    "Search the web for information about renewable energy.",
    [("web.search", {"query": "renewable energy 2025"})],
    [("web.search", '{"success":true,"results":[{"title":"Solar Power Trends","url":"https://example.com/solar"},{"title":"Wind Energy Guide","url":"https://example.com/wind"}]}')],
    "Found: 'Solar Power Trends' and 'Wind Energy Guide'.",
    "answer_vs_action", "medium", ["web.search"], False, False, False
), ensure_ascii=False))

# 9
lines.append(json.dumps(ex(
    "Research edge AI deployment strategies.",
    [("web.research", {"topic": "edge AI deployment strategies"})],
    [("web.research", '{"success":true,"summary":"Edge AI deployment: model quantization, on-device inference, and latency optimization."}')],
    "Edge AI deployment strategies: model quantization, on-device inference, and latency optimization.",
    "answer_vs_action", "medium", ["web.research"], False, False, False
), ensure_ascii=False))

# 10
lines.append(json.dumps(ex(
    "What is the difference between a goal and a skill in NEXUS?",
    [], [],
    "A goal in NEXUS is a learning objective with progress tracking, while a skill is a competency you've acquired. Goals have progress percentages; skills are binary (you have it or you don't).",
    "answer_vs_action", "easy", [], False, False, False
), ensure_ascii=False))

# 11
lines.append(json.dumps(ex(
    "Delete task task-8001 and confirm it's gone.",
    [("nexus.task.delete", {"id": "task-8001"})],
    [("nexus.task.delete", '{"success":true,"deleted_id":"task-8001"}')],
    "Task task-8001 has been deleted.",
    "answer_vs_action", "easy", ["nexus.task.delete"], False, False, False
), ensure_ascii=False))

# 12
lines.append(json.dumps(ex(
    "Fetch the NEXUS API reference page.",
    [("web.fetch", {"url": "https://nexus.example.com/api"})],
    [("web.fetch", '{"success":true,"title":"NEXUS API","content":"REST API for project and task management."}')],
    "Fetched NEXUS API reference: REST API for project and task management.",
    "answer_vs_action", "medium", ["web.fetch"], False, False, False
), ensure_ascii=False))

# 13
lines.append(json.dumps(ex(
    "Search my knowledge base for Docker Compose and then create a task based on what you find.",
    [("nexus.knowledge.search", {"query": "Docker Compose"}), ("nexus.task.create", {"title": "Review Docker Compose notes", "project": "Learning", "priority": "low"})],
    [("nexus.knowledge.search", '{"success":true,"results":[{"title":"Docker Compose Setup","content":"Use docker-compose.yml for multi-service apps."}]}'), ("nexus.task.create", '{"success":true,"id":"task-9002","title":"Review Docker Compose notes","project":"Learning"}')],
    "Found Docker Compose Setup notes. Created task 'Review Docker Compose notes' (task-9002) in the Learning project.",
    "answer_vs_action", "medium", ["nexus.knowledge.search", "nexus.task.create"], True, False, False
), ensure_ascii=False))

# 14
lines.append(json.dumps(ex(
    "I need to modify a task but I don't remember the task ID. Can you help me find it?",
    [("nexus.task.list", {})],
    [("nexus.task.list", '{"success":true,"tasks":[{"id":"task-1001","title":"Prepare Q4 report"},{"id":"task-2001","title":"Code review sprint"}]}')],
    "Your tasks: 'Prepare Q4 report' (task-1001) and 'Code review sprint' (task-2001). Which one would you like to update?",
    "answer_vs_action", "medium", ["nexus.task.list"], False, True, False
), ensure_ascii=False))

# 15
lines.append(json.dumps(ex(
    "Create a project called 'Research' and then list all projects.",
    [("nexus.project.create", {"name": "Research", "description": "Research projects"}), ("nexus.project.list", {})],
    [("nexus.project.create", '{"success":true,"id":"proj-401","name":"Research"}'), ("nexus.project.list", '{"success":true,"projects":[{"id":"p1","name":"Alpha"},{"id":"p2","name":"Beta"},{"id":"proj-401","name":"Research"}]}')],
    "Created project 'Research' (proj-401). You now have 3 projects.",
    "answer_vs_action", "medium", ["nexus.project.create", "nexus.project.list"], True, False, False
), ensure_ascii=False))

# 16
lines.append(json.dumps(ex(
    "What is the difference between analytics.view and learning.status?",
    [], [],
    "analytics.view retrieves productivity metrics and completion statistics, while learning.status retrieves your learning goals and skill progress. analytics.view is about work output; learning.status is about personal development.",
    "answer_vs_action", "medium", [], False, False, False
), ensure_ascii=False))

# 17
lines.append(json.dumps(ex(
    "Search for web development knowledge and then fetch a relevant article.",
    [("nexus.knowledge.search", {"query": "web development"}), ("web.fetch", {"url": "https://example.com/react-guide"})],
    [("nexus.knowledge.search", '{"success":true,"results":[{"title":"React Basics","content":"Components and state management."}]}'), ("web.fetch", '{"success":true,"title":"React Guide","content":"Learn React components and hooks."}')],
    "Knowledge base: 'React Basics'. Fetched article: 'React Guide - Learn React components and hooks.'",
    "answer_vs_action", "hard", ["nexus.knowledge.search", "web.fetch"], True, False, False
), ensure_ascii=False))

# 18
lines.append(json.dumps(ex(
    "I want to create a task for code review.",
    [("nexus.task.create", {"title": "Code review", "priority": "medium"})],
    [("nexus.task.create", '{"success":true,"id":"task-10001","title":"Code review","priority":"medium"}')],
    "Created task 'Code review' (task-10001) with medium priority. Which project should this be associated with?",
    "answer_vs_action", "medium", ["nexus.task.create"], False, True, False
), ensure_ascii=False))

# 19
lines.append(json.dumps(ex(
    "Research cloud computing trends and search for cloud news.",
    [("web.research", {"topic": "cloud computing trends 2025"}), ("web.search", {"query": "cloud computing news"})],
    [("web.research", '{"success":true,"summary":"Cloud trends: hybrid cloud, serverless architectures, and AI-powered cloud management."}'), ("web.search", '{"success":true,"results":[{"title":"Cloud Migration Guide","url":"https://example.com/cloud"}]}')],
    "Cloud trends: hybrid cloud, serverless architectures, AI-powered cloud management. News: 'Cloud Migration Guide'.",
    "answer_vs_action", "hard", ["web.research", "web.search"], True, False, False
), ensure_ascii=False))

# 20
lines.append(json.dumps(ex(
    "How does NEXUS handle user authentication?",
    [], [],
    "NEXUS uses role-based access control with MFA support. The account module stores user credentials, roles, and MFA settings. Authentication is verified at the application service layer before routing to the appropriate capability.",
    "answer_vs_action", "medium", [], False, False, False
), ensure_ascii=False))

with open("E:/Nexo/qwen3-agent/phase2/dataset/v2_final/category_03_action.jsonl", "a", encoding="utf-8") as f:
    for line in lines:
        f.write(line + "\n")

print(f"Added {len(lines)} answer_vs_action examples")
