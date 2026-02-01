from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from .schemas import InterviewerCreate       

from .db import get_db
from .models import Interviewer
from .auth_api import oauth2_scheme, decode_cognito_token

router = APIRouter(prefix="/interviewers", tags=["Interviewers"])

@router.get("/")
def list_interviewers(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    # Auth is optional, but keep for safety
    decode_cognito_token(token)

    interviewers = db.query(Interviewer).all()

    return [
        {
            "id": str(i.id),
            "name": i.name,
            "email": i.email,
        }
        for i in interviewers
    ]

@router.get("/")
def get_interviewers(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    # (Optional) restrict to recruiter/admin
    return [
        {
            "id": str(i.id),
            "name": i.name,
            "email": i.email,
        }
        for i in db.query(Interviewer).all()
    ]



@router.post("/", status_code=201)
def create_interviewer(
    payload: InterviewerCreate,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload_user = decode_cognito_token(token)

    # (optional) restrict to recruiter/admin
    existing = db.query(Interviewer).filter(
        Interviewer.email == payload.email
    ).first()

    if existing:
        raise HTTPException(400, "Interviewer with this email already exists")

    interviewer = Interviewer(
        name=payload.name,
        email=payload.email
    )

    db.add(interviewer)
    db.commit()
    db.refresh(interviewer)

    return {
        "id": str(interviewer.id),
        "name": interviewer.name,
        "email": interviewer.email
    }