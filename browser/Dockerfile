FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp/browser
RUN apt-get update && apt-get install -y --no-install-recommends chromium ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir playwright==1.64.1 \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin browser
WORKDIR /opt/browser
COPY agent.py events.py ./
USER 10001:10001
ENTRYPOINT ["python", "-u", "/opt/browser/agent.py"]
