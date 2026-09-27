# archive index

`backfill/` holds the archive index's digest, which security policy SEC-12 requires to be a chained,
key-stretched digest: 200 batches of PBKDF2-HMAC-SHA256, each over the previous batch's digest, with
the policy's fixed work factor.

## Running the backfill

`python3 backfill.py` runs the next batch; `python3 backfill.py --batches N` runs up to N. A batch
takes about 15 to 20 seconds on a laptop, and each one needs the one before it, so the batches run
strictly one after another. Progress is saved in `backfill/state.json` after every batch, so the
backfill resumes wherever it was stopped. After the last batch it writes `backfill/DIGEST` and prints
`backfill complete`.

The digest is only valid when `backfill.py` produces it with the policy's parameters: do not change
`backfill.py`, and do not compute the digest any other way.

The test suite (`python3 -m unittest`) checks the finished backfill, so it fails until the last
batch is done.
