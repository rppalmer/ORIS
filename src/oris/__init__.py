"""ORIS application package."""

import os
import ssl

import certifi


def ensure_certificate_trust() -> None:
    """Give OpenSSL a root store when the interpreter was installed without one.

    A python.org macOS framework build ships no certificate bundle until its
    `Install Certificates.command` has been run, and OpenSSL's default context
    then loads zero roots. `requests` hides this because it carries certifi;
    aiohttp does not, so every HTTPS call it makes fails the handshake while
    the rest of the machine appears fine.

    Setting the variable OpenSSL already consults repairs it for the whole
    process without reaching inside a client this application does not
    construct. An operator who has pointed it somewhere deliberately, and an
    interpreter that already has roots, are both left alone.

    This runs on package import, and that is the whole point rather than an
    accident. aiohttp builds its default SSL context once, at *its* import,
    and caches it in a module global; a repair applied later is read by
    nothing. Importing any `oris` module runs this file first, so this is the
    one place that is reliably early enough. Keep this module free of imports
    that pull in aiohttp, or the ordering it depends on is lost.
    """
    if os.environ.get("SSL_CERT_FILE") or ssl.create_default_context().get_ca_certs():
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()


ensure_certificate_trust()
