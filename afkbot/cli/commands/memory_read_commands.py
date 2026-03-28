"""Read-side memory CLI command registration."""

from __future__ import annotations

import asyncio
import json

import typer

from afkbot.cli.commands.memory_support import (
    filter_items,
    filter_memory_metadata,
    merge_search_hits,
    normalize_memory_kinds,
    normalize_source_kinds,
    resolve_cli_scope,
    serialize_memory_items,
)
from afkbot.cli.commands.runtime_assets_common import emit_structured_error
from afkbot.services.memory import (
    MemoryItemMetadata,
    MemoryScopeDescriptor,
    MemoryScopeMode,
    MemoryServiceError,
    MemoryVisibility,
    get_memory_service,
)
from afkbot.services.memory.digest import render_memory_digest
from afkbot.services.memory.runtime_scope import MemoryScopeResolutionError
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.settings import get_settings


def register_memory_read_commands(memory_app: typer.Typer) -> None:
    """Register read-only memory CLI commands under the given Typer app."""

    @memory_app.command("list")
    def list_memory(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        scope: MemoryScopeMode = typer.Option(
            "auto",
            "--scope",
            help="Target scope: auto, profile, chat, thread, or user_in_chat.",
        ),
        binding_id: str | None = typer.Option(
            None,
            "--binding-id",
            help="Resolve a concrete chat scope from one persisted channel binding id.",
        ),
        transport: str | None = typer.Option(None, "--transport", help="Explicit transport selector."),
        account_id: str | None = typer.Option(None, "--account-id", help="Explicit account selector."),
        peer_id: str | None = typer.Option(None, "--peer-id", help="Explicit chat/dialog selector."),
        thread_id: str | None = typer.Option(None, "--thread-id", help="Explicit topic/thread selector."),
        user_id: str | None = typer.Option(None, "--user-id", help="Explicit sender selector."),
        session_id: str | None = typer.Option(None, "--session-id", help="Optional logical session id."),
        visibility: MemoryVisibility | None = typer.Option(
            None,
            "--visibility",
            help="Optional visibility filter: local or promoted_global.",
        ),
        memory_kind: list[str] = typer.Option([], "--memory-kind", help="Repeatable semantic kind filter."),
        source_kind: list[str] = typer.Option([], "--source-kind", help="Repeatable source-kind filter."),
        limit: int = typer.Option(50, "--limit", min=1, help="Maximum items to list."),
        json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    ) -> None:
        """List memory items filtered by profile and optional scope."""

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
            metadata_items = asyncio.run(
                get_memory_service(settings).list(
                    profile_id=normalized_profile_id,
                    scope=resolved_scope,
                    visibility=visibility,
                    limit=limit,
                )
            )
            items = filter_items(
                items=serialize_memory_items(metadata_items),
                memory_kinds=normalize_memory_kinds(memory_kind),
                source_kinds=normalize_source_kinds(source_kind),
            )
        except (InvalidProfileIdError, MemoryScopeResolutionError, MemoryServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="memory_error")
            raise typer.Exit(code=1) from None

        payload = {
            "profile_id": normalized_profile_id,
            "scope": resolved_scope.model_dump(mode="json"),
            "count": len(items),
            "items": items,
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"Memory items: {len(items)}")
        for item in items:
            typer.echo(
                f"- {item['memory_key']} (scope={item['scope_kind']}, kind={item['memory_kind']}, "
                f"visibility={item['visibility']}, updated_at={item['updated_at']})"
            )

    @memory_app.command("profiles")
    def list_memory_profiles(
        json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    ) -> None:
        """List profile ids that currently own persisted memory rows."""

        try:
            settings = get_settings()
            profile_ids = asyncio.run(get_memory_service(settings).list_profiles())
        except MemoryServiceError as exc:
            emit_structured_error(exc, default_error_code="memory_error")
            raise typer.Exit(code=1) from None
        payload = {"profiles": profile_ids, "count": len(profile_ids)}
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"Profiles with memory: {len(profile_ids)}")
        for item in profile_ids:
            typer.echo(f"- {item}")

    @memory_app.command("show")
    def show_memory(
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
        """Show one memory item by logical key and scope."""

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
                get_memory_service(settings).get(
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
                    "profile_id": normalized_profile_id,
                    "scope": resolved_scope.model_dump(mode="json"),
                    "memory": item.model_dump(mode="json"),
                },
                ensure_ascii=True,
            )
        )

    @memory_app.command("search")
    def search_memory(
        query: str = typer.Argument(..., help="Semantic search query."),
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
        include_global: bool = typer.Option(
            False,
            "--include-global",
            help="Append promoted_global profile memory after local scoped hits.",
        ),
        global_limit: int | None = typer.Option(
            None,
            "--global-limit",
            min=1,
            help="Maximum promoted_global fallback hits when --include-global is enabled.",
        ),
        memory_kind: list[str] = typer.Option([], "--memory-kind", help="Repeatable semantic kind filter."),
        source_kind: list[str] = typer.Option([], "--source-kind", help="Repeatable source-kind filter."),
        limit: int = typer.Option(5, "--limit", min=1, help="Maximum local hits to return."),
        json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    ) -> None:
        """Search scoped memory items by semantic similarity."""

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
            memory_kinds = normalize_memory_kinds(memory_kind) or None
            source_kinds = normalize_source_kinds(source_kind) or None
            service = get_memory_service(settings)
            items = asyncio.run(
                service.search(
                    profile_id=normalized_profile_id,
                    query=query,
                    scope=resolved_scope,
                    visibility=None,
                    memory_kinds=memory_kinds,
                    source_kinds=source_kinds,
                    limit=limit,
                )
            )
            payload_items = serialize_memory_items(items)
            if include_global and not resolved_scope.is_profile_scope:
                global_items = asyncio.run(
                    service.search(
                        profile_id=normalized_profile_id,
                        query=query,
                        scope=MemoryScopeDescriptor.profile_scope(),
                        visibility="promoted_global",
                        memory_kinds=memory_kinds,
                        source_kinds=source_kinds,
                        limit=global_limit or limit,
                    )
                )
                payload_items = merge_search_hits(payload_items, serialize_memory_items(global_items))
        except (InvalidProfileIdError, MemoryScopeResolutionError, MemoryServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="memory_error")
            raise typer.Exit(code=1) from None

        payload = {
            "profile_id": normalized_profile_id,
            "scope": resolved_scope.model_dump(mode="json"),
            "count": len(payload_items),
            "items": payload_items,
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"Memory hits: {len(payload_items)}")
        for item in payload_items:
            typer.echo(
                f"- {item['memory_key']} (scope={item['scope_kind']}, "
                f"kind={item['memory_kind']}, score={item.get('score')})"
            )

    @memory_app.command("digest")
    def digest_memory(
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
        include_global: bool = typer.Option(
            False,
            "--include-global",
            help="Append promoted_global profile memory after local scoped items.",
        ),
        global_limit: int | None = typer.Option(
            None,
            "--global-limit",
            min=1,
            help="Maximum promoted_global fallback items when --include-global is enabled.",
        ),
        memory_kind: list[str] = typer.Option([], "--memory-kind", help="Repeatable semantic kind filter."),
        source_kind: list[str] = typer.Option([], "--source-kind", help="Repeatable source-kind filter."),
        limit: int = typer.Option(20, "--limit", min=1, help="Maximum local items to include."),
        json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    ) -> None:
        """Render a deterministic digest of one scoped memory view."""

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
            memory_kinds = normalize_memory_kinds(memory_kind)
            source_kinds = normalize_source_kinds(source_kind)
            service = get_memory_service(settings)
            local_items = asyncio.run(
                service.list(
                    profile_id=normalized_profile_id,
                    scope=resolved_scope,
                    visibility=None if not resolved_scope.is_profile_scope else "promoted_global",
                    limit=limit,
                )
            )
            local_items = filter_memory_metadata(
                items=local_items,
                memory_kinds=memory_kinds,
                source_kinds=source_kinds,
            )
            global_items: list[MemoryItemMetadata] = []
            if include_global and not resolved_scope.is_profile_scope:
                global_items = asyncio.run(
                    service.list(
                        profile_id=normalized_profile_id,
                        scope=MemoryScopeDescriptor.profile_scope(),
                        visibility="promoted_global",
                        limit=global_limit or limit,
                    )
                )
                global_items = filter_memory_metadata(
                    items=global_items,
                    memory_kinds=memory_kinds,
                    source_kinds=source_kinds,
                )
            digest = render_memory_digest(
                scope=resolved_scope,
                local_items=local_items,
                global_items=global_items,
            )
        except (InvalidProfileIdError, MemoryScopeResolutionError, MemoryServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="memory_error")
            raise typer.Exit(code=1) from None

        payload = {
            "profile_id": normalized_profile_id,
            "scope": resolved_scope.model_dump(mode="json"),
            "item_count": digest.item_count,
            "local_count": digest.local_count,
            "global_count": digest.global_count,
            "kind_counts": digest.kind_counts,
            "digest_md": digest.digest_md,
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(digest.digest_md)


__all__ = ["register_memory_read_commands"]
