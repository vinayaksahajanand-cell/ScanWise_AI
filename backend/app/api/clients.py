"""Client endpoints (the New Client form): create, list/search, read, update, delete."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuditEvent, Client, iso_utc

router = APIRouter(prefix="/api/clients", tags=["clients"])

TEXT_FIELDS = ("company", "email", "phone", "job_title", "website", "street", "city", "state", "zip_code", "country", "tax_id")
MAX_LEN = {"name": 255, "company": 255, "email": 255, "phone": 64, "job_title": 255, "website": 255, "street": 255,
           "city": 120, "state": 120, "zip_code": 32, "country": 120, "tax_id": 64}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = re.sub(r"\s+", " ", str(v)).strip()
    return v or None


class ClientIn(BaseModel):
    name: str
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    job_title: str | None = None
    website: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country: str | None = None
    tax_id: str | None = None
    source: str = "manual"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean(v) or ""
        if not v:
            raise ValueError("Name is required")
        return v

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if v not in ("manual", "contacts", "business_card"):
            raise ValueError("source must be manual, contacts or business_card")
        return v

    def checked(self) -> dict:
        data = {"name": self.name, "source": self.source, **{k: _clean(getattr(self, k)) for k in TEXT_FIELDS}}
        return _validate(data)


class ClientPatch(BaseModel):
    name: str | None = None
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    job_title: str | None = None
    website: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country: str | None = None
    tax_id: str | None = None


def _validate(data: dict) -> dict:
    for k, limit in MAX_LEN.items():
        if data.get(k) and len(data[k]) > limit:
            raise HTTPException(422, f"{k.replace('_', ' ').capitalize()} is too long (max {limit} characters)")
    if data.get("email") and not EMAIL.match(data["email"]):
        raise HTTPException(422, "Email address is not valid")
    if data.get("phone"):
        d = re.sub(r"\D", "", data["phone"])
        if not 7 <= len(d) <= 15 or re.search(r"[A-Za-z]", data["phone"]):
            raise HTTPException(422, "Phone number must have 7 to 15 digits")
    return data


def client_dict(c: Client) -> dict:
    return {"id": c.id, "name": c.name, **{k: getattr(c, k) for k in TEXT_FIELDS}, "source": c.source,
            "created_at": iso_utc(c.created_at), "updated_at": iso_utc(c.updated_at)}


def _get(db: Session, client_id: str) -> Client:
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404, "Client not found")
    return c


@router.get("")
def list_clients(q: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    query = db.query(Client)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.filter(or_(Client.name.ilike(like), Client.company.ilike(like), Client.email.ilike(like),
                                 Client.phone.ilike(like), Client.city.ilike(like)))
    total = query.count()
    rows = query.order_by(Client.created_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [client_dict(c) for c in rows]}


@router.post("", status_code=201)
def create_client(body: ClientIn, db: Session = Depends(get_db)):
    c = Client(**body.checked())
    db.add(c)
    db.flush()
    db.add(AuditEvent(entity_type="client", entity_id=c.id, action="created", detail=f"source: {c.source}"))
    db.commit()
    return client_dict(c)


@router.get("/{client_id}")
def get_client(client_id: str, db: Session = Depends(get_db)):
    return client_dict(_get(db, client_id))


@router.patch("/{client_id}")
def update_client(client_id: str, body: ClientPatch, db: Session = Depends(get_db)):
    c = _get(db, client_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        name = _clean(changes["name"])
        if not name:
            raise HTTPException(422, "Name is required")
        changes["name"] = name
    for k in TEXT_FIELDS:
        if k in changes:
            changes[k] = _clean(changes[k])
    _validate({**client_dict(c), **changes})
    for k, v in changes.items():
        setattr(c, k, v)
    db.add(AuditEvent(entity_type="client", entity_id=c.id, action="updated", detail=", ".join(sorted(changes))))
    db.commit()
    return client_dict(c)


@router.delete("/{client_id}", status_code=204)
def delete_client(client_id: str, db: Session = Depends(get_db)):
    c = _get(db, client_id)
    db.delete(c)
    db.add(AuditEvent(entity_type="client", entity_id=client_id, action="deleted"))
    db.commit()
    return Response(status_code=204)
