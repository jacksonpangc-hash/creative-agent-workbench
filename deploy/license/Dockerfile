FROM python:3.12-slim
WORKDIR /app
# Only licensing code/dependencies; no media tooling, model adapters, or desktop files.
COPY src/creative_agent/__init__.py src/creative_agent/commercial_license.py src/creative_agent/license_service.py src/creative_agent/license_deployment.py ./creative_agent/
COPY deploy/license/console.html deploy/license/console.js deploy/license/console.css ./console/
ENV LICENSE_CONSOLE_DIR=/app/console
RUN pip install --no-cache-dir 'fastapi>=0.115,<1' 'uvicorn>=0.34,<1' 'httpx>=0.28,<1' 'psycopg[binary]>=3.2,<4' 'cryptography>=44,<45' && useradd --uid 10001 --no-create-home licensing
USER 10001
EXPOSE 8000
CMD ["uvicorn", "creative_agent.license_service:application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
