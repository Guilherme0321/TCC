FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd -m -u 1000 sandbox
WORKDIR /app

COPY --chown=sandbox:sandbox requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=sandbox:sandbox cli.py /app/
COPY --chown=sandbox:sandbox pipeline /app/pipeline

RUN mkdir -p /app/output /app/work && chown -R sandbox:sandbox /app
USER sandbox

ENTRYPOINT ["python", "cli.py"]
CMD ["--help"]
