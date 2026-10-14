"""The proxy's OAuth callback ports, published on loopback for the terminal fallback.

The console path needs none of them (the operator pastes the callback address back), but the
fallback in docs/claude-proxy-cutover.md tunnels each provider's port to the server, so every
provider the console signs in must have its port published — on 127.0.0.1 only."""

from pathlib import Path

import yaml

from rsched.endpoints.cliproxy_login import CALLBACK_PORT

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


def test_every_signin_provider_has_its_callback_port_published_on_loopback_only():
    ports = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["cliproxy"]["ports"]
    published = {str(p) for p in ports}
    for provider, port in CALLBACK_PORT.items():
        assert f"127.0.0.1:{port}:{port}" in published, (provider, port, published)
    assert all(str(p).startswith("127.0.0.1:") for p in ports), ports
    assert CALLBACK_PORT["antigravity"] == 51121
