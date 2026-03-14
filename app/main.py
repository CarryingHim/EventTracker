import json, os, uuid
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from auth import admin_user, check_pw, current_user, hash_pw, make_token, organizer_user
from config import settings
from database import SessionLocal, get_db, init_db
from models import Event, EventTemplate, Favorite, Feedback, Participant, User

# ── Schemas ───────────────────────────────────────────────────────

class RegIn(BaseModel):
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=4)
    security_question: str = Field(min_length=5, max_length=200)
    security_answer: str = Field(min_length=1, max_length=100)

class LoginIn(BaseModel):
    username: str
    password: str

class ChangeUser(BaseModel):
    username: str = Field(min_length=2, max_length=30)

class ChangePw(BaseModel):
    oldPassword: str
    newPassword: str = Field(min_length=4)

class ResetPwIn(BaseModel):
    username: str
    security_answer: str
    new_password: str = Field(min_length=4)

class BeginnerIn(BaseModel):
    enabled: bool = False
    time: str = ""
    max: int = 0

class CustomFieldDef(BaseModel):
    key: str
    label: str
    type: Literal["text", "number", "select"] = "text"
    options: list[str] = []   # for select type
    required: bool = False

class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    description: str = ""
    icon: str = "🎲"
    color: str = "#6366f1"
    theme: str = "default"  # e.g. default, gothic, modern, sci-fi
    is_public: bool = True
    custom_fields: list[CustomFieldDef] = []

class EventIn(BaseModel):
    title: str = Field(max_length=60)
    location: str = Field(max_length=120)
    description: str = ""
    date: str
    time: str
    minPlayers: int = 1
    maxPlayers: int = 10
    template_id: Optional[str] = None
    custom_values: dict = {}
    beginner: BeginnerIn = BeginnerIn()

class FeedbackIn(BaseModel):
    type: Literal["bug", "feature"]
    title: str = Field(max_length=80)
    description: str

class FbStatusIn(BaseModel):
    status: Literal["new", "noted", "done", "wontfix"]

class AdminResetPw(BaseModel):
    new_password: str = Field(min_length=4)

class UpdateEmailIn(BaseModel):
    email: str = Field(default="", max_length=120)

class SetRoleIn(BaseModel):
    role: Literal["user", "organizer", "admin"]


# ── Helpers ───────────────────────────────────────────────────────

COOKIE = dict(key="token", httponly=True, samesite="lax",
              secure=settings.cookie_secure, max_age=30*24*3600, path="/")


def user_out(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "role": u.role,
        "isAdmin": u.is_admin, "isOrganizer": u.is_organizer,
        "email": u.email or "",
        "security_question": u.security_question or "",
        "created_at": u.created_at.isoformat()
    }


def template_out(t: EventTemplate, fav_ids: set = None) -> dict:
    fav_ids = fav_ids or set()
    return {
        "id": t.id, "name": t.name, "description": t.description,
        "icon": t.icon, "color": t.color, "theme": t.theme or "default",
        "is_public": t.is_public,
        "custom_fields": json.loads(t.custom_fields or "[]"),
        "creator": t.creator.username if t.creator else "",
        "creator_id": t.creator_id,
        "created_at": t.created_at.isoformat(),
        "is_favorite": t.id in fav_ids,
    }


def ev_out(ev: Event, uid: str) -> dict:
    normal = [p for p in ev.participants if not p.is_beginner]
    beg    = [p for p in ev.participants if p.is_beginner]
    tmpl = None
    if ev.template:
        tmpl = {"id": ev.template.id, "name": ev.template.name,
                "icon": ev.template.icon, "color": ev.template.color,
                "theme": ev.template.theme or "default"}
    return {
        "id": ev.id, "title": ev.title, "location": ev.location,
        "description": ev.description or "", "date": ev.date, "time": ev.time,
        "minPlayers": ev.min_players, "maxPlayers": ev.max_players,
        "host": ev.host.username, "hostId": ev.host_id,
        "createdAt": ev.created_at.isoformat(),
        "template": tmpl,
        "custom_values": json.loads(ev.custom_values or "{}"),
        "participants": [p.user.username for p in normal],
        "isJoined": any(p.user_id == uid for p in normal),
        "beginner": {
            "enabled": ev.beginner_enabled,
            "time": ev.beginner_time or "",
            "max": ev.beginner_max or 0,
            "participants": [p.user.username for p in beg],
            "isJoined": any(p.user_id == uid for p in beg),
        },
    }


async def get_ev(eid: str, db: AsyncSession) -> Event:
    r = await db.execute(
        select(Event).where(Event.id == eid)
        .options(
            selectinload(Event.host),
            selectinload(Event.template),
            selectinload(Event.participants).selectinload(Participant.user)
        )
    )
    ev = r.scalar_one_or_none()
    if not ev:
        raise HTTPException(404, "Event not found")
    return ev


async def get_fav_ids(user_id: str, db: AsyncSession) -> set:
    r = await db.execute(select(Favorite).where(Favorite.user_id == user_id))
    return {f.template_id for f in r.scalars().all()}


# ── Lifespan: DB init + admin seed + default templates ──────────

BUILTIN_TEMPLATES = [
    {
        "name": "Blood on the Clocktower",
        "description": "Social deduction game for 5-20 players",
        "icon": "🕰️", "color": "#7c3aed", "theme": "gothic",
        "custom_fields": [
            {"key": "script", "label": "Script", "type": "text", "required": False},
        ],
        "beginner_support": True,
    },
    {
        "name": "Pen & Paper RPG",
        "description": "Tabletop role-playing session",
        "icon": "🐉", "color": "#b45309", "theme": "parchment",
        "custom_fields": [
            {"key": "system", "label": "System (e.g. D&D, Pathfinder)", "type": "text", "required": False},
            {"key": "campaign", "label": "Campaign / Adventure", "type": "text", "required": False},
        ],
    },
    {
        "name": "Board Game Night",
        "description": "Casual board game evening",
        "icon": "♟️", "color": "#0891b2", "theme": "modern",
        "custom_fields": [
            {"key": "game", "label": "Game Title", "type": "text", "required": False},
        ],
    },
    {
        "name": "Quiz Night",
        "description": "Trivia and quiz competition",
        "icon": "🧠", "color": "#059669", "theme": "default",
        "custom_fields": [
            {"key": "topic", "label": "Topic / Theme", "type": "text", "required": False},
            {"key": "teams", "label": "Max Teams", "type": "number", "required": False},
        ],
    },
    {
        "name": "LAN Party",
        "description": "Multiplayer gaming event",
        "icon": "🎮", "color": "#dc2626", "theme": "sci-fi",
        "custom_fields": [
            {"key": "game", "label": "Main Game", "type": "text", "required": False},
            {"key": "bring_pc", "label": "Bring your own PC?", "type": "select",
             "options": ["Yes", "No", "Optional"], "required": False},
        ],
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        # Seed admin
        r = await db.execute(
            select(User).where(func.lower(User.username) == settings.admin_username.lower())
        )
        admin = r.scalar_one_or_none()
        if not admin:
            admin = User(
                id=str(uuid.uuid4()),
                username=settings.admin_username,
                password_hash=hash_pw(settings.admin_password),
                role="admin",
                security_question="What is the admin master key?",
                security_answer_hash=hash_pw(settings.admin_security_answer),
            )
            db.add(admin)
            await db.commit()
            await db.refresh(admin)
            print(f"[app] Admin seeded: {settings.admin_username}")

        # Seed built-in templates
        for t in BUILTIN_TEMPLATES:
            r = await db.execute(
                select(EventTemplate).where(
                    func.lower(EventTemplate.name) == t["name"].lower()
                )
            )
            if not r.scalar_one_or_none():
                fields = t.get("custom_fields", [])
                db.add(EventTemplate(
                    id=str(uuid.uuid4()),
                    name=t["name"],
                    description=t.get("description", ""),
                    icon=t.get("icon", "🎲"),
                    color=t.get("color", "#6366f1"),
                    theme=t.get("theme", "default"),
                    is_public=True,
                    custom_fields=json.dumps(fields),
                    creator_id=admin.id,
                ))
        await db.commit()
    yield


# ── App ───────────────────────────────────────────────────────────

app = FastAPI(title="EventHub", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# ── Auth routes ───────────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(body: RegIn, response: Response, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(User).where(func.lower(User.username) == body.username.lower()))
    if r.scalar_one_or_none():
        raise HTTPException(409, "Username already taken")
    user = User(
        id=str(uuid.uuid4()),
        username=body.username,
        password_hash=hash_pw(body.password),
        role="user",
        security_question=body.security_question,
        security_answer_hash=hash_pw(body.security_answer.lower().strip()),
    )
    db.add(user); await db.commit(); await db.refresh(user)
    response.set_cookie(**COOKIE, value=make_token(user))
    return user_out(user)


@app.post("/api/auth/login")
async def login(body: LoginIn, response: Response, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(User).where(func.lower(User.username) == body.username.lower()))
    user = r.scalar_one_or_none()
    if not user or not check_pw(body.password, user.password_hash):
        raise HTTPException(401, "Wrong username or password")
    response.set_cookie(**COOKIE, value=make_token(user))
    return user_out(user)


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("token", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: User = Depends(current_user)):
    return user_out(user)


@app.get("/api/auth/security-question")
async def get_security_question(username: str, db: AsyncSession = Depends(get_db)):
    """Returns the security question for a given username (for PW reset flow)."""
    r = await db.execute(select(User).where(func.lower(User.username) == username.lower()))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    return {"question": user.security_question}


@app.post("/api/auth/reset-password")
async def reset_password(body: ResetPwIn, db: AsyncSession = Depends(get_db)):
    """Self-service PW reset via security question."""
    r = await db.execute(select(User).where(func.lower(User.username) == body.username.lower()))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if not check_pw(body.security_answer.lower().strip(), user.security_answer_hash):
        raise HTTPException(400, "Security answer incorrect")
    user.password_hash = hash_pw(body.new_password)
    await db.commit()
    return {"ok": True}


# ── Account routes ────────────────────────────────────────────────

@app.put("/api/account/username")
async def change_username(body: ChangeUser, response: Response,
                          db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    r = await db.execute(select(User).where(
        func.lower(User.username) == body.username.lower(), User.id != user.id))
    if r.scalar_one_or_none():
        raise HTTPException(409, "Username already taken")
    user.username = body.username
    await db.commit(); await db.refresh(user)
    response.set_cookie(**COOKIE, value=make_token(user))
    return user_out(user)


@app.put("/api/account/password")
async def change_password(body: ChangePw, db: AsyncSession = Depends(get_db),
                          user: User = Depends(current_user)):
    if not check_pw(body.oldPassword, user.password_hash):
        raise HTTPException(400, "Current password incorrect")
    user.password_hash = hash_pw(body.newPassword)
    await db.commit()
    return {"ok": True}


@app.put("/api/account/email")
async def update_email(body: UpdateEmailIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    user.email = body.email.strip()
    await db.commit(); await db.refresh(user)
    return user_out(user)


# ── Template routes ───────────────────────────────────────────────

@app.get("/api/templates")
async def list_templates(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    r = await db.execute(
        select(EventTemplate)
        .options(selectinload(EventTemplate.creator))
        .where(EventTemplate.is_public == True)
        .order_by(EventTemplate.created_at.asc())
    )
    fav_ids = await get_fav_ids(user.id, db)
    templates = r.scalars().all()
    # Also include user's own private templates
    if not user.is_admin:
        r2 = await db.execute(
            select(EventTemplate)
            .options(selectinload(EventTemplate.creator))
            .where(EventTemplate.is_public == False, EventTemplate.creator_id == user.id)
        )
        templates = list(templates) + list(r2.scalars().all())
    return [template_out(t, fav_ids) for t in templates]


@app.get("/api/templates/all")
async def list_all_templates(db: AsyncSession = Depends(get_db), _: User = Depends(admin_user)):
    r = await db.execute(
        select(EventTemplate).options(selectinload(EventTemplate.creator))
        .order_by(EventTemplate.created_at.asc())
    )
    return [template_out(t) for t in r.scalars().all()]


@app.post("/api/templates", status_code=201)
async def create_template(body: TemplateIn, db: AsyncSession = Depends(get_db),
                          user: User = Depends(organizer_user)):
    t = EventTemplate(
        id=str(uuid.uuid4()),
        name=body.name,
        description=body.description,
        icon=body.icon,
        color=body.color,
        theme=body.theme,
        is_public=body.is_public,
        custom_fields=json.dumps([f.model_dump() for f in body.custom_fields]),
        creator_id=user.id,
    )
    db.add(t); await db.commit()
    r = await db.execute(
        select(EventTemplate).where(EventTemplate.id == t.id)
        .options(selectinload(EventTemplate.creator))
    )
    return template_out(r.scalar_one())


@app.put("/api/templates/{tid}")
async def update_template(tid: str, body: TemplateIn, db: AsyncSession = Depends(get_db),
                          user: User = Depends(organizer_user)):
    r = await db.execute(
        select(EventTemplate).where(EventTemplate.id == tid)
        .options(selectinload(EventTemplate.creator))
    )
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    if t.creator_id != user.id and not user.is_admin:
        raise HTTPException(403, "Not your template")
    t.name = body.name; t.description = body.description
    t.icon = body.icon; t.color = body.color; t.theme = body.theme
    t.is_public = body.is_public
    t.custom_fields = json.dumps([f.model_dump() for f in body.custom_fields])
    await db.commit(); await db.refresh(t)
    return template_out(t)


@app.delete("/api/templates/{tid}")
async def delete_template(tid: str, db: AsyncSession = Depends(get_db),
                          user: User = Depends(organizer_user)):
    r = await db.execute(select(EventTemplate).where(EventTemplate.id == tid))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    if t.creator_id != user.id and not user.is_admin:
        raise HTTPException(403, "Not your template")
    await db.delete(t); await db.commit()
    return {"ok": True}


# ── Favorites routes ──────────────────────────────────────────────

@app.post("/api/templates/{tid}/favorite")
async def add_favorite(tid: str, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    r = await db.execute(select(EventTemplate).where(EventTemplate.id == tid))
    if not r.scalar_one_or_none():
        raise HTTPException(404, "Template not found")
    r2 = await db.execute(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.template_id == tid))
    if not r2.scalar_one_or_none():
        db.add(Favorite(id=str(uuid.uuid4()), user_id=user.id, template_id=tid))
        await db.commit()
    return {"ok": True}


@app.delete("/api/templates/{tid}/favorite")
async def remove_favorite(tid: str, db: AsyncSession = Depends(get_db),
                          user: User = Depends(current_user)):
    r = await db.execute(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.template_id == tid))
    fav = r.scalar_one_or_none()
    if fav:
        await db.delete(fav); await db.commit()
    return {"ok": True}


# ── Event routes ──────────────────────────────────────────────────

@app.get("/api/events")
async def list_events(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    r = await db.execute(
        select(Event)
        .options(
            selectinload(Event.host),
            selectinload(Event.template),
            selectinload(Event.participants).selectinload(Participant.user)
        )
        .order_by(Event.date.asc(), Event.time.asc())
    )
    return [ev_out(ev, user.id) for ev in r.scalars().all()]


@app.post("/api/events", status_code=201)
async def create_event(body: EventIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    if body.minPlayers > body.maxPlayers:
        raise HTTPException(400, "Min players must be ≤ max players")
    if body.beginner.enabled and not body.beginner.time:
        raise HTTPException(400, "Beginner time required")
    ev = Event(
        id=str(uuid.uuid4()),
        title=body.title, location=body.location,
        description=body.description, date=body.date, time=body.time,
        min_players=body.minPlayers, max_players=body.maxPlayers,
        host_id=user.id,
        template_id=body.template_id or None,
        custom_values=json.dumps(body.custom_values),
        beginner_enabled=body.beginner.enabled,
        beginner_time=body.beginner.time,
        beginner_max=body.beginner.max,
    )
    db.add(ev); await db.commit()
    return ev_out(await get_ev(ev.id, db), user.id)


@app.put("/api/events/{eid}")
async def update_event(eid: str, body: EventIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    ev = await get_ev(eid, db)
    if ev.host_id != user.id and not user.is_admin:
        raise HTTPException(403, "Not your event")
    ev.title = body.title; ev.location = body.location; ev.description = body.description
    ev.date = body.date; ev.time = body.time
    ev.min_players = body.minPlayers; ev.max_players = body.maxPlayers
    ev.template_id = body.template_id or None
    ev.custom_values = json.dumps(body.custom_values)
    ev.beginner_enabled = body.beginner.enabled
    ev.beginner_time = body.beginner.time
    ev.beginner_max = body.beginner.max
    await db.commit()
    return ev_out(await get_ev(eid, db), user.id)


@app.delete("/api/events/{eid}")
async def delete_event(eid: str, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    ev = await get_ev(eid, db)
    if ev.host_id != user.id and not user.is_admin:
        raise HTTPException(403, "Not your event")
    await db.delete(ev); await db.commit()
    return {"ok": True}


async def _join(eid: str, uid: str, is_beg: bool, db: AsyncSession):
    ev = await get_ev(eid, db)
    pool = [p for p in ev.participants if p.is_beginner == is_beg]
    limit = ev.beginner_max if is_beg else ev.max_players
    if is_beg and not ev.beginner_enabled:
        raise HTTPException(400, "No beginner slot")
    if len(pool) >= limit:
        raise HTTPException(400, "Slot is full")
    if any(p.user_id == uid for p in pool):
        raise HTTPException(400, "Already joined")
    db.add(Participant(id=str(uuid.uuid4()), event_id=eid, user_id=uid, is_beginner=is_beg))
    await db.commit()
    return ev_out(await get_ev(eid, db), uid)


@app.post("/api/events/{eid}/join")
async def join(eid: str, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await _join(eid, user.id, False, db)

@app.post("/api/events/{eid}/leave")
async def leave(eid: str, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await db.execute(Participant.__table__.delete().where(
        Participant.event_id == eid, Participant.user_id == user.id,
        Participant.is_beginner == False))
    await db.commit()
    return ev_out(await get_ev(eid, db), user.id)

@app.post("/api/events/{eid}/join-beginner")
async def join_beg(eid: str, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await _join(eid, user.id, True, db)

@app.post("/api/events/{eid}/leave-beginner")
async def leave_beg(eid: str, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await db.execute(Participant.__table__.delete().where(
        Participant.event_id == eid, Participant.user_id == user.id,
        Participant.is_beginner == True))
    await db.commit()
    return ev_out(await get_ev(eid, db), user.id)


# ── Feedback routes ───────────────────────────────────────────────

@app.get("/api/feedback")
async def list_feedback(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    r = await db.execute(select(Feedback).order_by(Feedback.created_at.desc()))
    return [{"id": f.id, "user_id": f.user_id, "username": f.username, "type": f.type,
             "title": f.title, "description": f.description, "status": f.status,
             "created_at": f.created_at.isoformat()} for f in r.scalars().all()]


@app.post("/api/feedback", status_code=201)
async def submit_feedback(body: FeedbackIn, db: AsyncSession = Depends(get_db),
                          user: User = Depends(current_user)):
    fb = Feedback(id=str(uuid.uuid4()), user_id=user.id, username=user.username,
                  type=body.type, title=body.title, description=body.description)
    db.add(fb); await db.commit(); await db.refresh(fb)
    return {"id": fb.id, "username": fb.username, "type": fb.type,
            "title": fb.title, "description": fb.description,
            "status": fb.status, "created_at": fb.created_at.isoformat()}


@app.put("/api/feedback/{fid}/status")
async def update_fb_status(fid: str, body: FbStatusIn, db: AsyncSession = Depends(get_db),
                           _: User = Depends(admin_user)):
    r = await db.execute(select(Feedback).where(Feedback.id == fid))
    fb = r.scalar_one_or_none()
    if not fb:
        raise HTTPException(404, "Not found")
    fb.status = body.status; await db.commit()
    return {"id": fb.id, "status": fb.status}


@app.delete("/api/feedback/{fid}")
async def delete_feedback(fid: str, db: AsyncSession = Depends(get_db),
                          _: User = Depends(admin_user)):
    r = await db.execute(select(Feedback).where(Feedback.id == fid))
    fb = r.scalar_one_or_none()
    if not fb:
        raise HTTPException(404, "Not found")
    await db.delete(fb); await db.commit()
    return {"ok": True}


# ── Admin routes ──────────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_users(db: AsyncSession = Depends(get_db), _: User = Depends(admin_user)):
    r = await db.execute(select(User).order_by(User.created_at.asc()))
    return [user_out(u) for u in r.scalars().all()]


@app.put("/api/admin/users/{uid}/role")
async def set_role(uid: str, body: SetRoleIn, db: AsyncSession = Depends(get_db),
                   admin: User = Depends(admin_user)):
    if uid == admin.id:
        raise HTTPException(400, "Cannot change your own role")
    r = await db.execute(select(User).where(User.id == uid))
    u = r.scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")
    u.role = body.role; await db.commit()
    return user_out(u)


@app.put("/api/admin/users/{uid}/reset-password")
async def admin_reset_password(uid: str, body: AdminResetPw,
                               db: AsyncSession = Depends(get_db),
                               _: User = Depends(admin_user)):
    r = await db.execute(select(User).where(User.id == uid))
    u = r.scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")
    u.password_hash = hash_pw(body.new_password)
    await db.commit()
    return {"ok": True}


@app.delete("/api/admin/users/{uid}")
async def del_user(uid: str, db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    if uid == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    r = await db.execute(select(User).where(User.id == uid))
    u = r.scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")
    await db.delete(u); await db.commit()
    return {"ok": True}


# ── Health ────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ── Serve frontend (must be last) ─────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
