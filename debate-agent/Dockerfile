# livekit-agents, google-genai, Pillow and friends all publish 3.12 wheels.
# If a future dependency has no 3.12 wheel, fall back to python:3.11-slim —
# nothing in this service requires 3.12 specifically.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY agent.py config.py logging_config.py session.py transcript.py ./
COPY pipeline ./pipeline
COPY moderation ./moderation
COPY scripts ./scripts

RUN useradd --create-home appuser
USER appuser

# The worker dials OUT to LiveKit and receives jobs over that connection —
# there is nothing to expose and no public domain to attach.
# `start` is production mode (`dev` adds hot-reload and dev defaults).
CMD ["python", "agent.py", "start"]
