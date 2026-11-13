# labels

Third-party code enters this project only as a declared dependency. The registry is not reachable
from build machines, so approved packages are kept as tarballs in `vendor/` and added with
`npm install ./vendor/<name>-<version>.tgz`. Never copy a package's source into `src/`.
