FROM python:3.12-slim

# git: LLM이 만든 패치를 git apply로 적용할 때 필요
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 임베딩 모델을 이미지에 넣는다. 런타임에 받으면 워커 Pod가 새로 뜰 때마다 첫 장애가 약 14초 늦어지고(3-3 측정),
# 처리 시간 측정에 다운로드 시간이 섞인다. 모델 이름은 app/config.py의 embedding_model 기본값과 같아야 한다
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')" \
 && chmod -R a+rX /opt/fastembed
ENV HF_HUB_OFFLINE=1

COPY app ./app
COPY scripts ./scripts
COPY knowledge ./knowledge
COPY alembic.ini .
COPY migrations ./migrations
# 3단계(K3s): shopapi Pod는 이 소스로 뜨고, 워커는 같은 소스를 검증 대상 저장소로 쓴다.
# 한 이미지에 넣어야 배포된 shopapi와 워커가 검증하는 코드가 어긋나지 않는다. compose는 이 경로를 볼륨으로 덮는다
COPY targets/shopapi /repo

RUN useradd -m runner && mkdir -p /srv/artifacts && chown -R runner /srv /repo
USER runner

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
