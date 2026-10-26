FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY ui/ ./ui/
COPY vite.config.ts tsconfig*.json index.html ./
RUN npm run build

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock LICENSE README.md ./
COPY src/ ./src/
RUN uv sync --locked --extra team --no-dev && useradd --uid 10001 --create-home creative && mkdir /data && chown creative /data
COPY --from=frontend /build/dist /app/ui/dist
USER 10001:10001
ENV PATH="/app/.venv/bin:$PATH" TEAM_DATA_DIR=/data TEAM_UI_DIR=/app/ui/dist
ENTRYPOINT ["python", "-m", "creative_agent.team_cli"]
CMD ["serve"]
