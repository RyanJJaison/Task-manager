"""Background reminder checks for overrunning and unstarted tasks.

The job runs on a fixed interval and is deliberately conservative: it only
sends when a matching Reminder row does not already exist, and a send failure
for one task never prevents the others from being processed.
"""

from datetime import datetime

# A task is considered overrunning once it passes this multiple of its
# estimate.
OVERRUN_FACTOR = 1.3

CHECK_INTERVAL_MINUTES = 5

OVERRUN_TYPE = 'overrun'
START_TYPE = 'start'


def _send_email(mail, message_class, app, recipient, subject, body):
    """Send one email, reporting success as a boolean rather than raising."""
    if not recipient:
        return False, 'user has no email address'

    if not app.config.get('MAIL_SERVER'):
        return False, 'MAIL_SERVER is not configured'

    try:
        message = message_class(
            subject=subject,
            recipients=[recipient],
            body=body,
            sender=app.config.get('MAIL_DEFAULT_SENDER'),
        )
        mail.send(message)
        return True, None
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def _already_sent(reminder_model, todo_id, reminder_type):
    return (
        reminder_model.query
        .filter_by(todo_id=todo_id, reminder_type=reminder_type)
        .first()
        is not None
    )


def _record(db, reminder_model, todo_id, reminder_type):
    """Log a sent reminder.

    A unique constraint on (todo_id, reminder_type) means a concurrent run
    that already inserted the same row makes this fail, which is treated as
    "someone else sent it" rather than an error.
    """
    try:
        db.session.add(reminder_model(todo_id=todo_id, reminder_type=reminder_type))
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def check_overruns(app, db, models, mail, message_class):
    """Email the owner of any task that has run past its estimate."""
    todo_model = models['Todo']
    reminder_model = models['Reminder']
    sent = 0

    running = todo_model.query.filter(
        todo_model.status == 'in_progress',
        todo_model.actual_start.isnot(None),
        todo_model.estimated_minutes.isnot(None),
    ).all()

    for task in running:
        # Sum every session, not just the latest, so a task worked in several
        # stretches is measured on its total time.
        elapsed_minutes = task.elapsed_seconds() / 60
        threshold = task.estimated_minutes * OVERRUN_FACTOR

        if elapsed_minutes <= threshold:
            continue
        if _already_sent(reminder_model, task.id, OVERRUN_TYPE):
            continue

        owner = task.user
        ok, error = _send_email(
            mail, message_class, app,
            owner.email if owner else None,
            f'Task running long: {task.content[:60]}',
            f'"{task.content}" has been running for {elapsed_minutes:.0f} minutes '
            f'against an estimate of {task.estimated_minutes} minutes.',
        )

        if ok:
            if _record(db, reminder_model, task.id, OVERRUN_TYPE):
                sent += 1
        else:
            app.logger.warning('Overrun reminder for task %s not sent: %s', task.id, error)

    return sent


def check_missed_starts(app, db, models, mail, message_class):
    """Email the owner of any scheduled task whose start time has passed."""
    todo_model = models['Todo']
    reminder_model = models['Reminder']
    now = datetime.utcnow()
    sent = 0

    due = todo_model.query.filter(
        todo_model.status == 'scheduled',
        todo_model.scheduled_start.isnot(None),
        todo_model.scheduled_start <= now,
        todo_model.actual_start.is_(None),
    ).all()

    for task in due:
        if _already_sent(reminder_model, task.id, START_TYPE):
            continue

        owner = task.user
        ok, error = _send_email(
            mail, message_class, app,
            owner.email if owner else None,
            f'Scheduled task not started: {task.content[:60]}',
            f'"{task.content}" was scheduled to start at '
            f'{task.scheduled_start.strftime("%H:%M")} and has not been started.',
        )

        if ok:
            if _record(db, reminder_model, task.id, START_TYPE):
                sent += 1
        else:
            app.logger.warning('Start reminder for task %s not sent: %s', task.id, error)

    return sent


def run_checks(app, db, models, mail, message_class):
    """Run both checks inside an application context.

    Any unexpected error is logged and swallowed: an exception escaping here
    would stop the scheduler from running again.
    """
    with app.app_context():
        try:
            overruns = check_overruns(app, db, models, mail, message_class)
            missed = check_missed_starts(app, db, models, mail, message_class)
            if overruns or missed:
                app.logger.info(
                    'Reminders sent: %s overrun, %s missed start', overruns, missed
                )
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Reminder check failed: %s', exc)
