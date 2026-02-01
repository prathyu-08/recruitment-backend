from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Body, status
from sqlalchemy import func, and_,text
from sqlalchemy.orm import Session, joinedload
from uuid import UUID
from typing import List, Optional
from .db import get_db
from .utils.s3 import upload_profile_picture, upload_resume as upload_resume_s3, delete_file
from .models import (
    User,
    CandidateProfile,
    UserRole,
    CandidateEducation,
    CandidateExperience,
    CandidateSkill,
    Skill,
    CandidateProject,
    ProfileView,
    Resume,
    Application,
    SavedJob,
)
from .schemas import (
    CandidateProfileCreate,
    CandidateProfileRead,
    CandidateEducationCreate,
    CandidateEducationRead,
    CandidateExperienceCreate,
    CandidateExperienceRead,
    CandidateSkillRead,
    CandidateSkillInput,
    CandidateProjectCreate,
    CandidateProjectRead,
    ResumeRead,
    ProfileAnalytics,
    ProfileCompletion,
)
from .auth_api import  get_current_candidate
router = APIRouter(prefix="/candidate", tags=["Candidate"])


# =====================================================
# DEPENDENCIES & HELPERS
# =====================================================

def get_candidate_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_candidate)
) -> CandidateProfile:
    """Get candidate profile for the authenticated user."""
    if current_user.role != UserRole.user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Candidate access only"
        )
    profile = db.query(CandidateProfile).options(
        joinedload(CandidateProfile.user),
        joinedload(CandidateProfile.educations),
        joinedload(CandidateProfile.experiences),
        joinedload(CandidateProfile.skills).joinedload(CandidateSkill.skill),
        joinedload(CandidateProfile.projects),
        joinedload(CandidateProfile.resumes),
    ).filter(
        CandidateProfile.user_id == current_user.id
    ).first()
    
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate profile not found"
        )
    return profile


# =====================================================
# PROFILE MANAGEMENT
# =====================================================

@router.get(
    "/profile",
    response_model=CandidateProfileRead,
)
def get_profile(
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    user = profile.user

    return {
        "id": profile.id,
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "profile_picture": profile.profile_picture,
        "current_location": profile.current_location,
        "preferred_location": profile.preferred_location,
        "total_experience": profile.total_experience,
        "current_ctc": profile.current_ctc,
        "expected_ctc": profile.expected_ctc,
        "profile_summary": profile.profile_summary,
        "resume_headline": profile.resume_headline,
        "notice_period": profile.notice_period,
        "willing_to_relocate": profile.willing_to_relocate,
        "preferred_shift": profile.preferred_shift,
        "employment_type_preference": profile.employment_type_preference,
        "visibility": profile.visibility,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "last_active": profile.last_active,
        "is_active": profile.is_active,
        "created_at": profile.created_at,
    }


@router.put(
    "/profile",
    response_model=CandidateProfileRead,
)
def update_profile(
    payload: CandidateProfileCreate,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    try:
        ALLOWED_FIELDS = {
            "current_location",
            "preferred_location",
            "total_experience",
            "current_ctc",
            "expected_ctc",
            "profile_summary",
            "resume_headline",
            "notice_period",
            "willing_to_relocate",
            "preferred_shift",
            "employment_type_preference",
            "visibility",
            "linkedin_url",
            "github_url",
            "portfolio_url",
        }

        update_data = payload.dict(exclude_unset=True)

        for key, value in update_data.items():
            if key in ALLOWED_FIELDS:
                setattr(profile, key, value)

        profile.last_active = func.now()
        db.commit()
        db.refresh(profile)

        user = profile.user

        return {
            "id": profile.id,
            "user_id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "profile_picture": profile.profile_picture,
            "current_location": profile.current_location,
            "preferred_location": profile.preferred_location,
            "total_experience": profile.total_experience,
            "current_ctc": profile.current_ctc,
            "expected_ctc": profile.expected_ctc,
            "profile_summary": profile.profile_summary,
            "resume_headline": profile.resume_headline,
            "notice_period": profile.notice_period,
            "willing_to_relocate": profile.willing_to_relocate,
            "preferred_shift": profile.preferred_shift,
            "employment_type_preference": profile.employment_type_preference,
            "visibility": profile.visibility,
            "linkedin_url": profile.linkedin_url,
            "github_url": profile.github_url,
            "portfolio_url": profile.portfolio_url,
            "last_active": profile.last_active,
            "is_active": profile.is_active,
            "created_at": profile.created_at,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update profile: {str(e)}"
        )

@router.post(
    "/profile-picture",
    summary="Upload or replace profile picture"
)
def upload_profile_picture_api(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile),
):
    # =================================================
    # HARD VALIDATION (FIXES NoneType CRASH)
    # =================================================
    if file is None:
        raise HTTPException(status_code=400, detail="No file uploaded")

    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid file name")

    if not file.content_type:
        raise HTTPException(status_code=400, detail="Invalid file type")

    ALLOWED_TYPES = {"image/jpeg", "image/png", "image/jpg"}
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Only JPG or PNG allowed")

    # =================================================
    # FILE SIZE CHECK (SAFE)
    # =================================================
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)

    if size <= 0:
        raise HTTPException(status_code=400, detail="Empty file")

    if size > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Max size is 2MB")

    try:
        # =================================================
        # DELETE OLD PICTURE (IF EXISTS)
        # =================================================
        if profile.profile_picture:
            delete_file(profile.profile_picture)

        # =================================================
        # UPLOAD NEW PICTURE
        # =================================================
        s3_key = upload_profile_picture(file)

        profile.profile_picture = s3_key
        db.commit()

        return {
            "message": "Profile picture uploaded successfully",
            "s3_key": s3_key,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload profile picture: {str(e)}"
        )




# =====================================================
# EDUCATION - FULL CRUD
# =====================================================

@router.get(
    "/education",
    response_model=List[CandidateEducationRead],
    summary="Get education history",
    description="Retrieve all education records for the candidate"
)
def list_education(
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Get all education records."""
    return profile.educations


@router.post(
    "/education",
    status_code=status.HTTP_201_CREATED,
    summary="Add education",
    description="Add a new education record"
)
def add_education(
    data: CandidateEducationCreate,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Add education record."""
    try:
        education = CandidateEducation(
            candidate_id=profile.id,
            institution=data.institution,
            degree=data.degree,
            field_of_study=data.field_of_study,
            start_year=data.start_year,
            end_year=data.end_year,
            grade=data.grade,
        )
        
        db.add(education)
        db.commit()
        db.refresh(education)
        
        return {
            "message": "Education added successfully",
            "id": education.id,
            "education": education
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add education: {str(e)}"
        )


@router.put(
    "/education/{edu_id}",
    summary="Update education",
    description="Update an existing education record"
)
def update_education(
    edu_id: UUID,
    data: CandidateEducationCreate,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Update education record."""
    education = db.query(CandidateEducation).filter(
        and_(
            CandidateEducation.id == edu_id,
            CandidateEducation.candidate_id == profile.id
        )
    ).first()
    
    if not education:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Education record not found"
        )
    
    try:
        update_data = data.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(education, key, value)
        
        db.commit()
        
        return {
            "message": "Education updated successfully",
            "id": education.id
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update education: {str(e)}"
        )


@router.delete(
    "/education/{edu_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete education",
    description="Delete an education record"
)
def delete_education(
    edu_id: UUID,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Delete education record."""
    education = db.query(CandidateEducation).filter(
        and_(
            CandidateEducation.id == edu_id,
            CandidateEducation.candidate_id == profile.id
        )
    ).first()
    
    if not education:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Education record not found"
        )
    
    try:
        db.delete(education)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete education: {str(e)}"
        )


# =====================================================
# EXPERIENCE - FULL CRUD
# =====================================================

@router.get(
    "/experience",
    response_model=List[CandidateExperienceRead],
    summary="Get work experience",
    description="Retrieve all work experience records"
)
def list_experience(
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Get all experience records."""
    return profile.experiences


@router.post(
    "/experience",
    status_code=status.HTTP_201_CREATED,
    summary="Add experience",
    description="Add a new work experience record"
)
def add_experience(
    data: CandidateExperienceCreate,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Add experience record."""
    try:
        experience = CandidateExperience(
            candidate_id=profile.id,
            company_name=data.company_name,
            role=data.role,
            start_date=data.start_date,
            end_date=data.end_date,
            is_current=data.is_current,
            description=data.description,
        )
        
        db.add(experience)
        db.commit()
        db.refresh(experience)
        
        return {
            "message": "Experience added successfully",
            "id": experience.id,
            "experience": experience
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add experience: {str(e)}"
        )


@router.put(
    "/experience/{exp_id}",
    summary="Update experience",
    description="Update an existing work experience record"
)
def update_experience(
    exp_id: UUID,
    data: CandidateExperienceCreate,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Update experience record."""
    experience = db.query(CandidateExperience).filter(
        and_(
            CandidateExperience.id == exp_id,
            CandidateExperience.candidate_id == profile.id
        )
    ).first()
    
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experience record not found"
        )
    
    try:
        update_data = data.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(experience, key, value)
        
        db.commit()
        
        return {
            "message": "Experience updated successfully",
            "id": experience.id
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update experience: {str(e)}"
        )


@router.delete(
    "/experience/{exp_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete experience",
    description="Delete a work experience record"
)
def delete_experience(
    exp_id: UUID,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Delete experience record."""
    experience = db.query(CandidateExperience).filter(
        and_(
            CandidateExperience.id == exp_id,
            CandidateExperience.candidate_id == profile.id
        )
    ).first()
    
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experience record not found"
        )
    
    try:
        db.delete(experience)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete experience: {str(e)}"
        )


# =====================================================
# SKILLS MANAGEMENT
# =====================================================

@router.get("/skills")
def list_skills(
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    skills = (
        db.query(CandidateSkill)
        .options(joinedload(CandidateSkill.skill))
        .filter(CandidateSkill.candidate_id == profile.id)
        .all()
    )

    return [
        {
            "skill": {
                "id": cs.skill.id,
                "name": cs.skill.name
            },
            "proficiency": cs.proficiency,
            "years_of_experience": cs.years_of_experience
        }
        for cs in skills
    ]

@router.put(
    "/skills",
    summary="Update skills",
    description="Replace all skills with new list (upsert operation)"
)
def upsert_skills(
    skills: List[CandidateSkillInput] = Body(...),
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Replace all skills with new list."""
    if skills is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Skills payload missing"
        )
    
    try:
        # Delete existing skills
        db.query(CandidateSkill).filter(
            CandidateSkill.candidate_id == profile.id
        ).delete(synchronize_session=False)
        
        seen = set()
        
        for skill_input in skills:
            normalized_name = skill_input.name.strip().lower()
            
            if not normalized_name:
                continue
            
            if normalized_name in seen:
                continue
            seen.add(normalized_name)
            
            # Find or create skill
            skill = db.query(Skill).filter(
                func.lower(Skill.name) == normalized_name
            ).first()
            
            if not skill:
                skill = Skill(name=skill_input.name.strip().title())
                db.add(skill)
                db.flush()
            
            # Create candidate skill association
            candidate_skill = CandidateSkill(
                candidate_id=profile.id,
                skill_id=skill.id,
                proficiency=skill_input.proficiency,
                years_of_experience=skill_input.years_of_experience,
            )
            
            db.add(candidate_skill)
        
        db.commit()
        
        # Get updated skills
        updated_skills = db.query(CandidateSkill).filter(
            CandidateSkill.candidate_id == profile.id
        ).options(joinedload(CandidateSkill.skill)).all()
        
        return {
            "message": f"{len(seen)} skills updated successfully",
            "skills_count": len(seen),
            "skills": [
                {
                    "skill": {
                        "name": cs.skill.name,
                        "id": cs.skill.id
                    },
                    "proficiency": cs.proficiency,
                    "years_of_experience": cs.years_of_experience
                }
                for cs in updated_skills
            ]
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update skills: {str(e)}"
        )


# =====================================================
# PROJECTS - FULL CRUD
# =====================================================

@router.get(
    "/projects",
    response_model=List[CandidateProjectRead],
    summary="Get projects",
    description="Retrieve all projects"
)
def list_projects(
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Get all projects."""
    return profile.projects


@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    summary="Add project",
    description="Add a new project"
)
def add_project(
    data: CandidateProjectCreate,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Add project."""
    try:
        project = CandidateProject(
            candidate_id=profile.id,
            title=data.title,
            description=data.description,
            technologies_used=data.technologies_used,
            project_url=data.project_url,
            start_date=data.start_date,
            end_date=data.end_date,
        )
        
        db.add(project)
        db.commit()
        db.refresh(project)
        
        return {
            "message": "Project added successfully",
            "id": project.id,
            "project": project
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add project: {str(e)}"
        )


@router.put(
    "/projects/{project_id}",
    summary="Update project",
    description="Update an existing project"
)
def update_project(
    project_id: UUID,
    data: CandidateProjectCreate,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Update project."""
    project = db.query(CandidateProject).filter(
        and_(
            CandidateProject.id == project_id,
            CandidateProject.candidate_id == profile.id
        )
    ).first()
    
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    try:
        update_data = data.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(project, key, value)
        
        db.commit()
        
        return {
            "message": "Project updated successfully",
            "id": project.id
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update project: {str(e)}"
        )


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete project",
    description="Delete a project"
)
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Delete project."""
    project = db.query(CandidateProject).filter(
        and_(
            CandidateProject.id == project_id,
            CandidateProject.candidate_id == profile.id
        )
    ).first()
    
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    try:
        db.delete(project)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete project: {str(e)}"
        )


# =====================================================
# RESUME MANAGEMENT
# =====================================================

@router.post("/resume/upload", summary="Upload resume")
def upload_resume(
    file: UploadFile = File(...),
    is_primary: bool = True,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile),
):
    ALLOWED_TYPES = {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type")

    MAX_SIZE = 5 * 1024 * 1024
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_SIZE:
        raise HTTPException(status_code=400, detail="File too large")

    try:
        # 1️⃣ Mark existing primary false
        if is_primary:
            db.query(Resume).filter(
                Resume.candidate_id == profile.id,
                Resume.is_primary == True
            ).update({"is_primary": False})
            db.flush()

        # 2️⃣ Create DB record FIRST
        resume = Resume(
            candidate_id=profile.id,
            resume_s3_key="PENDING",
            original_filename=file.filename,
            content_type=file.content_type,
            file_size=file_size,
            is_primary=is_primary,
        )
        db.add(resume)
        db.flush()  # get resume.id

        # 3️⃣ Upload to S3
        s3_key = upload_resume_s3(file=file)

        # 4️⃣ Update real key
        resume.resume_s3_key = s3_key
        db.commit()
        db.refresh(resume)

        return {
            "message": "Resume uploaded successfully",
            "resume_id": resume.id,
            "s3_key": s3_key,
            "file_name": file.filename,
            "is_primary": is_primary,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))



@router.get(
    "/resume/list",
    response_model=List[ResumeRead],
    summary="List resumes",
    description="Get all uploaded resumes"
)
def list_resumes(
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """List all resumes."""
    return profile.resumes


@router.delete(
    "/resume/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete resume",
    description="Delete a resume file"
)
def delete_resume(
    resume_id: UUID,
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Delete resume."""
    resume = db.query(Resume).filter(
        and_(
            Resume.id == resume_id,
            Resume.candidate_id == profile.id
        )
    ).first()
    
    if not resume:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resume not found"
        )
    
    try:
        # Delete from S3
        try:
            delete_file(resume.resume_s3_key)
        except Exception:
            pass
        
        # Delete from database
        db.delete(resume)
        
        # If this was primary, mark another resume as primary if available
        if resume.is_primary:
            other_resume = db.query(Resume).filter(
                and_(
                    Resume.candidate_id == profile.id,
                    Resume.id != resume_id
                )
            ).first()
            
            if other_resume:
                other_resume.is_primary = True
        
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete resume: {str(e)}"
        )


# =====================================================
# PROFILE COMPLETION
# =====================================================

def calculate_profile_completion(profile: CandidateProfile) -> dict:
    """Calculate profile completion percentage and suggestions."""
    score = 0
    missing = []
    suggestions = []
    
    # Basic profile (20 points)
    basic_fields = [
        ("current_location", "current location"),
        ("profile_summary", "profile summary"),
        ("resume_headline", "resume headline"),
    ]
    
    basic_complete = True
    for field, field_name in basic_fields:
        if not getattr(profile, field):
            basic_complete = False
            suggestions.append(f"Add your {field_name}")
    
    if basic_complete and profile.total_experience is not None:
        score += 20
    else:
        missing.append("Complete basic profile info")
    
    # Education (10 points)
    if profile.educations and len(profile.educations) > 0:
        score += 10
    else:
        missing.append("Add education")
        suggestions.append("Add at least one education record")
    
    # Experience (20 points)
    if profile.experiences and len(profile.experiences) > 0:
        score += 20
    else:
        missing.append("Add work experience")
        suggestions.append("Add your work experience or internships")
    
    # Skills (15 points)
    skill_count = len(profile.skills) if profile.skills else 0
    if skill_count >= 5:
        score += 15
    elif skill_count > 0:
        score += 7
        suggestions.append(f"Add {5 - skill_count} more skills (recommended: 5+)")
    else:
        missing.append("Add skills")
        suggestions.append("Add at least 5 key skills")
    
    # Projects (15 points)
    if profile.projects and len(profile.projects) > 0:
        score += 15
    else:
        missing.append("Add projects")
        suggestions.append("Showcase your projects and work")
    
    
    # Resume (10 points)
    if profile.resumes and len(profile.resumes) > 0:
        score += 10
    else:
        missing.append("Upload resume")
        suggestions.append("Upload your latest resume")
    
    # Profile picture (5 points)
    if profile.profile_picture:
        score += 5
    else:
        suggestions.append("Add a professional profile picture")
    
    # Career preferences (5 points)
    if profile.expected_ctc and profile.preferred_location:
        score += 5
    else:
        suggestions.append("Set your career preferences (expected CTC and location)")
    
    return {
        "percentage": min(score, 100),
        "missing_sections": missing,
        "suggestions": suggestions[:5],  # Limit to top 5 suggestions
        "is_complete": score >= 80,  # Consider 80% as complete
        "score_breakdown": {
            "basic_profile": 20,
            "education": 10,
            "experience": 20,
            "skills": 15,
            "projects": 15,
            "resume": 10,
            "profile_picture": 5,
            "career_preferences": 5
        }
    }


@router.get(
    "/profile-completion",
    response_model=ProfileCompletion,
    summary="Get profile completion",
    description="Calculate profile completion percentage and get suggestions"
)
def profile_completion(
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Calculate and return profile completion metrics."""
    return calculate_profile_completion(profile)


# =====================================================
# ANALYTICS
# =====================================================

@router.get(
    "/profile-analytics",
    response_model=ProfileAnalytics,
    summary="Get profile analytics",
    description="Get comprehensive analytics for the candidate profile"
)
def get_profile_analytics(
    db: Session = Depends(get_db),
    profile: CandidateProfile = Depends(get_candidate_profile)
):
    """Get profile analytics."""
    try:
        # Profile views
        total_views = db.query(ProfileView).filter(
            ProfileView.candidate_id == profile.id
        ).count()
        
        # Recent views (last 30 days)
        recent_views = db.query(func.count(ProfileView.id)).filter(
            and_(
                ProfileView.candidate_id == profile.id,
                ProfileView.viewed_at >= func.now() - text("INTERVAL '30 days'")
            )
        ).scalar() or 0
        
        # Applications
        total_applications = db.query(Application).filter(
            Application.candidate_id == profile.id
        ).count()
        
        # Application status breakdown
        application_stats = db.query(
            Application.status,
            func.count(Application.id)
        ).filter(
            Application.candidate_id == profile.id
        ).group_by(Application.status).all()
        
        # Saved jobs
        saved_jobs_count = db.query(SavedJob).filter(
            SavedJob.candidate_id == profile.id
        ).count()
        
        # Profile completion
        completion_data = calculate_profile_completion(profile)
        
        return ProfileAnalytics(
            profile_views=total_views,
            recent_views=recent_views,
            total_applications=total_applications,
            saved_jobs=saved_jobs_count,
            application_breakdown={status.value: count for status, count in application_stats},
            profile_completion=completion_data["percentage"],
            profile_score=completion_data["percentage"]
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch analytics: {str(e)}"
        )