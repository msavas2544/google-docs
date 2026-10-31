import pytest

from google_docs.testing import FakeDocs


@pytest.fixture
def fake():
    return FakeDocs()


@pytest.fixture
def docs(fake):
    with fake.client() as client:
        yield client
