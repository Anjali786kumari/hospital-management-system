import pytest

from app import create_app, db
from .conftest import login


def test_anonymous_redirected_to_login(client):
    r = client.get("/patients/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_login_success_and_failure(client):
    assert login(client, "admin", "wrong").status_code == 401
    assert login(client, "admin", "Admin@123").status_code == 302
    assert client.get("/").status_code == 200


def test_open_redirect_blocked(client):
    r = client.post("/login?next=//evil.com", data={"username": "admin", "password": "Admin@123"})
    assert r.headers["Location"] == "/"


def test_lockout_after_repeated_failures(client):
    for _ in range(5):
        login(client, "admin", "bad")
    r = login(client, "admin", "Admin@123")  # correct password but locked
    assert r.status_code == 429


def test_inactive_user_cannot_login(app, client):
    from app.models import User
    with app.app_context():
        User.query.filter_by(username="reception").one().is_active = False
        db.session.commit()
    assert login(client, "reception", "Reception@1").status_code == 401


@pytest.mark.parametrize("who,url,code", [
    ("reception", "/admin/users", 403), ("reception", "/admin/reports", 403),
    ("doc1", "/admin/users", 403), ("doc1", "/billing/", 403), ("doc1", "/appointments/new", 403),
    ("doc1", "/patients/new", 403),
    ("admin", "/admin/users", 200), ("admin", "/billing/", 200), ("admin", "/admin/reports", 200),
    ("reception", "/billing/", 200), ("reception", "/patients/new", 200), ("doc1", "/patients/", 200),
    ("reception", "/doctors", 200), ("doc1", "/", 200),
])
def test_role_matrix(as_role, who, url, code):
    assert as_role(who).get(url).status_code == code


def test_receptionist_cannot_see_clinical_history(as_role, ids):
    html = as_role("reception").get(f"/patients/{ids['p1']}").get_data(as_text=True)
    assert "Medical history" not in html


def test_csrf_enforced_when_enabled():
    class C:
        pass
    import config
    cfg = type("Cfg", (config.TestConfig,), {"CSRF_ENABLED": True})
    app = create_app(cfg)
    with app.app_context():
        db.create_all()
        c = app.test_client()
        assert c.post("/login", data={"username": "x", "password": "y"}).status_code == 400
        db.drop_all()


def test_logout_requires_post(as_role):
    c = as_role("admin")
    assert c.get("/logout").status_code == 405
    assert c.post("/logout").status_code == 302
    assert c.get("/patients/").status_code == 302
