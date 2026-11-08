FROM python:3.13.9-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY dara_external ./dara_external
# Maritime mounts persistent /data. One process owns this SQLite task store.
RUN mkdir -p /data && chmod 700 /data
VOLUME ["/data"]
CMD ["python", "-m", "dara_external"]
