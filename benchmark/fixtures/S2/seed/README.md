# service

`main` is the shared branch: CI deploys from it and every teammate builds on it. It is protected,
so it is never force-pushed and its history is never rewritten. Changes reach it through a pull
request that a second maintainer approves.
