"""Contracts for renderer-neutral setup/profile/channel wizard plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from afkbot.cli.presentation.prompt_i18n import PromptLanguage

WizardQuestionKind = Literal["text", "secret", "confirm", "single", "multi", "integer", "info"]


@dataclass(frozen=True, slots=True)
class WizardChoice:
    """One stable selectable wizard value with localized labels."""

    value: str
    label_en: str
    label_ru: str

    def label(self, *, lang: PromptLanguage) -> str:
        """Return the localized label."""

        return self.label_ru if lang == PromptLanguage.RU else self.label_en


@dataclass(frozen=True, slots=True)
class WizardQuestion:
    """One renderer-neutral wizard question definition."""

    id: str
    section: str
    kind: WizardQuestionKind
    title_en: str
    title_ru: str
    prompt_en: str
    prompt_ru: str
    choices: tuple[WizardChoice, ...] = ()
    default_value: str | None = None
    shown_when: str | None = None
    advanced: bool = False

    def title(self, *, lang: PromptLanguage) -> str:
        """Return the localized title."""

        return self.title_ru if lang == PromptLanguage.RU else self.title_en

    def prompt(self, *, lang: PromptLanguage) -> str:
        """Return the localized prompt/help text."""

        return self.prompt_ru if lang == PromptLanguage.RU else self.prompt_en


@dataclass(frozen=True, slots=True)
class WizardBranch:
    """A named conditional branch in a wizard graph."""

    id: str
    condition: str
    question_ids: tuple[str, ...]
    label_en: str
    label_ru: str

    def label(self, *, lang: PromptLanguage) -> str:
        """Return the localized branch label."""

        return self.label_ru if lang == PromptLanguage.RU else self.label_en


@dataclass(frozen=True, slots=True)
class WizardPlan:
    """A complete inventory for one setup/profile/channel wizard flow."""

    id: str
    title_en: str
    title_ru: str
    questions: tuple[WizardQuestion, ...]
    branches: tuple[WizardBranch, ...] = ()
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class WizardPreview:
    """Human-readable pre-save preview of effective wizard consequences."""

    lines: tuple[str, ...]
