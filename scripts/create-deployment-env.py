#!/usr/bin/env python3
"""Creates operator-local secrets, never prints them and never overwrites a file."""

import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
path = root / "deployment/.env.production"
content = (
    "\n".join(
        [
            "SITE_DOMAIN=replace-with-your-domain.example",
            "TLS_EMAIL=replace-with-your-email@example.com",
            "APP_VERSION=0.1.0",
            "POSTGRES_PASSWORD=" + secrets.token_hex(32),
            "APP_DB_PASSWORD=" + secrets.token_hex(32),
            "REDIS_PASSWORD=" + secrets.token_hex(32),
            "S3_BUCKET=",
            "S3_ENDPOINT=",
            "AWS_ACCESS_KEY_ID=",
            "AWS_SECRET_ACCESS_KEY=",
            "AWS_DEFAULT_REGION=us-east-1",
            "LLM_BASE_URL=",
            "LLM_API_KEY=",
            "LLM_MODEL=",
            "LLM_INPUT_USD_PER_MILLION=0",
            "LLM_OUTPUT_USD_PER_MILLION=0",
            "LLM_BUDGET_MICROUSD=1000000",
            "LLM_TENANT_DAILY_BUDGET_MICROUSD=10000000",
            "OTEL_EXPORTER_OTLP_ENDPOINT=",
        ]
    )
    + "\n"
)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as file:
    file.write(content)
print(
    "Created deployment/.env.production with mode 0600. Fill domain, private storage and model settings locally."
)
