"""Typed module values shared by public and runtime Session admission."""
from __future__ import annotations

from functools import cached_property
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, WithJsonSchema, model_validator

from breadboard.modules import AuthorityDeclaration, ModuleInput


def _authority(value: object) -> AuthorityDeclaration:
    if isinstance(value, AuthorityDeclaration):
        return value
    return AuthorityDeclaration.from_dict(value)


ModuleAuthorityRequest = Annotated[
    AuthorityDeclaration,
    BeforeValidator(_authority),
    PlainSerializer(AuthorityDeclaration.to_dict, when_used="json"),
]
class ModuleInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: str = Field(min_length=1)
    body: str
    final: bool = Field(strict=True)

    @cached_property
    def decoded(self) -> ModuleInput:
        return ModuleInput.from_dict(self.model_dump())

    @model_validator(mode="after")
    def validate_document(self) -> ModuleInputRequest:
        self.decoded
        return self


def _module_input(value: object) -> ModuleInput:
    if isinstance(value, ModuleInput):
        return value
    return ModuleInput.from_dict(value)


ModuleInputValue = Annotated[
    ModuleInput,
    BeforeValidator(_module_input),
    PlainSerializer(ModuleInput.to_dict, when_used="json"),
    WithJsonSchema(ModuleInputRequest.model_json_schema()),
]
