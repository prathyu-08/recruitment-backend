from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from .db import get_db
from .auth_api import oauth2_scheme, decode_cognito_token
from .models import (
    User,
    UserRole,
    Recruiter,
    Job,
    Application,
    JobApplicationQuestion,
    JobApplicationAnswer,
)
from .schemas import (
    JobApplicationQuestionCreate,
    JobApplicationQuestionRead,
    JobApplicationAnswerCreate,
)

router = APIRouter(prefix="/jobs", tags=["Job Application Forms"])
@router.post(
    "/{job_id}/application-form",
    response_model=list[JobApplicationQuestionRead],
)
def create_application_form(
    job_id: UUID,
    questions: list[JobApplicationQuestionCreate],
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    user = db.query(User).filter(
        User.cognito_sub == payload["sub"],
        User.role == UserRole.recruiter,
    ).first()

    if not user:
        raise HTTPException(403, "Recruiter only")

    recruiter = db.query(Recruiter).filter(
        Recruiter.user_id == user.id
    ).first()

    job = db.query(Job).filter(
        Job.id == job_id,
        Job.recruiter_id == recruiter.id,
    ).first()

    if not job:
        raise HTTPException(404, "Job not found or not owned by recruiter")

    # 🔴 Replace existing form
    db.query(JobApplicationQuestion).filter(
        JobApplicationQuestion.job_id == job_id
    ).delete()

    records = []
    for q in questions:
        record = JobApplicationQuestion(
            job_id=job_id,
            question_text=q.question_text,
            field_type=q.field_type,
            options=q.options,
            is_required=q.is_required,
            order_index=q.order_index,
        )
        db.add(record)
        records.append(record)

    db.commit()
    return records
@router.get(
    "/{job_id}/application-form",
    response_model=list[JobApplicationQuestionRead],
)
def get_application_form(
    job_id: UUID,
    db: Session = Depends(get_db),
):
    return (
        db.query(JobApplicationQuestion)
        .filter(JobApplicationQuestion.job_id == job_id)
        .order_by(JobApplicationQuestion.order_index.asc())
        .all()
    )
@router.post("/applications/{application_id}/answers")
def submit_application_answers(
    application_id: UUID,
    answers: list[JobApplicationAnswerCreate],
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    decode_cognito_token(token)

    application = db.query(Application).filter(
        Application.id == application_id
    ).first()

    if not application:
        raise HTTPException(404, "Application not found")

    for ans in answers:
        db.add(
            JobApplicationAnswer(
                application_id=application_id,
                question_id=ans.question_id,
                answer=ans.answer,
            )
        )

    db.commit()
    return {"message": "Application answers saved"}
