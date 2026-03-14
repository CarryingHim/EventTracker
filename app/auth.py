from datetime import datetime, timedelta
from fastapi import Cookie, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from config import settings
from database import get_db
from models import User

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)


def hash_pw(p: str) -> str:
    return pwd.hash(p)

def check_pw(p: str, h: str) -> bool:
    return pwd.verify(p, h)


def make_token(user: User) -> str:
    exp = datetime.utcnow() + timedelta(days=settings.jwt_expire_days)
    return jwt.encode(
        {"sub": user.id, "username": user.username, "role": user.role, "exp": exp},
        settings.jwt_secret, algorithm="HS256"
    )


async def current_user(
    db: AsyncSession = Depends(get_db),
    cookie: str | None = Cookie(default=None, alias="token"),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    token = cookie or (creds.credentials if creds else None)
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(401, "Session expired")
    r = await db.execute(select(User).where(User.id == payload["sub"]))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    return user


async def organizer_user(user: User = Depends(current_user)) -> User:
    if not user.is_organizer:
        raise HTTPException(403, "Organizer or Admin only")
    return user
