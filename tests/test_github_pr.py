import base64
import json
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.approvals import Executor, handle_click
from app.github import PullRequester, apply_hunks, file_patches
from app.main import create_app
from app.notify.card import OPEN_PR_ACTION_ID
from app.notify.slack import SlackNotifier
from app.testing.runner import SandboxTestRunner
from app.worker import process_message
from tests.test_approvals import FakeSlack, actions
from tests.test_pipeline import HEADERS, INCIDENT
from tests.test_slack_card import RecordingClient

REPO, SUBDIR = "acme/checkout", "services/checkout"


class FakeGitHub:
    """base 브랜치에 sample_repo가 services/checkout 아래 있는 저장소. 쓰기 요청을 기록한다."""

    def __init__(self, root: str = "sample_repo"):
        self.files = {f"{SUBDIR}/{p.relative_to(root)}": p.read_text() for p in Path(root).rglob("*.py")}
        self.writes: list[tuple[str, dict]] = []

    def handle(self, r: httpx.Request) -> httpx.Response:
        path = r.url.path.removeprefix(f"/repos/{REPO}")
        if r.method == "GET" and path == "/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "base"}})
        if r.method == "GET" and path == "/git/trees/base":
            tree = [{"path": p, "mode": "100644", "type": "blob", "sha": p} for p in self.files]
            return httpx.Response(200, json={"sha": "base-tree", "tree": tree})
        if r.method == "GET" and path.startswith("/git/blobs/"):
            content = self.files[path.removeprefix("/git/blobs/")]
            return httpx.Response(200, json={"content": base64.b64encode(content.encode()).decode()})
        if r.method == "POST":
            body = json.loads(r.content)
            self.writes.append((path, body))
            if path == "/pulls":
                return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/7"})
            return httpx.Response(201, json={"sha": f"new{len(self.writes)}"})
        return httpx.Response(404, json={"message": "Not Found"})

    def runner(self, c) -> Executor:
        http = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(self.handle))
        return Executor(None, None, None, PullRequester(c.store, c.settings, http))


@pytest.fixture
def resolved(container):
    """실제 파이프라인으로 sample_repo 버그를 고친 장애. PR 설정이 켜져 있다."""
    container.settings.github_repo, container.settings.github_token, container.settings.github_path = REPO, "t", SUBDIR
    slack = SlackNotifier("")
    slack.client = RecordingClient()
    container.__dict__["notifier"] = slack
    inc = TestClient(create_app(container)).post("/api/v1/incidents", json=INCIDENT, headers=HEADERS).json()
    [(msg_id, payload)] = list(container.queue.consume("t", count=1, block_ms=10))
    assert process_message(container, msg_id, payload)["outcome"] == "resolved"
    container.__dict__["notifier"] = FakeSlack()
    return container, inc["incident_id"], slack.client.sent


def click(c, gh, incident_id, user="U-ALICE"):
    blocks = [{"type": "section"}, {"type": "actions", "block_id": "open_pr"}]
    return handle_click(c, gh.runner(c), incident_id, user, "C1", "2.0", blocks, OPEN_PR_ACTION_ID)


def test_patch_message_carries_the_pr_button(resolved):
    _, incident_id, sent = resolved
    [button] = sent[-1]["blocks"][-1]["elements"]
    assert (button["action_id"], button["value"]) == (OPEN_PR_ACTION_ID, incident_id)


def test_click_opens_one_draft_pr_with_the_patched_file(resolved, tmp_path):
    c, incident_id, _ = resolved
    gh = FakeGitHub()

    assert click(c, gh, incident_id) == "succeeded"
    assert click(c, gh, incident_id, "U-BOB") == "already"

    writes = dict(gh.writes)
    assert [p for p, _ in gh.writes] == ["/git/trees", "/git/commits", "/git/refs", "/pulls"], "두 번 눌러도 PR은 하나"
    tree = {e["path"]: e["content"] for e in writes["/git/trees"]["tree"]}
    assert sorted(tree) == [f"{SUBDIR}/shop/pricing.py", f"{SUBDIR}/tests/test_pricing.py"], "패치 경로 앞에 서비스 경로를 붙인다"
    shutil.copytree("sample_repo", tmp_path / "repo")
    assert SandboxTestRunner._apply(tmp_path, tmp_path / "repo", c.store.get(incident_id)["patch"]) is None
    for path, content in tree.items():
        assert content == (tmp_path / "repo" / path.removeprefix(f"{SUBDIR}/")).read_text(), "샌드박스가 테스트한 파일과 같다"
    assert writes["/git/trees"]["base_tree"] == "base-tree" and writes["/git/commits"]["parents"] == ["base"]
    assert writes["/git/refs"]["ref"] == f"refs/heads/aiops/fix-{incident_id}"
    pr = writes["/pulls"]
    assert (pr["head"], pr["base"], pr["draft"]) == (f"aiops/fix-{incident_id}", "main", True)
    assert incident_id in pr["body"] and "passed" in pr["body"]
    [row] = actions(c)
    assert (row["playbook"], row["target"], row["approver"], row["status"]) == ("github-pr", REPO, "U-ALICE", "succeeded")
    assert f"https://github.com/{REPO}/pull/7" in row["log"]
    [(_, _, blocks)] = c.notifier.updates
    assert all(b.get("block_id") != "open_pr" for b in blocks), "누른 뒤 버튼을 지운다"


def test_pr_is_disabled_without_settings(resolved):
    c, incident_id, _ = resolved
    c.settings.github_token = ""
    gh = FakeGitHub()
    assert click(c, gh, incident_id) == "no_proposal"
    assert gh.writes == [] and actions(c) == []


def test_patch_that_does_not_apply_to_the_base_branch_fails_without_a_pr(resolved):
    c, incident_id, _ = resolved
    gh = FakeGitHub()
    gh.files = {p: "다른 내용\n" for p in gh.files}

    assert click(c, gh, incident_id) == "failed"
    assert gh.writes == []
    [row] = actions(c)
    assert row["status"] == "failed" and "적용하지 못했다" in row["log"]


def test_hunks_apply_by_content_even_when_line_numbers_are_off():
    patch = "--- a/m.py\n+++ b/m.py\n@@ -40,3 +40,3 @@\n a = 1\n-b = 2\n+b = 3\n\n"
    [(old, new, hunks)] = file_patches(patch)
    assert (old, new) == ("m.py", "m.py")
    assert apply_hunks("x = 0\na = 1\nb = 2\n\nc = 4\n", hunks) == "x = 0\na = 1\nb = 3\n\nc = 4\n"
