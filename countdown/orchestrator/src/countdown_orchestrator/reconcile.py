"""One pass of the orchestrator: bring the renderers in line with what the broker wants."""

import logging
from typing import TYPE_CHECKING

from countdown_orchestrator.models import Assignment, Status, valid_slug
from countdown_orchestrator.runtime import Renderer, Runtime

if TYPE_CHECKING:
    from countdown_orchestrator.broker import OrchestratorBroker

logger = logging.getLogger(__name__)


def reconcile(broker: OrchestratorBroker, runtime: Runtime, max_renderers: int | None = None) -> None:
    """Make, start, stop and delete renderers until they match the broker's assignments.

    Idempotent, so a crash or a reboot only means running it again. One assignment failing is reported and never
    holds up the others. `max_renderers` is the host's capacity: an assignment beyond it stays pending until
    there's room (the one place that, later, chooses which renderer a screen goes to).
    """
    wanted = broker.assignments()
    existing = runtime.renderers()

    for assignment in wanted:
        try:
            _reconcile_one(broker, runtime, assignment, existing, max_renderers)
        except Exception as e:
            logger.exception("Couldn't reconcile %s", assignment.slug)
            _report(broker, assignment, Status.FAILED, f"{type(e).__name__}: {e}")

    for slug in existing.keys() - {a.slug for a in wanted}:
        # Not removed: a broker that is wrong or unreachable-then-empty must never delete every identity.
        logger.warning("Renderer %s isn't wanted by any assignment; leaving it alone", slug)


def _reconcile_one(
    broker: OrchestratorBroker,
    runtime: Runtime,
    assignment: Assignment,
    existing: dict[str, Renderer],
    max_renderers: int | None,
) -> None:
    if assignment.status == Status.DELETING:
        runtime.remove(assignment.slug)
        existing.pop(assignment.slug, None)
        broker.report(assignment, Status.DELETED)
        return

    if not valid_slug(assignment.slug):
        _report(broker, assignment, Status.FAILED, f"{assignment.slug!r} isn't a valid slug")
        return

    current = existing.get(assignment.slug)
    if assignment.status == Status.SUSPENDED:
        # Kept, stopped: ensure() starts it again when the broker lists the assignment as pending.
        if current is not None and current.running:
            runtime.stop(assignment.slug)
            existing[assignment.slug] = Renderer(assignment.slug, running=False, up_to_date=current.up_to_date)
            logger.info("Suspended renderer %s", assignment.slug)
        return

    if current and current.running and current.up_to_date:
        if assignment.status != Status.PROVISIONED:
            broker.report(assignment, Status.PROVISIONED)
        return

    running = sum(r.running for r in existing.values())
    starting = current is None or not current.running
    if starting and max_renderers and running >= max_renderers:
        logger.warning("At capacity (%d renderers running); %s stays pending", max_renderers, assignment.slug)
        return

    runtime.ensure(assignment)
    existing[assignment.slug] = Renderer(assignment.slug, running=True)
    if assignment.status != Status.PROVISIONED:
        broker.report(assignment, Status.PROVISIONED)
    logger.info("Provisioned renderer %s", assignment.slug)


def _report(broker: OrchestratorBroker, assignment: Assignment, status: Status, detail: str) -> None:
    """Report without raising, so a broker error can't stop the pass; it's retried on the next one."""
    if assignment.status == status:
        return  # already told
    try:
        broker.report(assignment, status, detail)
    except Exception:
        logger.exception("Couldn't report %s for %s", status, assignment.slug)
