#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_file(path: Path, content: str, executable: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def scaffold_repo(tmp_root: Path):
    for rel in ["runs", "plants", "inbox", "scripts", "schema", "runner"]:
        (tmp_root / rel).mkdir(parents=True, exist_ok=True)

    shutil.copy2(REPO_ROOT / "scripts" / "personalagentkit", tmp_root / "scripts" / "personalagentkit")
    shutil.copy2(REPO_ROOT / "scripts" / "dispatch.py", tmp_root / "scripts" / "dispatch.py")
    shutil.copy2(REPO_ROOT / "schema" / "run.schema.json", tmp_root / "schema" / "run.schema.json")
    shutil.copytree(REPO_ROOT / "runner", tmp_root / "runner", dirs_exist_ok=True)

    for rel in ["scripts/personalagentkit", "scripts/dispatch.py"]:
        path = tmp_root / rel
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    write_file(tmp_root / "MOTIVATION.md", "# Motivation\n")
    write_file(tmp_root.parent / "shared" / "charter.md", "# Charter\n\n## Operator\n\nGabriel Example\n")


def seed_inbox(repo_root: Path):
    write_file(repo_root / "inbox" / "001-to-gabriel.md", "Canonical pending\n")
    write_file(repo_root / "inbox" / "002-to-operator.md", "Legacy pending\n")
    write_file(repo_root / "inbox" / "003-to-gabriel.md", "Already replied\n")
    write_file(repo_root / "inbox" / "003-reply.md", "Reply present\n")
    write_file(repo_root / "inbox" / "004-from-operator.md", "Inbound mail should not count\n")


def assert_contains(text: str, needle: str, message: str):
    if needle not in text:
        raise AssertionError(f"{message}: missing {needle!r}")


def assert_not_contains(text: str, needle: str, message: str):
    if needle in text:
        raise AssertionError(f"{message}: found unexpected {needle!r}")


def verify_dispatcher_surface():
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        scaffold_repo(repo_root)
        seed_inbox(repo_root)

        dispatch = load_module(repo_root / "scripts" / "dispatch.py", "dispatch_inbox_verify")
        dispatcher = dispatch.Dispatcher(
            repo_root=repo_root,
            max_workers=1,
            tend_interval=300,
            max_cost=None,
            retro_interval=3600,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dispatcher._surface_inbox()
        output = buf.getvalue()

        assert_contains(output, "001-to-gabriel.md", "dispatcher should surface canonical operator mail")
        assert_contains(output, "002-to-operator.md", "dispatcher should preserve legacy operator mail support")
        assert_not_contains(output, "003-to-gabriel.md", "dispatcher should suppress replied operator mail")
        assert_not_contains(output, "004-from-operator.md", "dispatcher should ignore inbound mail files")


def verify_status_surface():
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        scaffold_repo(repo_root)
        seed_inbox(repo_root)

        result = subprocess.run(
            ["./scripts/personalagentkit", "status"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=True,
        )

        assert_contains(result.stdout, "001-to-gabriel.md", "status should surface canonical operator mail")
        assert_contains(result.stdout, "002-to-operator.md", "status should preserve legacy operator mail support")
        assert_not_contains(result.stdout, "003-to-gabriel.md", "status should suppress replied operator mail")
        assert_not_contains(result.stdout, "004-from-operator.md", "status should ignore inbound mail files")


def main():
    verify_dispatcher_surface()
    verify_status_surface()
    print("verify_operator_inbox_surfacing: ok")


if __name__ == "__main__":
    main()
