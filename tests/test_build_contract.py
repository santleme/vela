from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_image_contains_product_code_and_seeded_skills_outside_the_home_volume():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY vela_core/ /opt/vela/vela_core/" in dockerfile
    assert "COPY skills/ /opt/hermes/skills/" in dockerfile
    assert "VELA_STATE_PATH=/var/lib/hermes/vela/state.json" in dockerfile
    assert "VOLUME [\"/var/lib/hermes\"]" in dockerfile


def test_compose_keeps_state_in_the_named_hermes_volume():
    compose = (ROOT / "compose.yml").read_text(encoding="utf-8")

    assert "vela-home:/var/lib/hermes" in compose
    assert "VELA_CORE_ROOT: /opt/vela" in compose
    assert "VELA_STATE_PATH: /var/lib/hermes/vela/state.json" in compose
