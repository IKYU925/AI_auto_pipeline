#!/usr/bin/env python3
"""회신 채널(공통, 고객사별)에 since 이후 올라온 Slack 메시지를 JSON으로 출력한다. 읽기만 한다.
사용: slack_evidence.py <증거 폴더> <since ISO8601>. 토큰과 채널은 api Pod의 환경(aiops-config, aiops-secrets)에서 쓴다."""
import json
import subprocess
import sys
from datetime import datetime

since = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00")).timestamp()
code = f"""
import os, json, httpx
chans = {{"common": os.environ["ALERT_SLACK_CHANNEL"], **json.loads(os.environ.get("TENANT_SLACK_CHANNELS") or "{{}}")}}
out = {{}}
for name, ch in chans.items():
    r = httpx.get("https://slack.com/api/conversations.history", headers={{"Authorization": "Bearer " + os.environ["SLACK_BOT_TOKEN"]}},
                  params={{"channel": ch, "oldest": "{since}", "limit": 100}}, timeout=15).json()
    out[name] = {{"channel": ch, "ok": r.get("ok"), "error": r.get("error"),
                 "messages": [{{"ts": m["ts"], "thread_ts": m.get("thread_ts"), "text": (m.get("text") or "")[:200]}} for m in r.get("messages", [])]}}
print(json.dumps(out, ensure_ascii=False))
"""
r = subprocess.run(["kubectl", "-n", "aiops", "exec", "deploy/api", "-c", "api", "--", "python", "-c", code],
                   capture_output=True, text=True, timeout=60)
if r.returncode:
    sys.exit(r.stderr.strip()[-500:])
print(json.dumps(json.loads(r.stdout), ensure_ascii=False, indent=1))
