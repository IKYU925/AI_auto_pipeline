FROM python:3.12-slim

# git: LLM이 만든 패치를 git apply로 적용할 때 필요
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY knowledge ./knowledge

# fastembed 모델 캐시 볼륨도 runner가 쓸 수 있게 미리 만들어 둔다(없으면 볼륨이 root 소유로 생성됨)
RUN useradd -m runner && mkdir -p /srv/artifacts /tmp/fastembed_cache && chown -R runner /srv /tmp/fastembed_cache
USER runner

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
