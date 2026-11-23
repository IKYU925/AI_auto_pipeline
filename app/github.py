"""검증된 패치로 대상 서비스 저장소에 draft PR을 연다(F18). 클론 없이 GitHub REST의 git data API만 쓴다.

base 브랜치의 파일에 패치를 적용해 tree, commit, 브랜치(aiops/fix-<장애 ID>)를 만들고 draft PR을 연다. 머지하지 않는다.
"""
import base64
import re
from posixpath import join

import httpx

from app.actions import Proposal
from app.analysis.diff_analyzer import _strip_prefix, split_patch_by_file

_HUNK = re.compile(r"^@@ -(\d+)")


class PatchError(Exception):
    pass


def apply_hunks(old: str, hunks: list[tuple[int, list[str]]]) -> str:
    """hunk의 원본 줄(문맥과 삭제 줄)을 찾아 바꾼다. 줄 수는 믿지 않고(LLM이 자주 틀린다) 시작 줄 번호에 가장 가까운 곳을 고른다."""
    lines = old.split("\n")
    delta = 0
    for start, hunk in hunks:
        before = [h[1:] for h in hunk if h[0] in " -"]
        after = [h[1:] for h in hunk if h[0] in " +"]
        found = [i for i in range(len(lines) - len(before) + 1) if lines[i:i + len(before)] == before]
        if not found:
            raise PatchError(f"{start}번째 줄 근처 hunk의 원본 줄이 base 브랜치 파일에 없다")
        i = min(found, key=lambda f: abs(f - (start - 1 + delta)))
        lines[i:i + len(before)] = after
        delta += len(after) - len(before)
    return "\n".join(lines)


def file_patches(patch: str) -> list[tuple[str | None, str | None, list[tuple[int, list[str]]]]]:
    """파일마다 (원래 경로, 새 경로, [(원본 시작 줄, hunk 줄)]). 새 파일은 원래 경로, 지운 파일은 새 경로가 None이다."""
    out = []
    for part in split_patch_by_file(patch):
        lines = part.splitlines()
        old, new = _strip_prefix(lines[0][4:]), _strip_prefix(lines[1][4:])
        hunks: list[tuple[int, list[str]]] = []
        for line in lines[2:]:
            if m := _HUNK.match(line):
                hunks.append((int(m.group(1)), []))
            elif hunks and not line.startswith("\\"):
                hunks[-1][1].append(line or " ")  # LLM이 빈 문맥 줄의 공백을 빼먹는다
        out.append((old, new, hunks))
    return out


def pr_body(incident_id: str, incident: dict) -> str:
    tests = incident.get("test_result") or {}
    selection = incident.get("test_selection") or {}
    ran = ", ".join(f"`{t}`" for t in selection.get("tests") or []) or selection.get("reason", "-")
    return "\n".join([
        f"AI 장애 대응 자동화가 장애 `{incident_id}`에 대해 만든 수정안입니다. Slack에서 사람이 승인해 열었습니다.",
        "머지 전에 사람이 검토합니다. 자동화는 이 PR을 머지하지 않습니다.",
        "",
        f"- 장애: `{incident_id}` (자동화 API `GET /api/v1/incidents/{incident_id}`)",
        f"- 샌드박스 테스트: {tests.get('summary', '-')}",
        f"- 실행한 테스트: {ran}",
        "",
        "<details><summary>자동화 보고서</summary>",
        "",
        incident.get("report", "-"),
        "",
        "</details>",
    ])


class PullRequester:
    def __init__(self, store, settings, http: httpx.Client | None = None):
        self.store = store
        self.s = settings
        self.http = http or httpx.Client(
            base_url="https://api.github.com", timeout=30,
            headers={"Authorization": f"Bearer {settings.github_token}", "Accept": "application/vnd.github+json"})

    def _call(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, path, **kw)
        if r.is_error:
            raise RuntimeError(f"GitHub {method} {path} -> {r.status_code} {r.text[:300]}")
        return r.json()

    def run(self, incident_id: str, p: Proposal) -> tuple[bool, str]:
        incident = self.store.get(incident_id)
        api, base, branch = f"/repos/{p.target}", self.s.github_base_branch, f"aiops/fix-{incident_id}"
        base_sha = self._call("GET", f"{api}/git/ref/heads/{base}")["object"]["sha"]
        # ponytail: 재귀 tree는 큰 저장소(10만 항목 넘음)에서 잘린다. 그때는 파일이 없다고 실패한다. 서비스 하위 tree만 읽으면 풀린다
        tree = self._call("GET", f"{api}/git/trees/{base_sha}", params={"recursive": "1"})
        files = {t["path"]: t for t in tree["tree"] if t["type"] == "blob"}
        entries = []
        try:
            for old, new, hunks in file_patches(incident["patch"]):
                content = ""
                if old:
                    path = join(self.s.github_path, old)
                    if path not in files:
                        raise PatchError(f"{base} 브랜치에 {path}가 없다")
                    blob = self._call("GET", f"{api}/git/blobs/{files[path]['sha']}")
                    content = base64.b64decode(blob["content"]).decode()
                    if new != old:
                        entries.append({"path": path, "mode": files[path]["mode"], "type": "blob", "sha": None})
                if new:
                    path = join(self.s.github_path, new)
                    mode = files[path]["mode"] if path in files else "100644"
                    entries.append({"path": path, "mode": mode, "type": "blob", "content": apply_hunks(content, hunks)})
        except PatchError as e:
            return False, f"패치를 {p.target} {base}에 적용하지 못했다. PR을 만들지 않았다: {e}"
        if not entries:
            return False, "패치에서 바뀐 파일을 찾지 못했다. PR을 만들지 않았다"
        new_tree = self._call("POST", f"{api}/git/trees", json={"base_tree": tree["sha"], "tree": entries})["sha"]
        title = f"fix: 장애 {incident_id} 자동 수정안"
        commit = self._call("POST", f"{api}/git/commits", json={"message": title, "tree": new_tree, "parents": [base_sha]})["sha"]
        self._call("POST", f"{api}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})
        pr = self._call("POST", f"{api}/pulls", json={"title": title, "head": branch, "base": base, "draft": True,
                                                       "body": pr_body(incident_id, incident)})
        return True, f"draft PR {pr['html_url']} (브랜치 {branch})"
