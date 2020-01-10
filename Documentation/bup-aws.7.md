% bup-aws(7) Bup %BUP_VERSION%
% Johannes Berg <johannes@sipsolutions.net>
% %BUP_DATE%

# NAME

bup-aws - overview of the bup AWS storage driver

# DESCRIPTION

The bup AWS storage stores files in S3, and for consistency and atomicity
reasons (when updating refs) uses a DynamoDB table for each repository as
well.

# CONFIGURATION

The AWS backend configuration must go into a section called `[bup.aws]`,
the following options are needed/available:

[bup.aws]
: 

cachedir = ... [required]
: The folder to cache objects in, for future use. Note that this uses
  sparse files and only caches what has been downloaded/requested,
  with an extra file for each object indicating which ranges are
  present.
  This must be given, as otherwise a lot of (redundant) requests to
  objects will be made, single bytes may be downloaded at extra cost,
  and nothing will be cached. Object sizes will also be given away by
  the download, when downloading them.
  This folder can be shared with the encrypted repo's cachedir since
  different names are stored.
  This can be given as a relative path, in which case it will be
  relative to the directory that the config file is stored in.

downloadBlockSize = ... [optional, default 8k, must be > 0]
: When downloading, download this many bytes. The default is 8k as somewhere
  below ~4 or ~21k (depending on your connection to S3) the cost for the
  request is higher than the cost for the actual data, so it doesn't make
  much sense to download less. Additionally, since we round to blocks of
  this size, doing so hides the exact blob sizes in your repository. Note
  that this only makes sense if caching is enabled, otherwise byte-accurate
  downloads are always performed, which will likely end up costing more.

  Due to the use of sparse files, you probably want to keep this a multiple
  of sector or page size, or similar, and not use some arbitrary size.

  If you plan to restore a large amount of data, then you should probably
  set this to a rather large value so that request costs don't become an
  extra significant cost, since you'll likely need many contiguous objects
  (all the parts of a file, to restore a file.)

s3bucket = ... [mandatory]
: The S3 bucket in which to store objects other than the refs file(s).

dynamotable = ... [mandatory]
: The DynamoDB table in which to store the list of objects and the refs
  file(s).

region = ... [mandatory]
: The AWS region in which the S3 bucket and DynamoDB table are located.

accessKeyId = ... [mandatory unless sessionToken is given]
: The access key ID for the AWS account that has access to the S3 bucket and
  the DynamoDB table.

secretAccessKey = ... [mandatory unless sessionToken is given]
: The secret access key for the account.

sessionToken = ... [optional]
: A session token to use instead of the accessKeyId and secretAccessKey.

chunkSize = ... [optional, default 50 MiB]
: Upload chunk size, must be at least 5 MiB. Note that up to twice this much
  data is kept in memory while uploading, so don't increase it too much.
  However, need to balance this with request costs, so the default is bigger
  than the minimum of 5 MiB.

defaultStorageClass = ... [optional, default STANDARD]
: The S3 storage class to use by default. You probably don't want to change
  this, but rather use the more specific variables below.

idxStorageClass = ... [optional, defaults to defaultStorageClass]
: 

idxStorageClassSmall = ... [optional, defaults to idxStorageClass]
: 

idxStorageClassLarge = ... [optional, defaults to idxStorageClass]
: 

idxStorageClassThreshold = ... [optional, default 1 MiB, must be <= chunkSize]
: These three variables indicate the S3 storage class to use for indexes.
  The threshold is the maximum size of an object considered "small".
  Note that indexes are required for any kind of repository access, even
  writing (for deduplication), so you probably don't want to change this
  unless you have only a single machine that's making backups and can rely
  on its local cache (so the files never have to be synchronized).

metadataStorageClass = ... [optional, defaults to defaultStorageClass]
: 

metadataStorageClassSmall = ... [optional, defaults to metadataStorageClass]
: 

metadataStorageClassLarge = ... [optional, defaults to metadataStorageClass]
: 

metadataStorageClassThreshold = ... [optional, default 1 MiB, must be <= chunkSize]
: Similar to the corresponding `idx*` variables, except for metadata packs.
  This setting is only valid for repositories that set `bup.separatemeta`.
  Data from these packs will have to be retrieved for any kind of restore
  operation, so it may be useful to have them separate and keep them in more
  accessible storage than the actual data.

dataStorageClass = ... [optional, defaults to defaultStorageClass]
: 

dataStorageClassSmall = ... [optional, defaults to dataStorageClass]
: 

dataStorageClassLarge = ... [optional, defaults to dataStorageClass]
: 

dataStorageClassThreshold = ... [optional, default 1 MiB, must be <= chunkSize]
: Similar to the corresponding `idx*` and `metadata*` variables, except for
  data packs. This could for example be in `DEEP_ARCHIVE` class when restore
  is considered to be very infrequent (or only for disaster recovery).


Note that all string values must be UTF-8.

# THRESHOLD SETTINGS

Note that the thresholds must (curently) be less than 5 MiB, which is the
minimum chunk size for chunked uploads into S3, and thus the amount of data
we buffer before starting an upload - at which point we have to make a
decision where the object should go. If necessary, this could be fixed by
either buffering more, or moving the object after upload.

Note also that the thresholds default to 1 MiB. Theoretically, the pure storage
cost break-even point of S3 STANDARD vs. e.g. DEEP_ARCHIVE is significantly
lower (around 9-11 KiB depending on the region), but small objects like this
are still most likely "cheap enough". If you are planning to make very
frequent backups that may result in small objects, this setting may be
relevant for you.

You can calculate the pure storage-cost (not considering retrieval cost and
minimum storage duration) break-even point of GLACIER and DEEP_ARCHIVE
(in KiB) by

    S := cost of STANDARD tier in your region (per GiB/mo)
    L := cost of GLACIER or DEEP_ARCHIVE (per GiB/mo)
    break-even := (32 * L + 8 * S) / (S - L)

due to the amount of extra data the S3 requires for GLACIER or DEEP_ARCHIVE,
which is 8 KiB of regular storage and 32 KiB of deeper storage for each
object stored in deeper storage.

# LIFECYCLE POLICY

You should configure a
[`lifecycle policy for aborting incomplete multipart uploads`][lifecycle]
on the bucket that you intend to use, to clean up such incomplete multipart
uploads that bup may create if it crashes or its internet connectivity is
interrupted while uploading. Otherwise, partial objects may accumulate in S3
storage and you will be charged for them without ever seeing them.

See also [`"how do I create a lifecycle policy"`][creating].

# PERMISSIONS / AWS POLICY

It's possible to secure the account used for AWS access, e.g. against
deletion of (most of) your backup, with an AWS policy like this:

    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": [
            "dynamodb:DeleteItem",
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:Query",
            "dynamodb:Scan"
          ],
          "Resource": [
            "<your DynamoDB table ARN>"
          ]
        },
        {
          "Effect": "Allow",
          "Action": [
            "s3:AbortMultipartUpload",
            "s3:GetObject",
            "s3:PutObject"
          ],
          "Resource": [
            "<your S3 bucket ARN>",
            "<your S3 bucket ARN>/*"
          ]
        }
      ]
    }

Note that this specifies s3:GetObject, this is necessary to download
indexes, at least if multiple machines are making backups to the same
"repository" (DynamoDB table/S3 bucket). Use the encryption features
(see `bup-encrypted`(7)) to prevent (compromised) machines accessing
old data.

Since s3:PutObject is permitted, bucket versioning should be used to
prevent overwrite of old data.

Finally, also note that this doesn't prevent deletion of all entries
in the DynamoDB table. This means that the backup refs can be deleted
by any of the backup users, but this is recoverable by searching for
commit objects in the packs, you can do that with only the idx files
(as we store the object type in the CRC field). No code for that is
available right now, however.
This also means that all the entries could be deleted, resulting in a
new backup storing all objects again, and deleting the local copy of
the idx files.

# INITIALIZATION

After configuring appropriately

    bup init -r config:///path/to/file.conf

will attempt to create the S3 bucket and DynamoDB table in the configured
region. It will fail if you've already pre-created them, but bup will be
able to use them no matter how they were created.

# BUP

Part of the `bup`(1) suite.

[lifecycle]: https://docs.aws.amazon.com/AmazonS3/latest/dev/mpuoverview.html#mpu-abort-incomplete-mpu-lifecycle-config
[creating]: https://docs.aws.amazon.com/AmazonS3/latest/user-guide/create-lifecycle.html
