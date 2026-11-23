# F18. 검증된 패치로 GitHub draft PR 열기

상태: 구현 중(실제 GitHub 저장소 검증 전)
관련: F06(승인 후 조치), F09(Slack 카드), F17(ArgoCD 롤백)

## 문제

코드 버그를 고친 장애는 샌드박스 테스트를 통과한 패치를 `artifacts/<incident_id>.patch`로 저장하고 Slack에 올린다. 이 패치를 대상 서비스 저장소에 반영하려면 사람이 Slack에서 복사해 브랜치를 만들고 PR을 연다. 손으로 옮기다 패치가 깨지기 쉽고, 어느 장애의 수정인지 기록도 끊긴다.

## 목표와 비목표

- 목표: 패치 메시지의 `PR 만들기` 버튼을 사람이 누르면 대상 저장소에 draft PR이 열린다.
- 목표: PR에 올라가는 파일은 샌드박스가 테스트한 파일과 같다.
- 목표: 같은 장애에서 두 번 눌러도 PR은 하나다. 누가 눌렀는지 `actions`에 남는다.
- 목표: 설정이 없으면 버튼이 없고 지금 동작과 같다.
- 비목표: 자동 머지. 머지는 사람이 리뷰 뒤에 한다.
- 비목표: 버튼 없는 PR 자동 생성. 사람 승인이 PR을 여는 조건이다.
- 비목표: 실패한 PR 만들기 재시도. 다른 승인 조치와 같이 장애마다 한 번만 실행한다.

## 운영자 시나리오

1. 코드 버그 장애가 들어오고 자동화가 고친 패치가 테스트를 통과한다.
2. 카드 다음 메시지에 패치와 `PR 만들기: owner/name` 버튼이 붙는다.
3. 담당자가 누르면 `aiops/fix-<장애 ID>` 브랜치와 draft PR이 열리고, 스레드에 PR 주소가 회신된다.
4. 담당자가 PR을 리뷰하고 머지한다.

## 요구사항

- 설정(`app/config.py`): `GITHUB_REPO`(owner/name), `GITHUB_BASE_BRANCH`(기본 main), `GITHUB_PATH`(저장소 안의 서비스 경로), `GITHUB_TOKEN`. 저장소나 토큰이 비면 끈다.
- 버튼(`app/notify/card.py` `pr_button`, `app/worker.py`): 버튼 값은 장애 ID뿐이다. 저장소는 설정에서, 패치는 장애 기록에서 다시 읽는다.
- 승인(`app/approvals.py`): F06의 승인 처리를 그대로 쓴다. 조치 이름은 `github-pr`이다. 승인자 허용 목록(`ACTION_APPROVERS`), `actions` 기록, 한 번만 실행이 다른 조치와 같다. 장애 상태가 resolved이고 패치가 있을 때만 실행한다.
- PR(`app/github.py`): 클론하지 않는다. base 브랜치 tree에서 바뀐 파일을 읽고 패치를 적용해 tree, commit, 브랜치, draft PR을 REST로 만든다. 패치가 base 브랜치에 맞지 않으면 아무것도 쓰지 않고 실패로 남긴다.
- PR 본문: 장애 ID, 샌드박스 테스트 결과, 실행한 테스트, 자동화 보고서.
- 비밀값: `GITHUB_TOKEN`은 `aiops-secrets`로 들어온다. 로컬은 `scripts/k8s_secrets.sh`가 `.env`에서 읽는다. EKS는 Secrets Manager `ai-incident/aiops`에 키를 더하면 ExternalSecret이 가져온다. 키가 없어도 기존 배포는 그대로 뜬다.
- 토큰 권한: 대상 저장소 하나의 Contents, Pull requests 쓰기(fine-grained token).

## 완료 기준

- [x] 클릭 한 번에 draft PR 하나, 두 번째 클릭은 GitHub를 부르지 않음, 승인자와 PR 주소가 `actions`에 남음(`tests/test_github_pr.py`)
- [x] PR의 파일 내용이 샌드박스의 `git apply` 결과와 같고 경로 앞에 `GITHUB_PATH`가 붙음
- [x] 설정이 없으면 버튼이 없고(`tests/test_slack_card.py`) 눌러도 실행하지 않음
- [x] base 브랜치에 맞지 않는 패치는 GitHub에 쓰지 않고 failed로 남음
- [ ] 실제 GitHub 저장소에서 draft PR 생성 확인

## 설계 메모

- **승인 조치의 하나로 넣었다.** 버튼, 승인자 확인, 한 번만 실행, 기록, 회신을 F06에서 그대로 얻는다. 카드의 승인 버튼(롤백 등)과 조치 이름이 달라 둘 다 누를 수 있다.
- **패치는 내용으로 맞춘다.** LLM은 hunk의 줄 수를 자주 틀린다. 샌드박스가 `git apply --recount`를 쓰는 것과 같은 이유다. 줄 번호는 같은 내용이 여러 곳에 있을 때 고르는 데만 쓴다.
- **클론하지 않는다.** approvals Pod에 git과 작업 디렉터리를 두지 않는다. 바뀐 파일만 읽는다.

## 열린 질문

- 결과 회신은 다른 조치처럼 코드 블록에 들어가 PR 주소를 바로 누를 수 없다.
- 실패한 PR 만들기를 다시 하려면 `actions`의 기록을 지워야 한다.
- 대상 저장소의 base 브랜치가 샌드박스 사본(`targets/shopapi`)보다 앞서 있으면 패치가 맞지 않을 수 있다. 그때는 실패로 남고 PR을 만들지 않는다.

## 변경 이력

- 2026-11-23 구현.
