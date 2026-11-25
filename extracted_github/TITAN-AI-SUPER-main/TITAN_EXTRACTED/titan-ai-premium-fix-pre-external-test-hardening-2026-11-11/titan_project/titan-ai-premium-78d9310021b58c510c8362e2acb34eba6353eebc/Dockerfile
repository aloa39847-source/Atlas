# Build the mobile-first PWA bundle.
FROM node:24-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install --no-audit
COPY frontend/ ./
RUN npm run build

# Production API + same-origin frontend.
FROM python:3.12-slim
WORKDIR /app
RUN mkdir -p /data
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend/app /app/backend/app
COPY --from=frontend-build /frontend/dist /app/frontend/dist
ENV PYTHONPATH=/app/backend
ENV TITAN_APP_ENV=production
ENV TITAN_AUTH_REQUIRED=true
ENV TITAN_DATA_DIR=/data
ENV TITAN_DATABASE_URL=sqlite:////data/titan.db
EXPOSE 8000
CMD ["uvicorn", "app.star_app:app", "--host", "0.0.0.0", "--port", "8000"]
VOLUME ["/data"]
