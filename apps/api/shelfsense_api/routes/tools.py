"""Tools over HTTP: what the MCP server (and any other client) calls."""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from shelfsense_api.agents.tools import REGISTRY, ToolContext, execute, specs_for_role
from shelfsense_api.deps import CurrentPrincipal, SettingsDep, TenantSession
from shelfsense_api.guardrails import tools_for_role
from shelfsense_api.routes._common import READ_ONE_RESPONSES, READ_RESPONSES
from shelfsense_api.schemas import ToolRunOut, ToolSpecOut

router = APIRouter(prefix="/v1/tools", tags=["tools"])


@router.get("", response_model=list[ToolSpecOut], responses=READ_RESPONSES)
async def list_tools(principal: CurrentPrincipal) -> list[ToolSpecOut]:
    """Tools the caller's role may invoke, with their JSON Schemas."""
    return [
        ToolSpecOut(name=s.name, description=s.description, input_schema=s.input_schema)
        for s in specs_for_role(principal.role.value)
    ]


@router.post("/{name}", response_model=ToolRunOut, responses=READ_ONE_RESPONSES)
async def run_tool(
    name: str,
    arguments: dict[str, Any],
    principal: CurrentPrincipal,
    session: TenantSession,
    settings: SettingsDep,
) -> ToolRunOut:
    """Execute one tool under the caller's tenant and role. Logged like any planner call."""
    if name not in REGISTRY:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no tool named {name!r}")
    if name not in tools_for_role(principal.role.value) or REGISTRY[name].handler is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"role {principal.role.value!r} may not call {name!r}"
        )
    ctx = ToolContext(
        session=session, settings=settings, tenant_id=principal.tenant_id, role=principal.role.value
    )
    execution = await execute(ctx, name, arguments)
    return ToolRunOut(
        name=name,
        result=execution.result,
        error=execution.error,
        duration_ms=execution.duration_ms,
    )
