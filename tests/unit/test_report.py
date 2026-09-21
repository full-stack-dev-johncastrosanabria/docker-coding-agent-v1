"""Unit tests for completion-report finalization (tasks.md T043, src/dca/report.py).

The report is the artifact a developer trusts a change on, so these tests are mostly about one
thing: the launcher computes `final_outcome` and never takes it from the agent unchecked. Nearly
every case below hands the agent the claim `succeeded` and then asserts what the host-side evidence
actually permits - which is how a real over-claiming agent would present itself.

Every report produced here is additionally validated against
`contracts/completion-report.schema.json`, so a rule that D-FIN and the schema both express can
never drift apart between them.
"""

import importlib.util
import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "work"
SCHEMA = ROOT / "specs" / "001-bounded-coding-agent" / "contracts" / "completion-report.schema.json"


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


errors = _load("dca_errors", ROOT / "src" / "dca" / "errors.py")
events = _load("dca_events", ROOT / "src" / "dca" / "events.py")
report = _load("dca_report", ROOT / "src" / "dca" / "report.py")

RUN_ID = "run-2026-09-21T04-05-06Z-a1b2c3"
FINGERPRINT = "sha256:" + "0" * 64
COMMIT = "1" * 40

SOURCE = {"ref": "refs/heads/work", "commit": COMMIT, "bundle_sha256": "a" * 64,
          "uncommitted_ignored": False}
SANDBOX = {"mountless": True, "shared_skills": "off", "ssh_agent_forwarding": False,
           "network_policy_digest": "sha256:" + "b" * 64}
CHANGE_SET = {"branch": f"dca/{RUN_ID}", "base_commit": COMMIT, "head_commit": "2" * 40,
              "files": [{"path": "src/x.py", "status": "modified"}]}
VERSIONS = {"docker_agent": "v1.136.0", "claude_code": "2.1.278", "sbx": "v0.43.0", "drift": False}
LIMITS = {"steps": 60, "retries": 3, "wall_clock_seconds": 900, "tokens": 300000}


def check(identifier="make test", required=True, result="pass", fresh=True, by="launcher"):
    return {"id": identifier, "command_or_method": identifier, "required": required,
            "executed_by": by, "after_last_change": fresh, "result": result}


def agent_report(outcome="succeeded", value="direct", verification_type="deterministic",
                 checks=None, criteria=("the bug is fixed",), plan_ref=None, review=None,
                 **extra):
    body = {
        "outcome": outcome,
        "classification": {"value": value, "reason": "one file, one behaviour"},
        "verification": {"type": verification_type,
                         "checks": list(checks if checks is not None else [check()])},
        "acceptance_criteria": [{"text": text, "status": "satisfied"} for text in criteria],
        "risks": [],
        "blockers": [],
    }
    if plan_ref is not None:
        body["plan_ref"] = plan_ref
    if review is not None:
        body["review"] = review
    body.update(extra)
    return body


def analysis(stream_lines=(), exit_status=0, host_stop=None, sandbox_created=True):
    text = "\n".join([json.dumps({"type": "stream_started", "session_id": "s"})]
                     + list(stream_lines)
                     + [json.dumps({"type": "stream_stopped", "session_id": "s",
                                    "reason": "normal"})]) + "\n"
    return events.analyze(text, exit_status=exit_status, host_stop=host_stop,
                          sandbox_created=sandbox_created)


class ReportCase(unittest.TestCase):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def build(self, agent=None, run=None, change_set=None, source=None, sandbox=SANDBOX,
              approvals=(), limit_evidence_predates=None, launcher_checks=(), **kwargs):
        built = report.build(
            run_id=RUN_ID, backend="claude", trust_level="trusted",
            task_fingerprint=FINGERPRINT,
            source=dict(source or SOURCE),
            sandbox_settings=sandbox,
            analysis=run if run is not None else analysis(),
            agent_report=agent if agent is not None else agent_report(),
            change_set=dict(change_set or CHANGE_SET),
            limits_configured=LIMITS, versions=VERSIONS,
            launcher_checks=launcher_checks, approvals=approvals,
            limit_evidence_predates=limit_evidence_predates, **kwargs)
        self.assertEqual(report.validate(built, self.schema), built)
        return built


# --- D-FIN rule 1: no usable agent report ---------------------------------------------------------


class TestMissingAgentReport(ReportCase):
    def test_01_a_missing_report_is_blocked(self):
        built = self.build(agent=None if False else {})
        self.assertEqual(built["agent_outcome"], "missing")
        self.assertEqual(built["final_outcome"], "blocked")
        self.assertIsNone(built["classification"])
        self.assertIsNone(built["verification"])

    def test_02_a_report_that_is_not_an_object_is_missing(self):
        for value in ("succeeded", ["succeeded"], 7):
            with self.subTest(value=value):
                self.assertEqual(self.build(agent=value)["agent_outcome"], "missing")

    def test_03_an_unknown_outcome_word_is_missing(self):
        self.assertEqual(self.build(agent=agent_report(outcome="done"))["agent_outcome"],
                         "missing")

    def test_04_a_report_without_a_classification_is_missing(self):
        body = agent_report()
        del body["classification"]
        self.assertEqual(self.build(agent=body)["agent_outcome"], "missing")

    def test_05_a_report_without_verification_is_missing(self):
        body = agent_report()
        del body["verification"]
        self.assertEqual(self.build(agent=body)["agent_outcome"], "missing")

    def test_06_blocked_always_names_a_next_step(self):
        built = self.build(agent={})
        self.assertTrue(built["primary_reason"])
        self.assertTrue(built["human_action_required"])


# --- D-FIN rule 2: run integrity ------------------------------------------------------------------


class TestRunIntegrity(ReportCase):
    def test_10_a_malformed_stream_is_blocked_however_the_agent_claimed(self):
        built = self.build(run=events.analyze("garbage\n", exit_status=0))
        self.assertEqual(built["run_integrity"]["stream"], "malformed")
        self.assertEqual(built["final_outcome"], "blocked")
        self.assertEqual(built["outcome_overrides"][0]["from"], "succeeded")

    def test_11_a_truncated_stream_is_blocked(self):
        built = self.build(run=events.analyze(
            json.dumps({"type": "stream_started", "session_id": "s"}) + "\n", exit_status=0))
        self.assertEqual(built["final_outcome"], "blocked")

    def test_12_an_abnormal_exit_is_blocked(self):
        built = self.build(run=analysis(exit_status=137))
        self.assertEqual(built["final_outcome"], "blocked")

    def test_13_a_host_stop_is_not_by_itself_a_block(self):
        built = self.build(run=analysis(host_stop={"reason": "wall_clock"}),
                           limit_evidence_predates=True)
        self.assertEqual(built["run_integrity"]["stream"], "host-terminated")
        self.assertEqual(built["run_integrity"]["agent_exit"], "host-limit")
        self.assertEqual(built["final_outcome"], "succeeded")

    def test_14_no_sandbox_produces_the_shape_the_schema_demands(self):
        built = self.build(
            run=analysis(sandbox_created=False),
            source=dict(SOURCE, bundle_sha256=None),
            sandbox=None,
            change_set=dict(CHANGE_SET, files=[], head_commit=None, branch=None),
            primary_reason="no backend satisfies the required security gates",
            human_action_required="re-run --trust trusted, or fix the failing gate")
        self.assertEqual(built["run_integrity"],
                         {"stream": "none", "agent_exit": "not-started", "sandbox_created": False})
        self.assertIsNone(built["sandbox_settings"])
        self.assertIsNone(built["classification"])
        self.assertIsNone(built["verification"])
        self.assertIsNone(built["source"]["bundle_sha256"])
        self.assertEqual(built["final_outcome"], "blocked")


# --- D-FIN rule 3: none-adequate -------------------------------------------------------------------


class TestNoneAdequate(ReportCase):
    def test_20_none_adequate_is_blocked_with_an_empty_change_set(self):
        built = self.build(agent=agent_report(verification_type="none-adequate", checks=[]),
                           change_set=dict(CHANGE_SET, files=[]))
        self.assertEqual(built["final_outcome"], "blocked")
        self.assertEqual(built["change_set"]["files"], [])

    def test_21_none_adequate_with_files_changed_is_a_safety_event(self):
        built = report.build(
            run_id=RUN_ID, backend="claude", trust_level="trusted",
            task_fingerprint=FINGERPRINT, source=dict(SOURCE), sandbox_settings=SANDBOX,
            analysis=analysis(), agent_report=agent_report(verification_type="none-adequate",
                                                           checks=[]),
            change_set=dict(CHANGE_SET), limits_configured=LIMITS, versions=VERSIONS)
        self.assertEqual(built["final_outcome"], "blocked")
        self.assertIn(report.UNVERIFIED_CHANGES, built["safety_events"])


# --- D-FIN rule 4: success ---------------------------------------------------------------------------


class TestSuccess(ReportCase):
    def test_30_a_direct_task_with_a_passing_required_check_succeeds(self):
        built = self.build()
        self.assertEqual(built["final_outcome"], "succeeded")
        self.assertEqual(built["outcome_overrides"], [])
        self.assertIsNone(built["primary_reason"])
        self.assertEqual(report.exit_status(built), 0)

    def test_31_a_direct_task_needs_no_plan_and_no_review(self):
        built = self.build(agent=agent_report(value="direct", plan_ref=None, review=None))
        self.assertEqual(built["final_outcome"], "succeeded")

    def test_32_a_stale_required_check_blocks_success(self):
        built = self.build(agent=agent_report(checks=[check(fresh=False)]))
        self.assertEqual(built["final_outcome"], "blocked")
        self.assertIn("stale", built["primary_reason"])

    def test_33_a_failing_required_check_blocks_success_even_when_others_pass(self):
        built = self.build(agent=agent_report(checks=[
            check("make test", result="fail"), check("make lint", result="pass")]))
        self.assertEqual(built["final_outcome"], "failed")
        self.assertEqual(report.exit_status(built), 10)

    def test_34_no_required_check_at_all_blocks_success(self):
        built = self.build(agent=agent_report(checks=[check(required=False)]))
        self.assertEqual(built["final_outcome"], "blocked")

    def test_35_an_unsatisfied_acceptance_criterion_blocks_success(self):
        body = agent_report()
        body["acceptance_criteria"] = [{"text": "the bug is fixed", "status": "unsatisfied"}]
        built = self.build(agent=body)
        self.assertNotEqual(built["final_outcome"], "succeeded")

    def test_36_no_acceptance_criteria_at_all_blocks_success(self):
        built = self.build(agent=agent_report(criteria=()))
        self.assertNotEqual(built["final_outcome"], "succeeded")

    def test_37_the_launcher_reexecution_overrides_the_agents_own_run(self):
        built = self.build(agent=agent_report(checks=[check(by="agent", result="pass")]),
                           launcher_checks=[check(by="launcher", result="fail")])
        checks = built["verification"]["checks"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["executed_by"], "launcher")
        self.assertEqual(built["final_outcome"], "failed")

    def test_38_an_alternative_approach_can_also_succeed(self):
        built = self.build(agent=agent_report(
            verification_type="alternative",
            alternative_definition="compare rendered output against a recorded golden file",
            limitation="no repository-established deterministic check covers rendering"))
        self.assertEqual(built["final_outcome"], "succeeded")

    def test_39_an_alternative_approach_always_records_its_limitation(self):
        built = self.build(agent=agent_report(verification_type="alternative"))
        self.assertTrue(built["verification"]["alternative_definition"])
        self.assertTrue(built["verification"]["limitation"])


# --- FR-023a: success at a limit ------------------------------------------------------------------


class TestSuccessAtALimit(ReportCase):
    def test_40_a_limit_without_proof_that_the_evidence_predates_it_blocks_success(self):
        for reason in events.HOST_LIMIT_REASONS:
            with self.subTest(reason=reason):
                built = self.build(run=analysis(host_stop={"reason": reason}))
                self.assertEqual(built["final_outcome"], "blocked")
                self.assertIn("FR-023a", built["primary_reason"])

    def test_41_a_limit_with_proof_the_evidence_predates_it_allows_success(self):
        built = self.build(run=analysis(host_stop={"reason": "steps"}),
                           limit_evidence_predates=True)
        self.assertEqual(built["final_outcome"], "succeeded")
        self.assertEqual(built["limits"]["limit_reached"], "steps")

    def test_42_proof_cannot_rescue_a_failing_check(self):
        built = self.build(run=analysis(host_stop={"reason": "steps"}),
                           limit_evidence_predates=True,
                           agent=agent_report(checks=[check(result="fail")]))
        self.assertNotEqual(built["final_outcome"], "succeeded")


# --- native ceilings -------------------------------------------------------------------------------


class TestNativeCeiling(ReportCase):
    def ceiling_run(self):
        return events.analyze(
            "\n".join([json.dumps({"type": "stream_started", "session_id": "s"}),
                       json.dumps({"type": "budget_exceeded", "agent_name": "root",
                                   "budget": "max_tokens", "used": 120000, "max": 100000,
                                   "config_path": "budget.max_tokens"})]) + "\n",
            exit_status=1)

    def test_50_a_native_ceiling_is_a_task_outcome_not_an_abort(self):
        built = self.build(run=self.ceiling_run())
        self.assertEqual(built["run_integrity"]["stream"], "complete")
        self.assertEqual(built["run_integrity"]["agent_exit"], "normal")
        self.assertEqual(built["limits"]["limit_reached"], "native_ceiling")
        self.assertEqual(built["final_outcome"], "blocked")
        self.assertEqual(report.exit_status(built), 11)

    def test_51_the_native_config_path_is_named_in_the_reason(self):
        built = self.build(run=self.ceiling_run())
        self.assertEqual(built["limits"]["native_ceiling"]["config_path"], "budget.max_tokens")
        self.assertIn("native_ceiling", built["primary_reason"])

    def test_52_fr_023a_still_allows_success_at_a_native_ceiling(self):
        built = self.build(run=self.ceiling_run(), limit_evidence_predates=True)
        self.assertEqual(built["final_outcome"], "succeeded")

    def test_53_native_ceiling_is_null_when_no_ceiling_fired(self):
        built = self.build()
        self.assertIsNone(built["limits"]["native_ceiling"])


# --- planned tasks (CR3) ------------------------------------------------------------------------------


class TestPlannedTasks(ReportCase):
    GOOD_REVIEW = {"performed": True, "identical": True,
                   "fingerprint_before": "sha256:" + "c" * 64,
                   "fingerprint_after": "sha256:" + "c" * 64}

    def planned(self, **overrides):
        body = agent_report(value="planned", plan_ref="/run/dca/out/plan.md",
                            review=dict(self.GOOD_REVIEW))
        body.update(overrides)
        return body

    def test_60_planned_with_plan_and_an_identical_review_can_succeed(self):
        built = self.build(agent=self.planned())
        self.assertEqual(built["final_outcome"], "succeeded")

    def test_61_planned_without_a_plan_ref_is_blocked(self):
        built = self.build(agent=self.planned(plan_ref=None))
        self.assertEqual(built["final_outcome"], "blocked")
        self.assertIn("plan", built["primary_reason"].lower() + " ".join(built["blockers"]))

    def test_62_planned_without_a_review_is_blocked(self):
        built = self.build(agent=self.planned(review={"performed": False}))
        self.assertEqual(built["final_outcome"], "blocked")

    def test_63_planned_with_a_changed_candidate_is_blocked_and_records_the_safety_event(self):
        built = self.build(agent=self.planned(review=dict(self.GOOD_REVIEW, identical=False)))
        self.assertNotEqual(built["final_outcome"], "succeeded")
        self.assertIn(report.REVIEWER_MISMATCH, built["safety_events"])

    def test_64_a_conclusive_failure_before_any_limit_beats_a_missing_review(self):
        built = self.build(agent=self.planned(
            review={"performed": False},
            verification={"type": "deterministic", "checks": [check(result="fail")]}))
        self.assertEqual(built["final_outcome"], "failed")

    def test_65_a_direct_task_with_a_mismatched_review_is_still_not_a_success(self):
        built = self.build(agent=agent_report(
            value="direct", review=dict(self.GOOD_REVIEW, identical=False)))
        self.assertNotEqual(built["final_outcome"], "succeeded")
        self.assertIn(report.REVIEWER_MISMATCH, built["safety_events"])

    def test_66_a_direct_task_with_an_identical_review_succeeds(self):
        built = self.build(agent=agent_report(value="direct", review=dict(self.GOOD_REVIEW)))
        self.assertEqual(built["final_outcome"], "succeeded")


# --- FR-035a: failed vs blocked -----------------------------------------------------------------------


class TestFailedVersusBlocked(ReportCase):
    def test_70_a_check_that_could_not_run_is_blocked_not_failed(self):
        for result in ("error", "unresolved"):
            with self.subTest(result=result):
                built = self.build(agent=agent_report(checks=[check(result=result)]))
                self.assertEqual(built["final_outcome"], "blocked")

    def test_71_a_conclusive_failure_before_any_limit_is_failed(self):
        built = self.build(agent=agent_report(checks=[check(result="fail")]))
        self.assertEqual(built["final_outcome"], "failed")
        self.assertIsNone(built["human_action_required"])

    def test_72_a_failure_at_a_limit_is_blocked_because_it_may_be_unfinished(self):
        built = self.build(agent=agent_report(checks=[check(result="fail")]),
                           run=analysis(host_stop={"reason": "steps"}))
        self.assertEqual(built["final_outcome"], "blocked")

    def test_73_an_unanswered_approval_is_blocked_and_names_the_request(self):
        approval = {"id": f"apr-{RUN_ID}-1", "action_class": 15, "action": "add dependency",
                    "target": "requests", "normalized_target": "requests",
                    "reason": "needed for the HTTP client", "risk": "new supply-chain surface",
                    "status": "unanswered"}
        built = self.build(agent=agent_report(checks=[check(result="unresolved")]),
                           approvals=[approval])
        self.assertEqual(built["final_outcome"], "blocked")
        self.assertTrue(built["human_action_required"])

    def test_74_every_override_names_the_rule_that_fired(self):
        built = self.build(agent=agent_report(checks=[check(result="fail")]))
        self.assertEqual(built["outcome_overrides"],
                         [{"from": "succeeded", "to": "failed", "rule": "FR-035a"}])

    def test_75_an_agent_claim_that_matches_the_evidence_is_not_an_override(self):
        built = self.build(agent=agent_report(outcome="failed", checks=[check(result="fail")]))
        self.assertEqual(built["final_outcome"], "failed")
        self.assertEqual(built["outcome_overrides"], [])


# --- exit mapping, rendering, schema ------------------------------------------------------------------


class TestExitMapping(unittest.TestCase):
    def test_80_dispositions_map_to_their_contract_exit_statuses(self):
        self.assertEqual(errors.exit_status_for_outcome("succeeded"), 0)
        self.assertEqual(errors.exit_status_for_outcome("failed"), 10)
        self.assertEqual(errors.exit_status_for_outcome("blocked"), 11)

    def test_81_the_reportless_failures_carry_2_3_and_4(self):
        self.assertEqual(errors.UsageError.exit_code, 2)
        self.assertEqual(errors.PreconditionError.exit_code, 3)
        self.assertEqual(errors.InfraAbort.exit_code, 4)

    def test_82_an_unknown_outcome_is_a_programming_error_not_an_exit_code(self):
        with self.assertRaises(ValueError):
            errors.exit_status_for_outcome("partially")


class TestRendering(ReportCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.dir = WORK / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.dir, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_90_the_disposition_is_the_first_thing_rendered(self):
        text = report.render(self.build())
        self.assertIn("SUCCEEDED", text.split("\n")[2])

    def test_91_an_override_is_rendered_with_its_rule(self):
        text = report.render(self.build(agent=agent_report(checks=[check(result="fail")])))
        self.assertIn("Outcome overrides", text)
        self.assertIn("FR-035a", text)

    def test_92_a_safety_event_is_rendered_prominently(self):
        built = self.build(agent=agent_report(
            review={"performed": True, "identical": False}))
        self.assertIn(report.REVIEWER_MISMATCH, report.render(built))

    def test_93_checks_and_change_set_are_rendered(self):
        text = report.render(self.build())
        self.assertIn("make test", text)
        self.assertIn("src/x.py", text)

    def test_94_write_produces_both_artifacts_and_they_round_trip(self):
        built = self.build()
        json_path, markdown_path = report.write(built, self.dir)
        self.assertEqual(json.loads(Path(json_path).read_text(encoding="utf-8")), built)
        self.assertTrue(Path(markdown_path).read_text(encoding="utf-8").startswith("# " + RUN_ID))


class TestSchemaEnforcement(ReportCase):
    def test_95_the_validator_refuses_an_unknown_schema_keyword(self):
        _load("dca_jsonschema", ROOT / "src" / "dca" / "jsonschema.py")
        jsonschema = sys.modules["dca_jsonschema"]
        with self.assertRaises(jsonschema.UnsupportedKeyword):
            jsonschema.validate({"a": 1}, {"dependentRequired": {"a": ["b"]}})

    def test_96_a_hand_edited_report_that_over_claims_fails_the_schema(self):
        built = self.build(agent=agent_report(checks=[check(result="fail")]))
        built["final_outcome"] = "succeeded"
        with self.assertRaises(ValueError):
            report.validate(built, self.schema)

    def test_97_a_report_claiming_success_after_a_malformed_stream_fails_the_schema(self):
        built = self.build(run=events.analyze("garbage\n", exit_status=0))
        built["final_outcome"] = "succeeded"
        with self.assertRaises(ValueError):
            report.validate(built, self.schema)

    def test_98_an_unknown_top_level_field_fails_the_schema(self):
        built = self.build()
        built["surprise"] = True
        with self.assertRaises(ValueError):
            report.validate(built, self.schema)

    def test_99_every_produced_report_validates(self):
        for built in (self.build(),
                      self.build(agent={}),
                      self.build(agent=agent_report(checks=[check(result="fail")])),
                      self.build(run=analysis(exit_status=137))):
            self.assertEqual(report.validate(built, self.schema), built)


if __name__ == "__main__":
    unittest.main()
