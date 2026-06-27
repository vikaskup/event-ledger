from pydantic import BaseModel, Field
from typing import Literal


class TransactionRequest(BaseModel):
    eventId: str = Field(min_length=1)
    type: Literal["CREDIT", "DEBIT"]
    amount: float = Field(gt=0)
    eventTimestamp: str


class TransactionResponse(BaseModel):
    eventId: str
    accountId: str
    type: str
    amount: float
    eventTimestamp: str
    balanceAfter: float


class BalanceResponse(BaseModel):
    accountId: str
    balance: float


class AccountDetailResponse(BaseModel):
    accountId: str
    balance: float
    transactions: list[TransactionResponse]
