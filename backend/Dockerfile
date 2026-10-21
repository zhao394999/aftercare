FROM python:3.14.7-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir -r backend/requirements.lock && groupadd --gid 10001 aftercare && useradd --uid 10001 --gid aftercare --no-create-home aftercare
COPY backend /app/backend
COPY deployment/postgres/grants.sql /app/deployment/postgres/grants.sql
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","2","--no-access-log"]
