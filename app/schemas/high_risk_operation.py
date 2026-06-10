from pydantic import BaseModel, Field


class HighRiskOperationRequest(BaseModel):
    reason: str = Field(..., min_length=8, max_length=500)
    ticket_id: str | None = Field(default=None, max_length=128)
