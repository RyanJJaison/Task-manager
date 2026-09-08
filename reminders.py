"""Detection of tasks that need a reminder.

The checks are pure queries plus a Reminder insert, so the same logic serves
whatever delivers the reminder. Delivery is currently a browser notification
raised by the client after polling the check endpoint.
"""

from datetime import datetime, timedelta

# A task is considered overrunning once it passes this multiple of its
# estimate.
OVERRUN_FACTOR = 1.3

# How often the client polls, in seconds. The start check looks back over a
# slightly longer window so a task cannot slip between two polls.
POLL_INTERVAL_SECONDS = 60
START_LOOKBACK_SECONDS = POLL_INTERVAL_SECONDS * 5

OVERRUN_TYPE = 'overrun'
START_TYPE = 'start'
STALE_TYPE = 'stale'


def _claim(db, reminder_model, todo_id, reminder_type):
    """Record a reminder, returning True only if this caller recorded it.

    The unique constraint on (todo_id, reminder_type) means a concurrent poll
    that inserted the same row first causes this to fail, in which case the
    reminder belongs to that other caller and must not be delivered twice.
    """
    if (
        reminder_model.query
        .filter_by(todo_id=todo_id, reminder_type=reminder_type)
        .first()
    ):
        return False

    try:
        db.session.add(reminder_model(todo_id=todo_id, reminder_type=reminder_type))
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def find_overruns(db, todo_model, reminder_model, user_id):
    """Tasks in progress that have passed their estimate.

    Each returned task has its reminder recorded, so a later poll will not
    report it again.
    """
    candidates = todo_model.query.filter(
        todo_model.user_id == user_id,
        todo_model.status == 'in_progress',
        todo_model.actual_start.isnot(None),
        todo_model.estimated_minutes.isnot(None),
    ).all()

    found = []
    for task in candidates:
        # Sum every session, not just the latest, so a task worked in several
        # stretches is measured on its total time.
        elapsed_minutes = task.elapsed_seconds() / 60
        if elapsed_minutes <= task.estimated_minutes * OVERRUN_FACTOR:
            continue
        if not _claim(db, reminder_model, task.id, OVERRUN_TYPE):
            continue

        found.append({
            'todo_id': task.id,
            'title': task.content,
            'overrun_minutes': int(elapsed_minutes - task.estimated_minutes),
        })

    return found


def find_due_to_start(db, todo_model, reminder_model, user_id, now=None):
    """Scheduled tasks whose start time has passed without being started."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(seconds=START_LOOKBACK_SECONDS)

    candidates = todo_model.query.filter(
        todo_model.user_id == user_id,
        todo_model.status == 'scheduled',
        todo_model.scheduled_start.isnot(None),
        todo_model.scheduled_start <= now,
        todo_model.scheduled_start >= cutoff,
        todo_model.actual_start.is_(None),
    ).all()

    found = []
    for task in candidates:
        if not _claim(db, reminder_model, task.id, START_TYPE):
            continue
        found.append({'todo_id': task.id, 'title': task.content})

    return found


def find_stale(db, todo_model, reminder_model, user_id, now=None):
    """Tasks never started that have sat longer than their estimate.

    This complements the overrun check, which measures time actually worked.
    A task can only overrun once someone starts it, so a task left untouched
    would otherwise never raise anything at all.
    """
    now = now or datetime.utcnow()

    candidates = todo_model.query.filter(
        todo_model.user_id == user_id,
        todo_model.status.in_(('pending', 'scheduled')),
        todo_model.actual_start.is_(None),
        todo_model.estimated_minutes.isnot(None),
        todo_model.date_created.isnot(None),
    ).all()

    found = []
    for task in candidates:
        age_minutes = (now - task.date_created).total_seconds() / 60
        if age_minutes <= task.estimated_minutes * OVERRUN_FACTOR:
            continue
        if not _claim(db, reminder_model, task.id, STALE_TYPE):
            continue

        found.append({
            'todo_id': task.id,
            'title': task.content,
            'age_minutes': int(age_minutes),
        })

    return found
