FROM python:3.12-slim

WORKDIR /app
COPY requirements-job.txt .
RUN pip install --no-cache-dir -r requirements-job.txt
COPY src ./src
COPY data/snapshot ./data/snapshot
COPY data/manifest.json ./data/manifest.json
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "road_toll.cli", "pipeline"]
