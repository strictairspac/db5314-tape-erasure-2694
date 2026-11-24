"""gb-5314 结档测试共用夹具：把 MySQL/Redis 替换为内存 SQLite 与桩缓存。"""

import os

os.environ.setdefault('AUTH_KEY', 'test-secret-key')
os.environ['SEED_DEMO'] = 'false'
os.environ['REDIS_URL'] = 'redis://unused:6379/0'
os.environ['DB_DRIVER'] = 'sqlite'
os.environ['DB_NAME'] = ':memory:'

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from src.config import settings
from src.controllers import workflow as workflow_controller
from src.main import app
from src.models.tables import audit, evidence, runs, schema, users
from src.routes import workflow as workflow_routes
from src.services import workflow as workflow_service
from src.services import security as security_service
from src.services.security import hash_password

TEST_ENGINE = create_engine(
    'sqlite://',
    connect_args={'check_same_thread': False},
    poolclass=StaticPool,
)


class FakeCache:
    def __init__(self):
        self.items = []

    def ping(self):
        return True

    def lpush(self, key, value):
        self.items.append(value)

    def ltrim(self, key, start, end):
        self.items = self.items[start:end + 1] if end >= 0 else self.items[start:]

    def brpop(self, key, timeout=0):
        return None


FAKE_CACHE = FakeCache()
settings.engine = TEST_ENGINE
settings.cache = FAKE_CACHE
workflow_service.engine = TEST_ENGINE
workflow_service.cache = FAKE_CACHE
workflow_routes.engine = TEST_ENGINE
workflow_routes.cache = FAKE_CACHE
workflow_controller.engine = TEST_ENGINE
security_service.engine = TEST_ENGINE


def reset_database():
    schema.drop_all(TEST_ENGINE)
    schema.create_all(TEST_ENGINE)
    FAKE_CACHE.items.clear()


def make_client():
    return TestClient(app)


def seed_user(username):
    with TEST_ENGINE.begin() as conn:
        conn.execute(users.insert().values(username=username, role=username,
                         password_hash=hash_password('password')))


def token(client, username):
    response = client.post('/login', json={'username': username, 'password': 'password'})
    assert response.status_code == 200, response.text
    return response.json()['token']


def auth(client, username):
    return {'Authorization': f"Bearer {token(client, username)}"}


def seed_case(payload, title='留存检查样例', author='operator'):
    return workflow_service.make_case(title, payload, author)


def drain_queue():
    """模拟 worker 把当前所有排队/执行中任务跑完。"""
    finished = []
    while True:
        job = workflow_service.claim()
        if job is None:
            break
        run_id, run_token, payload = job
        from src.services.domain import solve
        result = solve(payload, TEST_ENGINE)
        workflow_service.finish(run_id, run_token, result=result)
        finished.append(run_id)
    return finished


def insert_reviewed_evidence(case_id, reviewer='reviewer', note='核对通过'):
    run_id = workflow_service.enqueue(case_id, 'operator')['id']
    (finished_run_id,) = drain_queue()
    assert finished_run_id == run_id
    with TEST_ENGINE.connect() as conn:
        evidence_id = conn.execute(select(evidence.c.id).where(evidence.c.run_id == run_id)).scalar_one()
    workflow_service.review(evidence_id, note, reviewer)
    return run_id, evidence_id


def table_rows(table):
    with TEST_ENGINE.connect() as conn:
        return [dict(row) for row in conn.execute(select(table)).mappings()]


def audit_actions(case_id):
    with TEST_ENGINE.connect() as conn:
        return [row['action'] for row in conn.execute(
            select(audit.c.action).where(audit.c.reference == str(case_id))).mappings()]
