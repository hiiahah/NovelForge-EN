
from typing import List, Optional, Tuple

from loguru import logger
from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import BibleUpdateReview, ForeshadowItem, KGRelation, Project
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.services.kg_provider import get_provider


FREE_PROJECT_NAME = "__free__"

# Projects whose external graph cleanup failed on delete; retried lazily so a
# transient Neo4j outage does not leave undiscoverable orphan graphs.
_pending_graph_cleanup: set[int] = set()


def normalize_project_name(name: Optional[str]) -> str:
    """Trim whitespace and reject blank names."""
    normalized = " ".join(str(name or "").split())
    if not normalized:
        raise ValueError("Project name cannot be blank")
    return normalized


def _find_project_by_name_ci(session: Session, name: str, exclude_id: Optional[int] = None) -> Optional[Project]:
    stmt = select(Project).where(func.lower(Project.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Project.id != exclude_id)
    return session.exec(stmt).first()


# Get or create the reserved project (__free__)
def get_or_create_free_project(session: Session) -> Project:
    proj = session.exec(select(Project).where(Project.name == FREE_PROJECT_NAME)).first()
    if proj:
        return proj
    proj = Project(name=FREE_PROJECT_NAME, description="System reserved project: stores free cards")
    session.add(proj)
    session.commit()
    session.refresh(proj)
    return proj


def get_projects(session: Session) -> List[Project]:
    statement = select(Project).order_by(Project.id.desc())
    return session.exec(statement).all()


def get_project(session: Session, project_id: int) -> Optional[Project]:
    statement = (
        select(Project)
        .where(Project.id == project_id)
    )
    return session.exec(statement).first()


def create_project(session: Session, project_in: ProjectCreate) -> Tuple[Project, List[int]]:
    name = normalize_project_name(project_in.name)
    if name == FREE_PROJECT_NAME:
        raise ValueError(f"'{FREE_PROJECT_NAME}' is a reserved project name")
    if _find_project_by_name_ci(session, name):
        raise ValueError(f"Project name already exists: {name}")

    db_project = Project.model_validate(project_in)
    db_project.name = name
    session.add(db_project)
    session.commit()
    session.refresh(db_project)

    triggered_run_ids: List[int] = []
    template_error: Optional[str] = None
    # Trigger project creation event
    try:
        from app.core import emit_event

        event_data = {
            "session": session,
            "project_id": db_project.id,
            "template": project_in.template,
        }

        emit_event("project.created", event_data)
        triggered_run_ids = event_data.get("triggered_run_ids", [])
        template_error = event_data.get("error")
    except Exception as exc:
        # Do not block project creation, but make template-init failures visible.
        template_error = str(exc)
        logger.warning(f"[ProjectCreate] project.created trigger failed for '{name}': {exc}")

    if project_in.template and not triggered_run_ids:
        # A template was requested but no initialization workflow started: the
        # project must not look fully initialized.
        logger.warning(f"[ProjectCreate] template '{project_in.template}' produced no workflow run for project {db_project.id} ({template_error or 'no matching trigger'})")

    # Refresh to load newly created cards into project relationships
    session.refresh(db_project)

    return db_project, triggered_run_ids


def update_project(session: Session, project_id: int, project_in: ProjectUpdate) -> Optional[Project]:
    db_project = session.get(Project, project_id)
    if not db_project:
        return None
    if db_project.name == FREE_PROJECT_NAME and project_in.model_dump(exclude_unset=True).get("name") not in (None, FREE_PROJECT_NAME):
        raise ValueError(f"The reserved project '{FREE_PROJECT_NAME}' cannot be renamed")
    project_data = project_in.model_dump(exclude_unset=True)
    if "name" in project_data:
        new_name = normalize_project_name(project_data["name"])
        if new_name == FREE_PROJECT_NAME and db_project.name != FREE_PROJECT_NAME:
            raise ValueError(f"'{FREE_PROJECT_NAME}' is a reserved project name")
        if _find_project_by_name_ci(session, new_name, exclude_id=project_id):
            raise ValueError(f"Project name already exists: {new_name}")
        project_data["name"] = new_name
    for key, value in project_data.items():
        setattr(db_project, key, value)
    session.add(db_project)
    session.flush()
    session.refresh(db_project)
    return db_project


def _delete_project_owned_rows(session: Session, project_id: int) -> None:
    """Delete project-owned rows that have no ORM cascade (cards cascade via the
    Project relationship; these tables do not)."""
    for model in (ForeshadowItem, BibleUpdateReview, KGRelation):
        rows = session.exec(select(model).where(model.project_id == project_id)).all()
        for row in rows:
            session.delete(row)


def _cleanup_graph(project_id: int) -> bool:
    try:
        kg = get_provider()
        kg.delete_project_graph(project_id)
        return True
    except Exception as exc:
        logger.warning(f"[ProjectDelete] Graph cleanup failed for project {project_id}; queued for retry: {exc}")
        return False


def retry_pending_graph_cleanup() -> List[int]:
    """Retry external graph cleanup for projects whose delete left a graph behind."""
    done: List[int] = []
    for pid in list(_pending_graph_cleanup):
        if _cleanup_graph(pid):
            _pending_graph_cleanup.discard(pid)
            done.append(pid)
    return done


def pending_graph_cleanup_ids() -> List[int]:
    return sorted(_pending_graph_cleanup)


def delete_project(session: Session, project_id: int) -> bool:
    project = session.get(Project, project_id)
    if not project:
        return False
    # Reserved projects cannot be deleted
    if getattr(project, 'name', None) == FREE_PROJECT_NAME:
        return False
    # SQL side is one transaction: owned rows + cards (cascade) + project.
    try:
        _delete_project_owned_rows(session, project_id)
        session.delete(project)
        session.commit()
    except Exception:
        session.rollback()
        raise
    # External graph cleanup happens after the SQL commit; failures are tracked
    # for retry rather than silently ignored.
    if not _cleanup_graph(project_id):
        _pending_graph_cleanup.add(project_id)
    return True
