"""Version 1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    activity,
    analytics,
    auth,
    availability,
    calendar,
    career,
    developer,
    health,
    intelligence,
    knowledge,
    learning,
    ml,
    planner,
    projects,
    recommendations,
    risks,
    tags,
    tasks,
    users,
    work_sessions,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(users.router)
api_v1_router.include_router(projects.router)
api_v1_router.include_router(tasks.router)
api_v1_router.include_router(tags.router)
api_v1_router.include_router(activity.router)
api_v1_router.include_router(calendar.router)
api_v1_router.include_router(work_sessions.router)
api_v1_router.include_router(planner.router)
api_v1_router.include_router(availability.router)
api_v1_router.include_router(knowledge.router)
api_v1_router.include_router(analytics.router)
api_v1_router.include_router(developer.router)
api_v1_router.include_router(risks.router)
api_v1_router.include_router(recommendations.router)
api_v1_router.include_router(intelligence.router)
api_v1_router.include_router(learning.router)
api_v1_router.include_router(career.router)
api_v1_router.include_router(ml.router)

__all__ = ["api_v1_router"]
