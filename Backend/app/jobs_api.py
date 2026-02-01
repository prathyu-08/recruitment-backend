from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from uuid import UUID

from .db import get_db
from .models import (
    Job,
    Recruiter,
    Skill,
    JobSkill,
    JobStatus,
    Company,
)
from .schemas import JobCreate, JobRead, JobUpdate
from .auth_api import get_current_recruiter

router = APIRouter(prefix="/jobs", tags=["Jobs"])


# =====================================================
# CREATE JOB (RECRUITER ONLY)
# =====================================================
@router.post("/", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(get_current_recruiter),
):
    # ---------------- VALIDATION ----------------
    if not payload.skills:
        raise HTTPException(
            status_code=400,
            detail="At least one skill is required",
        )

    # ---------------- CREATE JOB ----------------
    job = Job(
    title=payload.title,
    description=payload.description,
    description_file_key=payload.description_file_key,
    location=payload.location,
    min_experience=payload.min_experience,
    max_experience=payload.max_experience,
    salary_min=payload.salary_min,
    salary_max=payload.salary_max,
    employment_type=payload.employment_type,
    recruiter_id=recruiter.id,
    company_id=recruiter.company_id,
    status=JobStatus.active,
    is_active=True,
)


    db.add(job)
    db.flush()  # get job.id before commit

    # ---------------- HANDLE SKILLS ----------------
    for skill_name in set(payload.skills):
        name = skill_name.strip().lower()
        if not name:
            continue

        skill = db.query(Skill).filter(
            func.lower(Skill.name) == name
        ).first()

        if not skill:
            skill = Skill(name=name.title())
            db.add(skill)
            db.flush()

        db.add(JobSkill(job_id=job.id, skill_id=skill.id))

    db.commit()
    db.refresh(job)

    # ---------------- 🔔 NOTIFICATIONS ----------------
    from .notification_utils import create_notification
    from .models import User, UserRole

    candidates = db.query(User).filter(
        User.role == UserRole.user
    ).all()

    for user in candidates:
        create_notification(
            db,
            user.id,
            "New Job Posted",
            f"A new job '{job.title}' has been posted. Check it out!"
        )

    # ---------------- RESPONSE ----------------
    return serialize_job(job)


# =====================================================
# READ MY JOBS (RECRUITER ONLY)
# =====================================================
@router.get("/my", response_model=list[JobRead])
def get_my_jobs(
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(get_current_recruiter),
):
    jobs = (
        db.query(Job)
        .options(joinedload(Job.job_skills).joinedload(JobSkill.skill))
        .filter(Job.recruiter_id == recruiter.id)
        .order_by(Job.created_at.desc())
        .all()
    )

    return [serialize_job(job) for job in jobs]


# =====================================================
# READ ALL JOBS (PUBLIC)
# =====================================================
@router.get("/", response_model=list[JobRead])
def get_all_jobs(db: Session = Depends(get_db)):
    jobs = (
        db.query(Job)
        .options(
            joinedload(Job.company),
            joinedload(Job.job_skills).joinedload(JobSkill.skill),
        )
        .filter(
            Job.is_active == True,
            Job.status == JobStatus.active,
        )
        .order_by(Job.created_at.desc())
        .all()
    )

    return [serialize_job(job) for job in jobs]


# =====================================================
# SEARCH JOBS (PUBLIC) ⚠ MUST BE ABOVE /{job_id}
# =====================================================
@router.get("/search", response_model=list[dict])
def search_jobs(
    keyword: str | None = None,
    location: str | None = None,
    min_experience: float | None = None,
    db: Session = Depends(get_db),
):
    q = (
        db.query(Job)
        .join(Company)
        .options(
            joinedload(Job.company),
            joinedload(Job.job_skills).joinedload(JobSkill.skill),
        )
        .filter(
            Job.is_active == True,
            Job.status == JobStatus.active,
        )
    )

    if keyword:
        q = q.filter(Job.title.ilike(f"%{keyword}%"))

    if location:
        q = q.filter(Job.location.ilike(f"%{location}%"))

    if min_experience is not None:
        q = q.filter(Job.min_experience <= min_experience)

    jobs = q.order_by(Job.created_at.desc()).all()

    return [
    {
        "job_id": str(job.id),
        "title": job.title,
        "company_name": job.company.name if job.company else None,
        "location": job.location,
        "min_experience": job.min_experience,
        "max_experience": job.max_experience,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,

        # ✅ ADD THESE TWO LINES
        "description": job.description,
        "description_file_key": job.description_file_key,

        "skills": [js.skill.name for js in job.job_skills],
    }
    for job in jobs
]



# =====================================================
# READ JOB BY ID (PUBLIC)
# =====================================================
@router.get("/{job_id}", response_model=JobRead)
def get_job_by_id(job_id: UUID, db: Session = Depends(get_db)):
    job = (
        db.query(Job)
        .options(joinedload(Job.job_skills).joinedload(JobSkill.skill))
        .filter(
            Job.id == job_id,
            Job.is_active == True,
            Job.status == JobStatus.active,
        )
        .first()
    )

    if not job:
        raise HTTPException(404, "Job not found")

    return serialize_job(job)


# =====================================================
# UPDATE JOB (RECRUITER ONLY)
# =====================================================
@router.put("/{job_id}", response_model=JobRead)
def update_job(
    job_id: UUID,
    payload: JobUpdate,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(get_current_recruiter),
):
    job = (
        db.query(Job)
        .filter(
            Job.id == job_id,
            Job.recruiter_id == recruiter.id,
            Job.is_active == True,
        )
        .first()
    )

    if not job:
        raise HTTPException(404, "Job not found")

    update_data = payload.model_dump(exclude_unset=True)

    # ---------- Update fields ----------
    for key, value in update_data.items():
        if key != "skills":
            setattr(job, key, value)

    # ---------- Update skills ----------
    if payload.skills is not None:
        db.query(JobSkill).filter(
            JobSkill.job_id == job.id
        ).delete()

        for skill_name in set(payload.skills):
            name = skill_name.strip().lower()
            if not name:
                continue

            skill = db.query(Skill).filter(
                func.lower(Skill.name) == name
            ).first()

            if not skill:
                skill = Skill(name=name.title())
                db.add(skill)
                db.flush()

            db.add(JobSkill(job_id=job.id, skill_id=skill.id))

    db.commit()
    db.refresh(job)
    return serialize_job(job)


# =====================================================
# DELETE JOB (RECRUITER ONLY – SOFT DELETE)
# =====================================================
@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(get_current_recruiter),
):
    job = (
        db.query(Job)
        .filter(
            Job.id == job_id,
            Job.recruiter_id == recruiter.id,
            Job.is_active == True,
        )
        .first()
    )

    if not job:
        raise HTTPException(404, "Job not found")

    job.is_active = False
    job.status = JobStatus.closed
    db.commit()
    return None


# =====================================================
# UNARCHIVE JOB (RECRUITER ONLY)
# =====================================================
@router.put("/{job_id}/unarchive", response_model=JobRead)
def unarchive_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    recruiter: Recruiter = Depends(get_current_recruiter),
):
    job = (
        db.query(Job)
        .filter(
            Job.id == job_id,
            Job.recruiter_id == recruiter.id,
            Job.is_active == False,
        )
        .first()
    )

    if not job:
        raise HTTPException(404, "Job not found")

    job.is_active = True
    job.status = JobStatus.active
    db.commit()
    db.refresh(job)
    return serialize_job(job)


# =====================================================
# HELPER – SERIALIZE JOB
# =====================================================
def serialize_job(job: Job) -> JobRead:
    return JobRead(
        id=job.id,
        title=job.title,
        description=job.description,
        location=job.location,
        min_experience=job.min_experience,
        max_experience=job.max_experience,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        employment_type=job.employment_type,
        status=job.status,
        recruiter_id=job.recruiter_id,
        company_id=job.company_id,
        is_active=job.is_active,
        created_at=job.created_at,
        skills=[js.skill.name for js in job.job_skills],
    )
