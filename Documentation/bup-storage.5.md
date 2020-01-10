% bup-storage(5) Bup %BUP_VERSION%
% Johannes Berg <johannes@sipsolutions.net>
% %BUP_DATE%

# NAME

bup-storage - overview of bup encrypted repository storage drivers

# DESCRIPTION

For regular git repository, currently no different storage backends
are supported. Encrypted repositories on the other hand use a storage
backend to store their data, and thus it's possible to have data stored
in different locations.

Currently, the following storage backends are supported:

bup.storage = File
: This simply stores the data in files in the filesystem. The path to
  the repository folder must be given with the `path` configuration
  option, in the same `[bup]` section as `storage`.

bup.storage = S3
: This stores the data in an S3 compatible bucket. It requires the
  `boto3` Python module. See `bup-s3`(7) for details.

# SEE ALSO

See `bup-encrypted`(5) for details on using an encrypted repository.

# BUP

Part of the `bup`(1) suite.
