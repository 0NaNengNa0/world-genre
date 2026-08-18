"""Tests for the read path, now that the API serves published JSON.

These replace tests/test_db_integration.py, which ran against a real Postgres
binary via pgserver. There is no equivalent for BigQuery - no local emulator
exists - so the assertion surface moved: instead of checking that SQL against
a real database returns the right rows, these check that the API contract
holds over whatever the publish step wrote, and that a missing publish fails
in a recoverable way rather than a confusing one.

The SQL itself is covered separately, by parsing every query in the BigQuery
dialect (tests/test_bigquery_sql.py). That catches dialect errors without
credentials or a network, which is the class of bug the port could realistically
introduce.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import published


@pytest.fixture
def publish_root(tmp_path, monkeypatch):
    """Point the reader at a temporary directory and clear its cache.

    Cache clearing is essential, not hygiene: the reader holds payloads for a
    TTL, so without this a test would serve whatever a previous test wrote.
    """
    monkeypatch.setenv("PUBLISH_DIR", str(tmp_path))
    published.reset_cache()
    yield tmp_path
    published.reset_cache()


def write(root, relative: str, payload) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


SUMMARY = {
    "code": "jp",
    "name": "Japan",
    "artist_count": 100,
    "top_genres": ["pop", "rock"],
    "distinctive_genres": ["j-pop"],
    "top_artists": ["YOASOBI"],
    "domestic_share": {
        "domestic_percentage": 82.0,
        "coverage_percentage": 60.0,
        "classified_entries": 60,
        "total_entries": 100,
    },
    "cover_image": None,
}

DETAIL = {
    **SUMMARY,
    "artists": ["YOASOBI"],
    "popularity": {
        "genres": [
            {
                "genre": "pop",
                "score": 90,
                "distinctiveness": 0.0,
                "sources": ["lastfm"],
                "percentage": 60.0,
            }
        ],
        "other_percentage": 40.0,
        "other_genre_count": 5,
    },
    "distinctiveness": {
        "genres": [
            {
                "genre": "j-pop",
                "score": 40,
                "distinctiveness": 30.0,
                "sources": ["lastfm"],
                "percentage": 100.0,
            }
        ],
        "other_percentage": 0.0,
        "other_genre_count": 0,
    },
    "top_tracks": [],
    "hidden_gems": [],
    "artist_popularity": [],
    "genre_details": {
        "j-pop": {
            "genre": "j-pop",
            "country_code": "jp",
            "country_name": "Japan",
            "summary": "Japanese pop music.",
            "url": "https://last.fm/tag/j-pop",
            "score": None,
            "distinctiveness": None,
            "artists": [
                {"artist": "YOASOBI", "streams": 5000, "best_position": 1}
            ],
        }
    },
}


class TestBeforeAnythingIsPublished:
    """A deployed API with no publish yet is a real state, not a crash.

    It happens on every first deploy, and the failure has to point at the
    pipeline rather than looking like a bug or a missing country.
    """

    def test_countries_returns_503_not_500(self, publish_root):
        response = TestClient(app, raise_server_exceptions=False).get("/api/countries")
        assert response.status_code == 503
        assert "publish" in response.json()["detail"].lower()

    def test_health_reports_degraded(self, publish_root):
        response = TestClient(app, raise_server_exceptions=False).get("/api/health")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"

    def test_health_leaks_no_location(self, publish_root):
        # This endpoint is public and unauthenticated. It must never disclose
        # a bucket name, path or project id.
        body = TestClient(app, raise_server_exceptions=False).get("/api/health").text
        assert str(publish_root) not in body
        assert "gs://" not in body


class TestServingPublishedData:
    def test_countries_list(self, publish_root):
        write(publish_root, "countries.json", {"countries": [SUMMARY]})
        data = TestClient(app).get("/api/countries").json()
        assert [c["code"] for c in data["countries"]] == ["jp"]

    def test_country_detail(self, publish_root):
        write(publish_root, "country/jp.json", DETAIL)
        data = TestClient(app).get("/api/countries/jp").json()
        assert data["name"] == "Japan"
        assert data["popularity"]["genres"][0]["genre"] == "pop"

    def test_country_code_is_case_insensitive(self, publish_root):
        # The frontend passes codes straight through from ISO data, which is
        # not consistently lowercased.
        write(publish_root, "country/jp.json", DETAIL)
        assert TestClient(app).get("/api/countries/JP").status_code == 200

    def test_unknown_country_is_404_not_503(self, publish_root):
        write(publish_root, "countries.json", {"countries": [SUMMARY]})
        response = TestClient(app, raise_server_exceptions=False).get(
            "/api/countries/zz"
        )
        assert response.status_code == 404

    def test_genre_detail_comes_from_the_country_payload(self, publish_root):
        # Nested rather than its own file, so this is a dict lookup on data
        # already loaded rather than a second fetch.
        write(publish_root, "country/jp.json", DETAIL)
        data = TestClient(app).get("/api/countries/jp/genres/j-pop").json()
        assert data["summary"] == "Japanese pop music."
        assert data["artists"][0]["artist"] == "YOASOBI"

    def test_unknown_genre_is_404(self, publish_root):
        write(publish_root, "country/jp.json", DETAIL)
        response = TestClient(app, raise_server_exceptions=False).get(
            "/api/countries/jp/genres/polka"
        )
        assert response.status_code == 404

    def test_health_ok_once_published(self, publish_root):
        write(publish_root, "countries.json", {"countries": [SUMMARY]})
        assert TestClient(app).get("/api/health").json()["status"] == "ok"


class TestCaching:
    def test_repeat_reads_do_not_rehit_storage(self, publish_root):
        """The whole point of publishing is that reads are cheap.

        Against GCS each miss is an HTTP round trip, so a per-request read
        would reintroduce the latency the publish step exists to remove.
        """
        write(publish_root, "countries.json", {"countries": [SUMMARY]})
        client = TestClient(app)
        client.get("/api/countries")

        (publish_root / "countries.json").unlink()
        # Still served: the payload is cached, not re-read.
        assert client.get("/api/countries").status_code == 200

    def test_reset_cache_forces_a_reread(self, publish_root):
        write(publish_root, "countries.json", {"countries": [SUMMARY]})
        client = TestClient(app)
        client.get("/api/countries")

        write(publish_root, "countries.json", {"countries": []})
        published.reset_cache()
        assert client.get("/api/countries").json()["countries"] == []


class TestFrontendServing:
    """The SPA is served from this same service, which is what removes CORS.

    Same-origin means the browser never makes a cross-origin request, so
    there is no allow-list to keep in sync with each deploy - the failure mode
    that makes a working API look like a broken site.
    """

    @pytest.fixture
    def with_frontend(self, tmp_path, monkeypatch):
        import importlib

        from app import main

        static = tmp_path / "static"
        (static / "assets").mkdir(parents=True)
        (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
        (static / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
        monkeypatch.setenv("FRONTEND_DIR", str(static))
        return TestClient(importlib.reload(main).app)

    def test_root_serves_the_app(self, with_frontend):
        response = with_frontend.get("/")
        assert response.status_code == 200
        assert "<html>" in response.text

    def test_client_route_serves_the_app_not_a_404(self, with_frontend):
        # Deep links must work on refresh; the server has no such route.
        assert with_frontend.get("/compare").status_code == 200

    def test_unknown_api_path_is_404_not_the_app(self, with_frontend):
        # Without the guard this returns index.html with status 200, which a
        # client sees as "unexpected token <" rather than a missing endpoint.
        response = with_frontend.get("/api/nope", follow_redirects=False)
        assert response.status_code == 404
        assert "<html>" not in response.text

    def test_missing_asset_is_404_not_the_app(self, with_frontend):
        # Handing index.html back for a missing .js makes the browser try to
        # parse HTML as JavaScript - a needlessly confusing failure.
        assert with_frontend.get("/assets/missing.js").status_code == 404
