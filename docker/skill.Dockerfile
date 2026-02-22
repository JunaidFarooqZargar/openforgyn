FROM python:3.12-slim

RUN pip install --no-cache-dir pytest httpx

WORKDIR /skill

USER nobody

CMD ["python", "-m", "pytest", "-v", "."]
