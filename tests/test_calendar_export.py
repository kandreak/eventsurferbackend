from datetime import date, time

from icalendar import Calendar

from events.calendar_export import serialize_calendar
from events.models import Event


def _event(**overrides) -> Event:
    values = {
        "title": "Community workshop",
        "description": "A hands-on event.",
        "date": date(2026, 12, 12),
        "start_time": None,
        "end_time": None,
        "venue": "Town Hall",
        "address": "1 Main Street",
        "city": "Rome",
        "participants": [],
        "confidence": 0.9,
        "needs_review": False,
        "date_is_explicit": True,
        "time_is_explicit": False,
        "source_post_url": "https://www.facebook.com/example/posts/1",
        "source_name": "Example Organization",
    }
    values.update(overrides)
    return Event(**values)


def test_calendar_exports_dated_events_and_undated_tasks():
    data = serialize_calendar(
        [
            _event(),
            _event(
                title="Evening lecture",
                start_time=time(18, 30),
                end_time=time(20, 0),
                time_is_explicit=True,
            ),
            _event(title="Unscheduled announcement", date=None, needs_review=True),
        ],
        "Europe/Rome",
    )
    calendar = Calendar.from_ical(data)
    exported = calendar.walk("VEVENT")
    undated = calendar.walk("VTODO")

    assert len(exported) == 2
    assert len(undated) == 1
    assert exported[0]["DTSTART"].dt == date(2026, 12, 12)
    assert exported[0]["DTEND"].dt == date(2026, 12, 13)
    assert exported[1]["DTSTART"].dt.hour == 18
    assert exported[1]["DTSTART"].dt.tzinfo is not None
    assert "https://www.facebook.com/example/posts/1" in exported[0]["DESCRIPTION"]
    assert undated[0]["SUMMARY"] == "Unscheduled announcement"
    assert "DTSTART" not in undated[0]
    assert "DUE" not in undated[0]
    assert undated[0]["STATUS"] == "NEEDS-ACTION"
    assert "https://www.facebook.com/example/posts/1" in undated[0]["DESCRIPTION"]
    assert "Location: Town Hall, 1 Main Street, Rome" in undated[0]["DESCRIPTION"]
