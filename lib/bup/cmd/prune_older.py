
from binascii import hexlify, unhexlify
from collections import defaultdict
from itertools import groupby
from time import localtime, strftime, time
import sys

from bup import options
from bup.compat import argv_bytes
from bup.gc import bup_gc
from bup.git import check_repo_or_die
from bup.helpers import die_if_errors, log, partition, period_as_secs
from bup.io import byte_stream, path_msg
from bup.repo import main_repo_location, repo_for_location
from bup.rm import bup_rm
from bup.url import URL
from bup.vfs import save_names_for_commit_utcs


def branches(repo, refnames=tuple()):
    # read the full list from the remote refs() call so it finishes
    # and we can do rev_list() during the iteration
    return list((name[11:], hexlify(sha)) for (name,sha)
                in repo.refs(patterns=(b'refs/heads/' + n for n in refnames),
                             limit_to_heads=True))

def classify_saves(saves, period_start):
    """For each (utc, ...) in saves, yield (True, (utc, ...)) if the
    save should be kept and (False, (utc, ...)) if the save should be
    removed.

    """

    def retain_newest_in_region(region):
        for save in region[0:1]:
            yield True, save
        for save in region[1:]:
            yield False, save

    matches, rest = partition(lambda s: s[0] >= period_start['all'], saves)
    for save in matches:
        yield True, save

    tm_ranges = ((period_start['dailies'], lambda s: (localtime(s[0]).tm_year,
                                                      localtime(s[0]).tm_yday)),
                 (period_start['weeklies'], lambda s: strftime('%G-%V',
                                                               localtime(s[0]))),
                 (period_start['monthlies'], lambda s: (localtime(s[0]).tm_year,
                                                        localtime(s[0]).tm_mon)),
                 (period_start['yearlies'], lambda s: localtime(s[0]).tm_year))

    # Break the decreasing utc sorted saves up into the respective
    # period ranges (dailies, monthlies, ...).  Within each range,
    # group the saves by the period scale (days, months, ...), and
    # then yield a "keep" action (True, utc) for the newest save in
    # each group, and a "drop" action (False, utc) for the rest.
    for pstart, time_region_id in tm_ranges:
        matches, rest = partition(lambda s: s[0] >= pstart, rest)
        for region_id_, region_saves in groupby(matches, time_region_id):
            yield from retain_newest_in_region(list(region_saves))

    # Finally, drop any saves older than the specified periods
    for save in rest:
        yield False, save


optspec = """
bup prune-older [options...] [BRANCH...]
--
keep-all-for=       retain all saves within the PERIOD
keep-dailies-for=   retain the newest save per day within the PERIOD
keep-weeklies-for=  retain the newest save per week within the PERIOD
keep-monthlies-for= retain the newest save per month within the PERIOD
keep-yearlies-for=  retain the newest save per year within the PERIOD
wrt=                end all periods at this number of seconds since the epoch
pretend       don't prune, just report intended actions to standard output
gc            collect garbage after removals [1]
gc-threshold= only rewrite a packfile if it's over this percent garbage [10]
#,compress=   set compression level to # (0-9, 9 is highest) [1]
r,remote=     remote repository path
v,verbose     increase log output (can be used more than once)
unsafe        use the command even though it may be DANGEROUS
"""

def main(argv):
    o = options.Options(optspec)
    opt, flags_, roots = o.parse_bytes(argv[1:])
    roots = [argv_bytes(x) for x in roots]

    if not opt.unsafe:
        o.fatal('refusing to run dangerous, experimental command without --unsafe')

    now = int(time()) if opt.wrt is None else opt.wrt
    if not isinstance(now, int):
        o.fatal('--wrt value ' + str(now) + ' is not an integer')

    period_start = {}
    for period, extent in (('all', opt.keep_all_for),
                           ('dailies', opt.keep_dailies_for),
                           ('weeklies', opt.keep_weeklies_for),
                           ('monthlies', opt.keep_monthlies_for),
                           ('yearlies', opt.keep_yearlies_for)):
        if extent is not None:
            if isinstance(extent, int): # bup.options...
                extent = str(extent)
            secs = period_as_secs(extent.encode('ascii'))
            if not secs:
                o.fatal(f'{path_msg(extent)} is not a valid period')
            period_start[period] = now - secs

    if not period_start:
        o.fatal('at least one keep argument is required')

    period_start = defaultdict(lambda: float('inf'), period_start)

    if opt.verbose:
        epoch_ymd = strftime('%Y-%m-%d-%H%M%S', localtime(0))
        for kind in ['all', 'dailies', 'weeklies', 'monthlies', 'yearlies']:
            period_utc = period_start[kind]
            if period_utc == float('inf'):
                continue
            if period_utc == float('-inf'):
                log(f'keeping all {kind}\n')
                continue
            try:
                period_local = localtime(period_utc)
            except OverflowError:
                if period_utc < 0:
                    log('keeping %s since %d seconds before %s\n'
                        %(kind, abs(period_utc), epoch_ymd))
                elif period_utc > 0:
                    log('keeping %s since %d seconds after %s\n'
                        %(kind, period_utc, epoch_ymd))
                else:
                    log('keeping %s since %s\n' % (kind, epoch_ymd))
            else:
                when = strftime('%Y-%m-%d-%H%M%S', period_local)
                log(f'keeping {kind} since {when}\n')

    loc = main_repo_location(argv_bytes(opt.remote) if opt.remote else None,
                             o.fatal)
    is_local = isinstance(loc, URL) and loc.scheme == b'file'
    if is_local:
        check_repo_or_die(loc.path)
    elif opt.gc and not opt.pretend:
        o.fatal('--gc must run on the repository host; use --no-gc and run '
                'bup gc on the server')

    # This could be more efficient, but for now just build the whole list
    # in memory and let bup_rm() do some redundant work.

    def parse_info(f):
        author_secs = f.readline().strip()
        return int(author_secs)

    sys.stdout.flush()
    out = byte_stream(sys.stdout)

    with repo_for_location(loc, compression_level=opt.compress) as repo:
        removals = []
        for branch, branch_id in branches(repo, roots):
            die_if_errors()
            # At the moment, oids are irrelevant; the save name is crucial
            revs = list(repo.rev_list(branch_id, format=b'%at', parse=parse_info))
            saves = ((utc, unhexlify(oidx), save_name) \
                     for ((oidx, utc), save_name) \
                     in zip(revs, save_names_for_commit_utcs(x[1] for x in revs)))
            for keep_save, (utc, oid_, save_name) \
                    in classify_saves(saves, period_start):
                assert(keep_save in (False, True))
                # FIXME: base removals on hashes
                if opt.pretend:
                    out.write(b'%s %s/%s\n' % (b'+' if keep_save else b'-',
                                               branch, save_name))
                elif not keep_save:
                    removals.append(b'%s/%s' % (branch, save_name))

        if not opt.pretend:
            die_if_errors()
            bup_rm(repo, removals, verbosity=opt.verbose)

    if not opt.pretend and opt.gc:
        die_if_errors()
        bup_gc(threshold=opt.gc_threshold,
               compression=opt.compress,
               verbosity=opt.verbose)
