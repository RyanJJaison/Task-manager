"""Day scheduling for pending tasks, backed by the Groq chat completions API.

As with estimation, a failure of the remote API degrades to an empty schedule
rather than raising: the caller reports that nothing could be scheduled and
leaves the tasks untouched.
"""

import json
import os
from datetime import datetime, timedelta

import requests

from estimation import GROQ_ENDPOINT, DEFAULT_MODEL, REQUEST_TIMEOUT_SECONDS

# Minutes of breathing room the model is asked to leave between tasks.
BUFFER_MINUTES = 10

SYSTEM_PROMPT = (
    'You schedule a working day. Fit the given tasks into the available '
    'working hours, in a sensible order, respecting deadlines and treating a '
    'lower priority number as more urgent. Leave a short gap of about '
    f'{BUFFER_MINUTES} minutes between consecutive tasks. Never overlap two '
    'tasks. Never schedule outside the stated working window. Omit any task '
    'that does not fit rather than shortening it. Reply with JSON only, in '
    'exactly this shape: '
    '{"schedule": [{"todo_id": <int>, "start": "<ISO 8601>", "end": "<ISO 8601>"}]}. '
    'Add no commentary and no other fields.'
)


def _parse_iso(value):
    """Read an ISO 8601 timestamp, tolerating a trailing Z."""
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1]
    parsed = datetime.fromisoformat(text)
    # Compare everything in naive UTC, matching how the models store times.
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _build_user_prompt(tasks, work_start, work_end):
    lines = [
        f'Working window: {work_start.isoformat()} to {work_end.isoformat()}.',
        'Tasks to schedule:',
    ]

    for task in tasks:
        parts = [
            f'- todo_id={task.id}',
            f'estimated_minutes={task.estimated_minutes or 30}',
            f'priority={task.priority}',
        ]
        if task.deadline:
            parts.append(f'deadline={task.deadline.isoformat()}')
        parts.append(f'description="{task.content}"')
        lines.append(' '.join(parts))

    lines.append('Respond with JSON only.')
    return '\n'.join(lines)


def _validate_block(block, allowed_ids, work_start, work_end):
    """Turn one raw schedule entry into (todo_id, start, end), or None.

    Rejects unknown ids, reversed intervals and anything falling outside the
    working window, since the model's output is not trustworthy by itself.
    """
    try:
        todo_id = int(block['todo_id'])
        start = _parse_iso(block['start'])
        end = _parse_iso(block['end'])
    except (KeyError, TypeError, ValueError):
        return None

    if todo_id not in allowed_ids:
        return None
    if end <= start:
        return None
    if start < work_start or end > work_end:
        return None

    return todo_id, start, end


def _drop_overlaps(blocks):
    """Keep the earliest non-overlapping run of blocks.

    The prompt forbids overlaps, but a returned schedule that contains them
    would double-book the user, so they are discarded here as well.
    """
    kept = []
    last_end = None
    for todo_id, start, end in sorted(blocks, key=lambda b: b[1]):
        if last_end is not None and start < last_end:
            continue
        kept.append((todo_id, start, end))
        last_end = end
    return kept


def working_window(user, day=None):
    """The user's working window for a given day, as naive UTC datetimes."""
    base = day or datetime.utcnow()
    start = base.replace(hour=user.work_start_hour, minute=0, second=0, microsecond=0)
    end = base.replace(hour=user.work_end_hour, minute=0, second=0, microsecond=0)

    if end <= start:
        # A window that does not span forwards cannot hold anything.
        return start, start

    # Never schedule into the past when planning the current day.
    now = datetime.utcnow()
    if start < now < end:
        start = now.replace(second=0, microsecond=0) + timedelta(minutes=1)

    return start, end


def build_schedule(tasks, work_start, work_end):
    """Ask the API for a day plan.

    Returns (blocks, error) where blocks is a list of validated
    (todo_id, start, end) tuples. On failure blocks is empty and error
    describes the problem. This function does not raise.
    """
    if not tasks:
        return [], None

    api_key = os.environ.get('GROQ_API_KEY')
    if not api_key:
        return [], 'GROQ_API_KEY is not set'

    body = {
        'model': os.environ.get('GROQ_MODEL', DEFAULT_MODEL),
        'response_format': {'type': 'json_object'},
        'temperature': 0.2,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': _build_user_prompt(tasks, work_start, work_end)},
        ],
    }

    try:
        response = requests.post(
            GROQ_ENDPOINT,
            headers={'Authorization': f'Bearer {api_key}'},
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            return [], f'Groq API returned HTTP {response.status_code}: {response.text[:200]}'

        text = response.json()['choices'][0]['message']['content'].strip()

        # json_object mode constrains syntax but not the surrounding wrapper.
        if not text.startswith('{'):
            start_brace, end_brace = text.find('{'), text.rfind('}')
            if start_brace == -1 or end_brace <= start_brace:
                raise ValueError('no JSON object found in response')
            text = text[start_brace:end_brace + 1]

        payload = json.loads(text)
        raw_blocks = payload.get('schedule')
        if not isinstance(raw_blocks, list):
            raise ValueError('schedule was not a list')

        allowed_ids = {task.id for task in tasks}
        validated = []
        for block in raw_blocks:
            checked = _validate_block(block, allowed_ids, work_start, work_end)
            if checked:
                validated.append(checked)

        return _drop_overlaps(validated), None
    except Exception as exc:
        return [], f'{type(exc).__name__}: {exc}'
