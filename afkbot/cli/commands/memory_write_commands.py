"""Write-side memory CLI command registration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.memory_support import (
    resolve_cli_scope,
    resolve_optional_text_or_file,
)
from afkbot.cli.commands.runtime_assets_common import emit_structured_error
from afkbot.services.memory import (
    MemoryKind,
    MemoryScopeMode,
    MemoryServiceError,
    MemorySourceKind,
    MemoryVisibility,
    get_memory_service,
)
from afkbot.services.memory.runtime_scope import MemoryScopeResolutionError
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.settings import get_settings


def register_memory_write_commands(memory_app: typer.Typer) -> None:
    """Register write-side memory CLI commands under the given Typer app."""

    @memory_app.command("set")
    def set_memory(
        memory_key: str = typer.Argument(..., help="Logical memory key."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        scope: MemoryScopeMode = typer.Option(
            "profile",
            "--scope",
            help="Target scope: auto, profile, chat, thread, or user_in_chat.",
        ),
        binding_id: str | None = typer.Option(None, "--binding-id", help="Resolve scope from one binding id."),
        transport: str | None = typer.Option(None, "--transport", help="Explicit transport selector."),
        account_id: str | None = typer.Option(None, "--account-id", help="Explicit account selector."),
        peer_id: str | None = typer.Option(None, "--peer-id", help="Explicit chat/dialog selector."),
        thread_id: str | None = typer.Option(None, "--thread-id", help="Explicit topic/thread selector."),
        user_id: str | None = typer.Option(None, "--user-id", help="Explicit sender selector."),
        session_id: str | None = typer.Option(None, "--session-id", help="Optional logical session id."),
        text: str | None = typer.Option(None, "--text", help="Inline semantic content."),
        from_file: Path | None = typer.Option(
            None,
            "--from-file",
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
            help="Read semantic content from a local file.",
        ),
        summary: str | None = typer.Option(
            None,
            "--summary",
            help="Optional short human-readable summary for this memory item.",
        ),
        details_text: str | None = typer.Option(
            None,
            "--details-text",
            help="Optional inline markdown details for this memory item.",
        ),
        details_file: Path | None = typer.Option(
            None,
            "--details-file",
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
            help="Read markdown details from a local file.",
        ),
        source: str | None = typer.Option(None, "--source", help="Optional logical source label."),
        source_kind: MemorySourceKind = typer.Option(
            "manual",
            "--source-kind",
            help="Source kind: manual, auto, watcher, or automation.",
        ),
        memory_kind: MemoryKind = typer.Option(
            "note",
            "--memory-kind",
            help="Semantic kind: fact, preference, decision, task, risk, or note.",
        ),
        visibility: MemoryVisibility | None = typer.Option(
            None,
            "--visibility",
            help="Optional visibility: local or promoted_global.",
        ),
    ) -> None:
        """Create or replace one scoped semantic memory item."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            resolved_scope = asyncio.run(
                resolve_cli_scope(
                    profile_id=normalized_profile_id,
                    scope=scope,
                    binding_id=binding_id,
                    transport=transport,
                    account_id=account_id,
                    peer_id=peer_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    session_id=session_id,
                )
            )
            content = resolve_optional_text_or_file(text=text, from_file=from_file)
            details_md = resolve_optional_text_or_file(text=details_text, from_file=details_file)
            if content is None and summary is None and details_md is None:
                raise_usage_error("Provide semantic content via --text/--from-file or summary/details.")
            item = asyncio.run(
                get_memory_service(settings).upsert(
                    profile_id=normalized_profile_id,
                    scope=resolved_scope,
                    memory_key=memory_key,
                    content=content,
                    summary=summary,
                    details_md=details_md,
                    source=source,
                    source_kind=source_kind,
                    memory_kind=memory_kind,
                    visibility=visibility,
                )
            )
        except (
            InvalidProfileIdError,
            MemoryScopeResolutionError,
            MemoryServiceError,
            OSError,
            ValueError,
        ) as exc:
            emit_structured_error(exc, default_error_code="memory_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "profile_id": normalized_profile_id,
                    "scope": resolved_scope.model_dump(mode="json"),
                    "memory": item.model_dump(mode="json"),
                },
                ensure_ascii=True,
            )
        )

    @memory_app.command("delete")
    def delete_memory(
        memory_key: str = typer.Argument(..., help="Logical memory key."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        scope: MemoryScopeMode = typer.Option(
            "profile",
            "--scope",
            help="Target scope: auto, profile, chat, thread, or user_in_chat.",
        ),
        binding_id: str | None = typer.Option(None, "--binding-id", help="Resolve scope from one binding id."),
        transport: str | None = typer.Option(None, "--transport", help="Explicit transport selector."),
        account_id: str | None = typer.Option(None, "--account-id", help="Explicit account selector."),
        peer_id: str | None = typer.Option(None, "--peer-id", help="Explicit chat/dialog selector."),
        thread_id: str | None = typer.Option(None, "--thread-id", help="Explicit topic/thread selector."),
        user_id: str | None = typer.Option(None, "--user-id", help="Explicit sender selector."),
        session_id: str | None = typer.Option(None, "--session-id", help="Optional logical session id."),
    ) -> None:
        """Delete one scoped memory item by logical key."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            resolved_scope = asyncio.run(
                resolve_cli_scope(
                    profile_id=normalized_profile_id,
                    scope=scope,
                    binding_id=binding_id,
                    transport=transport,
                    account_id=account_id,
                    peer_id=peer_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    session_id=session_id,
                )
            )
            deleted = asyncio.run(
                get_memory_service(settings).delete(
                    profile_id=normalized_profile_id,
                    memory_key=memory_key,
                    scope=resolved_scope,
                )
            )
        except (InvalidProfileIdError, MemoryScopeResolutionError, MemoryServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="memory_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "ok": bool(deleted),
                    "profile_id": normalized_profile_id,
                    "scope": resolved_scope.model_dump(mode="json"),
                    "memory_key": memory_key,
                },
                ensure_ascii=True,
            )
        )

    @memory_app.command("promote")
    def promote_memory(
        memory_key: str = typer.Argument(..., help="Logical memory key in local scope."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        scope: MemoryScopeMode = typer.Option(
            "auto",
            "--scope",
            help="Source scope: auto, chat, thread, or user_in_chat.",
        ),
        binding_id: str | None = typer.Option(None, "--binding-id", help="Resolve source scope from one binding id."),
        transport: str | None = typer.Option(None, "--transport", help="Explicit transport selector."),
        account_id: str | None = typer.Option(None, "--account-id", help="Explicit account selector."),
        peer_id: str | None = typer.Option(None, "--peer-id", help="Explicit chat/dialog selector."),
        thread_id: str | None = typer.Option(None, "--thread-id", help="Explicit topic/thread selector."),
        user_id: str | None = typer.Option(None, "--user-id", help="Explicit sender selector."),
        session_id: str | None = typer.Option(None, "--session-id", help="Optional logical session id."),
        target_memory_key: str | None = typer.Option(
            None,
            "--target-memory-key",
            help="Optional logical key for the promoted profile-global copy.",
        ),
    ) -> None:
        """Promote one local scoped memory item into profile-global memory."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            resolved_scope = asyncio.run(
                resolve_cli_scope(
                    profile_id=normalized_profile_id,
                    scope=scope,
                    binding_id=binding_id,
                    transport=transport,
                    account_id=account_id,
                    peer_id=peer_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    session_id=session_id,
                )
            )
            item = asyncio.run(
                get_memory_service(settings).promote(
                    profile_id=normalized_profile_id,
                    memory_key=memory_key,
                    from_scope=resolved_scope,
                    target_memory_key=target_memory_key,
                )
            )
        except (InvalidProfileIdError, MemoryScopeResolutionError, MemoryServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="memory_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "profile_id": normalized_profile_id,
                    "from_scope": resolved_scope.model_dump(mode="json"),
                    "memory": item.model_dump(mode="json"),
                },
                ensure_ascii=True,
            )
        )

    @memory_app.command("gc")
    def garbage_collect_memory(
        profile_id: str | None = typer.Option(
            None,
            "--profile",
            help="Optional profile id; omit to collect all profiles.",
        ),
    ) -> None:
        """Apply configured retention/overflow limits to memory storage."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id) if profile_id is not None else None
            deleted = asyncio.run(
                get_memory_service(settings).garbage_collect(profile_id=normalized_profile_id)
            )
        except (InvalidProfileIdError, MemoryServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="memory_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "profile_id": profile_id,
                    "deleted": deleted,
                },
                ensure_ascii=True,
            )
        )


__all__ = ["register_memory_write_commands"]
