"""
API Router

Collects every feature's router into one APIRouter that app/main.py mounts under API_PREFIX
(/api/v1, see core/urls.py). Registration order matters wherever two routes could match the same
path: a literal segment like /projects/import or /conversations/ids must be registered before a
parameterized sibling like /projects/{project_id}, or the generic route swallows it.
"""

from fastapi import APIRouter

from app.features.analytics import conversations_router, stats_router
from app.features.api_keys import providers_router
from app.features.api_keys import router as api_keys_router
from app.features.auth import router as auth_router
from app.features.chat import latency_router, preview_router, public_router
from app.features.evaluation import router as evaluation_router
from app.features.knowledge import router as knowledge_router
from app.features.pages import admin_router as pages_admin_router
from app.features.pages import public_router as pages_public_router
from app.features.media import avatars_router, backgrounds_router, voices_router
from app.features.projects import publication_router, start_audio_router, transfer_router
from app.features.projects import router as projects_router
from app.features.pronunciation import router as pronunciation_router
from app.features.site_settings import admin_router as site_settings_admin_router
from app.features.site_settings import public_router as site_settings_public_router
from app.features.users import admin_users_router, profile_router

api_router = APIRouter()

# Authentication and the user's own account
api_router.include_router(auth_router.router)  # /auth: register, login, logout, password reset
api_router.include_router(profile_router.router)  # /me: own profile, picture, password, deletion

# Projects — the literal /projects/import route first (see module docstring)
api_router.include_router(transfer_router.router)  # /projects/import, /projects/{id}/export
api_router.include_router(projects_router.router)  # /projects: CRUD
api_router.include_router(publication_router.router)  # /projects/{id}/publication
api_router.include_router(start_audio_router.router)  # /projects/{id}/start-audio
api_router.include_router(preview_router.router)  # /projects/{id}/chat/messages, /chat/transcriptions
api_router.include_router(latency_router.router)  # /projects/{id}/latency-test/messages, /transcriptions

# Libraries and keys
api_router.include_router(avatars_router.router)  # /avatars
api_router.include_router(backgrounds_router.router)  # /backgrounds
api_router.include_router(voices_router.router)  # /voice-clips: voice library for local TTS
api_router.include_router(providers_router.router)  # /providers: provider registry
api_router.include_router(api_keys_router.router)  # /api-keys: the user's stored keys
api_router.include_router(pronunciation_router.router)  # /pronunciation: the user's TTS word list
api_router.include_router(knowledge_router.router)  # /knowledge-bases, /knowledge-documents, /providers/rag-status
api_router.include_router(evaluation_router.router)  # /test-sets, /test-cases, /evaluation/runs, /providers/evaluation-status

# Analytics
api_router.include_router(stats_router.router)  # /analytics/stats, /timeseries, /overview
api_router.include_router(conversations_router.router)  # /conversations: list, ids, detail, export, batch-delete

# Public chat (no login)
api_router.include_router(public_router.router)  # /public/{slug}...

# Admin, site settings and the editable public pages
api_router.include_router(admin_users_router.router)  # /admin/users
api_router.include_router(site_settings_admin_router.router)  # /admin/settings
api_router.include_router(site_settings_public_router.router)  # /settings/public
api_router.include_router(pages_admin_router.router)  # /admin/pages: editable landing/legal/custom pages
api_router.include_router(pages_public_router.router)  # /pages: their public read side
