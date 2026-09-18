"""Tombstone (spec 0006 E4): routes retired to voiceai.modules.auth.controller; re-mount by re-adding handlers — see git history."""

from fastapi import APIRouter

auth_router = APIRouter(prefix="/auth", tags=["Auth"])
