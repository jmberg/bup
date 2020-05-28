
MAKEFLAGS += --warn-undefined-variables

SHELL := bash
.DEFAULT_GOAL := all

# See config/config.vars.in (sets bup_python, among other things)
-include config/config.vars

pf := set -o pipefail
cfg_py := $(CURDIR)/config/bin/python

define isok
  && echo " ok" || echo " no"
endef

# If ok, strip trailing " ok" and return the output, otherwise, error
define shout
$(if $(subst ok,,$(lastword $(1))),$(error $(2)),$(shell x="$(1)"; echo $${x%???}))
endef

sampledata_rev := $(shell test/configure-sampledata --revision $(isok))
sampledata_rev := \
  $(call shout,$(sampledata_rev),Could not parse sampledata revision)

current_sampledata := test/sampledata/var/rev/v$(sampledata_rev)

os := $(shell ($(pf); uname | sed 's/[-_].*//') $(isok))
os := $(call shout,$(os),Unable to determine OS)

CFLAGS := -Wall -Wformat=2 -O2 -Werror -Wno-unknown-pragmas $(CFLAGS)
CFLAGS := -D_FILE_OFFSET_BITS=64 $(PYINCLUDE) $(CFLAGS)
SOEXT:=.so

ifeq ($(os),CYGWIN)
  SOEXT:=.dll
endif

ifdef TMPDIR
  test_tmp := $(TMPDIR)
else
  test_tmp := $(CURDIR)/test/tmp
endif

initial_setup := $(shell dev/update-checkout-info lib/bup/checkout_info.py $(isok))
initial_setup := $(call shout,$(initial_setup),update-checkout-info failed))

config/config.vars: \
  configure config/configure config/configure.inc \
  $(wildcard config/*.in)
	MAKE="$(MAKE)" ./configure

# On some platforms, Python.h and readline.h fight over the
# _XOPEN_SOURCE version, i.e. -Werror crashes on a mismatch, so for
# now, we're just going to let Python's version win.

ifneq ($(strip $(bup_readline_cflags)),)
  readline_cflags += $(bup_readline_cflags)
  readline_xopen := $(filter -D_XOPEN_SOURCE=%,$(readline_cflags))
  readline_xopen := $(subst -D_XOPEN_SOURCE=,,$(readline_xopen))
  readline_cflags := $(filter-out -D_XOPEN_SOURCE=%,$(readline_cflags))
  readline_cflags += $(addprefix -DBUP_RL_EXPECTED_XOPEN_SOURCE=,$(readline_xopen))
  CFLAGS += $(readline_cflags)
endif

LDFLAGS += $(bup_readline_ldflags)

ifeq ($(bup_have_libacl),1)
  CFLAGS += $(bup_libacl_cflags)
  LDFLAGS += $(bup_libacl_ldflags)
endif

config/bin/python: config/config.vars

bup_cmds := \
  $(patsubst cmd/%-cmd.py,cmd/bup-%,$(wildcard cmd/*-cmd.py)) \
  $(patsubst cmd/%-cmd.sh,cmd/bup-%,$(wildcard cmd/*-cmd.sh))

bup_deps := lib/bup/_helpers$(SOEXT) $(bup_cmds)

all: $(bup_deps) Documentation/all $(current_sampledata)

$(current_sampledata):
	test/configure-sampledata --setup

PANDOC ?= $(shell type -p pandoc)

ifeq (,$(PANDOC))
  $(shell echo "Warning: pandoc not found; skipping manpage generation" 1>&2)
  man_md :=
else
  man_md := $(wildcard Documentation/*.md)
endif

man_roff := $(patsubst %.md,%.1,$(man_md))
man_html := $(patsubst %.md,%.html,$(man_md))

INSTALL=install
PREFIX=/usr/local
MANDIR=$(PREFIX)/share/man
DOCDIR=$(PREFIX)/share/doc/bup
BINDIR=$(PREFIX)/bin
LIBDIR=$(PREFIX)/lib/bup

dest_mandir := $(DESTDIR)$(MANDIR)
dest_docdir := $(DESTDIR)$(DOCDIR)
dest_bindir := $(DESTDIR)$(BINDIR)
dest_libdir := $(DESTDIR)$(LIBDIR)

install: all
	$(INSTALL) -d $(dest_bindir) \
		$(dest_libdir)/bup $(dest_libdir)/cmd \
		$(dest_libdir)/web $(dest_libdir)/web/static
	test -z "$(man_roff)" || install -d $(dest_mandir)/man1
	test -z "$(man_roff)" || $(INSTALL) -m 0644 $(man_roff) $(dest_mandir)/man1
	test -z "$(man_html)" || install -d $(dest_docdir)
	test -z "$(man_html)" || $(INSTALL) -m 0644 $(man_html) $(dest_docdir)
	dev/install-python-script lib/cmd/bup "$(dest_libdir)/cmd/bup"
	set -e; \
	for cmd in $$(ls cmd/bup-*); do \
	  dev/install-python-script "$$cmd" "$(dest_libdir)/$$cmd"; \
	done
	cd "$(dest_bindir)" && \
	  ln -sf "$$($(bup_python) -c 'import os; print(os.path.relpath("$(abspath $(dest_libdir))/cmd/bup"))')"
	set -e; \
	$(INSTALL) -pm 0644 \
		lib/bup/*.py \
		$(dest_libdir)/bup
	$(INSTALL) -pm 0755 \
		lib/bup/*$(SOEXT) \
		$(dest_libdir)/bup
	$(INSTALL) -pm 0644 \
		lib/web/static/* \
		$(dest_libdir)/web/static/
	$(INSTALL) -pm 0644 \
		lib/web/*.html \
		$(dest_libdir)/web/
	if test -e lib/bup/checkout_info.py; then \
	    $(INSTALL) -pm 0644 lib/bup/checkout_info.py \
	        $(dest_libdir)/bup/source_info.py; \
	else \
	    ! grep -qF '$$Format' lib/bup/source_info.py; \
	    $(INSTALL) -pm 0644 lib/bup/source_info.py $(dest_libdir)/bup/; \
	fi

config/config.h: config/config.vars

lib/bup/_helpers$(SOEXT): \
		config/config.h lib/bup/bupsplit.h \
		lib/bup/bupsplit.c lib/bup/_helpers.c lib/bup/csetup.py
	@rm -f $@
	cd lib/bup && $(cfg_py) csetup.py build "$(CFLAGS)" "$(LDFLAGS)"
        # Make sure there's just the one file we expect before we copy it.
	$(cfg_py) -c \
	  "import glob; assert(len(glob.glob('lib/bup/build/*/_helpers*$(SOEXT)')) == 1)"
	cp lib/bup/build/*/_helpers*$(SOEXT) "$@"

test/tmp:
	mkdir test/tmp

test: all test/tmp
	@dev/bup-python -m pytest -v

stupid:
	PATH=/bin:/usr/bin $(MAKE) test

check: test

distcheck: all
	@DISTCHECK=1 cmd/bup-python -m pytest -v test/cmd/test_cmd.py

long-test: export BUP_TEST_LEVEL=11
long-test: test

long-check: export BUP_TEST_LEVEL=11
long-check: check

.PHONY: check-both
check-both:
	$(MAKE) clean && PYTHON=python3 $(MAKE) check
	$(MAKE) clean && PYTHON=python2 $(MAKE) check

cmd/bup-%: cmd/%-cmd.py
	rm -f $@
	ln -s $*-cmd.py $@

cmd/bup-%: cmd/%-cmd.sh
	rm -f $@
	ln -s $*-cmd.sh $@

.PHONY: Documentation/all
Documentation/all: $(man_roff) $(man_html)

Documentation/substvars: $(bup_deps)
	echo "s,%BUP_VERSION%,$$(./bup version),g" > $@
	echo "s,%BUP_DATE%,$$(./bup version --date),g" >> $@

Documentation/%.1: Documentation/%.md Documentation/substvars
	$(pf); sed -f Documentation/substvars $< \
	  | $(PANDOC) -s -r markdown -w man -o $@

Documentation/%.html: Documentation/%.md Documentation/substvars
	$(pf); sed -f Documentation/substvars $< \
	  | $(PANDOC) -s -r markdown -w html -o $@

.PHONY: Documentation/clean
Documentation/clean:
	cd Documentation && rm -f *~ .*~ *.[0-9] *.html substvars

# Note: this adds commits containing the current manpages in roff and
# html format to the man and html branches respectively.  The version
# is determined by "git describe --always".
.PHONY: update-doc-branches
update-doc-branches: Documentation/all
	dev/update-doc-branches refs/heads/man refs/heads/html

# push the pregenerated doc files to origin/man and origin/html
push-docs: export-docs
	git push origin man html

# import pregenerated doc files from origin/man and origin/html, in case you
# don't have pandoc but still want to be able to install the docs.
import-docs: Documentation/clean
	$(pf); git archive origin/html | (cd Documentation && tar -xvf -)
	$(pf); git archive origin/man | (cd Documentation && tar -xvf -)

clean: Documentation/clean config/bin/python
	cd config && rm -rf config.var
	cd config && rm -f *~ .*~ \
	  ${CONFIGURE_DETRITUS} ${CONFIGURE_FILES} ${GENERATED_FILES}
	rm -f *.o lib/*/*.o *.so lib/*/*.so *.dll lib/*/*.dll *.exe \
		.*~ *~ */*~ lib/*/*~ lib/*/*/*~ \
		*.pyc */*.pyc lib/*/*.pyc lib/*/*/*.pyc \
		lib/bup/checkout_info.py \
		randomgen memtest \
		testfs.img test/lib/testfs.img
	for x in $$(ls cmd/*-cmd.py cmd/*-cmd.sh | grep -vF python-cmd.sh | cut -b 5-); do \
	    echo "cmd/bup-$${x%-cmd.*}"; \
	done | xargs -t rm -f
	if test -e test/mnt; then test/cleanup-mounts-under test/mnt; fi
	if test -e test/mnt; then rm -r test/mnt; fi
	if test -e test/tmp; then test/cleanup-mounts-under test/tmp; fi
        # FIXME: migrate these to test/mnt/
	if test -e test/lib/testfs; \
	  then umount test/lib/testfs || true; fi
	rm -rf *.tmp *.tmp.meta test/*.tmp lib/*/*/*.tmp build lib/bup/build test/lib/testfs
	if test -e test/tmp; then test/force-delete test/tmp; fi
	test/configure-sampledata --clean
        # Remove last so that cleanup tools can depend on it
	rm -rf config/bin
