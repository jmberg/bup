% bup-encrypted(5) Bup %BUP_VERSION%
% Johannes Berg <johannes@sipsolutions.net>
% %BUP_DATE%

# NAME

bup-encrypted - overview of encrypted bup repositories

# DESCRIPTION

Encrypted repositories are intended to protect the data at rest.
An attacker who can access the data should neither be able to
read it without the keys, nor be able to prove that any content
(even content that is a priori known to the attacker) exists in
a given repository.

(NOTE: in order to actually not allow an attacker to prove object
existence, we also take care to encrypt the object lengths in a
repository pack file so that fingerprinting attacks based on the
hashsplit algorithm size sequence are not possible.)

Additionally, it should be possible to configure a repository to
be write-only, so that even the person/system adding new backups
into the repository is not able to read the data of old backups.
In this case, such a person can prove existence of objects, this
is clearly needed for deduplication to work.

As a consequence, encrypted repositories are protected by two
keys:

 * a symmetric key, and
 * a private/public key pair.

The symmetric key is needed for both kinds of repository access
(read and write) and is used to encrypt index files and refs.
The private/public parts of the key pair are used to decrypt and
encrypt (respectively) the actual data content, so somebody who
has only the public key cannot read the actual data.

We call these keys the

 * repokey: is the symmetric key
 * readkey: is the private part of the key pair
 * writekey: is the public part of the key pair

The keys thus permit the following accesses:

 * repokey: can enumerate objects and check object existence
   (via the index files), read/update refs and write new index
   files into the repository (but not data)
 * writekey: can write data into the repository, but not update
   indexes
 * readkey: can read data stored in the repository, but only
   very inefficiently (no access to indexes with just this key)

The only useful and supported levels of access this allows are:

 * no keys: no access at all

   For example, with the File storage backend this means that anyone
   with access to the storage location can see only encrypted
   files, and can tell how many objects are stored (roughly) from the
   number and size of the files, but not what they contain.

 * repokey & writekey: can make new backups, but not read old ones

   This might be a server that is making backups, but should not be able
   to read old backups, so that in case it's compromised, only current
   data is compromised, and not all backed up data as well.

 * repokey & readkey: can make new backups and read old ones

   This is full access to the repository, needed to list and restore
   data from it. Since the writekey (public part) can of course be
   derived from the readkey (private part) this can also write to the
   repository.

# CONFIGURATION

In order to configure/use encrypted repositories, first create
a configuration file template using `bup-genkey`(1) and store it
somewhere on the filesystem.

You will need to modify the resulting configuration file and at
least fill in the `storage` storage driver setting, see `bup-storage`(5)
for the available drivers and their settings.

In order to use such a repository, pass it as a "remote" repository
to any bup command, e.g.

    bup save -r config:///path/to/your/file.conf -n branch ...
    bup ls -r config:///path/to/your/file.conf branch/latest/

The configuration file is read by bup on the local machine, the
repository itself is not accessed through a bup server.

The following configuration settings are supported:

\[bup]
: 

type = Encrypted [mandatory]
: This indicates the repository type is an encrypted repository.

storage = ... [mandatory]
: This indicates the type of storage to use. See `bup-storage`(5)
  for more information.

repokey = ... [mandatory]
: The (symmetric) repository key, this must be present for bup to
  be able to access the repository at all. This key is used to
  encrypt idx files (that indicate which objects exist in a pack)
  and the refs file(s).

writekey = ... [mandatory unless readkey is configured]
: The public part of the asymmetric repo read/write key pair, used
  to write to the repository. A backup system can be configured with
  only this (and not `bup.readkey`) in which case it can make backups
  but not read them back, this could be useful e.g. to avoid having
  even all old backup data leaked after a system compromise.

readkey = ... [optional]
: The private part of the asymmetric repo read/write key pair, used
  to decrypt pack files. This is only needed to restore from the
  repository.

refsname = ... [optional, default "refs"]
: This is the (file) name under which the refs are stored, this may be useful
  to avoid concurrency issues if multiple systems are writing to the same
  repository, each can have its own refs file to avoid failing the atomic
  update in case of races. If set, then must also be set to restore from
  the same backup. Note that if set then there can be multiple branches in
  the same repository with the same name, in different refs files.

\[bup "repo"]
: 

id = ... [mandatory]
: Identifies the repository on this machine. `bup-genkey`(1) generates
  this. The local cache for the repository is kept in a directory named
  after this ID, below the XDG cache directory (`$XDG_CACHE_HOME/bup/remote`,
  or `~/.cache/bup/remote` if that is not set). Index files are cached
  there in order to avoid downloading them on each new backup run.

Settings for the repository itself, such as compression or the blob
size, are stored in the repository and are set with `bup config`,
using the `config://` URL of the configuration file:

    bup config -r config:///path/to/your/file.conf core.compression 5
    bup config -r config:///path/to/your/file.conf bup.split.files legacy:16

# REQUIREMENTS

Encrypted repositories require the python `libnacl` module. The
`zstandard` module is optional, and is only needed if zstd
compression is used.

# BUGS

There's currently no way to encrypt the configuration file or the
keys contained therein with a password, or to derive them from a
password.

# SEE ALSO

See `bup-genkey`(1) for the key generation subcommand.

See `bup-storage`(5) for the different storage drivers.

# BUP

Part of the `bup`(1) suite.
