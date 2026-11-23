from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Name
from app.services.age import valid_public_key


class RecipientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    # An age public key ("age1..."). Private keys are never accepted here.
    public_key: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("public_key")
    @classmethod
    def _public_key(cls, value: str) -> str:
        key = value.strip()
        if key.upper().startswith("AGE-SECRET-KEY"):
            raise ValueError("That is a private key. Paste the public key, which starts with age1.")
        if not valid_public_key(key):
            raise ValueError(
                "Not a valid age public key. It starts with age1 and has 62 characters."
            )
        return key


class RecipientOut(BaseModel):
    id: str
    name: str
    public_key: str
    fingerprint: str
    created_at: datetime
    # Names of the jobs that encrypt to this key.
    used_by: list[str] = Field(default_factory=list)


class RecipientRefOut(BaseModel):
    id: str
    name: str
    fingerprint: str
