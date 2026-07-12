FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY app/requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app/main.py ./main.py

RUN addgroup --system --gid 10001 app && \
    adduser --system --uid 10001 --ingroup app app && \
    chown -R app:app /app

USER 10001:10001
EXPOSE 5000
CMD ["python", "main.py"]
