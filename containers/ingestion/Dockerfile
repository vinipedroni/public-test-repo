FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip3 install -r requirements.txt --no-cache-dir

EXPOSE 8501

RUN adduser -u 5678 --disabled-password --gecos "" appuser

WORKDIR /home/appuser/app
RUN ls -la
RUN pwd
COPY image_ingestion.py /home/appuser/app
RUN cat image_ingestion.py

RUN chown -R appuser /home/appuser/app

USER appuser

ENTRYPOINT ["python3.12", "image_ingestion.py"]