"""Certificate trust applies to the application, not one search provider."""

import os
import subprocess
import sys

from oris import ensure_certificate_trust


def test_importing_oris_leaves_a_usable_root_store():
    """A fresh process must have trusted roots before its HTTP clients initialize."""
    environment = {
        key: value for key, value in os.environ.items() if key != "SSL_CERT_FILE"
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import oris; import ssl; print(len(ssl.create_default_context().get_ca_certs()))",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    )
    assert int(result.stdout.strip()) > 0


def test_a_deliberate_certificate_setting_is_left_alone(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/company-roots.pem")
    ensure_certificate_trust()
    assert os.environ["SSL_CERT_FILE"] == "/etc/ssl/company-roots.pem"
