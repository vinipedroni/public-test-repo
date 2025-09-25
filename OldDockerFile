FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    software-properties-common \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY requirements.txt .

RUN pip3 install -r requirements.txt --no-cache-dir

EXPOSE 8501

RUN adduser -u 5678 --disabled-password --gecos "" appuser

WORKDIR /home/appuser/app
COPY inference_ui.py /home/appuser/app
RUN cat inference_ui.py

RUN ls -la
RUN chown -R appuser /home/appuser/app

USER appuser

ENTRYPOINT ["streamlit", "run", "inference_ui.py", "--server.port=8501", "--server.address=0.0.0.0"]