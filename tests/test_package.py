"""Tests for the initial package scaffold."""


def test_package_imports() -> None:
    """The source package is installed and importable."""
    import oris

    assert oris.__doc__ == "ORIS application package."
