"""Runtime follow-up hints and early-stop decisions after one tool batch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from afkbot.services.apps.credential_manifest import AppCredentialManifest
from afkbot.services.agent_loop.execution_posture import first_execution_blocker
from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
from afkbot.services.tools.base import ToolCall, ToolResult

if TYPE_CHECKING:
    from afkbot.services.apps.registry_core import AppDefinition


@dataclass(frozen=True, slots=True)
class ToolFollowupDecision:
    """Deterministic follow-up hint or early-stop decision after one tool batch."""

    consecutive_missing_file_reads: int
    history_prompt: str | None = None
    final_message: str | None = None


class LLMToolFollowupPolicy:
    """Build follow-up prompts or early-stop messages from tool execution results."""

    def __init__(self, *, tool_skill_resolver: ToolSkillResolver) -> None:
        self._tool_skill_resolver = tool_skill_resolver

    def determine(
        self,
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
        visible_tool_names: set[str],
        consecutive_missing_file_reads: int,
        profile_id: str,
    ) -> ToolFollowupDecision:
        """Return bounded runtime hint or early-stop after one tool execution batch."""

        if self._all_missing_file_reads(tool_calls=tool_calls, tool_results=tool_results):
            next_count = consecutive_missing_file_reads + 1
            if "file.write" not in visible_tool_names and next_count >= 3:
                return ToolFollowupDecision(
                    consecutive_missing_file_reads=next_count,
                    final_message=(
                        "The requested file could not be found, and the current tool surface does "
                        "not allow creating or editing files. Provide an existing path or enable "
                        "file write access."
                    ),
                )
            if next_count >= 2:
                if "file.write" in visible_tool_names:
                    prompt = (
                        "Runtime hint: the requested path does not exist yet. Stop guessing more "
                        "filenames. If the task is to create a new file, pick one path and call "
                        "`file.write` next."
                    )
                else:
                    prompt = (
                        "Runtime hint: the requested path does not exist, and only read-only file "
                        "tools are visible. Do not keep guessing filenames; explain the missing file "
                        "or ask for a concrete existing path."
                    )
                return ToolFollowupDecision(
                    consecutive_missing_file_reads=next_count,
                    history_prompt=prompt,
                )
            return ToolFollowupDecision(consecutive_missing_file_reads=next_count)

        if self._has_file_search_file_path_error(
            tool_calls=tool_calls,
            tool_results=tool_results,
        ):
            return ToolFollowupDecision(
                consecutive_missing_file_reads=0,
                history_prompt=(
                    "Runtime hint: `file.search` only accepts directories. Use `file.read` for one "
                    "file, or `diffs.render` if the user asked to show changes."
                ),
            )

        if (
            "diffs.render" in visible_tool_names
            and not any(call.name == "diffs.render" for call in tool_calls)
            and self._has_inline_diff_suggestion(tool_results=tool_results)
        ):
            return ToolFollowupDecision(
                consecutive_missing_file_reads=0,
                history_prompt=(
                    "Runtime hint: the successful file mutation result already includes "
                    "`diff_suggestion` params. If the user asked for a diff or changes, call "
                    "`diffs.render` with that payload instead of re-reading or re-editing the file."
                ),
            )

        display_text = self._single_display_text(tool_results=tool_results)
        if display_text is not None:
            return ToolFollowupDecision(
                consecutive_missing_file_reads=0,
                final_message=display_text,
            )

        credentials_prompt = self._build_credentials_followup_prompt(
            tool_calls=tool_calls,
            tool_results=tool_results,
            visible_tool_names=visible_tool_names,
            profile_id=profile_id,
        )
        if credentials_prompt is not None:
            return ToolFollowupDecision(
                consecutive_missing_file_reads=0,
                history_prompt=credentials_prompt,
            )

        browser_prompt = self._build_browser_followup_prompt(
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        if browser_prompt is not None:
            return ToolFollowupDecision(
                consecutive_missing_file_reads=0,
                history_prompt=browser_prompt,
            )

        bash_prompt = self._build_bash_followup_prompt(
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        if bash_prompt is not None:
            return ToolFollowupDecision(
                consecutive_missing_file_reads=0,
                history_prompt=bash_prompt,
            )

        execution_blocker = first_execution_blocker(
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        if execution_blocker is not None:
            return ToolFollowupDecision(
                consecutive_missing_file_reads=0,
                final_message=execution_blocker.message,
            )

        return ToolFollowupDecision(consecutive_missing_file_reads=0)

    def _build_credentials_followup_prompt(
        self,
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
        visible_tool_names: set[str],
        profile_id: str,
    ) -> str | None:
        """Suggest the next secure credential step after empty credentials.list results."""

        if "credentials.request" not in visible_tool_names:
            return None
        if len(tool_calls) != len(tool_results):
            return None
        prompts: list[str] = []
        for tool_call, result in zip(tool_calls, tool_results, strict=True):
            if tool_call.name != "credentials.list" or not result.ok:
                continue
            payload = result.payload
            bindings_raw = payload.get("bindings")
            if not isinstance(bindings_raw, list):
                continue
            app_name = str(tool_call.params.get("app_name") or "").strip().lower()
            if not app_name:
                continue
            app_definition = self._tool_skill_resolver.app_registry(profile_id=profile_id).get(app_name)
            if app_definition is None:
                continue
            missing_slugs = self._missing_required_credentials(
                app_definition=app_definition,
                bindings=bindings_raw,
            )
            if not missing_slugs:
                continue
            first_slug = missing_slugs[0]
            prompts.append(
                "Runtime hint: no stored credentials were found for integration "
                f"`{app_name}`. Call `credentials.request` next for one missing field "
                f"(start with `{first_slug}`), then continue until required bindings exist "
                "before using `app.run`."
            )
        if not prompts:
            return None
        return "\n".join(dict.fromkeys(prompts))

    @staticmethod
    def _build_browser_followup_prompt(
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> str | None:
        """Suggest the next browser step after a classified browser failure."""

        if len(tool_calls) != len(tool_results):
            return None
        prompts: list[str] = []
        for tool_call, result in zip(tool_calls, tool_results, strict=True):
            if tool_call.name != "browser.control" or result.ok:
                continue
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            error_class = str(metadata.get("browser_error_class") or "").strip()
            suggested_next_action = str(metadata.get("suggested_next_action") or "").strip()
            raw_reason = str(result.reason or "").strip()
            if error_class == "browser_target_closed":
                prompts.append(
                    "Runtime hint: the previous browser page or context was closed. "
                    "Treat the current browser session as dead, reopen it with "
                    "`browser.control action='open'`, then continue the task instead of "
                    f"stopping with a generic apology. Raw reason: {raw_reason}"
                )
                continue
            if error_class == "browser_session_missing":
                prompts.append(
                    "Runtime hint: there is no live browser session yet. Call "
                    "`browser.control action='open'` first before navigate/content/screenshot."
                )
                continue
            if error_class == "browser_runtime_missing":
                prompts.append(
                    "Runtime hint: browser runtime is unavailable in this environment. "
                    "Explain that browsing could not start and include the concrete runtime "
                    f"reason. Raw reason: {raw_reason}"
                )
                continue
            if error_class == "browser_action_timeout":
                prompts.append(
                    "Runtime hint: the browser action timed out. Prefer one more guided attempt "
                    "with `browser.control action='wait'` using a selector/text/url target, "
                    "then retry a lighter next action before finalizing."
                )
                continue
            if error_class in {"browser_invalid", "browser_invalid_request"}:
                prompts.append(
                    "Runtime hint: the browser request shape was invalid. Retry with supported "
                    "browser fields such as `selector`, `label`, `placeholder`, `field_name`, "
                    "`role`, `target_text`, `key`, or `value`, and explain the concrete "
                    f"validation error if you cannot continue. Raw reason: {raw_reason}"
                )
                continue
            if suggested_next_action == "inspect_error" and raw_reason:
                prompts.append(
                    "Runtime hint: the browser action failed. Explain the concrete browser error "
                    f"to the user instead of claiming no capability. Raw reason: {raw_reason}"
                )
        if not prompts:
            return None
        return "\n".join(dict.fromkeys(prompts))

    @staticmethod
    def _build_bash_followup_prompt(
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> str | None:
        """Suggest the next step when `bash.exec` leaves a live interactive session open."""

        if len(tool_calls) != len(tool_results):
            return None
        prompts: list[str] = []
        for tool_call, result in zip(tool_calls, tool_results, strict=True):
            if tool_call.name != "bash.exec" or not result.ok:
                continue
            payload = result.payload
            session_id = str(payload.get("session_id") or "").strip()
            running = payload.get("running") is True
            if not session_id or not running:
                continue
            prompts.append(
                "Runtime hint: `bash.exec` left a live shell session running "
                f"(`session_id={session_id}`). Continue with `bash.exec` using that same "
                "`session_id`. Use `chars` to answer prompts such as `y\\n`, or call "
                "`bash.exec` again with the same `session_id` and empty `chars` to poll for "
                "more output. Do not finalize while the session is still running unless the task "
                "is blocked."
            )
        if not prompts:
            return None
        return "\n".join(dict.fromkeys(prompts))

    @staticmethod
    def _missing_required_credentials(
        *,
        app_definition: AppDefinition,
        bindings: list[object],
    ) -> tuple[str, ...]:
        """Return required credential slugs missing from visible app runtime bindings."""

        manifest = app_definition.credential_manifest
        if manifest is None:
            return ()
        required = LLMToolFollowupPolicy._required_slugs_from_manifest(manifest)
        if not required:
            return ()
        existing: set[str] = set()
        for item in bindings:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("credential_name") or item.get("CREDENTIAL_SLUG") or "").strip()
            if slug:
                existing.add(slug)
        return tuple(slug for slug in required if slug not in existing)

    @staticmethod
    def _required_slugs_from_manifest(manifest: AppCredentialManifest) -> tuple[str, ...]:
        """Return deterministic required credential slugs for one app manifest."""

        ordered: list[str] = []
        seen: set[str] = set()
        for action_manifest in manifest.actions.values():
            for slug in action_manifest.required:
                normalized = str(slug).strip()
                if not normalized or normalized in seen:
                    continue
                ordered.append(normalized)
                seen.add(normalized)
        for slug, field in manifest.fields.items():
            normalized = str(slug).strip()
            if not normalized or normalized in seen or not field.required_by_default:
                continue
            ordered.append(normalized)
            seen.add(normalized)
        return tuple(ordered)

    @staticmethod
    def _all_missing_file_reads(
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> bool:
        if not tool_calls or len(tool_calls) != len(tool_results):
            return False
        for tool_call, result in zip(tool_calls, tool_results, strict=True):
            if tool_call.name != "file.read":
                return False
            if result.ok or result.error_code != "file_read_invalid":
                return False
            if "Path does not exist" not in str(result.reason or ""):
                return False
        return True

    @staticmethod
    def _has_file_search_file_path_error(
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> bool:
        if len(tool_calls) != len(tool_results):
            return False
        for tool_call, result in zip(tool_calls, tool_results, strict=True):
            if tool_call.name != "file.search":
                continue
            if result.ok or result.error_code != "file_search_invalid":
                continue
            if "Path is not a directory" in str(result.reason or ""):
                return True
        return False

    @staticmethod
    def _has_inline_diff_suggestion(*, tool_results: list[ToolResult]) -> bool:
        for result in tool_results:
            if not result.ok:
                continue
            suggestion = result.payload.get("diff_suggestion")
            if isinstance(suggestion, dict) and suggestion:
                return True
        return False

    @staticmethod
    def _single_display_text(*, tool_results: list[ToolResult]) -> str | None:
        """Return one deterministic final text emitted directly by a successful tool."""

        if len(tool_results) != 1:
            return None
        result = tool_results[0]
        if not result.ok:
            return None
        display_text = result.payload.get("display_text")
        if not isinstance(display_text, str):
            return None
        text = display_text.strip()
        return text or None
