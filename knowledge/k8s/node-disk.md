---
service: common
category: general
doc_type: runbook
updated_at: 2026-11-14
---
# 노드 디스크 부족 대응

## 증상

노드 디스크 사용률 알림(`NodeFilesystemSpaceFillingUp`, `NodeFilesystemAlmostOutOfSpace`)이 뜬다. 디스크가 더 차면 kubelet이 `DiskPressure`로 Pod를 축출하고, 새 이미지를 받지 못한다.

## 판단

어느 마운트 지점인지 먼저 본다. 알림 라벨의 `mountpoint`가 `/vagrant`면 VM이 아니라 맥과 공유한 폴더라서 맥 디스크가 찬 것이다. `/`나 실험용 볼륨(`/srv/appdata`)이면 노드 디스크다. 노드 디스크는 쌓인 이미지, 로그, 임시 파일이 흔한 원인이다. 코드 수정 대상이 아니다.

## 조치

1. 사용량을 본다. `vagrant ssh <node> -c "df -h; sudo du -xh --max-depth=1 /var/lib/rancher /var/log 2>/dev/null | sort -h | tail"`
2. 쓰지 않는 이미지를 정리한다. `vagrant ssh <node> -c "sudo k3s crictl rmi --prune"`
3. 오래된 로그와 임시 파일은 허용 목록의 정리 플레이북으로 지운다. 승인 후에만 실행한다(plan.md 9-8).
4. 맥 디스크(`/vagrant`)가 원인이면 맥에서 Docker 이미지와 빌드 캐시, 쓰지 않는 VM을 정리한다.

## 확인

사용률이 알림 기준 아래로 내려가고 `kubectl describe node <node>`에 `DiskPressure`가 없으면 해소다.
