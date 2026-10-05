"""TEMPORARY probe — deleted before the run finishes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.analytics_fixtures import seeded_client

pytestmark = pytest.mark.integration


async def test_probe(non_raising_client, truncated_database, db_session: AsyncSession) -> None:
    client = non_raising_client
    seed, auth = await seeded_client(client, db_session)

    def show(label, r):
        body = r.text[:300]
        print(f"\n### {label} -> {r.status_code} {body}")

    show("goal bad status", await client.post("/api/v1/learning/goals", headers=auth, json={"title": "x", "status": "nearly"}))
    show("goal bad priority", await client.post("/api/v1/learning/goals", headers=auth, json={"title": "x", "priority": "urgent"}))
    show("goal filter bad status", await client.get("/api/v1/learning/goals", headers=auth, params={"status": "bogus"}))
    show("activity filter bad type", await client.get("/api/v1/learning/activities", headers=auth, params={"activity_type": "studied"}))
    show("effort huge", await client.post("/api/v1/learning/goals", headers=auth, json={"title": "x", "estimated_effort_minutes": 999999999999}))
    show("duration huge", await client.post("/api/v1/learning/activities", headers=auth, json={"title": "x", "activity_type": "study_session", "duration_minutes": 999999999}))

    skill = (await client.post("/api/v1/learning/skills", headers=auth, json={"name": "FastAPI"})).json()
    skill2 = (await client.post("/api/v1/learning/skills", headers=auth, json={"name": "  FastAPI  "})).json()
    print("\n### duplicate trimmed:", skill2)
    print("### whitespace name:", (await client.post("/api/v1/learning/skills", headers=auth, json={"name": "   "})).status_code)
    show("patch dup name", await client.patch(f"/api/v1/learning/skills/{skill['id']}", headers=auth, json={"name": "  FastAPI  "}))
    show("patch current_level null", await client.patch(f"/api/v1/learning/skills/{skill['id']}", headers=auth, json={"current_level": None}))
    show("patch target_level null", await client.patch(f"/api/v1/learning/skills/{skill['id']}", headers=auth, json={"target_level": None}))

    goal = (await client.post("/api/v1/learning/goals", headers=auth, json={"title": "g"})).json()
    show("patch title null", await client.patch(f"/api/v1/learning/goals/{goal['id']}", headers=auth, json={"title": None}))
    show("patch progress null", await client.patch(f"/api/v1/learning/goals/{goal['id']}", headers=auth, json={"progress": None}))

    show("future occurred_at", await client.post("/api/v1/learning/activities", headers=auth, json={"title": "future", "activity_type": "study_session", "skill_id": skill["id"], "occurred_at": "2030-01-01T00:00:00Z"}))
    show("skills after future", await client.get("/api/v1/learning/skills", headers=auth))

    # recommendation day counts
    now = await db_session.scalar(select(func.now()))
    today = now.astimezone(UTC).date()
    for delta in (-3, -1, 0, 1, 2, 7):
        await client.post("/api/v1/learning/goals", headers=auth, json={"title": f"Goal {delta}", "target_date": (today + timedelta(days=delta)).isoformat(), "progress": 10})
    r = await client.post("/api/v1/learning/recommendations", headers=auth)
    print("\n### recommendations", r.status_code)
    for row in r.json():
        print("   -", row["reason"])

    r = await client.get("/api/v1/learning/goals", headers=auth)
    print("\n### goals total/summary", r.json()["total"], r.json()["summary"], r.json()["by_status"])