"""Structured text diff rendering for tools and future UI surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import HtmlDiff, SequenceMatcher, unified_diff


@dataclass(frozen=True, slots=True)
class DiffBundle:
    """Rendered diff payload in unified and/or HTML forms."""

    before_label: str
    after_label: str
    added_lines: int
    removed_lines: int
    changed: bool
    unified_diff: str | None = None
    html: str | None = None
    markdown_preview: str | None = None


def render_diff_bundle(
    *,
    before_text: str,
    after_text: str,
    before_label: str,
    after_label: str,
    output_format: str,
    context_lines: int,
) -> DiffBundle:
    """Render one diff bundle using deterministic stdlib renderers."""

    normalized_format = output_format.strip().lower()
    before_lines = before_text.splitlines()
    after_lines = after_text.splitlines()

    unified_text: str | None = None
    if normalized_format in {"unified", "both"}:
        unified_lines = list(
            unified_diff(
                before_lines,
                after_lines,
                fromfile=before_label,
                tofile=after_label,
                lineterm="",
                n=context_lines,
            )
        )
        unified_text = "\n".join(unified_lines)

    html_text: str | None = None
    if normalized_format in {"html", "both"}:
        renderer = HtmlDiff(wrapcolumn=120)
        html_text = renderer.make_file(
            before_lines,
            after_lines,
            fromdesc=before_label,
            todesc=after_label,
            context=True,
            numlines=context_lines,
            charset="utf-8",
        )

    added_lines = 0
    removed_lines = 0
    if unified_text is not None:
        for line in unified_text.splitlines():
            if line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
                continue
            if line.startswith("+"):
                added_lines += 1
            elif line.startswith("-"):
                removed_lines += 1
    else:
        added_lines, removed_lines = _estimate_changed_line_counts(
            before_lines=before_lines,
            after_lines=after_lines,
        )

    return DiffBundle(
        before_label=before_label,
        after_label=after_label,
        added_lines=added_lines,
        removed_lines=removed_lines,
        changed=before_text != after_text,
        unified_diff=unified_text,
        html=html_text,
        markdown_preview=_build_markdown_preview(
            before_label=before_label,
            after_label=after_label,
            added_lines=added_lines,
            removed_lines=removed_lines,
            changed=before_text != after_text,
            unified_diff=unified_text,
        ),
    )


def _estimate_changed_line_counts(*, before_lines: list[str], after_lines: list[str]) -> tuple[int, int]:
    """Estimate added and removed lines when unified output was not requested."""

    added_lines = 0
    removed_lines = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=before_lines, b=after_lines).get_opcodes():
        if tag == "equal":
            continue
        if tag in {"replace", "delete"}:
            removed_lines += i2 - i1
        if tag in {"replace", "insert"}:
            added_lines += j2 - j1
    return added_lines, removed_lines


def _build_markdown_preview(
    *,
    before_label: str,
    after_label: str,
    added_lines: int,
    removed_lines: int,
    changed: bool,
    unified_diff: str | None,
) -> str | None:
    """Build one compact markdown representation suitable for agent replies."""

    if unified_diff is None:
        return None
    title = f"**Changes:** `{before_label}` -> `{after_label}`"
    if not changed:
        return f"{title}\n\nNo content changes."
    summary = f"+{added_lines} / -{removed_lines}"
    return f"{title} ({summary})\n\n```diff\n{unified_diff}\n```"
