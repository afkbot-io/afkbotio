"""Serializable wizard inventory helpers."""

from __future__ import annotations

from typing import Any

from afkbot.services.wizard.channel_catalog import channel_plan
from afkbot.services.wizard.contracts import WizardBranch, WizardPlan, WizardQuestion
from afkbot.services.wizard.profile_catalog import setup_profile_plan


def all_wizard_plans() -> tuple[WizardPlan, ...]:
    """Return all first-class wizard inventories in stable order."""

    return (
        setup_profile_plan(),
        channel_plan("telegram"),
        channel_plan("telethon"),
        channel_plan("partyflow"),
    )


def serialize_wizard_plan(plan: WizardPlan) -> dict[str, Any]:
    """Serialize one wizard plan for tests, diagnostics, or future CLI inspection."""

    return {
        "id": plan.id,
        "schema_version": plan.schema_version,
        "title_en": plan.title_en,
        "title_ru": plan.title_ru,
        "questions": [_serialize_question(question) for question in plan.questions],
        "branches": [_serialize_branch(branch) for branch in plan.branches],
    }


def _serialize_question(question: WizardQuestion) -> dict[str, Any]:
    return {
        "id": question.id,
        "section": question.section,
        "kind": question.kind,
        "title_en": question.title_en,
        "title_ru": question.title_ru,
        "prompt_en": question.prompt_en,
        "prompt_ru": question.prompt_ru,
        "default_value": question.default_value,
        "shown_when": question.shown_when,
        "advanced": question.advanced,
        "choices": [
            {
                "value": choice.value,
                "label_en": choice.label_en,
                "label_ru": choice.label_ru,
            }
            for choice in question.choices
        ],
    }


def _serialize_branch(branch: WizardBranch) -> dict[str, Any]:
    return {
        "id": branch.id,
        "condition": branch.condition,
        "question_ids": list(branch.question_ids),
        "label_en": branch.label_en,
        "label_ru": branch.label_ru,
    }
