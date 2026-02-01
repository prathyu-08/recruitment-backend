from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from uuid import UUID

from .db import get_db
from .models import Notification, User
from .auth_api import oauth2_scheme, decode_cognito_token

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.get("/")
def get_my_notifications(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    user = db.query(User).filter(
        User.cognito_sub == payload["sub"]
    ).first()

    return db.query(Notification).filter(
        Notification.user_id == user.id
    ).order_by(Notification.created_at.desc()).all()


@router.put("/{notification_id}/read")
def mark_notification_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    payload = decode_cognito_token(token)

    user = db.query(User).filter(
        User.cognito_sub == payload["sub"]
    ).first()

    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user.id
    ).first()

    if notification:
        notification.is_read = True
        db.commit()

    return {"message": "Notification marked as read"}