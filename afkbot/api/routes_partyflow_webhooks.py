"""FastAPI routes for PartyFlow outgoing webhook ingress."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from afkbot.services.channels.partyflow_runtime_registry import (
    get_partyflow_webhook_runtime_registry,
)
from afkbot.settings import get_settings

router = APIRouter(tags=["partyflow-webhooks"])


@router.post("/v1/channels/partyflow/{endpoint_id}/webhook")
async def receive_partyflow_webhook(endpoint_id: str, request: Request) -> JSONResponse:
    """Accept one PartyFlow outgoing webhook and delegate it to the live channel runtime."""

    settings = get_settings()
    runtime = get_partyflow_webhook_runtime_registry(settings).get(endpoint_id)
    if runtime is None:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error_code": "partyflow_channel_not_active",
                "reason": f"PartyFlow channel runtime is not active: {endpoint_id}",
            },
        )
    body = await request.body()
    status_code, payload = await runtime.handle_webhook(
        headers={str(key).lower(): value for key, value in request.headers.items()},
        body=body,
    )
    return JSONResponse(status_code=status_code, content=payload)
