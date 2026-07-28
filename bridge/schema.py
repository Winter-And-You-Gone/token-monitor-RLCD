from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Bucket(BaseModel):
    tokens_used: int
    cost_usd: float
    percent_used: Optional[float] = None
    tokens_limit: Optional[int] = None


class ModelBreakdown(BaseModel):
    model: str
    tokens: int
    cost_usd: float


class ClaudeUsage(BaseModel):
    today: Bucket
    month: Bucket
    lifetime: Bucket
    by_model: list[ModelBreakdown] = Field(default_factory=list)


class OtherAgentUsage(BaseModel):
    agent: str
    today: Bucket
    month: Bucket
    lifetime: Bucket
    by_model: list[ModelBreakdown] = Field(default_factory=list)


class Weather(BaseModel):
    temp_c: Optional[float] = None
    code: Optional[int] = None
    condition: str = ""
    icon: str = ""
    city: str = ""
    city_ascii: str = ""


class DeepSeek(BaseModel):
    balance: Optional[float] = None
    currency: str = "CNY"
    granted: Optional[float] = None
    topped: Optional[float] = None
    today_tokens: int = 0
    available: bool = False


class PetState(BaseModel):
    state: str = "idle"
    agent: str = ""
    event: str = ""
    sessions: int = 0
    subagents: int = 0
    asset: str = "clawd-idle-follow.svg"
    updated_at: Optional[datetime] = None


class RadarPoint(BaseModel):
    model: str           # "sol", "terra", "luna"
    effort: str          # "ultra", "max", "xhigh"
    iq: Optional[float] = None
    price: Optional[float] = None
    minutes: Optional[float] = None
    passed: int = 0
    tasks: int = 112


class RadarTrend(BaseModel):
    model: str
    effort: str
    iqs: list[float] = Field(default_factory=list)


class CodexRadar(BaseModel):
    updated_at: Optional[str] = None
    available: bool = False
    points: list[RadarPoint] = Field(default_factory=list)
    trends: list[RadarTrend] = Field(default_factory=list)


class UsageReport(BaseModel):
    updated_at: datetime
    source: str = "ccusage"
    claude: ClaudeUsage
    other: list[OtherAgentUsage] = Field(default_factory=list)
    weather: Optional[Weather] = None
    deepseek: Optional[DeepSeek] = None
    codexradar: Optional[CodexRadar] = None
    pet: PetState = Field(default_factory=PetState)