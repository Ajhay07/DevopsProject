FROM python:3.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as non-root user for security best practices
RUN useradd -u 8888 appuser && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["python", "main.py"]
CMD ["--continuous", "--mode", "recommend"]
