from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def load_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ALLOWED_HOSTS", "127.0.0.1,localhost,testserver")
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "2")

    for module_name in [
        "app.config",
        "app.db",
        "app.auth",
        "app.security",
        "app.summarizer",
        "app.main",
    ]:
        sys.modules.pop(module_name, None)

    module = importlib.import_module("app.main")
    importlib.reload(module)
    return module.app


def extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


def register_web_user(client: TestClient, name: str = "Alice", email: str = "alice@example.com", password: str = "pass123"):
    page = client.get("/register")
    csrf_token = extract_csrf(page.text)
    response = client.post(
        "/register",
        data={"csrf_token": csrf_token, "name": name, "email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def get_auth_header(client: TestClient, email: str = "alice@example.com", password: str = "pass123") -> dict[str, str]:
    login_response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_web_forms_require_csrf(tmp_path, monkeypatch):
    app = load_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/register",
            data={"name": "Alice", "email": "alice@example.com", "password": "pass123"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"


def test_api_auth_and_post_flow(tmp_path, monkeypatch):
    app = load_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        register = client.post(
            "/api/v1/auth/register",
            json={"name": "Alice", "email": "alice@example.com", "password": "pass123"},
        )
        assert register.status_code == 200
        headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

        created = client.post(
            "/api/v1/posts",
            json={"title": "AI Future", "content": "<p>Artificial intelligence is changing blogging workflows quickly.</p>"},
            headers=headers,
        )
        assert created.status_code == 200
        post_id = created.json()["id"]

        liked = client.post(f"/api/v1/posts/{post_id}/likes", headers=headers)
        assert liked.status_code == 200
        assert liked.json()["liked"] is True

        comment = client.post(
            f"/api/v1/posts/{post_id}/comments",
            json={"content": "Very useful writeup"},
            headers=headers,
        )
        assert comment.status_code == 200
        assert comment.json()["content"] == "Very useful writeup"

        listing = client.get("/api/v1/posts?q=Future&page=1&page_size=5")
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["pagination"]["total_items"] == 1
        assert payload["items"][0]["like_count"] == 1
        assert payload["items"][0]["comment_count"] == 1


def test_web_post_creation_supports_uploads_and_search(tmp_path, monkeypatch):
    app = load_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        register_web_user(client)

        new_page = client.get("/posts/new")
        csrf_token = extract_csrf(new_page.text)
        response = client.post(
            "/posts/new",
            data={
                "csrf_token": csrf_token,
                "title": "Uploaded Post",
                "content": "<p>This post includes a featured image and searchable text about automation.</p>",
            },
            files={"featured_image": ("cover.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png")},
            follow_redirects=False,
        )
        assert response.status_code == 303

        dashboard = client.get("/dashboard?q=automation")
        assert dashboard.status_code == 200
        assert "Uploaded Post" in dashboard.text
        assert "/uploads/" in dashboard.text
