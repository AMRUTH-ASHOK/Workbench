from datetime import datetime
from zoneinfo import ZoneInfo

WINDOWS = [(13, 30), (16, 15), (19, 0), (22, 0)]


def latest_window(at=None):
    at = at or datetime.now(ZoneInfo("Asia/Kolkata"))
    at = at.astimezone(ZoneInfo("Asia/Kolkata"))
    if at.weekday() > 4 or (at.hour, at.minute) < WINDOWS[0] or (at.hour, at.minute) > (22, 30):
        return None
    due = [w for w in WINDOWS if w <= (at.hour, at.minute)]
    return at.strftime("%Y-%m-%d") + "T%02d:%02d" % due[-1]
