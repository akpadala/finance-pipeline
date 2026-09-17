# Single image used by both the `pipeline` and `dashboard` services in
# docker-compose.yml -- which command runs is decided per-service there,
# not here, so we don't need two Dockerfiles for two steps of one project.
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (separate layer) so `docker compose build`
# doesn't reinstall every package just because a .py file changed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# No CMD here on purpose -- docker-compose.yml's `command:` for each
# service is what actually runs (generate -> ingest -> dbt for `pipeline`,
# streamlit for `dashboard`). Keeps this Dockerfile a plain "here's the
# environment" image, reusable by either service.
