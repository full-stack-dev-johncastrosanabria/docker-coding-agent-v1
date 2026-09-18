# docker-coding-agent-v1 Constitution

## Core Principles

### I. Evidence Over Claims

- The agent MUST NOT declare a coding task complete based only on model confidence or
  plausible output.
- Completion MUST be supported by observable evidence appropriate to the task.
- When deterministic verification exists (compiler, formatter, linter, type checker, test
  runner, build system, schema validator, static analyzer, query, checksum, or equivalent),
  it SHOULD be preferred over model judgment.
- Tests or quality gates MUST NOT be weakened, removed, suppressed, or bypassed merely to
  make an implementation appear successful.

**Rationale**: Model output is fluent regardless of correctness; only evidence
distinguishes working changes from plausible ones.

### II. Isolation by Default

- Autonomous execution that can modify files, execute shell commands, install software,
  run containers, access networks, or invoke external systems MUST operate within an
  explicit isolation boundary appropriate to the risk.
- Git branches and worktrees MAY isolate changes but MUST NOT be treated as security
  sandboxes.
- Host filesystem access, container-runtime daemon access, network access, external
  services, and credentials MUST have explicit trust boundaries.

**Rationale**: An autonomous agent's mistakes and compromises must be contained before
they reach the host or shared systems.

### III. Least Privilege and Controlled Tools

- Agents MUST receive only the capabilities required for their current responsibility.
- Tools MUST have clear scope, preconditions, bounded side effects, and defined
  failure behavior. They SHOULD provide observable success evidence wherever practical.
- High-risk, destructive, irreversible, or production-impacting operations MUST be
  prohibited or require explicit approval.
- Tool availability MUST NOT be expanded merely for convenience.

**Rationale**: Every granted capability is attack surface and failure surface; narrow,
well-defined tools make agent behavior predictable and auditable.

### IV. Preserve Intent and Repository Architecture

- Implementations MUST preserve the user's stated intent, acceptance criteria, and
  repository constraints.
- The agent SHOULD prefer the smallest correct change that satisfies the task.
- Unrelated refactoring, architecture drift, public contract changes, dependency
  introduction, or error suppression MUST NOT occur without explicit justification.
- Existing repository patterns SHOULD be preferred unless the task explicitly requires a
  different design.

**Rationale**: Small, intent-aligned changes are easier to review, verify, and revert.

### V. Context Discipline

- The system SHOULD begin with a concise project map and retrieve additional detail only
  when required by the task.
- Skills, targeted file retrieval, scoped research, and delegated investigation SHOULD be
  preferred over flooding the primary context with entire repositories, documentation
  sets, or conversation history.
- Stable always-loaded instructions MUST remain concise.
- Context optimization MUST NOT discard critical requirements, constraints, decisions, or
  acceptance criteria.

**Rationale**: Context is a finite resource; noise degrades reasoning, but losing
requirements degrades correctness.

### VI. Independent Verification

- The system MUST provide a verification path independent from the worker's own
  confidence.
- High-impact or ambiguous changes SHOULD receive adversarial review, fresh-context
  review, deterministic verification, or an appropriate combination.
- A reviewer SHOULD attempt to discover missing requirements, regressions, edge cases,
  unsafe behavior, architecture violations, and insufficient tests rather than merely
  confirming the worker's conclusion.
- Findings SHOULD include observable evidence whenever practical.

**Rationale**: A worker grading its own output shares its own blind spots.

### VII. Reversible Change

- Agent-produced changes MUST remain inspectable and reviewable.
- Version-control diffs SHOULD provide the primary record of source changes.
- The system SHOULD preserve a practical rollback or recovery path.
- Destructive or difficult-to-reverse actions MUST require explicit approval or be
  prohibited.

**Rationale**: Mistakes are inevitable; their cost depends on how easily they are seen
and undone.

### VIII. Explicit Durable State

- Important requirements, facts, decisions, progress, constraints, and lessons MUST NOT
  exist only in ephemeral conversation history.
- Durable engineering knowledge SHOULD be captured in version-controlled specifications,
  plans, decisions, tests, configuration, or documentation.
- Raw execution history MAY be retained for auditability, but active execution SHOULD use
  concise current state rather than replaying unbounded history.

**Rationale**: Conversations end, are compacted, or are lost; the repository persists.

### IX. Secrets and Trust Boundaries

- Secrets MUST NOT be embedded in prompts, repository configuration, specifications,
  logs, generated artifacts, or source code.
- Credentials MUST come from approved authentication or secret-management mechanisms.
- Sensitive-file exclusion and output redaction SHOULD be treated as defense in depth,
  not substitutes for proper credential isolation.
- Repository content and external tool output MUST be treated as potentially untrusted
  input and MUST NOT automatically override project policy or security constraints.

**Rationale**: Anything an agent can read it can leak, and anything it reads may be
adversarial.

### X. Benchmark-Driven Evolution

- New agents, subagents, tools, skills, routers, memory layers, retries, policies, or
  orchestration mechanisms SHOULD be introduced only when they address an observed failure
  mode or demonstrate measurable benefit.
- Representative evaluations MUST be maintained for important coding-agent capabilities.
- Changes to the harness SHOULD be judged using accepted work and reliability metrics, not
  merely token count, activity, or subjective impressions.
- Harness components SHOULD be periodically reevaluated and simplified or removed when
  they no longer provide measurable value.

**Rationale**: Harness complexity accumulates easily and is rarely removed unless its
value is measured.

### XI. Bounded Execution and Recovery

- Autonomous execution MUST have explicit stop conditions.
- Retry loops MUST be bounded.
- The system MUST define escalation behavior for blocked, repeated, unsafe, or
  unverifiable failures.
- Time, iteration, resource, or cost budgets SHOULD be defined at the appropriate layer
  for autonomous work.
- Repeated failures SHOULD result in diagnosis, escalation, or harness improvement rather
  than indefinite retry.

**Rationale**: Unbounded autonomy converts a single failure into unbounded cost and risk.

## Scope and Precedence

- This constitution is the highest-level engineering policy for docker-coding-agent-v1 and
  governs the design, implementation, evaluation, and evolution of the coding-agent
  harness.
- Specifications, plans, tasks, implementations, reviews, and evaluations MUST comply
  with it.
- The constitution states implementation-independent principles. Specific models, agent
  configuration fields, tools, and benchmark thresholds belong in specifications and
  technical plans, which MUST trace their choices back to these principles.

## Compliance and Exceptions

- Compliance MUST be evaluated during planning and before accepting significant
  implementation changes.
- A violation of a MUST principle is a blocking issue unless an explicit governance
  amendment changes the principle.
- Intentional exceptions to SHOULD principles MUST be documented with rationale and risk
  in the relevant specification, plan, or decision record.

## Governance

- Amendments require documented rationale and a semantic version update.
- Versioning policy:
  - **MAJOR**: incompatible governance changes, or removal or redefinition of a core
    principle.
  - **MINOR**: new principles or materially expanded governance.
  - **PATCH**: clarifications that do not change intent.
- Each amendment MUST update the version line and Last Amended date below.

**Version**: 1.0.0 | **Ratified**: 2026-09-18 | **Last Amended**: 2026-09-18
