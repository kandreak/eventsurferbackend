from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event as CalendarEvent, Todo as CalendarTodo

from events.models import Event


def build_calendar(events: list[Event], timezone: str) -> Calendar:
    calendar = Calendar()
    calendar.add("prodid", "-//Events Aggregator//Milestone 0//EN")
    calendar.add("version", "2.0")
    calendar.add("X-WR-CALNAME", "Events")
    zone = ZoneInfo(timezone)

    for event in events:
        if event.date is None:
            calendar_event = CalendarTodo()
            calendar_event.add("dtstamp", datetime.now(dt_timezone.utc))
            calendar_event.add("status", "NEEDS-ACTION")
        else:
            calendar_event = CalendarEvent()
        calendar_event.add("uid", f"{uuid4()}@events-aggregator")
        calendar_event.add("summary", event.title)

        description_parts = [event.description] if event.description else []
        description_parts.append(f"Source: {event.source_name}")
        description_parts.append(f"Original post: {event.source_post_url}")

        location = ", ".join(
            part for part in (event.venue, event.address, event.city) if part
        )
        if event.date is None and location:
            description_parts.append(f"Location: {location}")
        calendar_event.add("description", "\n".join(description_parts))

        if event.date is not None and location:
            calendar_event.add("location", location)

        if event.date is not None:
            if event.start_time is None:
                calendar_event.add("dtstart", event.date)
                calendar_event.add("dtend", event.date + timedelta(days=1))
            else:
                start = _event_datetime(event.date, event.start_time, zone)
                calendar_event.add("dtstart", start)
                if event.end_time is not None:
                    end = _event_datetime(event.date, event.end_time, zone)
                    if end <= start:
                        end += timedelta(days=1)
                    calendar_event.add("dtend", end)

        calendar.add_component(calendar_event)

    return calendar


def _event_datetime(
    event_date: date, event_time: time, zone: ZoneInfo
) -> datetime:
    if event_time.tzinfo is not None:
        return datetime.combine(event_date, event_time)
    return datetime.combine(event_date, event_time, tzinfo=zone)


def serialize_calendar(events: list[Event], timezone: str) -> bytes:
    return build_calendar(events, timezone).to_ical()
