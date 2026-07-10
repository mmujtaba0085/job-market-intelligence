import pytest


@pytest.fixture(autouse=True)
def _clear_flask_cache():
    import web_viewer
    web_viewer.cache.clear()
    yield
