import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id                  = Column(String, primary_key=True, default=uid)
    username            = Column(String(30), unique=True, nullable=False)
    password_hash       = Column(String, nullable=False)
    _is_admin_legacy     = Column("is_admin", Boolean, default=False, nullable=False)  # legacy DB column
    role                = Column(String(10), default="user", nullable=False)  # user | organizer | admin
    email               = Column(String(120), default="", nullable=False)
    security_question   = Column(String(200), default="", nullable=False)
    security_answer_hash= Column(String, default="", nullable=False)
    created_at          = Column(DateTime, default=datetime.utcnow, nullable=False)

    events      = relationship("Event", back_populates="host", cascade="all, delete-orphan")
    slots       = relationship("Participant", back_populates="user", cascade="all, delete-orphan")
    feedback    = relationship("Feedback", back_populates="user")
    favorites   = relationship("Favorite", back_populates="user", cascade="all, delete-orphan")
    templates   = relationship("EventTemplate", back_populates="creator", cascade="all, delete-orphan")

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_organizer(self):
        return self.role in ("organizer", "admin")


class EventTemplate(Base):
    __tablename__ = "event_templates"
    id          = Column(String, primary_key=True, default=uid)
    name        = Column(String(60), nullable=False)
    description = Column(Text, default="")
    icon        = Column(String(10), default="🎲")   # emoji
    color       = Column(String(20), default="#6366f1")  # accent color
    is_public   = Column(Boolean, default=True, nullable=False)
    custom_fields = Column(Text, default="[]")  # JSON array of field definitions
    creator_id  = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    creator     = relationship("User", back_populates="templates")
    events      = relationship("Event", back_populates="template")


class Event(Base):
    __tablename__ = "events"
    id               = Column(String, primary_key=True, default=uid)
    title            = Column(String(60), nullable=False)
    location         = Column(String(120), nullable=False)
    description      = Column(Text, default="")
    date             = Column(String(10), nullable=False)
    time             = Column(String(5), nullable=False)
    min_players      = Column(Integer, default=1, nullable=False)
    max_players      = Column(Integer, default=10, nullable=False)
    host_id          = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    template_id      = Column(String, ForeignKey("event_templates.id", ondelete="SET NULL"), nullable=True)
    custom_values    = Column(Text, default="{}")  # JSON: field_key -> value
    beginner_enabled = Column(Boolean, default=False, nullable=False)
    beginner_time    = Column(String(5), default="")
    beginner_max     = Column(Integer, default=0)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)

    host         = relationship("User", back_populates="events")
    template     = relationship("EventTemplate", back_populates="events")
    participants = relationship("Participant", back_populates="event", cascade="all, delete-orphan")


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("event_id", "user_id", "is_beginner"),)
    id          = Column(String, primary_key=True, default=uid)
    event_id    = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    user_id     = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    is_beginner = Column(Boolean, default=False, nullable=False)

    event = relationship("Event", back_populates="participants")
    user  = relationship("User", back_populates="slots")


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "template_id"),)
    id          = Column(String, primary_key=True, default=uid)
    user_id     = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(String, ForeignKey("event_templates.id", ondelete="CASCADE"), nullable=False)

    user     = relationship("User", back_populates="favorites")
    template = relationship("EventTemplate")


class Feedback(Base):
    __tablename__ = "feedback"
    id          = Column(String, primary_key=True, default=uid)
    user_id     = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username    = Column(String(30), nullable=False)
    type        = Column(String(10), nullable=False)
    title       = Column(String(80), nullable=False)
    description = Column(Text, nullable=False)
    status      = Column(String(10), default="new", nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="feedback")
