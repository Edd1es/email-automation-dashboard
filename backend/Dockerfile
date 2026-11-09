FROM python:3.13-slim
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
ENV DATABASE_PATH=/data/app.db
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "300", "wsgi:app"]
