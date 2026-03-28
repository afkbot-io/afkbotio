"""Machine-readable app action parameter schema manifests."""

from __future__ import annotations

from types import NoneType, UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined


class ActionParameterFieldManifest(BaseModel):
    """Serializable field description for one app action parameter."""

    name: str
    type: str
    required: bool
    default: object | None = None

    def serialize(self) -> dict[str, object]:
        """Return deterministic JSON-friendly payload."""

        return self.model_dump(mode="json", exclude_none=True)


class AppActionSchemaManifest(BaseModel):
    """Serializable parameter schema for one canonical app action."""

    action: str
    fields: tuple[ActionParameterFieldManifest, ...]

    def serialize(self) -> dict[str, object]:
        """Return deterministic JSON-friendly payload."""

        return {
            "action": self.action,
            "fields": [field.serialize() for field in self.fields],
        }


def build_action_schema_manifest(*, action: str, model: type[BaseModel]) -> AppActionSchemaManifest:
    """Project one Pydantic params model into a machine-readable action schema."""

    fields: list[ActionParameterFieldManifest] = []
    for field_name, field_info in model.model_fields.items():
        default = None if field_info.default is PydanticUndefined else field_info.default
        fields.append(
            ActionParameterFieldManifest(
                name=field_name,
                type=_describe_type(field_info.annotation),
                required=field_info.is_required(),
                default=default,
            )
        )
    return AppActionSchemaManifest(action=action, fields=tuple(fields))


def _describe_type(annotation: object) -> str:
    origin = get_origin(annotation)
    if origin in {Literal}:
        literal_args = get_args(annotation)
        if literal_args:
            return "literal[" + ", ".join(repr(item) for item in literal_args) + "]"
        return "literal"
    if origin in {list, tuple, set, frozenset}:
        item_args = get_args(annotation)
        item_type = "object" if not item_args else _describe_type(item_args[0])
        return f"array[{item_type}]"
    if origin is dict:
        return "object"
    if origin in {UnionType, Union}:
        args = [arg for arg in get_args(annotation) if arg is not NoneType]
        if len(args) == 1:
            return _describe_type(args[0])
        return "union[" + ", ".join(_describe_type(arg) for arg in args) + "]"
    if annotation in {str}:
        return "string"
    if annotation in {int}:
        return "integer"
    if annotation in {float}:
        return "number"
    if annotation in {bool}:
        return "boolean"
    if annotation in {dict, object, Any}:
        return "object"
    return getattr(annotation, "__name__", str(annotation))
