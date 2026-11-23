import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, SecretStr, field_validator

_USERNAME_RE = re.compile(r"^[a-z0-9_.-]+$")


def _normalise(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


Username = Annotated[str, BeforeValidator(_normalise)]


class SetupRequest(BaseModel):
    username: Username = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        if not _USERNAME_RE.match(value):
            raise ValueError("Username may only contain letters, digits, '.', '_' and '-'.")
        return value


class LoginRequest(BaseModel):
    username: Username = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: str
    username: str


class AuthStatus(BaseModel):
    setup_required: bool


class ChangePasswordRequest(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=256)
    new_password: SecretStr = Field(min_length=12, max_length=128)
