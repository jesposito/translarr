from fastapi import Header, HTTPException

from server.config import settings


async def require_secret(x_translarr_secret: str | None = Header(default=None)) -> None:
    if settings.webhook_secret and x_translarr_secret != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="invalid webhook secret")
