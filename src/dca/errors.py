"""Typed launcher failures and their canonical exit statuses (tasks.md T044).

Standard library only. These three exceptions are the ONLY way the launcher leaves a run without a
completion report, so each one carries the exit status the CLI contract assigns it and nothing else
in the codebase invents an exit number.

WHY THESE ARE DISTINCT FROM A TASK OUTCOME. A task that ran and did not succeed is a *disposition*:
it produces a report and exits 10 or 11. The three errors here mean no trustworthy disposition
exists at all - the caller asked for something impossible (2), the host refused before any sandbox
(3), or the infrastructure itself failed (4). data-model.md is explicit that Docker or git
infrastructure failure is NEVER recorded as a task `blocked` outcome, because doing so would report
a judgement about the developer's code that the system never actually formed.
"""

USAGE = 2
PRECONDITION = 3
INFRA = 4

SUCCEEDED = 0
FAILED = 10
BLOCKED = 11

# The only mapping from a final task disposition to a process exit status (contracts/launcher-cli).
OUTCOME_EXIT = {
    "succeeded": SUCCEEDED,
    "failed": FAILED,
    "blocked": BLOCKED,
}


class DcaError(Exception):
    """Base for the three reportless failures. `exit_code` is authoritative."""

    exit_code = INFRA


class UsageError(DcaError):
    """The request itself is unusable: bad flags, undecodable input. No report."""

    exit_code = USAGE


class PreconditionError(DcaError):
    """The host refused before provisioning: dirty tree, stale gate, bad ref. No report.

    A precondition failure leaves no task disposition at all, which is why it is not `blocked`:
    `blocked` is a statement that the agent could not finish the work, and here it never started.
    """

    exit_code = PRECONDITION


class InfraAbort(DcaError):
    """Docker, sbx or git infrastructure failed. No report, best-effort sandbox removal.

    Reached in exactly the three ways data-model.md lists: source-bundle failure before any
    sandbox, provisioning failure before the agent starts, and retrieval failure after it ran.
    """

    exit_code = INFRA


def exit_status_for_outcome(outcome):
    """succeeded -> 0, failed -> 10, blocked -> 11. Anything else is a programming error."""
    try:
        return OUTCOME_EXIT[outcome]
    except KeyError:
        raise ValueError(f"not a final outcome: {outcome!r}") from None
