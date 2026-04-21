"""FastAPI routes for PartyFlow outgoing webhook ingress."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from afkbot.services.channels.endpoint_service import (
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
)
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
        try:
            endpoint = await get_channel_endpoint_service(settings).get(endpoint_id=endpoint_id)
        except ChannelEndpointServiceError as exc:
            if exc.error_code != "channel_endpoint_not_found":
                raise
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error_code": "partyflow_channel_not_found",
                    "reason": f"PartyFlow channel endpoint is not configured: {endpoint_id}",
                },
            ) from exc
        if endpoint.transport != "partyflow":
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error_code": "partyflow_channel_not_found",
                    "reason": f"PartyFlow channel endpoint is not configured: {endpoint_id}",
                },
            )
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error_code": "partyflow_channel_not_active",
                "reason": f"PartyFlow channel runtime is not active: {endpoint_id}",
                "retry_after": 1,
            },
            headers={"Retry-After": "1"},
        )
    body = await request.body()
    status_code, payload = await runtime.handle_webhook(
        headers={str(key).lower(): value for key, value in request.headers.items()},
        body=body,
    )
    headers: dict[str, str] = {}
    retry_after = payload.get("retry_after")
    if status_code in {429, 503} and isinstance(retry_after, int):
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=status_code, content=payload, headers=headers or None)
