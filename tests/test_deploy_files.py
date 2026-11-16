"""Guard rails on the deploy files (Docker is not installed on the dev machine, so these are
plain-text checks of the promises made to the owner of the shared VM)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tc.common.config import Settings

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")


def _active_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def test_compose_publishes_no_port_at_all():
    active = "\n".join(_active_lines(COMPOSE))
    assert not re.search(r"^\s*ports\s*:", active, re.M)
    assert "expose" not in active.lower()
    assert "network_mode" not in active and "privileged" not in active
    assert not re.search(r"^\s*EXPOSE\b", DOCKERFILE, re.M | re.I)


def test_compose_caps_cpu_memory_and_logs():
    lines = _active_lines(COMPOSE)
    assert 'cpus: "0.5"' in lines
    assert "mem_limit: 768m" in lines
    assert "restart: unless-stopped" in lines
    assert "driver: json-file" in lines
    assert 'max-size: "10m"' in lines and 'max-file: "3"' in lines


def test_compose_is_valid_yaml_with_the_expected_structure():
    yaml = pytest.importorskip("yaml")  # present transitively; not a project dependency
    doc = yaml.safe_load(COMPOSE)
    svc = doc["services"]["collector"]
    assert doc["name"] == "tc-collector" and list(doc["services"]) == ["collector"]
    assert "ports" not in svc and "expose" not in svc
    assert (svc["cpus"], svc["mem_limit"], svc["restart"]) == ("0.5", "768m", "unless-stopped")
    assert svc["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
    assert svc["healthcheck"]["test"][-1] == "tc.health"
    assert svc["cap_drop"] == ["ALL"]


def test_compose_healthcheck_volume_and_env_file():
    lines = _active_lines(COMPOSE)
    assert any("tc.health" in ln for ln in lines)
    assert "env_file: ../.env" in lines
    volumes = [ln for ln in lines if ln.startswith("- ../data:/app/data")]
    assert len(volumes) == 1  # the only mount: no docker.sock, no host paths outside data/
    assert "docker.sock" not in COMPOSE


def test_dockerfile_is_slim_non_root_and_lf():
    assert re.search(r"^FROM python:3\.12-slim\s*$", DOCKERFILE, re.M)
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, re.M)
    assert users and users[-1] not in ("root", "0")
    assert 'CMD ["python", "-m", "tc"]' in DOCKERFILE
    for path in (ROOT / "docker" / "Dockerfile", ROOT / "docker" / "compose.yaml"):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} must use LF endings"


VM_SH = ROOT / "scripts" / "vm.sh"


def _git_bash() -> str | None:
    bash = shutil.which("bash")
    if bash is None or "system32" in bash.lower():  # WSL launcher, not a real bash here
        return None
    return bash


def test_vm_script_is_lf_and_only_drives_its_own_compose_project():
    text = VM_SH.read_text(encoding="utf-8")
    assert b"\r\n" not in VM_SH.read_bytes()
    code = [ln.split("#")[0] for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(code)
    # every compose call goes through dc(), pinned to docker/compose.yaml
    assert code.count("compose -f") == 1 and '"$ROOT/docker/compose.yaml"' in code
    for forbidden in ("prune", "docker rm ", "docker stop", "systemctl enable", "systemctl start",
                      "ufw", "iptables", "sysctl", "crontab", "HF_TOKEN\")\"\n"):  # fmt: skip
        assert forbidden not in code, forbidden
    assert 'echo "$v"' not in code and "printf '%s' \"$v\"" not in code  # token never printed


def test_vm_script_syntax():
    bash = _git_bash()
    if bash is None:
        pytest.skip("bash not available")
    res = subprocess.run([bash, "-n", str(VM_SH)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_vm_script_set_env_replaces_values_without_printing_them(tmp_path):
    bash = _git_bash()
    if bash is None:
        pytest.skip("bash not available")
    (tmp_path / ".env").write_text("HF_REPO=\nHF_TOKEN=\nPOLL_SECONDS=120\n", encoding="utf-8")
    script = (
        f'source "{VM_SH.as_posix()}"; ROOT="{tmp_path.as_posix()}"; '
        r"set_env HF_TOKEN 'hf_abc/+=&\x'; set_env HF_REPO 'me/ds'; set_env NEW_KEY 1; "
        'printf "%s|%s" "$(get_env HF_REPO)" "$(get_env POLL_SECONDS)"'
    )
    res = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout == "me/ds|120"
    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert lines == ["HF_REPO=me/ds", r"HF_TOKEN=hf_abc/+=&\x", "POLL_SECONDS=120", "NEW_KEY=1"]
    assert list(tmp_path.iterdir()) == [tmp_path / ".env"]  # no temp file left


def test_env_example_lists_every_documented_variable_with_the_right_defaults():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = dict(re.findall(r"^([A-Z_]+)=(.*)$", text, re.M))
    for key in (
        "POLL_SECONDS", "HF_REPO", "HF_TOKEN", "CONTACT_EMAIL", "MAX_LOCAL_MB",
        "RAW_SAMPLE_EVERY_MIN", "RUN_FOR_MINUTES", "TRIP_UPDATES_URL", "ALERTS_URL",
        "STATIC_GTFS_URL",
    ):  # fmt: skip
        assert key in values, key
    defaults = Settings(_env_file=None)
    assert int(values["POLL_SECONDS"]) == defaults.poll_seconds == 120
    assert int(values["MAX_LOCAL_MB"]) == defaults.max_local_mb == 2048
    assert int(values["RAW_SAMPLE_EVERY_MIN"]) == defaults.raw_sample_every_min == 60
    assert int(values["RUN_FOR_MINUTES"]) == defaults.run_for_minutes == 0
    assert values["TRIP_UPDATES_URL"] == defaults.trip_updates_url
    assert values["HF_TOKEN"] == ""  # never ship a secret in the example
