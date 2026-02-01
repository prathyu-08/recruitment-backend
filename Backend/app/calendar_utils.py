from datetime import datetime, timedelta

def generate_interview_ics(
    title: str,
    description: str,
    start_time: datetime,
    duration_minutes: int = 60,
):
    end_time = start_time + timedelta(minutes=duration_minutes)

    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Recruitment Portal//Interview Calendar//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
SUMMARY:{title}
DESCRIPTION:{description}
DTSTART:{start_time.strftime('%Y%m%dT%H%M%S')}
DTEND:{end_time.strftime('%Y%m%dT%H%M%S')}
END:VEVENT
END:VCALENDAR
"""