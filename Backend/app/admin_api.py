from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from .db import get_db
from .models import User, UserRole, Job, Application, Interview, Resume
from .auth_api import oauth2_scheme, decode_cognito_token

router = APIRouter(prefix="/admin", tags=["Admin"])


# ==================================================
# RECRUITER = ADMIN GUARD
# ==================================================
def get_admin_user(db: Session, token: str):
    payload = decode_cognito_token(token)
    user = db.query(User).filter(User.cognito_sub == payload["sub"]).first()

    if not user or user.role != UserRole.recruiter:
        raise HTTPException(status_code=403, detail="Recruiter access only")

    return user


# ==================================================
# TOTAL APPLICATIONS PER JOB
# ==================================================
@router.get("/applications-per-job")
def applications_per_job(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    get_admin_user(db, token)

    result = (
        db.query(
            Job.title,
            func.count(Application.id).label("applications"),
        )
        .outerjoin(Application, Job.id == Application.job_id)
        .group_by(Job.id)
        .all()
    )

    return [
        {"job_title": r.title, "applications": r.applications}
        for r in result
    ]


# ==================================================
# CANDIDATES BY STATUS
# ==================================================
@router.get("/application-status-summary")
def application_status_summary(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    get_admin_user(db, token)

    result = (
        db.query(Application.status, func.count(Application.id))
        .group_by(Application.status)
        .all()
    )

    return {status: count for status, count in result}


# ==================================================
# UPCOMING INTERVIEWS
# ==================================================
@router.get("/upcoming-interviews")
def upcoming_interviews(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    get_admin_user(db, token)

    interviews = (
        db.query(Interview)
        .filter(Interview.scheduled_at >= datetime.utcnow())
        .order_by(Interview.scheduled_at)
        .all()
    )

    return [
        {
            "candidate": i.application.candidate.user.full_name,
            "job_title": i.application.job.title,
            "scheduled_at": i.scheduled_at,
            "meeting_link": i.meeting_link,
        }
        for i in interviews
    ]


# ==================================================
# RECENT RESUMES
# ==================================================
@router.get("/recent-resumes")
def recent_resumes(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    get_admin_user(db, token)

    resumes = (
        db.query(Resume)
        .order_by(Resume.uploaded_at.desc())
        .limit(10)
        .all()
    )

    return [
        {
            "candidate": r.candidate.user.full_name,
            "filename": r.original_filename,
            "uploaded_at": r.uploaded_at,
        }
        for r in resumes
    ]
# ==================================================
# JOB PERFORMANCE (✅ MISSING FIXED)
# ==================================================
@router.get("/job-performance")
def job_performance(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    get_admin_user(db, token)

    jobs = db.query(Job).all()

    data = []
    for job in jobs:
        applications_count = (
            db.query(func.count(Application.id))
            .filter(Application.job_id == job.id)
            .scalar()
        )

        data.append({
            "job_id": job.id,
            "job_title": job.title,
            "applications": applications_count,
        })

    return data