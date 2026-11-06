FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JOBS_CONTAINER=1
WORKDIR /app

COPY requirements.txt requirements-mcp.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-mcp.txt \
    && groupadd --gid 10001 jobs \
    && useradd --uid 10001 --gid jobs --no-create-home jobs \
    && mkdir -p /app/config /app/data /app/out \
    && chown jobs:jobs /app/config /app/data /app/out

COPY jobs/ ./jobs/
COPY plugins/ ./plugins/
COPY config.example.yaml rules.yaml skills.yaml LICENSE ./
COPY tools/docker-entrypoint.py /usr/local/bin/jobs-container

USER 10001:10001
EXPOSE 8090 8091 8092
ENTRYPOINT ["python", "/usr/local/bin/jobs-container"]
CMD ["panel", "--host", "0.0.0.0", "--port", "8090"]
