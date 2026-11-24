import hashlib
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update

from src.config.settings import cache, engine
from src.constants.workflow import Status
from src.models.tables import audit, cases, evidence, runs
from src.services import domain
from src.services.closure import is_closed, scrub_payload
from src.utils.encoding import canonical
from src.utils.time import utc_time


def log(conn, actor, action, reference):
    conn.execute(audit.insert().values(actor=actor, action=action, reference=str(reference), created_at=utc_time()))


def recent(table):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table).order_by(table.c.id.desc()).limit(100)).mappings()]


def found(conn, table, identity):
    row = conn.execute(select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该记录')
    return dict(row)


def make_case(title, payload, actor):
    title = title.strip()
    if not title:
        raise HTTPException(422, '档案名称不能为空')
    try:
        if len(canonical(payload)) > 512 * 1024:
            raise ValueError('业务输入超过 512 KiB')
        if callable(getattr(domain, 'validate', None)):
            domain.validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    with engine.begin() as conn:
        case_id = conn.execute(cases.insert().values(slug=uuid.uuid4().hex, title=title, payload=payload,
                               author=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'create_case', case_id)
        return found(conn, cases, case_id)


def enqueue(case_id, actor):
    with engine.begin() as conn:
        # 行锁与结档串行化：关档后的新请求在此退回，结档前已受理的请求照常入队。
        case = conn.execute(select(cases).where(cases.c.id == case_id).with_for_update()).mappings().first()
        if case is None:
            raise HTTPException(404, '找不到该记录')
        if is_closed(dict(case)):
            raise HTTPException(409, '档案已结档，不再受理新的执行请求')
        # 快照固定受理时的原资料，结档后排队任务仍按原资料执行。
        run_id = conn.execute(runs.insert().values(case_id=case_id, status=Status.QUEUED,
                              owner=actor, payload_snapshot=case['payload'],
                              created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'enqueue', run_id)
        result = found(conn, runs, run_id)
    try:
        cache.lpush('worker:wake', 'queued')
        cache.ltrim('worker:wake', 0, 99)
    except Exception:
        # The durable database queue is polled even if Redis is temporarily unavailable.
        pass
    return result


def claim():
    ready = or_(runs.c.status == Status.QUEUED,
                and_(runs.c.status == Status.RUNNING, runs.c.started_at < utc_time(-600)))
    with engine.begin() as conn:
        run = conn.execute(select(runs.c.id, runs.c.case_id, runs.c.payload_snapshot).where(ready)
                           .order_by(runs.c.id).limit(1).with_for_update(skip_locked=True)).first()
        if not run:
            return None
        token = str(uuid.uuid4())
        conn.execute(runs.update().where(runs.c.id == run.id).values(
            started_at=utc_time(), status=Status.RUNNING, attempt_token=token))
        if run.payload_snapshot is None:
            raise RuntimeError(f'执行 {run.id} 缺少受理时的资料快照，无法按原资料执行')
        return run.id, token, run.payload_snapshot


def finish(run_id, token, result=None, error=None):
    digest = hashlib.sha256(canonical(result)).hexdigest() if result is not None else None
    status = Status.FAILED if error else Status.COMPLETE
    with engine.begin() as conn:
        count = conn.execute(runs.update().where(runs.c.id == run_id,
                        runs.c.attempt_token == token, runs.c.status == Status.RUNNING)
                        .values(status=status, finished_at=utc_time(), error=error,
                                payload_snapshot=None)).rowcount
        if not count:
            return
        if result is not None:
            conn.execute(evidence.insert().values(run_id=run_id, report=result, digest=digest))
        log(conn, 'worker', status.value, run_id)


def review(identity, note, actor):
    note = note.strip()
    if not note:
        raise HTTPException(422, '复核意见不能为空')
    with engine.begin() as conn:
        found(conn, evidence, identity)
        count = conn.execute(evidence.update().where(evidence.c.id == identity, evidence.c.reviewed_at.is_(None))
                            .values(review_note=note, reviewer=actor, reviewed_at=utc_time())).rowcount
        if count != 1:
            raise HTTPException(409, '该证据已经复核')
        log(conn, actor, 'review', identity)
        return found(conn, evidence, identity)


def close_case(case_id, actor):
    """复核员签署后由经办员结档。

    整段处理在单个事务内完成，中断回滚后重试不会重复登记；
    已结档的档案直接返回当前状态，不重复写入审计。
    """
    with engine.begin() as conn:
        case = conn.execute(select(cases).where(cases.c.id == case_id).with_for_update()).mappings().first()
        if case is None:
            raise HTTPException(404, '找不到该记录')
        case_row = dict(case)
        if is_closed(case_row):
            # 幂等重试：结档已完成，不重复登记。
            return case_row
        signed = conn.execute(
            select(evidence.c.id).select_from(evidence.join(runs, runs.c.id == evidence.c.run_id))
            .where(runs.c.case_id == case_id, evidence.c.reviewed_at.is_not(None)).limit(1)).first()
        if signed is None:
            raise HTTPException(409, '复核员尚未签署任何证据，不能结档')
        conn.execute(cases.update().where(cases.c.id == case_id).values(
            payload=scrub_payload(case_row['payload']),
            closed_at=utc_time(), closed_by=actor))
        # 已结束执行的快照随即擦除；排队或执行中的任务保留快照，完成后由 finish 擦除。
        conn.execute(update(runs).where(runs.c.case_id == case_id,
                     runs.c.status.in_((Status.COMPLETE, Status.FAILED))).values(payload_snapshot=None))
        log(conn, actor, 'close_case', case_id)
        return found(conn, cases, case_id)
