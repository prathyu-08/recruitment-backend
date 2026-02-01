import os
import boto3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from botocore.exceptions import ClientError

# ==================================================
# AWS CONFIG (ENVIRONMENT VARIABLES)
# ==================================================
AWS_REGION = os.getenv("AWS_REGION")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

SES_SENDER_EMAIL = os.getenv("SES_SENDER_EMAIL")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

# ==================================================
# AWS CLIENTS
# ==================================================
ses = boto3.client(
    "ses",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

def get_resume_bytes(s3_key: str) -> bytes:
    try:
        response = s3.get_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
        )
        return response["Body"].read()
    except ClientError as e:
        raise RuntimeError(f"Failed to fetch file from S3: {e}")
# ==================================================
# SIMPLE EMAIL (NO ATTACHMENT)
# ==================================================
def send_email(to_email: str, subject: str, body: str) -> None:
    """
    Send a plain text email using AWS SES
    """
    ses.send_email(
        Source=SES_SENDER_EMAIL,
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": body, "Charset": "UTF-8"}
            },
        },
    )

# ==================================================
# DOWNLOAD FILE FROM S3 (RESUME)
# ==================================================
def get_file_bytes_from_s3(s3_key: str) -> bytes:
    """
    Download a file from S3 and return raw bytes
    """
    response = s3.get_object(
        Bucket=S3_BUCKET_NAME,
        Key=s3_key,
    )
    return response["Body"].read()

# ==================================================
# EMAIL WITH ATTACHMENT (SES RAW EMAIL)
# ==================================================
def send_email_with_attachment(
    to_email: str,
    subject: str,
    body: str,
    file_bytes: bytes,
    filename: str,
) -> None:
    """
    Send an email with attachment using AWS SES raw email
    """

    msg = MIMEMultipart()
    msg["From"] = SES_SENDER_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject

    # Email body
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Attachment
    attachment = MIMEApplication(file_bytes)
    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=filename,
    )
    msg.attach(attachment)

    ses.send_raw_email(
        Source=SES_SENDER_EMAIL,
        Destinations=[to_email],
        RawMessage={"Data": msg.as_string()},
    )

# ==================================================
# HELPER: SEND EMAIL WITH S3 ATTACHMENT
# ==================================================
def send_email_with_s3_attachment(
    to_email: str,
    subject: str,
    body: str,
    s3_key: str,
    filename: str,
) -> None:
    """
    Fetch file from S3 and send it as an email attachment
    """
    file_bytes = get_file_bytes_from_s3(s3_key)
    send_email_with_attachment(
        to_email=to_email,
        subject=subject,
        body=body,
        file_bytes=file_bytes,
        filename=filename,
    )
