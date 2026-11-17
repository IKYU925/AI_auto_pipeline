# 4단계 4-3. 런북 안내 경로의 정답 런북 적중(runbook_hit)과 S21

설정·인프라 장애는 코드 수정 대신 런북을 안내한다. 지금까지는 분류와 결과(`no_code_fix`)만 채점했고, 올바른 런북을 안내했는지는 채점하지 않았다. 이 문서는 시나리오마다 정답 런북을 정해 두고, 검색된 근거 문서에 그 런북이 들어 있는지 센 결과다. 함께 추가한 S21(없는 이미지 태그 배포)의 결과도 담았다.

## 정답 문서 ID로 채점한 이유

런북 안내 경로에서는 LLM이 보고서 문장을 쓰지 않는다. 보고서는 템플릿이고, 검색 단계가 고른 문서 ID를 `*참고 문서*`로 붙일 뿐이다(`app/graph/nodes.py`의 `render_report`). 그래서 채점할 대상은 생성이 아니라 검색이다.

Ragas faithfulness 같은 LLM 채점은 생성된 답이 근거에 충실한지를 본다. 이 경로에는 생성된 답이 없어서 맞지 않는다. 채점 모델을 따로 보정해야 하고, 돌릴 때마다 점수가 흔들리고, 비용도 든다. 정답 문서 ID를 시나리오에 적어 두고 포함 여부를 세면 이 세 가지가 모두 사라진다. 대신 이 방식은 "정답 런북이 상위 5개 안에 있는가"만 보고, 순위와 다른 문서의 잡음은 보지 않는다. 그 부분은 아래 표에 근거 문서 목록을 그대로 남겨 사람이 본다.

구현은 다음과 같다.

- 시나리오 `expected.runbook`에 정답 런북 경로를 적는다(S11, S14, S15, S16, S17, S21).
- `run_cluster`는 장애의 `result.context_doc_ids`(상위 5개)에 그 경로가 있으면 `runbook_hit`를 True로 적는다.
- `check_scenarios`는 정답 런북 파일이 `knowledge/`에 실제로 있는지 확인한다. 경로에 오타가 있으면 `runbook_hit`가 경고 없이 False로 나오기 때문이다.

## 결과

로컬 K3s(Vagrant 3노드)에서 실제 감지 경로와 실제 LLM으로 실행했다. 이미지는 `base-0703be4`다. 2026-11-16에 두 번 나눠 돌렸다.

- S21과 S11은 다른 맥북에서 돌렸다. 숫자는 PR #54 본문에서 옮겼고, 원본 증거는 그 맥북의 `results/verify/`에 있다.
- S14, S15, S16은 이 맥에서 21:52~22:15(KST)에, S17은 23:38~23:53(KST)에 돌렸다. 원본 값은 [phase4_runbook_hit.csv](phase4_runbook_hit.csv)다. doctor 기준 시계 차이는 DB 0초, Prometheus 1초다.

| 시나리오 | 장애 | 알림 | 분류(기대) | 검색된 근거 문서(상위 5) | runbook_hit | 감지 | 전달 | 처리 | 비용 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S21 1회 | 없는 이미지 태그 배포 | PodImagePullFailed | config(config) | image-pull x5 | True | - | 30.9초 | 13.9초 | $0.000377 |
| S21 2회 | 같음 | PodImagePullFailed | config(config) | image-pull x4, crashloop | True | - | 301.0초 | 17.3초 | $0.000386 |
| S11 | 필수 환경변수 누락 | KubePodCrashLooping | config(config) | config-env x2, crashloop x3 | True | - | 40.9초 | 2.5초 | $0.000322 |
| S14 | 설정값 형식 오류 | KubePodCrashLooping | config(config) | crashloop x2, config-env, oom-killed, node-not-ready | True | 131초 | 30.2초 | 4.5초 | $0.00033 |
| S15 | 메모리 초과 | PodOOMKilled | infra(infra) | oom-killed x5 | True | 55초 | 30.2초 | 4.7초 | $0.000396 |
| S16 | 앱 노드(k3s-w1) 전원 차단 3분 | KubeNodeNotReady | infra(infra) | node-not-ready x2, node-disk x2, crashloop | True | 139초 | 159.3초 | 4.6초 | $0.000258 |
| S17 | 실험용 볼륨(k3s-w1) 950M 채우기 | AppdataDiskFull | infra(infra) | node-disk x4, node-not-ready | True | 81초 | 40.3초 | 4.6초 | $0.000286 |

정답 런북은 7건 모두 상위 5개 안에 있었다. 분류와 결과도 7건 모두 기대와 같았다. 이 측정의 비용은 S14~S17 합계 $0.081이다. 대부분은 S16에서 노드 장애 중에 걸어 둔 S02(코드 버그)의 처리분($0.080)이다.

## 해석

- **근거 문서에 같은 런북이 여러 번 나온다.** 검색 결과 5개는 문서가 아니라 청크 단위다. 그래서 S15는 상위 5개가 모두 `k8s/oom-killed.md`다. 보고서의 `*참고 문서*`는 이름 중복을 지워 한 번만 보여 주므로 사람이 보는 결과에는 문제가 없다. 다만 한 런북이 5자리를 다 차지하면 두 번째 후보 런북은 들어올 자리가 없다. 이번 7건은 정답이 1위여서 영향이 없었다.
- **S21 2회의 전달 301초는 Alertmanager의 group_interval로 설명된다(추정).** 로컬 설정은 `group_by: [alertname, namespace, service]`, `group_wait: 30s`, `group_interval: 5m`이다(infra_local_set `kube-prometheus-stack.values.yaml.j2`). 1회의 전달 30.9초는 group_wait와 맞는다. 2회는 1회의 같은 알림이 풀리고 5분 안에 다시 떴다. 그래서 새 그룹의 group_wait가 아니라 남아 있던 그룹의 다음 group_interval에 실려 나간 것으로 본다. 301.0초가 300초와 거의 같다는 점이 근거다. Alertmanager 로그로는 확인하지 않았다. 실제 장애에서 같은 알림이 5분 안에 다시 뜨면 최대 5분 늦게 들어온다는 뜻이다.
- **S21 분류 신뢰도는 0.50이다(PR #54에서 측정).** 코드 수정 경로로 넘어가는 기준과 같은 값이다. 컨테이너가 뜨지 않아 로그가 없고, 단서는 알림 문구뿐이다. 이번에는 config로 맞았지만, 여유가 없다. 4-3의 배포 이력 컨텍스트(알림 시점의 리비전과 이미지)가 이 경우를 보강한다.
- **S15는 장애 하나가 2건으로 접수됐다.** shopapi는 replica가 2개이고, 메모리 제한을 낮추자 두 Pod가 모두 OOMKilled됐다. 두 알림은 같은 Alertmanager 그룹으로 함께 왔지만 자동화의 중복 제거를 통과했고, Slack 카드도 2장이 올라갔다(`slack_*.json`, 공통 채널). 중복 제거 지문은 알림 문구의 첫 줄로 만든다(`app/core/fingerprint.py`). 그런데 문구에 Pod 이름(`shopapi-775d8668d4-2lqvx`, `shopapi-775d8668d4-p6kvj`)이 들어 있고, `normalize`는 12자 이상의 16진수와 숫자만 지운다. 그래서 Pod 이름 끝부분이 남아 지문이 달라진다. 3단계 측정(3-7)은 접수 건수를 세지 않아 이것을 놓쳤다. 이후 [#55](https://github.com/IKYU925/AI_auto_pipeline/pull/55)에서 지문이 Deployment Pod 이름의 해시와 접미사를 지우도록 고쳤다(`dd030cb`). 같은 Deployment의 두 Pod 알림이 1건으로 접수되는지는 `tests/test_alertmanager.py`의 테스트(`eac65bc`)로 확인한다.
- **S16의 감지와 전달은 3단계와 같은 모양이다.** 감지 139초(3단계 119초)는 NotReady 판정과 `for: 1m`이고, 전달 159초(3단계 180초)는 자동화 api가 있는 k3s-w1이 꺼져 있는 동안 웹훅을 받을 곳이 없었던 시간이다. 노드 장애 중에 걸어 둔 S02는 노드 복구 뒤 분류 단계부터 이어서 resolved로 끝났고, Slack 메시지는 2개(카드와 패치)로 중복이 없었다.

- **S17은 승인 한 번으로 해소됐다.** 카드의 정리 조치(`cleanup-appdata @ k3s-w1`)를 승인하자 6.4초 만에 끝났고(succeeded), 승인부터 알림 해소까지 27.2초였다. 알림부터 승인 요청까지는 47.1초다. 승인 대기 705.7초는 사람이 카드를 늦게 본 시간이라 시스템 성능이 아니다.
- **S17 앞에 S15의 여파로 다른 카드가 하나 올라왔다.** 직전 S15 실행에서 OOM으로 재시작이 반복돼 `KubePodCrashLooping`도 떴고, 복원 뒤에 롤백 제안 카드로 접수됐다(`aa54df512090`). 이 카드를 승인했지만, 제안 뒤 배포 리비전이 바뀌어(76에서 77로) 롤백하지 않았다. 롤백 전에 리비전을 다시 확인하는 안전장치가 의도대로 동작한 것이다.

## 측정 환경

S14~S16을 돌리기 전에 k3s-cp 메모리를 2GB에서 3GB로 늘렸다(infra_local_set PR #11). 다른 맥북의 측정 중에 cp가 메모리 부족으로 k3s를 두 번 재시작했기 때문이다. 늘린 뒤 측정이 끝날 때까지(부팅 후 26분) cp의 available은 1,499MB, k3s 재시작은 0회였다.
