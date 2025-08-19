FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY sentinel/ sentinel/
CMD ["python", "-m", "sentinel.cli", "run", "--sample", "3"]
