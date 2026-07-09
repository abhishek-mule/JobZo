import logging
from datetime import date
from typing import Sequence

from sqlalchemy import select

from database.models import Task
from database.connection import get_session

logger = logging.getLogger("jobzo.tasks")


def list_pending_tasks() -> Sequence[Task]:
    session = get_session()
    try:
        return session.execute(
            select(Task)
            .where(Task.done == False)
            .order_by(Task.due_date.asc(), Task.created_at.desc())
        ).scalars().all()
    finally:
        session.close()


def list_overdue_tasks() -> Sequence[Task]:
    session = get_session()
    try:
        return session.execute(
            select(Task)
            .where(Task.done == False, Task.due_date < date.today())
            .order_by(Task.due_date.asc())
        ).scalars().all()
    finally:
        session.close()


def create_task(
    type: str,
    title: str,
    application_id: str | None = None,
    due_date: date | None = None,
    notes: str = "",
) -> Task | None:
    session = get_session()
    try:
        task = Task(
            application_id=application_id,
            type=type,
            title=title,
            due_date=due_date or date.today(),
            notes=notes,
        )
        session.add(task)
        session.commit()
        logger.info("Task created: %s", title)
        return task
    except Exception as e:
        session.rollback()
        logger.error("Task creation error: %s", e)
        return None
    finally:
        session.close()


def complete_task(task_id: str) -> bool:
    session = get_session()
    try:
        task = session.execute(
            select(Task).where(Task.id == task_id)
        ).scalar_one_or_none()

        if not task:
            logger.error("Task %s not found", task_id)
            return False

        task.done = True
        session.commit()
        logger.info("Task completed: %s", task_id)
        return True
    except Exception as e:
        session.rollback()
        logger.error("Task completion error: %s", e)
        return False
    finally:
        session.close()
