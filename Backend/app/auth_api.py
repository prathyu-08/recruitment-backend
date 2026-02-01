from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from jose import jwt
import requests
import os
import boto3
from pydantic import EmailStr
from fastapi.security import OAuth2PasswordBearer

from .db import get_db
from .models import User, Recruiter, Company, UserRole, CandidateProfile

router = APIRouter(prefix="/auth", tags=["Auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

COGNITO_REGION = os.getenv("AWS_REGION")
USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")

cognito = boto3.client(
    "cognito-idp",
    region_name=COGNITO_REGION,
)

# =====================================================
# DECODE COGNITO TOKEN
# =====================================================
def decode_cognito_token(token: str) -> dict:
    jwks_url = (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
        f"{USER_POOL_ID}/.well-known/jwks.json"
    )
    jwks = requests.get(jwks_url).json()

    header = jwt.get_unverified_header(token)
    key = next(k for k in jwks["keys"] if k["kid"] == header["kid"])

    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=CLIENT_ID,
        issuer=f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{USER_POOL_ID}",
    )

# =====================================================
# COMPLETE LOGIN (AUTO CREATE USER / PROFILE)
# =====================================================
@router.post("/complete-login")
def complete_login(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    payload = decode_cognito_token(token)

    sub = payload["sub"]
    email = payload["email"]
    role_str = payload.get("custom:role", "user")

    # ---- User ----
    user = db.query(User).filter(User.cognito_sub == sub).first()
    if not user:
        user = User(
            cognito_sub=sub,
            email=email,
            full_name=payload.get("custom:full_name", email),
            role=UserRole(role_str),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    recruiter_id = None

    # ---- Candidate profile auto-create ----
    if user.role == UserRole.user:
        profile = db.query(CandidateProfile).filter(
            CandidateProfile.user_id == user.id
        ).first()

        if not profile:
            db.add(CandidateProfile(user_id=user.id))
            db.commit()

    # ---- Recruiter auto-create ----
    if user.role == UserRole.recruiter:
        recruiter = db.query(Recruiter).filter(
            Recruiter.user_id == user.id
        ).first()

        if not recruiter:
            company = Company(
                name=payload.get("custom:company_name", "Unknown"),
                industry=payload.get("custom:industry"),
                website=payload.get("custom:website"),
                location=payload.get("custom:location"),
            )
            db.add(company)
            db.commit()
            db.refresh(company)

            recruiter = Recruiter(
                user_id=user.id,
                company_id=company.id,
                designation=payload.get("custom:designation"),
            )
            db.add(recruiter)
            db.commit()
            db.refresh(recruiter)

        recruiter_id = recruiter.id

    return {
        "user_id": str(user.id),
        "role": user.role.value,  # ✅ ALWAYS STRING
        "recruiter_id": str(recruiter_id) if recruiter_id else None,
    }

# =====================================================
# FORGOT PASSWORD (COGNITO)
# =====================================================
@router.post("/forgot-password")
def forgot_password(data: dict):
    email = data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    try:
        cognito.forgot_password(
            ClientId=CLIENT_ID,
            Username=email,
        )
    except Exception:
        pass  # Do not reveal existence

    return {"message": "If the email exists, a reset code has been sent"}

@router.post("/confirm-reset-password")
def confirm_reset_password(
    email: EmailStr,
    confirmation_code: str,
    new_password: str,
):
    try:
        cognito.confirm_forgot_password(
            ClientId=CLIENT_ID,
            Username=email,
            ConfirmationCode=confirmation_code,
            Password=new_password,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"message": "Password reset successful"}

# =====================================================
# AUTH DEPENDENCIES
# =====================================================
def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_cognito_token(token)
    sub = payload.get("sub")

    user = db.query(User).filter(User.cognito_sub == sub).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def get_current_candidate(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = get_current_user(token, db)

    if user.role != UserRole.user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Candidate access only",
        )

    return user


def get_current_recruiter(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Recruiter:
    user = get_current_user(token, db)

    if user.role != UserRole.recruiter:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recruiter access only",
        )

    recruiter = db.query(Recruiter).filter(
        Recruiter.user_id == user.id
    ).first()

    if not recruiter:
        raise HTTPException(status_code=404, detail="Recruiter profile not found")

    return recruiter