from app.analysis import code_context


def test_frames_outside_repo_are_skipped(tmp_path):
    repo = tmp_path / "repo"
    (repo / "shop").mkdir(parents=True)
    (repo / "shop" / "a.py").write_text("x = 1\n")
    outside = tmp_path / "site-packages" / "lib.py"
    outside.parent.mkdir()
    outside.write_text("y = 2\n")
    stack = f'File "/srv/app/shop/a.py", line 1, in f\nFile "{outside}", line 1, in g\n'

    context = code_context.from_stacktrace(str(repo), stack)
    assert "### shop/a.py" in context
    assert "lib.py" not in context
