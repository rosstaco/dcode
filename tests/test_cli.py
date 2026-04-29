"""Tests for dcode CLI entrypoint and package metadata."""


class TestVersion:
    def test_resolves_via_importlib_metadata(self):
        from importlib.metadata import version

        import dcode

        assert dcode.__version__ == version("dcode")
