from fastapi import APIRouter

from app.api.endpoints import ai, authnd, cards, genspark, llm_configs, projects, prompts
from app.api.endpoints import assistant as assistant_ep
from app.api.endpoints import autonomous as autonomous_ep
from app.api.endpoints import bible as bible_ep
from app.api.endpoints import chapter_reviews as chapter_reviews_ep
from app.api.endpoints import context as context_ep
from app.api.endpoints import creative as creative_ep
from app.api.endpoints import foreshadow as foreshadow_ep
from app.api.endpoints import craft as craft_ep
from app.api.endpoints import forge as forge_ep
from app.api.endpoints import knowledge as knowledge_ep
from app.api.endpoints import lab as lab_ep
from app.api.endpoints import memory as memory_ep
from app.api.endpoints import relation_graph as relation_graph_ep
from app.api.endpoints import story_memory as story_memory_ep
from app.api.endpoints import workflow_agent as workflow_agent_ep
from app.api.endpoints import workflows as workflows_ep


api_router = APIRouter()
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(llm_configs.router, prefix="/llm-configs", tags=["llm-configs"])
api_router.include_router(authnd.router, prefix="/authnd", tags=["authnd"])
api_router.include_router(genspark.router, prefix="/genspark", tags=["genspark"])

api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(assistant_ep.router, prefix="/ai", tags=["assistant"])
api_router.include_router(workflow_agent_ep.router, tags=["workflow-agent"])
api_router.include_router(prompts.router, prefix="/prompts", tags=["prompts"])
api_router.include_router(cards.router, prefix="", tags=["cards"])
api_router.include_router(chapter_reviews_ep.router, prefix="/chapter-reviews", tags=["chapter-reviews"])

api_router.include_router(context_ep.router, prefix="/context", tags=["context"])
api_router.include_router(creative_ep.router, prefix="/creative", tags=["creative"])
api_router.include_router(memory_ep.router, prefix="/memory", tags=["memory"])
api_router.include_router(relation_graph_ep.router, prefix="/relation-graph", tags=["relation-graph"])
api_router.include_router(foreshadow_ep.router, prefix="/foreshadow", tags=["foreshadow"])
api_router.include_router(bible_ep.router, prefix="/bible", tags=["bible"])
api_router.include_router(lab_ep.router, prefix="/lab", tags=["lab"])
api_router.include_router(story_memory_ep.router, prefix="/story-memory", tags=["story-memory"])
api_router.include_router(forge_ep.router, prefix="/forge", tags=["forge"])
api_router.include_router(craft_ep.router, prefix="/craft", tags=["craft"])
api_router.include_router(autonomous_ep.router, prefix="/autonomous", tags=["autonomous"])
api_router.include_router(knowledge_ep.router, prefix="/knowledge", tags=["knowledge"])
api_router.include_router(workflows_ep.router, tags=["workflows"])
