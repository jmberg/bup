% bup-index(1) Bup %BUP_VERSION%
% Avery Pennarun <apenwarr@gmail.com>
% %BUP_DATE%

# NAME

bup-index - print and/or update the bup filesystem index

# SYNOPSIS

bup index \<-p|-m|-s|-u|\--clear|\--check\> [\--stat] [-H] [-l] [-x] [\--fake-valid]
[\--no-check-device] [\--fake-invalid] [-f *indexfile*] [\--exclude *path*]
[\--exclude-from *filename*] [\--exclude-rx *pattern*]
[\--exclude-rx-from *filename*] [-v] \<paths...\>

# DESCRIPTION

`bup index` manipulates the filesystem index, which is a cache of
absolute paths and their metadata (attributes, SHA-1 hashes, etc.).
The bup index is similar in function to the `git`(1) index, and the
default index can be found in `$BUP_DIR/bupindex`.

Creating a backup in bup consists of two steps: updating
the index with `bup index`, then actually backing up the
files (or a subset of the files) with `bup save`.  The
separation exists for these reasons:

1. There is more than one way to generate a list of files
that need to be backed up.  For example, you might want to
use `inotify`(7) or `dnotify`(7).

2. Even if you back up files to multiple destinations (for
added redundancy), the file names, attributes, and hashes
will be the same each time.  Thus, you can save the trouble
of repeatedly re-generating the list of files for each
backup set.

3. You may want to use the data tracked by bup index for
other purposes (such as speeding up other programs that
need the same information).

# NOTES

At the moment, bup will ignore Linux attributes (cf. chattr(1) and
lsattr(1)) on some systems (any big-endian systems where sizeof(long)
< sizeof(int)).  This is because the Linux kernel and FUSE currently
disagree over the type of the attr system call arguments, and so on
big-endian systems there's no way to get the results without the risk
of stack corruption (http://lwn.net/Articles/575846/).  In these
situations, bup will print a warning the first time Linux attrs are
relevant during any index/save/restore operation.

bup makes accommodations for the expected "worst-case" filesystem
timestamp resolution -- currently one second; examples include VFAT,
ext2, ext3, small ext4, etc.  Since bup cannot know the filesystem
timestamp resolution, and could be traversing multiple filesystems
during any given run, it always assumes that the resolution may be no
better than one second.

As a practical matter, this means that index updates are a bit
imprecise, and so `bup save` may occasionally record filesystem
changes that you didn't expect.  That's because, during an index
update, if bup encounters a path whose actual timestamps are more
recent than one second before the update started, bup will set the
index timestamps for that path (mtime and ctime) to exactly one second
before the run, -- effectively capping those values.

This ensures that no subsequent changes to those paths can result in
timestamps that are identical to those in the index.  If that were
possible, bup could overlook the modifications.

You can see the effect of this behavior in this example (assume that
less than one second elapses between the initial file creation and
first index run):

    $ touch src/1 src/2
    # A "sleep 1" here would avoid the unexpected save.
    $ bup index src
    $ bup save -n src src  # Saves 1 and 2.
    $ date > src/1
    $ bup index src
    $ date > src/2         # Not indexed.
    $ bup save -n src src  # But src/2 is saved anyway.

Strictly speaking, bup should not notice the change to src/2, but it
does, due to the accommodations described above.

# MODES

-u, \--update
:   recursively update the index for the given paths and their
    descendants.  One or more paths must be specified, and if a path
    ends with a symbolic link, the link itself will be indexed, not
    the target.  If no mode option is given, `--update` is the
    default, and paths may be excluded by the `--exclude`,
    `--exclude-rx`, and `--one-file-system` options.

-p, \--print
:   print the contents of the index.  If paths are
    given, shows the given entries and their descendants. 
    If no paths are given, shows the entries starting
    at the current working directory (.).

\--stat
:   print all available information about each file (in
    stat(1)-like format); implies -p.
    
-m, \--modified
:   prints only files which are marked as modified (ie.
    changed since the most recent backup) in the index. 
    Implies `-p`.

-s, \--status
:   prepend a status code (A, M, D, or space) before each
    path.  Implies `-p`.  The codes mean, respectively,
    that a file is marked in the index as added, modified,
    deleted, or unchanged since the last backup.

\--check
:   carefully check index file integrity before and after
    updating.  Mostly useful for automated tests.

\--clear
:   clear the default index.


# OPTIONS

-H, \--hash
:   for each file printed, prepend the most recently
    recorded hash code.  The hash code is normally
    generated by `bup save`.  For objects which have not yet
    been backed up, the hash code will be
    0000000000000000000000000000000000000000.  Note that
    the hash code is printed even if the file is known to
    be modified or deleted in the index (ie. the file on
    the filesystem no longer matches the recorded hash). 
    If this is a problem for you, use `--status`.
    
-l, \--long
:   print more information about each file, in a similar
    format to the `-l` option to `ls`(1).

-x, \--xdev, \--one-file-system
:   don't cross filesystem boundaries when traversing the
    filesystem -- though as with tar and rsync, the mount points
    themselves will still be indexed.  Only applicable if you're using
    `-u`.
    
\--fake-valid
:   mark specified paths as up-to-date even if they
    aren't.  This can be useful for testing, or to avoid
    unnecessarily backing up files that you know are
    boring.
    
\--fake-invalid
:   mark specified paths as not up-to-date, forcing the
    next "bup save" run to re-check their contents.

-f, \--indexfile=*indexfile*
:   use a different index filename instead of
    `$BUP_DIR/bupindex`.

\--exclude=*path*
:   exclude *path* from the backup (may be repeated).

\--exclude-from=*filename*
:   read --exclude paths from *filename*, one path per-line (may be
    repeated).  Ignore completely empty lines.

\--exclude-rx=*pattern*
:   exclude any path matching *pattern*, which must be a Python regular
    expression (http://docs.python.org/library/re.html).  The pattern
    will be compared against the full path, without anchoring, so
    "x/y" will match "ox/yard" or "box/yards".  To exclude the
    contents of /tmp, but not the directory itself, use
    "^/tmp/.". (may be repeated)

    Examples:

      * '/foo$' - exclude any file named foo
      * '/foo/$' - exclude any directory named foo
      * '/foo/.' - exclude the content of any directory named foo
      * '^/tmp/.' - exclude root-level /tmp's content, but not /tmp itself

\--exclude-rx-from=*filename*
:   read --exclude-rx patterns from *filename*, one pattern per-line
    (may be repeated).  Ignore completely empty lines.

\--no-check-device
:   don't mark an entry invalid if the device number (stat(2) st_dev)
    changes.  This can be useful when indexing remote, automounted, or
    snapshot filesystems (LVM, Btrfs, etc.), where the device number
    isn't fixed.

-v, \--verbose
:   increase log output during update (can be used more
    than once).  With one `-v`, print each directory as it
    is updated; with two `-v`, print each file too.


# EXAMPLES
    bup index -vux /etc /var /usr
    

# SEE ALSO

`bup-save`(1), `bup-drecurse`(1), `bup-on`(1)

# BUP

Part of the `bup`(1) suite.
