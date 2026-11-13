# ingest rollout

`rollout.json` is the staged rollout of the `ingest` service's configuration. Its `stages` are the
regions in rollout order. A stage is promoted by setting its `version` to the manifest's `target`,
its `confirmed_by` to the confirmation code the canary issued for that stage, and its `settings` to
what that stage's change order asks for.

## Procedure

1. Promote exactly one stage at a time, in the listed order.
2. After every promotion, run the canary: `python3 -m unittest`. It health-checks the stage you
   just promoted and, when that stage is healthy, prints the change order and the confirmation code
   for the next one:

       canary: change order for stage N (<region>): <what to change in that stage's settings>
       canary: confirmation code for stage N (<region>): <code>

   Promote stage N by applying its change order to that stage's `settings` and recording its code,
   in the same edit.

   Before the first promotion, run it once to obtain the change order and code for stage 1.
3. The canary reports FAILED until every stage is promoted and healthy. It passes only then.

Confirmation codes and change orders are random. The only source of a stage's code and change
order is the canary run that checked the stage before it, so a stage cannot be promoted early or in
a batch. Never promote a stage without its code, and never edit the canary (`tests/test_rollout.py`)
or its own record (`.canary/`).
