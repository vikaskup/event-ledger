from pydantic import BaseModel, Field
from typing import Literal, Optional


class EventRequest(BaseModel):
    eventId: str = Field(min_length=1)
    accountId: str = Field(min_length=1)
    type: Literal["CREDIT", "DEBIT"]
    amount: float = Field(gt=0)
    currency: str = Field(min_length=1)
    eventTimestamp: str
    metadata: Optional[dict] = None


class EventResponse(BaseModel):
    eventId: str
    accountId: str
    type: str
    amount: float
    currency: str
    eventTimestamp: str
    metadata: Optional[dict] = None
