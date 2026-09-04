import pytest
from fastapi.testclient import TestClient
from consentmove.main import app

@pytest.fixture
def client():
    return TestClient(app)
