"""Structured credential manifests for integration actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CredentialFieldManifest:
    """One secret/config field used by an integration."""

    slug: str
    description: str
    secret: bool = True
    required_by_default: bool = True


@dataclass(frozen=True, slots=True)
class ActionCredentialManifest:
    """Credential requirements for one action."""

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AppCredentialManifest:
    """All credential requirements for one integration app."""

    fields: Mapping[str, CredentialFieldManifest] = field(default_factory=dict)
    actions: Mapping[str, ActionCredentialManifest] = field(default_factory=dict)

    def serialize(self) -> dict[str, object]:
        """Return JSON-serializable manifest payload."""

        fields = {
            slug: {
                "slug": item.slug,
                "description": item.description,
                "secret": item.secret,
                "required_by_default": item.required_by_default,
            }
            for slug, item in self.fields.items()
        }
        actions = {
            action: {
                "required": list(requirements.required),
                "optional": list(requirements.optional),
            }
            for action, requirements in self.actions.items()
        }
        return {
            "fields": fields,
            "actions": actions,
        }
