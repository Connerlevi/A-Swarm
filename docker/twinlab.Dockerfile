FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY twinlab/ twinlab/
CMD ["python", "-m", "twinlab.cli", "replay", "incident-a", "incident-b"]
