FROM python:3.12-slim

RUN pip install --no-cache-dir pytest httpx

WORKDIR /skill

USER nobody

ENTRYPOINT ["python", "-m", "pytest", "-v"]
