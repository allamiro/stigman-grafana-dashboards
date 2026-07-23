# STIG Manager posture exporter
#   docker build -t stigman-exporter:1.0.0 metrics-history/exporter
#   docker run -d --name stigman-exporter -p 9633:9633 \
#     -e STIGMAN_API_URL=http://host.docker.internal:54000/api \
#     -e KEYCLOAK_TOKEN_URL=http://host.docker.internal:8180/realms/stigman/protocol/openid-connect/token \
#     -e OIDC_CLIENT_ID=nexus-reporter \
#     -e OIDC_CLIENT_SECRET=... \
#     stigman-exporter:1.0.0
FROM python:3.12.13-slim

RUN adduser --system --no-create-home --group exporter
WORKDIR /app
COPY --chmod=0644 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chmod=0764 stigman_exporter.py .
RUN chmod 0764 /app/stigman_exporter.py /app/requirements.txt

USER exporter
EXPOSE 9633
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD \
  python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('EXPORTER_PORT','9633')+'/metrics',timeout=4)"
CMD ["python", "stigman_exporter.py"]
