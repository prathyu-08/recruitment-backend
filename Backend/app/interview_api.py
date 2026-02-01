from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime, date
from .schemas import ScheduleInterviewRequest


from .db import get_db
from .models import (
    Interview,
    Interviewer,
    InterviewInterviewer,
    InterviewSlot,
    Application,
    ApplicationStatus,
    User,
    UserRole,
    
)
from .auth_api import oauth2_scheme, decode_cognito_token
from .email_utils import send_email
from .email_templates import interview_slot_confirmed
router = APIRouter(prefix="/interviews", tags=["Interviews"])

PORTAL_URL = "http://localhost:8501"  # 👈 change when deployed

# =====================================================
# 📅 CREATE INTERVIEW (DIRECT + SLOT)
# =====================================================
@router.post("/schedule")
def schedule_interview(
    payload: ScheduleInterviewRequest,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload_token = decode_cognito_token(token)

    # ---------------- AUTH ----------------
    recruiter_user = db.query(User).filter(
        User.cognito_sub == payload_token["sub"],
        User.role == UserRole.recruiter
    ).first()

    if not recruiter_user:
        raise HTTPException(403, "Only recruiters can schedule interviews")

    # ---------------- APPLICATION ----------------
    application = db.query(Application).filter(
        Application.id == payload.application_id
    ).first()

    if not application:
        raise HTTPException(404, "Application not found")

    if application.status != ApplicationStatus.shortlisted:
        raise HTTPException(400, "Candidate must be shortlisted")

    if application.interview:
        raise HTTPException(400, "Interview already exists")

    # ---------------- CREATE INTERVIEW ----------------
    interview = Interview(
        application_id=application.id,
        interview_type=payload.interview_type,
        meeting_link=payload.meeting_link,
        location=payload.location,
        scheduled_at=payload.scheduled_at if payload.schedule_mode == "direct" else None,
        status="scheduled",
    )

    db.add(interview)

    # 🔥 CRITICAL: MOVE APPLICATION TO INTERVIEW STAGE
    application.status = ApplicationStatus.interview

    db.commit()
    db.refresh(interview)

    # ---------------- INTERVIEWERS ----------------
    for interviewer_id in payload.interviewer_ids:
        db.add(
            InterviewInterviewer(
                interview_id=interview.id,
                interviewer_id=interviewer_id
            )
        )

    db.commit()
    db.refresh(interview)

    # ---------------- 🔔 NOTIFICATION ----------------
    from .notification_utils import create_notification

    create_notification(
        db,
        application.candidate.user.id,
        "Interview Scheduled",
        f"Your interview for '{application.job.title}' has been scheduled."
    )

    # ---------------- DIRECT INTERVIEW EMAIL ----------------
    if payload.schedule_mode == "direct":
        from .email_utils import get_resume_bytes, send_email_with_attachment
        from .calendar_utils import generate_interview_ics

        candidate = application.candidate.user
        job = application.job

        resume = application.resume
        resume_bytes = None
        resume_filename = None

        if resume:
            resume_bytes = get_resume_bytes(resume.resume_s3_key)
            resume_filename = resume.original_filename or "resume.pdf"

        ics_content = generate_interview_ics(
            title=f"Interview – {job.title}",
            description=f"Interview Type: {interview.interview_type}\nMeeting: {interview.meeting_link or interview.location}",
            start_time=interview.scheduled_at,
        )

        subject = f"Interview Scheduled – {job.title}"
        body = f"""
Hi {candidate.full_name},

Your interview has been scheduled.

Job Role: {job.title}
Interview Type: {interview.interview_type.title()}
Date & Time: {interview.scheduled_at}

Meeting Details:
{interview.meeting_link or interview.location}

Calendar invite is attached.

Regards,
Recruitment Team
"""

        # Candidate
        if resume_bytes:
            send_email_with_attachment(
                candidate.email,
                subject,
                body,
                resume_bytes,
                resume_filename
            )

        send_email_with_attachment(
            candidate.email,
            subject,
            body,
            ics_content.encode("utf-8"),
            "interview.ics"
        )

        # Interviewers
        for interviewer in interview.interviewers:
            send_email_with_attachment(
                interviewer.email,
                subject,
                body,
                ics_content.encode("utf-8"),
                "interview.ics"
            )

    return {
        "message": "Interview scheduled successfully",
        "interview_id": str(interview.id),
    }

# =====================================================
# 🔄 RESCHEDULE INTERVIEW (RECRUITER)
# =====================================================
@router.put("/reschedule/{application_id}")
def reschedule_interview(
    application_id: UUID,
    new_scheduled_at: str,   # ISO datetime string
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    # ---------------- AUTH ----------------
    recruiter = db.query(User).filter(
        User.cognito_sub == payload["sub"],
        User.role == UserRole.recruiter
    ).first()

    if not recruiter:
        raise HTTPException(403, "Only recruiters can reschedule interviews")

    # ---------------- FETCH INTERVIEW ----------------
    interview = db.query(Interview).filter(
        Interview.application_id == application_id
    ).first()

    if not interview:
        raise HTTPException(404, "Interview not found")

    application = interview.application

    # ---------------- VALIDATE STATE ----------------
    if application.status != ApplicationStatus.interview:
        raise HTTPException(
            400,
            "Only interviews in interview stage can be rescheduled"
        )

    # ---------------- PARSE DATETIME SAFELY ----------------
    from datetime import datetime
    try:
        new_dt = datetime.fromisoformat(new_scheduled_at)
    except ValueError:
        raise HTTPException(400, "Invalid datetime format")

    # ---------------- UPDATE INTERVIEW ----------------
    interview.scheduled_at = new_dt
    interview.status = "rescheduled"

    db.commit()
    db.refresh(interview)

    # ---------------- NOTIFICATION ----------------
    from .notification_utils import create_notification

    create_notification(
        db,
        application.candidate.user.id,
        "Interview Rescheduled",
        f"Your interview for '{application.job.title}' has been rescheduled to "
        f"{new_dt.strftime('%d %b %Y, %I:%M %p')}."
    )

    # ---------------- EMAIL + CALENDAR ----------------
    from .calendar_utils import generate_interview_ics
    from .email_utils import send_email_with_attachment

    candidate = application.candidate.user

    ics_content = generate_interview_ics(
        title=f"Interview – {application.job.title}",
        description="Interview rescheduled",
        start_time=new_dt,
    )

    subject = f"Interview Rescheduled – {application.job.title}"
    body = f"""
Hi {candidate.full_name},

Your interview for the position of "{application.job.title}" has been rescheduled.

🔁 Updated Interview Details
----------------------------
Interview Type: {interview.interview_type.title()}
New Date & Time: {new_dt.strftime('%d %b %Y, %I:%M %p')}

{"Meeting Link: " + interview.meeting_link if interview.meeting_link else ""}
{"Interview Location: " + interview.location if interview.location else ""}

The updated calendar invite is attached to this email.

Regards,
Recruitment Team
"""

    # Candidate email
    send_email_with_attachment(
        candidate.email,
        subject,
        body,
        ics_content.encode("utf-8"),
        "interview.ics"
    )

    # Interviewers email
    for interviewer in interview.interviewers:
        send_email_with_attachment(
            interviewer.email,
            subject,
            body,
            ics_content.encode("utf-8"),
            "interview.ics"
        )

    return {"message": "Interview rescheduled successfully"}

# =====================================================
# 🔧 SHARED CANCEL LOGIC (FINAL)
# =====================================================
def _cancel_interview(
    *,
    application_id: UUID,
    cancelled_by: str,  # "recruiter" or "candidate"
    db: Session,
):
    interview = db.query(Interview).filter(
        Interview.application_id == application_id
    ).first()

    if not interview:
        raise HTTPException(404, "Interview not found")

    # Cancel interview
    interview.status = "cancelled"
    interview.scheduled_at = None

    # ✅ FINAL RULE: CANCEL = REJECT
    interview.application.status = ApplicationStatus.rejected

    db.commit()

    # 🔔 Notification
    from .notification_utils import create_notification

    candidate = interview.application.candidate.user

    create_notification(
        db,
        candidate.id,
        "Interview Cancelled",
        f"Your interview was cancelled by the {cancelled_by}. "
        "Your application has been marked as rejected."
    )

    return {
        "message": f"Interview cancelled by {cancelled_by}",
        "new_status": "rejected"
    }

# =====================================================
# ❌ CANCEL INTERVIEW – RECRUITER
# =====================================================
@router.put("/cancel/{application_id}")
def cancel_interview_by_recruiter(
    application_id: UUID,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    recruiter = db.query(User).filter(
        User.cognito_sub == payload["sub"],
        User.role == UserRole.recruiter,
    ).first()

    if not recruiter:
        raise HTTPException(403, "Only recruiters can cancel interviews")

    return _cancel_interview(
        application_id=application_id,
        cancelled_by="recruiter",
        db=db,
    )


# =====================================================
# ❌ CANCEL INTERVIEW – CANDIDATE
# =====================================================
@router.put("/cancel-by-candidate/{application_id}")
def cancel_interview_by_candidate(
    application_id: UUID,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    candidate = db.query(User).filter(
        User.cognito_sub == payload["sub"],
        User.role == UserRole.user,
    ).first()

    if not candidate:
        raise HTTPException(403, "Only candidates can cancel interviews")

    return _cancel_interview(
        application_id=application_id,
        cancelled_by="candidate",
        db=db,
    )

@router.post("/slots/{interview_id}")
def add_interview_slots(
    interview_id: UUID,
    interview_date: date,
    slots: list[dict] = Body(...),  # [{start_time, end_time}]
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    recruiter = db.query(User).filter(
        User.cognito_sub == payload["sub"],
        User.role == UserRole.recruiter
    ).first()

    if not recruiter:
        raise HTTPException(403, "Only recruiters allowed")

    interview = db.query(Interview).filter(
        Interview.id == interview_id
    ).first()

    if not interview:
        raise HTTPException(404, "Interview not found")

    interview.slots.clear()

    for slot in slots:
        start_dt = datetime.combine(
            interview_date,
            datetime.strptime(slot["start_time"], "%H:%M").time()
        )
        end_dt = datetime.combine(
            interview_date,
            datetime.strptime(slot["end_time"], "%H:%M").time()
        )

        db.add(
            InterviewSlot(
                interview_id=interview.id,
                start_time=start_dt,
                end_time=end_dt
            )
        )

    db.commit()

    candidate = interview.application.candidate.user
    job = interview.application.job

    # 📩 SLOT SELECTION EMAIL
    send_email(
        to_email=candidate.email,
        subject=f"Select Interview Slot – {job.title}",
        body=f"""
Hi {candidate.full_name},

You have been shortlisted for the position of {job.title}.

The recruiter has shared multiple interview time slots.
Please log in to the portal and select one convenient slot.

👉 {PORTAL_URL}/my-applications

Interview Type: {interview.interview_type.title()}

Regards,
Recruitment Team
"""
    )

    return {"message": "Interview slots sent to candidate"}
@router.get("/slots/{application_id}")
def get_interview_slots(
    application_id: UUID,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    user = db.query(User).filter(User.cognito_sub == payload["sub"]).first()
    if not user or user.role != UserRole.user:
        raise HTTPException(403, "Only candidates allowed")

    interview = db.query(Interview).filter(
        Interview.application_id == application_id
    ).first()

    if not interview:
        raise HTTPException(404, "Interview not found")

    return [
        {
            "slot_id": str(slot.id),
            "start_time": slot.start_time,
            "end_time": slot.end_time,
            "is_selected": slot.is_selected,
        }
        for slot in interview.slots
    ]

@router.put("/slots/select/{slot_id}")
def select_interview_slot(
    slot_id: UUID,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    candidate_user = db.query(User).filter(
        User.cognito_sub == payload["sub"],
        User.role == UserRole.user
    ).first()

    if not candidate_user:
        raise HTTPException(403, "Only candidates can select slots")

    slot = db.query(InterviewSlot).filter(
        InterviewSlot.id == slot_id
    ).first()

    if not slot:
        raise HTTPException(404, "Slot not found")

    interview = slot.interview
    application = interview.application

    if interview.scheduled_at:
        raise HTTPException(400, "Interview already confirmed")

    db.query(InterviewSlot).filter(
        InterviewSlot.interview_id == interview.id
    ).update({"is_selected": False})

    slot.is_selected = True
    interview.scheduled_at = slot.start_time

    db.commit()
    db.refresh(interview)

    from .email_utils import get_resume_bytes, send_email_with_attachment
    from .calendar_utils import generate_interview_ics

    resume = application.resume
    resume_bytes = None
    resume_filename = None

    if resume:
        resume_bytes = get_resume_bytes(resume.resume_s3_key)
        resume_filename = resume.original_filename or "resume.pdf"

    ics_content = generate_interview_ics(
        title=f"Interview – {application.job.title}",
        description=f"Interview Type: {interview.interview_type}\nMeeting: {interview.meeting_link or interview.location}",
        start_time=interview.scheduled_at,
    )

    subject = f"Interview Confirmed – {application.job.title}"

    body = f"""
Hi {candidate_user.full_name},

Your interview slot has been confirmed.

Job Role: {application.job.title}
Interview Type: {interview.interview_type.title()}
Date & Time: {interview.scheduled_at}

Calendar invite is attached.

Regards,
Recruitment Team
"""

    # Candidate
    if resume_bytes:
        send_email_with_attachment(
            candidate_user.email, subject, body,
            resume_bytes, resume_filename
        )

    send_email_with_attachment(
        candidate_user.email, subject, body,
        ics_content.encode("utf-8"), "interview.ics"
    )

    # Interviewers
    for interviewer in interview.interviewers:
        send_email_with_attachment(
            interviewer.email, subject, body,
            ics_content.encode("utf-8"), "interview.ics"
        )

    return {"message": "Interview slot confirmed successfully"}

def notify_all_on_cancel(interview, reason):
    candidate = interview.application.candidate.user
    recruiter = interview.application.job.recruiter.user
    interviewers = interview.interviewers

    emails = {
        candidate.email,
        recruiter.email,
        *[i.email for i in interviewers]
    }

    for email in emails:
        send_email(
            to_email=email,
            subject="Interview Cancelled",
            body=reason
        )