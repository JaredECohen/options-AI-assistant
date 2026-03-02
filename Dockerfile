FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json /frontend/package.json
RUN npm install
COPY frontend /frontend
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md /app/
RUN uv sync --no-dev
COPY . /app
COPY --from=frontend /frontend/dist /app/frontend/dist
ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
