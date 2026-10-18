"""Task timestamps and elapsed time, stored in UTC and displayed in Bangkok."""

from datetime import datetime, timedelta, timezone
import time
import uuid


BANGKOK = timezone(timedelta(hours=7))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def run_id():
    return datetime.now(BANGKOK).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def file_timestamp(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def format_timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(BANGKOK).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return "—"


def format_duration(seconds):
    seconds = max(0, int(float(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ชม.")
    if minutes:
        parts.append(f"{minutes} นาที")
    parts.append(f"{seconds} วินาที")
    return " ".join(parts)


class TaskTimer:
    def __init__(self):
        self.started_at = utc_now()
        self.start_tick = time.monotonic()
        self.finished_at = None
        self.elapsed_seconds = None

    def snapshot(self, finished=False):
        if finished and self.finished_at is None:
            self.finished_at = utc_now()
            self.elapsed_seconds = round(time.monotonic() - self.start_tick, 3)
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": (self.elapsed_seconds if self.finished_at
                                else round(time.monotonic() - self.start_tick, 3)),
        }
