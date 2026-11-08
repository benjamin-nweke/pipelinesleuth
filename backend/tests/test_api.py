"""
FastAPI Integration Tests
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from backend.app.main import app
from backend.app.database.session import init_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_dashboard_stats_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    assert "metrics" in data
    assert "total_investigations" in data["metrics"]


@pytest.mark.asyncio
async def test_demos_list_and_load():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # List demos
        list_resp = await ac.get("/api/demos")
        assert list_resp.status_code == 200
        demos = list_resp.json()
        assert len(demos) == 3
        keys = [d["key"] for d in demos]
        assert "schema_drift" in keys
        assert "data_quality" in keys
        assert "type_mismatch" in keys

        # Load demo scenario A
        load_resp = await ac.post("/api/demos/schema_drift/load")
        assert load_resp.status_code == 200
        proj = load_resp.json()
        assert proj["id"] is not None
        assert "Schema Drift" in proj["name"]
        assert len(proj["files"]) >= 3


@pytest.mark.asyncio
async def test_settings_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "ai_provider" in data
        assert "docker_available" in data
