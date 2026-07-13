"""tests/test_robots_txt.py — /robots.txt disallows everything, is
reachable without a session (crawlers never authenticate), and is
served at the domain root, not under /static/."""


def test_robots_txt_reachable_without_session():
    import web_viewer
    client = web_viewer.app.test_client()
    r = client.get("/robots.txt")
    assert r.status_code == 200


def test_robots_txt_disallows_everything():
    import web_viewer
    client = web_viewer.app.test_client()
    r = client.get("/robots.txt")
    body = r.get_data(as_text=True)
    assert "User-agent: *" in body
    assert "Disallow: /" in body


def test_robots_txt_is_plain_text():
    import web_viewer
    client = web_viewer.app.test_client()
    r = client.get("/robots.txt")
    assert r.content_type.startswith("text/plain")
