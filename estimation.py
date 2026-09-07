"""Task effort estimation backed by the Groq chat completions API.

Every function here is written so that a failure of the remote API degrades to
a usable default rather than raising. Task creation must never depend on the
network being available.
"""

import json
import os

import requests

GROQ_ENDPOINT = 'https://api.groq.com/openai/v1/chat/completions'

# Production-tier Groq model that supports JSON response formats. Overridable
# so the model can be changed without a code edit. Confirm a replacement is
# listed by GET /openai/v1/models for your key before switching: access to a
# given model varies per account.
DEFAULT_MODEL = 'openai/gpt-oss-120b'

# Used whenever the API is unavailable, misconfigured, or returns something
# unusable.
FALLBACK_MINUTES = 30

# Guards against a malformed response proposing an absurd duration. The upper
# bound is two weeks of working time: large multi-day tasks are legitimate, so
# this only rejects values that indicate a parsing problem.
MIN_MINUTES = 1
MAX_MINUTES = 60 * 24 * 14

# Observed live latency ranges from about 2s to 11s, so this leaves headroom
# for the slow tail without holding a request open indefinitely.
REQUEST_TIMEOUT_SECONDS = 30

SYSTEM_PROMPT = (
    'You estimate how long a single task will take a person to finish. '
    'Reply with JSON only, matching this shape exactly: '
    '{"estimated_minutes": <integer>, "subtasks": [<string>, ...]}. '
    'estimated_minutes is the whole-minute estimate for the entire task. '
    'subtasks is an ordered list of at most six short concrete steps. '
    'Add no commentary and no fields beyond those two.'
)


def _build_user_prompt(content, accuracy_ratio=None, examples=None):
    """Assemble the user turn, including calibration when it is known.

    examples is an optional list of (description, estimated_minutes,
    actual_minutes) triples from the user's finished tasks.
    """
    lines = [f'Task: {content}']

    if accuracy_ratio is not None:
        lines.append(
            f'Calibration: this user historically takes {accuracy_ratio:.2f}x '
            'their estimated time on completed tasks. Adjust the estimate to '
            'reflect that tendency.'
        )

    if examples:
        lines.append("Recent finished tasks by this user, for reference:")
        for description, estimated, actual in examples:
            lines.append(
                f'- "{description}": estimated {estimated} min, '
                f'actually took {actual:.0f} min'
            )

    lines.append('Respond with JSON only.')
    return '\n'.join(lines)


def _parse_response(raw_text):
    """Pull the estimate and subtasks out of a model reply.

    Tolerates code fences and surrounding prose, because json_object mode
    constrains syntax but not the wrapper the model chooses.
    """
    text = (raw_text or '').strip()

    if text.startswith('```'):
        # Drop a leading ```json / ``` fence and anything after the closer.
        text = text.split('```')[1] if text.count('```') >= 2 else text.strip('`')
        if text.lstrip().lower().startswith('json'):
            text = text.lstrip()[4:]

    text = text.strip()

    if not text.startswith('{'):
        # Fall back to the outermost brace pair if the model added prose.
        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end <= start:
            raise ValueError('no JSON object found in response')
        text = text[start:end + 1]

    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError('response JSON was not an object')

    minutes = int(float(payload['estimated_minutes']))
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValueError(f'estimated_minutes out of range: {minutes}')

    raw_subtasks = payload.get('subtasks') or []
    if not isinstance(raw_subtasks, list):
        raise ValueError('subtasks was not a list')

    subtasks = [str(s).strip() for s in raw_subtasks if str(s).strip()]

    return minutes, subtasks[:6]


def estimate_task(content, accuracy_ratio=None, examples=None):
    """Estimate a task's duration.

    Returns (estimated_minutes, subtasks, error). On any failure the estimate
    is FALLBACK_MINUTES, subtasks is empty, and error describes what went
    wrong. This function does not raise.
    """
    api_key = os.environ.get('GROQ_API_KEY')
    if not api_key:
        return FALLBACK_MINUTES, [], 'GROQ_API_KEY is not set'

    body = {
        'model': os.environ.get('GROQ_MODEL', DEFAULT_MODEL),
        'response_format': {'type': 'json_object'},
        'temperature': 0.2,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {
                'role': 'user',
                'content': _build_user_prompt(content, accuracy_ratio, examples),
            },
        ],
    }

    # requests is used rather than urllib because Groq sits behind Cloudflare,
    # which rejects urllib's TLS fingerprint with "error code: 1010" (HTTP 403)
    # no matter what headers are sent.
    try:
        response = requests.post(
            GROQ_ENDPOINT,
            headers={'Authorization': f'Bearer {api_key}'},
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            # Include the body for diagnosis; it never contains the API key.
            return (
                FALLBACK_MINUTES,
                [],
                f'Groq API returned HTTP {response.status_code}: {response.text[:200]}',
            )

        raw_text = response.json()['choices'][0]['message']['content']
        minutes, subtasks = _parse_response(raw_text)
        return minutes, subtasks, None
    except Exception as exc:
        return FALLBACK_MINUTES, [], f'{type(exc).__name__}: {exc}'
