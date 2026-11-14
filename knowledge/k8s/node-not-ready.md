---
service: common
category: general
doc_type: runbook
updated_at: 2026-11-14
---
# 노드 장애(KubeNodeNotReady) 대응

## 증상

`KubeNodeNotReady` 알림이 뜬다. `kubectl get nodes`에서 노드가 `NotReady`다. 그 노드의 Pod는 응답하지 않고, 일정 시간이 지나면 Terminating으로 표시된다.

## 판단

이 클러스터는 앱(shop, aiops)이 모두 `workload=app` 노드(k3s-w1)에만 뜬다. postgres와 redis는 local-path 볼륨이라 데이터가 그 노드 디스크에 묶여 다른 노드로 옮길 수 없다. 그래서 다른 노드로 옮기는 대신 노드를 복구한다. 코드 수정 대상이 아니다.

## 조치

1. 노드 상태와 조건을 본다. `kubectl describe node <node>`의 Conditions(Ready, MemoryPressure, DiskPressure)를 확인한다.
2. VM이 살아 있는지 맥에서 확인한다. infra_local_set 폴더에서 `vagrant status <node>`
3. VM이 꺼져 있으면 `vagrant up <node>`로 켠다. 켜져 있는데 NotReady면 `vagrant ssh <node> -c "sudo systemctl status k3s-agent"`로 k3s 에이전트를 확인하고 재시작한다.
4. 복구 뒤 Pod가 Running으로 돌아오는지 본다. `kubectl get pods -A -o wide | grep <node>`
5. 장애 동안 처리 중이던 자동화 작업은 워커가 체크포인트부터 이어 간다. 접수가 빠진 알림이 있는지 Alertmanager(:30093)에서 확인한다.

## 확인

노드가 Ready이고 그 노드의 Pod가 모두 Running이면 해소다.
