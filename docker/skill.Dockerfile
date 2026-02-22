FROM python:3.12-slim

RUN pip install --no-cache-dir pytest pytest-asyncio httpx

WORKDIR /skill

USER nobody

CMD ["python", "-m", "pytest", "-v", "."]
