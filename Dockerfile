FROM python:3.12-slim

# git: LLM이 만든 패치를 git apply로 적용할 때 필요
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY knowledge ./knowledge

RUN useradd -m runner && mkdir -p /srv/artifacts && chown -R runner /srv
USER runner

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
