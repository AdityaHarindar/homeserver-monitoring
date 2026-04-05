FROM python:3.12-alpine
WORKDIR /app
COPY exporter.py .
# No external dependencies — uses only stdlib (urllib, json, http.server)
CMD ["python", "-u", "exporter.py"]
