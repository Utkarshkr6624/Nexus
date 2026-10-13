import json, os

examples = []

# === answer_vs_action: 40 new examples ===
# Using a compact approach with template data

a2a_data = [
    # (user_content, tool_calls_list, tool_results_list, assistant_content, difficulty, multi_step, requires_clarification, requires_confirmation)
    ("Create a task called 'Prepare Q4 report' in the Finance project with high priority and due date Friday.",
     [("nexus.task.create", {"title":"Prepare Q4 report","project":"Finance","priority":"high","due_date":"Friday"})],
     [("nexus.task.create", "{\"success\":true,\"id\":\"task-1001\",\"title\":\"Prepare Q4 report\",\"project\":\"Finance\",\"priority\":\"high\"}")],
     "Created task 'Prepare Q4 report' (task-1001) in the Finance project with high priority. Due date set to Friday.",
     "medium", False, False, False),

    ("I need to update my learning goal progress. Check my current learning status first.",
     [("nexus.learning.status", {})],
     [("nexus.learning.status", "{\"success\":true,\"goals\":[{\"title\":\"Learn Python\",\"progress\":0.75},{\"title\":\"Learn Docker\",\"progress\":0.4}],\"skills\":[\"Python\",\"Docker\"]}")],
     "Your current learning status: 'Learn Python' is 75% complete, 'Learn Docker' is 40% complete. Skills: Python, Docker. What would you like to update?",
     "medium", False, True, False),

    ("Search for anything related to cybersecurity in my knowledge base.",
     [("nexus.knowledge.search", {"query":"cybersecurity"})],
     [("nexus.knowledge.search", "{\"success\":true,\"results\":[{\"title\":\"Security Best Practices\",\"content\":\"Use MFA and regular audits.\"},{\"title\":\"Incident Response\",\"content\":\"Follow the IR playbook.\"}]}")],
     "Found 2 cybersecurity-related entries: 'Security Best Practices' (Use MFA and regular audits) and 'Incident Response' (Follow the IR playbook).",
     "medium", False, False, False),

    ("Show me my career goals and current role.",
     [("nexus.career.view", {})],
     [("nexus.career.view", "{\"success\":true,\"profile\":{\"role\":\"Senior Developer\",\"experience_years\":8,\"goals\":[\"Lead Engineer\",\"Tech Lead\"],\"skills\":[\"Python\",\"Kubernetes\"]},\"portfolio_evidence\":[{\"title\":\"NEXUS\",\"type\":\"project\"}]}")],
     "Your profile: Senior Developer with 8 years experience. Current goals: Lead Engineer, Tech Lead. Skills: Python, Kubernetes.",
     "medium", False, False, False),

    ("I want to create a new project for my open source work. Can you help?",
     [], [], "I can create a project for you. What name and description would you like for this open source project?",
     "easy", False, True, False),

    ("What is the difference between task.list and task.create?",
     [], [], "task.list retrieves existing tasks (optionally filtered by project), while task.create adds a new task to a project. task.list is read-only; task.create modifies data and requires confirmation in NEXUS.",
     "easy", False, False, False),

    ("I want to delete a project. Proceed with deleting project 'OldProject'.",
     [], [], "This action cannot be undone. All tasks and data under 'OldProject' will be permanently removed. Confirm deletion?",
     "medium", False, False, True),

    ("Fetch the latest NEXUS changelog from the web.",
     [("web.fetch", {"url":"https://nexus.example.com/changelog"})],
     [("web.fetch", "{\"success\":true,\"title\":\"NEXUS Changelog\",\"content\":\"v2.1: Added voice assistant. v2.0: Major architecture overhaul. v1.9: Bug fixes.\"}")],
     "Latest NEXUS changelog: v2.1 added voice assistant, v2.0 was a major architecture overhaul, v1.9 included bug fixes.",
     "medium", False, False, False),

    ("Look up information about container orchestration tools on the web.",
     [("web.search", {"query":"container orchestration tools 2025"})],
     [("web.search", "{\"success\":true,\"results\":[{\"title\":\"Kubernetes vs Docker Swarm\",\"url\":\"https://example.com/k8s-vs-swarm\"},{\"title\":\"Nomad Guide\",\"url\":\"https://example.com/nomad\"}]}")],
     "Found results: 'Kubernetes vs Docker Swarm' (https://example.com/k8s-vs-swarm) and 'Nomad Guide' (https://example.com/nomad).",
     "easy", False, False, False),

    ("Research the best practices for container security.",
     [("web.research", {"topic":"container security best practices"})],
     [("web.research", "{\"success\":true,\"summary\":\"Key container security practices: use minimal base images, scan for vulnerabilities, enforce non-root users, use network policies, and rotate credentials regularly.\"}")],
     "Container security best practices: use minimal base images, scan for vulnerabilities, enforce non-root users, use network policies, and rotate credentials regularly.",
     "medium", False, False, False),
]

def make_example(user_content, tool_calls, tool_results, assistant_content, difficulty, multi_step, req_clarify, req_confirm):
    msgs = [{"role":"user","content":user_content}]
    if tool_calls:
        tc_list = []
        for name, args in tool_calls:
            tc_list.append({"type":"function","function":{"name":name,"arguments":args}})
            msgs.append({"role":"assistant","content":"","tool_calls":tc_list})
            tc_list = []
        for name, content in tool_results:
            msgs.append({"role":"tool","name":name,"content":content})
    msgs.append({"role":"assistant","content":assistant_content})
    tools_used = [tc[0] for tc in tool_calls] if tool_calls else []
    return {"messages":msgs,"metadata":{"category":"answer_vs_action","difficulty":difficulty,"tools_used":tools_used,"multi_step":multi_step,"requires_clarification":req_clarify,"requires_confirmation":req_confirm}}

for item in a2a_data:
    examples.append(make_example(*item))

print(f"Generated {len(examples)} answer_vs_action examples so far")
