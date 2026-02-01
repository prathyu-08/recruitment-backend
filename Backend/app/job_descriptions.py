from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from uuid import UUID, uuid4

from botocore.exceptions import ClientError
import os
import boto3
#
from .db import get_db
from .auth_api import oauth2_scheme, decode_cognito_token
from .models import (
    User,
    UserRole,
    JobDescription,
    JobDescriptionSkill,
)
from .schemas import JobDescriptionCreate, JobDescriptionRead

# ==========
# ROUTER
# ===========
router = APIRouter(prefix="/job-descriptions", tags=["Job Descriptions"])

# ===============
# AWS / S3 CONFIG
# ================
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

# ======================================
# 1️⃣ CREATE JOB DESCRIPTION (TEXT EDITOR)
# =========================================
@router.post("", response_model=JobDescriptionRead)
def create_job_description(
    data: JobDescriptionCreate,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    user = db.query(User).filter(User.cognito_sub == payload["sub"]).first()
    if not user or user.role != UserRole.recruiter:
        raise HTTPException(
            status_code=403,
            detail="Only recruiters can create job descriptions",
        )

    jd = JobDescription(
        title=data.title,
        description_text=data.description_text,
        experience_level=data.experience_level,
        job_type=data.job_type,
        location=data.location,
    )

    db.add(jd)
    db.commit()
    db.refresh(jd)

    # Save required skills
    for skill_id in data.skill_ids:
        db.add(
            JobDescriptionSkill(
                job_description_id=jd.id,
                skill_id=skill_id,
            )
        )

    db.commit()
    return jd

@router.post("/upload")
def upload_job_description_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    # ✅ Correct recruiter validation (DB-based)
    user = db.query(User).filter(
        User.cognito_sub == payload["sub"]
    ).first()

    if not user or user.role != UserRole.recruiter:
        raise HTTPException(
            status_code=403,
            detail="Only recruiters can upload job descriptions",
        )

    # ✅ Allow only PDF / DOC / DOCX
    if not file.filename.lower().endswith((".pdf", ".doc", ".docx")):
        raise HTTPException(
            status_code=400,
            detail="Only PDF, DOC, DOCX files are allowed",
        )

    file_key = f"job_descriptions/{uuid4()}_{file.filename}"

    try:
        s3.upload_fileobj(
            file.file,
            S3_BUCKET_NAME,
            file_key,
            ExtraArgs={
                "ContentType": file.content_type or "application/octet-stream"
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"S3 upload failed: {str(e)}",
        )

    return {
        "file_key": file_key,
        "filename": file.filename,
    }



# =====================================================
# 3️⃣ LIST JOB DESCRIPTIONS (ACTIVE ONLY)
# =====================================================
@router.get("", response_model=list[JobDescriptionRead])
def list_job_descriptions(
    db: Session = Depends(get_db),
):
    return (
        db.query(JobDescription)
        .filter(JobDescription.is_active == True)
        .order_by(JobDescription.created_at.desc())
        .all()
    )


# ==========================
# 4️⃣ UPDATE JOB DESCRIPTION
# ==========================
@router.put("/{jd_id}", response_model=JobDescriptionRead)
def update_job_description(
    jd_id: UUID,
    data: JobDescriptionCreate,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    user = db.query(User).filter(User.cognito_sub == payload["sub"]).first()
    if not user or user.role != UserRole.recruiter:
        raise HTTPException(
            status_code=403,
            detail="Only recruiters can update job descriptions",
        )

    jd = db.query(JobDescription).filter(JobDescription.id == jd_id).first()
    if not jd:
        raise HTTPException(
            status_code=404,
            detail="Job description not found",
        )

    jd.title = data.title
    jd.description_text = data.description_text
    jd.experience_level = data.experience_level
    jd.job_type = data.job_type
    jd.location = data.location

    db.commit()
    db.refresh(jd)
    return jd




# =====================================================
# 5️⃣ GET JOB DESCRIPTION FILE (VIEW / DOWNLOAD)
# =====================================================
@router.get("/file/{file_key:path}")
def get_job_description_file(
    file_key: str,
    token: str = Depends(oauth2_scheme),
):
    # Any logged-in user (candidate / recruiter) can view JD
    decode_cognito_token(token)

    try:
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": S3_BUCKET_NAME,
                "Key": file_key,
            },
            ExpiresIn=3600,  # 1 hour
        )
    except ClientError:
        raise HTTPException(
            status_code=404,
            detail="Job description file not found",
        )

    return {"url": presigned_url}
