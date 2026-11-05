"""API-Architect — registration. Compile API specs into first-class Hermes tools."""
import logging
from pathlib import Path

from . import schemas, tools

logger = logging.getLogger(__name__)


def register(ctx):
    """Wire schemas to handlers and bundle the usage skill."""
    from . import tools as tools_mod

    tools_mod._ctx = ctx  # module singleton: config + ctx.llm for the handlers

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                try:
                    ctx.register_skill(child.name, skill_md)
                except Exception as exc:  # never fail registration on a skill
                    logger.warning("api-architect: could not register skill %s: %s", child.name, exc)

    ctx.register_tool(name="architect_learn", toolset="api_architect", schema=schemas.ARCHITECT_LEARN, handler=tools.architect_learn)
    ctx.register_tool(name="architect_commit", toolset="api_architect", schema=schemas.ARCHITECT_COMMIT, handler=tools.architect_commit)
    ctx.register_tool(name="architect_list", toolset="api_architect", schema=schemas.ARCHITECT_LIST, handler=tools.architect_list)
    ctx.register_tool(name="api_call", toolset="api_architect", schema=schemas.API_CALL, handler=tools.api_call)
# NOTE: no hooks, no capabilities, no network access inside register() (doctor blocks sockets here).
