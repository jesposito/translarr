import hmac

from fastapi import Header, HTTPException

from server.config import settings


async def require_secret(x_translarr_secret: str | None = Header(default=None)) -> None:
    if settings.webhook_secret and not hmac.compare_digest(
        (x_translarr_secret or "").encode(), settings.webhook_secret.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid webhook secret")
