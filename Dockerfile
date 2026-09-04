# Stage 1: build the React frontend (web/) into web/dist
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: the FastAPI server, serving the built frontend at /
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-server.txt

COPY . .
COPY --from=web /web/dist ./web/dist

# Persisted job database lives here; mount a volume in production
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000
CMD ["uvicorn", "app:app", "--app-dir", "server", "--host", "0.0.0.0", "--port", "8000"]
