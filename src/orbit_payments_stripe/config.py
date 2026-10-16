# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Validated Stripe credentials and bounded client settings."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class StripeConfig(BaseModel):
    """Stripe adapter settings; credentials are required and masked in representations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr
    webhook_secret: SecretStr
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_network_retries: int = Field(default=2, ge=0, le=5)
    # Stripe's SDK treats a falsey tolerance as "disable timestamp age checks".
    webhook_tolerance_seconds: int = Field(default=300, ge=1, le=3600)

    @field_validator("api_key", "webhook_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        """Reject empty API and webhook credentials after trimming whitespace."""
        if not value.get_secret_value().strip():
            raise ValueError("Stripe credentials must not be empty.")
        return value

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> StripeConfig:
        """Load explicitly named credentials without providing insecure defaults."""
        values = os.environ if environ is None else environ
        try:
            api_key = values["STRIPE_SECRET_KEY"]
            webhook_secret = values["STRIPE_WEBHOOK_SECRET"]
        except KeyError as exc:
            raise ValueError(f"Missing required environment variable: {exc.args[0]}.") from None
        return cls(api_key=SecretStr(api_key), webhook_secret=SecretStr(webhook_secret))


__all__ = ["StripeConfig"]
