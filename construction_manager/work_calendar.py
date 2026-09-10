from __future__ import annotations

from datetime import date, datetime, time, timedelta


class WorkCalendar:
    """Mon-Fri calendar with an 8-hour day (08-12 and 13-17)."""

    HOURS_PER_DAY = 8.0
    SESSIONS = ((time(8), time(12)), (time(13), time(17)))

    def __init__(
        self,
        exceptions: dict[str, bool] | None = None,
        weekend_working: bool = False,
        ignore_noise_restrictions: bool = False,
        sessions: tuple[tuple[time, time], ...] | None = None,
    ) -> None:
        self.exceptions = {date.fromisoformat(day): value for day, value in (exceptions or {}).items()}
        self.weekend_working = bool(weekend_working)
        self.ignore_noise_restrictions = bool(ignore_noise_restrictions)
        selected_sessions = tuple(sessions or self.SESSIONS)
        if not selected_sessions or any(finish <= start for start, finish in selected_sessions):
            raise ValueError("每天至少需要一个有效工作时段。")
        if any(
            selected_sessions[index][1] > selected_sessions[index + 1][0]
            for index in range(len(selected_sessions) - 1)
        ):
            raise ValueError("每日工作时段不能重叠。")
        self.SESSIONS = selected_sessions
        self.HOURS_PER_DAY = sum(
            (finish.hour * 60 + finish.minute - start.hour * 60 - start.minute) / 60
            for start, finish in self.SESSIONS
        )

    @staticmethod
    def parse_sessions(value: str | None) -> tuple[tuple[time, time], ...]:
        raw_value = str(value or "08:00-12:00,13:00-17:00")
        sessions: list[tuple[time, time]] = []
        try:
            for raw_session in raw_value.replace("；", ",").replace("，", ",").split(","):
                if not raw_session.strip():
                    continue
                raw_start, raw_finish = raw_session.strip().split("-", 1)
                sessions.append(
                    (
                        time.fromisoformat(raw_start.strip()),
                        time.fromisoformat(raw_finish.strip()),
                    )
                )
        except ValueError as error:
            raise ValueError("工作时段格式应为 08:00-12:00,13:00-17:00。") from error
        sessions.sort(key=lambda item: item[0])
        if not sessions:
            raise ValueError("每天至少需要一个工作时段。")
        return tuple(sessions)

    @classmethod
    def from_settings(
        cls, settings: dict, exceptions: dict[str, bool] | None = None
    ) -> "WorkCalendar":
        project_exceptions = dict(exceptions or {})
        if settings.get("overtime_holiday"):
            project_exceptions = {
                day: working
                for day, working in project_exceptions.items()
                if bool(working)
            }
        sessions = list(cls.parse_sessions(settings.get("work_sessions")))
        if settings.get("overtime_lunch") and len(sessions) > 1:
            sessions = [(sessions[0][0], sessions[-1][1])]
        if settings.get("overtime_night"):
            night_finish = time(22)
            if sessions[-1][1] < night_finish:
                sessions.append((sessions[-1][1], night_finish))
        return cls(
            project_exceptions,
            weekend_working=bool(settings.get("weekend_working", 0)),
            ignore_noise_restrictions=bool(
                settings.get("ignore_noise_restrictions", 0)
            ),
            sessions=tuple(sessions),
        )

    def limited_daily_hours(self, maximum_hours: float) -> "WorkCalendar":
        remaining = max(0.01, float(maximum_hours))
        sessions: list[tuple[time, time]] = []
        for start, finish in self.SESSIONS:
            available = (
                finish.hour * 60 + finish.minute - start.hour * 60 - start.minute
            ) / 60
            if remaining <= 1e-9:
                break
            used = min(available, remaining)
            sessions.append(
                (
                    start,
                    (datetime.combine(date.min, start) + timedelta(hours=used)).time(),
                )
            )
            remaining -= used
        return WorkCalendar(
            {day.isoformat(): working for day, working in self.exceptions.items()},
            weekend_working=self.weekend_working,
            ignore_noise_restrictions=self.ignore_noise_restrictions,
            sessions=tuple(sessions),
        )

    def is_working_day(self, day: date) -> bool:
        return self.exceptions.get(day, self.weekend_working or day.weekday() < 5)

    def next_working_day(self, day: date, include: bool = True) -> date:
        current = day if include else day + timedelta(days=1)
        while not self.is_working_day(current):
            current += timedelta(days=1)
        return current

    def previous_working_day(self, day: date, include: bool = True) -> date:
        current = day if include else day - timedelta(days=1)
        while not self.is_working_day(current):
            current -= timedelta(days=1)
        return current

    def next_work_time(self, value: datetime) -> datetime:
        current = value
        while True:
            if not self.is_working_day(current.date()):
                current = datetime.combine(self.next_working_day(current.date(), False), self.SESSIONS[0][0])
                continue
            for start, finish in self.SESSIONS:
                session_start = datetime.combine(current.date(), start)
                session_finish = datetime.combine(current.date(), finish)
                if current < session_start:
                    return session_start
                if session_start <= current < session_finish:
                    return current
            current = datetime.combine(self.next_working_day(current.date(), False), self.SESSIONS[0][0])

    def add_work_hours(self, value: datetime, hours: float) -> datetime:
        if hours < 0:
            return self.subtract_work_hours(value, -hours)
        current = self.next_work_time(value)
        remaining = float(hours)
        if remaining <= 1e-9:
            return current
        while remaining > 1e-9:
            for start, finish in self.SESSIONS:
                session_start = datetime.combine(current.date(), start)
                session_finish = datetime.combine(current.date(), finish)
                if session_start <= current < session_finish:
                    available = (session_finish - current).total_seconds() / 3600
                    used = min(available, remaining)
                    current += timedelta(hours=used)
                    remaining -= used
                    break
            if remaining > 1e-9:
                current = self.next_work_time(current)
        return current

    def subtract_work_hours(self, value: datetime, hours: float) -> datetime:
        current, remaining = value, float(hours)
        while remaining > 1e-9:
            day = current.date()
            if not self.is_working_day(day):
                day = self.previous_working_day(day, False)
                current = datetime.combine(day, self.SESSIONS[-1][1])
                continue
            used_session = False
            for start, finish in reversed(self.SESSIONS):
                session_start = datetime.combine(day, start)
                endpoint = min(current, datetime.combine(day, finish))
                if endpoint > session_start:
                    available = (endpoint - session_start).total_seconds() / 3600
                    used = min(available, remaining)
                    current = endpoint - timedelta(hours=used)
                    remaining -= used
                    used_session = True
                    break
            if remaining > 1e-9 and (not used_session or current <= datetime.combine(day, self.SESSIONS[0][0])):
                day = self.previous_working_day(day, False)
                current = datetime.combine(day, self.SESSIONS[-1][1])
        return current

    def finish_from_start_time(self, start: datetime, duration_days: float) -> tuple[datetime, datetime]:
        actual_start = self.next_work_time(start)
        return actual_start, self.add_work_hours(actual_start, duration_days * self.HOURS_PER_DAY)

    def start_for_finish_time(self, finish: datetime, duration_days: float) -> datetime:
        return self.subtract_work_hours(finish, duration_days * self.HOURS_PER_DAY)

    def finish_from_start(self, start: date, duration: int) -> tuple[date, date]:
        actual_start = self.next_working_day(start)
        finish = actual_start
        for _ in range(duration - 1):
            finish = self.next_working_day(finish, False)
        return actual_start, finish

    def start_for_finish(self, finish: date, duration: int) -> date:
        current = self.previous_working_day(finish)
        for _ in range(duration - 1):
            current = self.previous_working_day(current, False)
        return current

    def shift_working_days(self, day: date, days: int) -> date:
        current = self.next_working_day(day)
        for _ in range(days):
            current = self.next_working_day(current, False)
        return current

    def working_days_between(self, start: date, finish: date) -> int:
        count, current = 0, start
        while current <= finish:
            count += int(self.is_working_day(current))
            current += timedelta(days=1)
        return count

    @staticmethod
    def natural_days_between(start: date, finish: date) -> int:
        """Calendar-day span for contract durations, counting both end dates."""
        if finish < start:
            return 0
        return (finish - start).days + 1

    def work_hours_between(self, start: datetime, finish: datetime) -> float:
        """Working time in an elapsed datetime span, including idle work periods."""
        return sum(
            (interval_finish - interval_start).total_seconds()
            for interval_start, interval_finish in self.work_intervals_between(start, finish)
        ) / 3600

    def work_intervals_between(
        self, start: datetime, finish: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Return productive calendar sessions intersecting an elapsed time span."""
        if finish <= start:
            return []
        intervals: list[tuple[datetime, datetime]] = []
        current_day = start.date()
        while current_day <= finish.date():
            if self.is_working_day(current_day):
                for session_start, session_finish in self.SESSIONS:
                    session_start_at = datetime.combine(current_day, session_start)
                    session_finish_at = datetime.combine(current_day, session_finish)
                    overlap_start = max(start, session_start_at)
                    overlap_finish = min(finish, session_finish_at)
                    if overlap_finish > overlap_start:
                        intervals.append((overlap_start, overlap_finish))
            current_day += timedelta(days=1)
        return intervals
