from fastapi import APIRouter

from app.router.auth_router import router as auth_router
from app.router.chat_router import router as chat_router
from app.router.document_router import router as document_router
from app.router.knowledge_router import router as knowledge_router
from app.router.memory_router import router as memory_router
from app.router.search_router import router as search_router


api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(chat_router)
api_router.include_router(knowledge_router)
api_router.include_router(document_router)
api_router.include_router(search_router)
api_router.include_router(memory_router)
