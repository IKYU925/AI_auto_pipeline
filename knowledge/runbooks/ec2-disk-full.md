---
service: common
category: infra
doc_type: runbook
updated_at: 2026-11-15
---
# EC2 루트 디스크 부족 대응

## 증상

CloudWatch 경보 `<tenant>-<service>-DiskUsageHigh`가 울린다. CloudWatch Agent의 `disk_used_percent`(path `/`)가 90% 이상으로 2분 넘게 이어진 상태다. 디스크가 가득 차면 컨테이너 로그를 쓰지 못하고, 새 이미지를 받지 못하며, 애플리케이션의 임시 파일 쓰기가 실패한다.

## 판단

지표 경보라서 스택트레이스가 없다. 코드 수정 대상이 아니다. 먼저 무엇이 디스크를 차지하는지 본다. 흔한 원인은 쌓인 Docker 이미지와 빌드 캐시, 지우지 않은 컨테이너 로그, 큰 임시 파일이다.

## 조치

인스턴스에는 인바운드가 열려 있지 않다. SSM Session Manager로 접속한다(`aws ssm start-session --target <instance-id>`).

1. 사용량을 본다. `df -h /; sudo du -xh --max-depth=1 / 2>/dev/null | sort -h | tail; sudo docker system df`
2. 쓰지 않는 이미지와 정지한 컨테이너를 정리한다. `sudo docker image prune -a -f; sudo docker container prune -f`
3. `/var/tmp`, `/tmp`의 큰 파일은 무엇인지 확인한 뒤 지운다. 실험에서 채운 파일은 `/var/tmp/s19-fill`이다.
4. 계속 차오르면 EBS 볼륨 크기를 늘린다. 인프라는 infra_template의 `terraform/envs/aiops-lab`이 관리하므로 거기서 바꾼다.

4단계에서는 자동 조치를 하지 않는다. 위 명령은 사람이 확인하고 실행한다.

## 확인

`df -h /`의 사용률이 90% 아래로 내려가고, 경보가 `OK`로 돌아오면 해소다. 자동화는 `OK` 이벤트로 장애의 경보 해소 시각을 남긴다.
