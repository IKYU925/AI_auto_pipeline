FROM python:3.12-slim

# git: LLM이 만든 패치를 git apply로 적용할 때 필요
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY knowledge ./knowledge
COPY alembic.ini .
COPY migrations ./migrations
# 3단계(K3s): shopapi Pod는 이 소스로 뜨고, 워커는 같은 소스를 검증 대상 저장소로 쓴다.
# 한 이미지에 넣어야 배포된 shopapi와 워커가 검증하는 코드가 어긋나지 않는다. compose는 이 경로를 볼륨으로 덮는다
COPY targets/shopapi /repo

# fastembed 모델 캐시 볼륨도 runner가 쓸 수 있게 미리 만들어 둔다(없으면 볼륨이 root 소유로 생성됨)
RUN useradd -m runner && mkdir -p /srv/artifacts /tmp/fastembed_cache && chown -R runner /srv /repo /tmp/fastembed_cache
USER runner

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
