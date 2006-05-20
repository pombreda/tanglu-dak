#!/usr/bin/env python

# Installs Debian packages from queue/accepted into the pool
# Copyright (C) 2000, 2001, 2002, 2003, 2004, 2006  James Troup <james@nocrew.org>

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

###############################################################################

#    Cartman: "I'm trying to make the best of a bad situation, I don't
#              need to hear crap from a bunch of hippy freaks living in
#              denial.  Screw you guys, I'm going home."
#
#    Kyle: "But Cartman, we're trying to..."
#
#    Cartman: "uhh.. screw you guys... home."

###############################################################################

import errno, fcntl, os, sys, time, re
import apt_pkg
import daklib.database
import daklib.logging
import daklib.queue 
import daklib.utils

###############################################################################

Cnf = None
Options = None
Logger = None
Urgency_Logger = None
projectB = None
Upload = None
pkg = None

reject_message = ""
changes = None
dsc = None
dsc_files = None
files = None
Subst = None

install_count = 0
install_bytes = 0.0

installing_to_stable = 0

###############################################################################

# FIXME: this should go away to some Debian specific file
# FIXME: should die if file already exists

class Urgency_Log:
    "Urgency Logger object"
    def __init__ (self, Cnf):
        "Initialize a new Urgency Logger object"
        self.Cnf = Cnf
        self.timestamp = time.strftime("%Y%m%d%H%M%S")
        # Create the log directory if it doesn't exist
        self.log_dir = Cnf["Dir::UrgencyLog"]
        if not os.path.exists(self.log_dir):
            umask = os.umask(00000)
            os.makedirs(self.log_dir, 02775)
        # Open the logfile
        self.log_filename = "%s/.install-urgencies-%s.new" % (self.log_dir, self.timestamp)
        self.log_file = daklib.utils.open_file(self.log_filename, 'w')
        self.writes = 0

    def log (self, source, version, urgency):
        "Log an event"
        self.log_file.write(" ".join([source, version, urgency])+'\n')
        self.log_file.flush()
        self.writes += 1

    def close (self):
        "Close a Logger object"
        self.log_file.flush()
        self.log_file.close()
        if self.writes:
            new_filename = "%s/install-urgencies-%s" % (self.log_dir, self.timestamp)
            daklib.utils.move(self.log_filename, new_filename)
        else:
            os.unlink(self.log_filename)

###############################################################################

def reject (str, prefix="Rejected: "):
    global reject_message
    if str:
        reject_message += prefix + str + "\n"

# Recheck anything that relies on the database; since that's not
# frozen between accept and our run time.

def check():
    propogate={}
    nopropogate={}
    for file in files.keys():
        # The .orig.tar.gz can disappear out from under us is it's a
        # duplicate of one in the archive.
        if not files.has_key(file):
            continue
        # Check that the source still exists
        if files[file]["type"] == "deb":
            source_version = files[file]["source version"]
            source_package = files[file]["source package"]
            if not changes["architecture"].has_key("source") \
               and not Upload.source_exists(source_package, source_version,  changes["distribution"].keys()):
                reject("no source found for %s %s (%s)." % (source_package, source_version, file))

        # Version and file overwrite checks
        if not installing_to_stable:
            if files[file]["type"] == "deb":
                reject(Upload.check_binary_against_db(file), "")
            elif files[file]["type"] == "dsc":
                reject(Upload.check_source_against_db(file), "")
                (reject_msg, is_in_incoming) = Upload.check_dsc_against_db(file)
                reject(reject_msg, "")

        # propogate in the case it is in the override tables:
        if changes.has_key("propdistribution"):
            for suite in changes["propdistribution"].keys():
		if Upload.in_override_p(files[file]["package"], files[file]["component"], suite, files[file].get("dbtype",""), file):
		    propogate[suite] = 1
		else:
		    nopropogate[suite] = 1

    for suite in propogate.keys():
	if suite in nopropogate:
	    continue
	changes["distribution"][suite] = 1

    for file in files.keys():
        # Check the package is still in the override tables
        for suite in changes["distribution"].keys():
            if not Upload.in_override_p(files[file]["package"], files[file]["component"], suite, files[file].get("dbtype",""), file):
                reject("%s is NEW for %s." % (file, suite))

###############################################################################

def init():
    global Cnf, Options, Upload, projectB, changes, dsc, dsc_files, files, pkg, Subst

    Cnf = daklib.utils.get_conf()

    Arguments = [('a',"automatic","Dinstall::Options::Automatic"),
                 ('h',"help","Dinstall::Options::Help"),
                 ('n',"no-action","Dinstall::Options::No-Action"),
                 ('p',"no-lock", "Dinstall::Options::No-Lock"),
                 ('s',"no-mail", "Dinstall::Options::No-Mail")]

    for i in ["automatic", "help", "no-action", "no-lock", "no-mail", "version"]:
	if not Cnf.has_key("Dinstall::Options::%s" % (i)):
	    Cnf["Dinstall::Options::%s" % (i)] = ""

    changes_files = apt_pkg.ParseCommandLine(Cnf,Arguments,sys.argv)
    Options = Cnf.SubTree("Dinstall::Options")

    if Options["Help"]:
        usage()

    Upload = daklib.queue.Upload(Cnf)
    projectB = Upload.projectB

    changes = Upload.pkg.changes
    dsc = Upload.pkg.dsc
    dsc_files = Upload.pkg.dsc_files
    files = Upload.pkg.files
    pkg = Upload.pkg
    Subst = Upload.Subst

    return changes_files

###############################################################################

def usage (exit_code=0):
    print """Usage: dak process-accepted [OPTION]... [CHANGES]...
  -a, --automatic           automatic run
  -h, --help                show this help and exit.
  -n, --no-action           don't do anything
  -p, --no-lock             don't check lockfile !! for cron.daily only !!
  -s, --no-mail             don't send any mail
  -V, --version             display the version number and exit"""
    sys.exit(exit_code)

###############################################################################

def action ():
    (summary, short_summary) = Upload.build_summaries()

    (prompt, answer) = ("", "XXX")
    if Options["No-Action"] or Options["Automatic"]:
        answer = 'S'

    if reject_message.find("Rejected") != -1:
        print "REJECT\n" + reject_message,
        prompt = "[R]eject, Skip, Quit ?"
        if Options["Automatic"]:
            answer = 'R'
    else:
        print "INSTALL to " + ", ".join(changes["distribution"].keys()) 
	print reject_message + summary,
        prompt = "[I]nstall, Skip, Quit ?"
        if Options["Automatic"]:
            answer = 'I'

    while prompt.find(answer) == -1:
        answer = daklib.utils.our_raw_input(prompt)
        m = daklib.queue.re_default_answer.match(prompt)
        if answer == "":
            answer = m.group(1)
        answer = answer[:1].upper()

    if answer == 'R':
        do_reject ()
    elif answer == 'I':
        if not installing_to_stable:
            install()
        else:
            stable_install(summary, short_summary)
    elif answer == 'Q':
        sys.exit(0)

###############################################################################

# Our reject is not really a reject, but an unaccept, but since a) the
# code for that is non-trivial (reopen bugs, unannounce etc.), b) this
# should be exteremly rare, for now we'll go with whining at our admin
# folks...

def do_reject ():
    Subst["__REJECTOR_ADDRESS__"] = Cnf["Dinstall::MyEmailAddress"]
    Subst["__REJECT_MESSAGE__"] = reject_message
    Subst["__CC__"] = "Cc: " + Cnf["Dinstall::MyEmailAddress"]
    reject_mail_message = daklib.utils.TemplateSubst(Subst,Cnf["Dir::Templates"]+"/process-accepted.unaccept")

    # Write the rejection email out as the <foo>.reason file
    reason_filename = os.path.basename(pkg.changes_file[:-8]) + ".reason"
    reject_filename = Cnf["Dir::Queue::Reject"] + '/' + reason_filename
    # If we fail here someone is probably trying to exploit the race
    # so let's just raise an exception ...
    if os.path.exists(reject_filename):
        os.unlink(reject_filename)
    fd = os.open(reject_filename, os.O_RDWR|os.O_CREAT|os.O_EXCL, 0644)
    os.write(fd, reject_mail_message)
    os.close(fd)

    daklib.utils.send_mail(reject_mail_message)
    Logger.log(["unaccepted", pkg.changes_file])

###############################################################################

def install ():
    global install_count, install_bytes

    print "Installing."

    Logger.log(["installing changes",pkg.changes_file])

    # Begin a transaction; if we bomb out anywhere between here and the COMMIT WORK below, the DB will not be changed.
    projectB.query("BEGIN WORK")

    # Add the .dsc file to the DB
    for file in files.keys():
        if files[file]["type"] == "dsc":
            package = dsc["source"]
            version = dsc["version"]  # NB: not files[file]["version"], that has no epoch
            maintainer = dsc["maintainer"]
            maintainer = maintainer.replace("'", "\\'")
            maintainer_id = daklib.database.get_or_set_maintainer_id(maintainer)
            fingerprint_id = daklib.database.get_or_set_fingerprint_id(dsc["fingerprint"])
            install_date = time.strftime("%Y-%m-%d")
            filename = files[file]["pool name"] + file
            dsc_component = files[file]["component"]
            dsc_location_id = files[file]["location id"]
            if not files[file].has_key("files id") or not files[file]["files id"]:
                files[file]["files id"] = daklib.database.set_files_id (filename, files[file]["size"], files[file]["md5sum"], dsc_location_id)
            projectB.query("INSERT INTO source (source, version, maintainer, file, install_date, sig_fpr) VALUES ('%s', '%s', %d, %d, '%s', %s)"
                           % (package, version, maintainer_id, files[file]["files id"], install_date, fingerprint_id))

            for suite in changes["distribution"].keys():
                suite_id = daklib.database.get_suite_id(suite)
                projectB.query("INSERT INTO src_associations (suite, source) VALUES (%d, currval('source_id_seq'))" % (suite_id))

            # Add the source files to the DB (files and dsc_files)
            projectB.query("INSERT INTO dsc_files (source, file) VALUES (currval('source_id_seq'), %d)" % (files[file]["files id"]))
            for dsc_file in dsc_files.keys():
                filename = files[file]["pool name"] + dsc_file
                # If the .orig.tar.gz is already in the pool, it's
                # files id is stored in dsc_files by check_dsc().
                files_id = dsc_files[dsc_file].get("files id", None)
                if files_id == None:
                    files_id = daklib.database.get_files_id(filename, dsc_files[dsc_file]["size"], dsc_files[dsc_file]["md5sum"], dsc_location_id)
                # FIXME: needs to check for -1/-2 and or handle exception
                if files_id == None:
                    files_id = daklib.database.set_files_id (filename, dsc_files[dsc_file]["size"], dsc_files[dsc_file]["md5sum"], dsc_location_id)
                projectB.query("INSERT INTO dsc_files (source, file) VALUES (currval('source_id_seq'), %d)" % (files_id))

    # Add the .deb files to the DB
    for file in files.keys():
        if files[file]["type"] == "deb":
            package = files[file]["package"]
            version = files[file]["version"]
            maintainer = files[file]["maintainer"]
            maintainer = maintainer.replace("'", "\\'")
            maintainer_id = daklib.database.get_or_set_maintainer_id(maintainer)
            fingerprint_id = daklib.database.get_or_set_fingerprint_id(changes["fingerprint"])
            architecture = files[file]["architecture"]
            architecture_id = daklib.database.get_architecture_id (architecture)
            type = files[file]["dbtype"]
            source = files[file]["source package"]
            source_version = files[file]["source version"]
            filename = files[file]["pool name"] + file
	    if not files[file].has_key("location id") or not files[file]["location id"]:
		files[file]["location id"] = daklib.database.get_location_id(Cnf["Dir::Pool"],files[file]["component"],daklib.utils.where_am_i())
            if not files[file].has_key("files id") or not files[file]["files id"]:
                files[file]["files id"] = daklib.database.set_files_id (filename, files[file]["size"], files[file]["md5sum"], files[file]["location id"])
            source_id = daklib.database.get_source_id (source, source_version)
            if source_id:
                projectB.query("INSERT INTO binaries (package, version, maintainer, source, architecture, file, type, sig_fpr) VALUES ('%s', '%s', %d, %d, %d, %d, '%s', %d)"
                               % (package, version, maintainer_id, source_id, architecture_id, files[file]["files id"], type, fingerprint_id))
            else:
                projectB.query("INSERT INTO binaries (package, version, maintainer, architecture, file, type, sig_fpr) VALUES ('%s', '%s', %d, %d, %d, '%s', %d)"
                               % (package, version, maintainer_id, architecture_id, files[file]["files id"], type, fingerprint_id))
            for suite in changes["distribution"].keys():
                suite_id = daklib.database.get_suite_id(suite)
                projectB.query("INSERT INTO bin_associations (suite, bin) VALUES (%d, currval('binaries_id_seq'))" % (suite_id))

    # If the .orig.tar.gz is in a legacy directory we need to poolify
    # it, so that apt-get source (and anything else that goes by the
    # "Directory:" field in the Sources.gz file) works.
    orig_tar_id = Upload.pkg.orig_tar_id
    orig_tar_location = Upload.pkg.orig_tar_location
    legacy_source_untouchable = Upload.pkg.legacy_source_untouchable
    if orig_tar_id and orig_tar_location == "legacy":
        q = projectB.query("SELECT DISTINCT ON (f.id) l.path, f.filename, f.id as files_id, df.source, df.id as dsc_files_id, f.size, f.md5sum FROM files f, dsc_files df, location l WHERE df.source IN (SELECT source FROM dsc_files WHERE file = %s) AND f.id = df.file AND l.id = f.location AND (l.type = 'legacy' OR l.type = 'legacy-mixed')" % (orig_tar_id))
        qd = q.dictresult()
        for qid in qd:
            # Is this an old upload superseded by a newer -sa upload?  (See check_dsc() for details)
            if legacy_source_untouchable.has_key(qid["files_id"]):
                continue
            # First move the files to the new location
            legacy_filename = qid["path"] + qid["filename"]
            pool_location = daklib.utils.poolify (changes["source"], files[file]["component"])
            pool_filename = pool_location + os.path.basename(qid["filename"])
            destination = Cnf["Dir::Pool"] + pool_location
            daklib.utils.move(legacy_filename, destination)
            # Then Update the DB's files table
            q = projectB.query("UPDATE files SET filename = '%s', location = '%s' WHERE id = '%s'" % (pool_filename, dsc_location_id, qid["files_id"]))

    # If this is a sourceful diff only upload that is moving non-legacy
    # cross-component we need to copy the .orig.tar.gz into the new
    # component too for the same reasons as above.
    #
    if changes["architecture"].has_key("source") and orig_tar_id and \
       orig_tar_location != "legacy" and orig_tar_location != dsc_location_id:
        q = projectB.query("SELECT l.path, f.filename, f.size, f.md5sum FROM files f, location l WHERE f.id = %s AND f.location = l.id" % (orig_tar_id))
        ql = q.getresult()[0]
        old_filename = ql[0] + ql[1]
        file_size = ql[2]
        file_md5sum = ql[3]
        new_filename = daklib.utils.poolify(changes["source"], dsc_component) + os.path.basename(old_filename)
        new_files_id = daklib.database.get_files_id(new_filename, file_size, file_md5sum, dsc_location_id)
        if new_files_id == None:
            daklib.utils.copy(old_filename, Cnf["Dir::Pool"] + new_filename)
            new_files_id = daklib.database.set_files_id(new_filename, file_size, file_md5sum, dsc_location_id)
            projectB.query("UPDATE dsc_files SET file = %s WHERE source = %s AND file = %s" % (new_files_id, source_id, orig_tar_id))

    # Install the files into the pool
    for file in files.keys():
        destination = Cnf["Dir::Pool"] + files[file]["pool name"] + file
        daklib.utils.move(file, destination)
        Logger.log(["installed", file, files[file]["type"], files[file]["size"], files[file]["architecture"]])
        install_bytes += float(files[file]["size"])

    # Copy the .changes file across for suite which need it.
    copy_changes = {}
    copy_dot_dak = {}
    for suite in changes["distribution"].keys():
        if Cnf.has_key("Suite::%s::CopyChanges" % (suite)):
            copy_changes[Cnf["Suite::%s::CopyChanges" % (suite)]] = ""
        # and the .dak file...
        if Cnf.has_key("Suite::%s::CopyDotDak" % (suite)):
            copy_dot_dak[Cnf["Suite::%s::CopyDotDak" % (suite)]] = ""
    for dest in copy_changes.keys():
        daklib.utils.copy(pkg.changes_file, Cnf["Dir::Root"] + dest)
    for dest in copy_dot_dak.keys():
        daklib.utils.copy(Upload.pkg.changes_file[:-8]+".dak", dest)

    projectB.query("COMMIT WORK")

    # Move the .changes into the 'done' directory
    daklib.utils.move (pkg.changes_file,
                os.path.join(Cnf["Dir::Queue::Done"], os.path.basename(pkg.changes_file)))

    # Remove the .dak file
    os.unlink(Upload.pkg.changes_file[:-8]+".dak")

    if changes["architecture"].has_key("source") and Urgency_Logger:
        Urgency_Logger.log(dsc["source"], dsc["version"], changes["urgency"])

    # Undo the work done in queue.py(accept) to help auto-building
    # from accepted.
    projectB.query("BEGIN WORK")
    for suite in changes["distribution"].keys():
        if suite not in Cnf.ValueList("Dinstall::QueueBuildSuites"):
            continue
        now_date = time.strftime("%Y-%m-%d %H:%M")
        suite_id = daklib.database.get_suite_id(suite)
        dest_dir = Cnf["Dir::QueueBuild"]
        if Cnf.FindB("Dinstall::SecurityQueueBuild"):
            dest_dir = os.path.join(dest_dir, suite)
        for file in files.keys():
            dest = os.path.join(dest_dir, file)
            # Remove it from the list of packages for later processing by apt-ftparchive
            projectB.query("UPDATE queue_build SET in_queue = 'f', last_used = '%s' WHERE filename = '%s' AND suite = %s" % (now_date, dest, suite_id))
            if not Cnf.FindB("Dinstall::SecurityQueueBuild"):
                # Update the symlink to point to the new location in the pool
                pool_location = daklib.utils.poolify (changes["source"], files[file]["component"])
                src = os.path.join(Cnf["Dir::Pool"], pool_location, os.path.basename(file))
                if os.path.islink(dest):
                    os.unlink(dest)
                os.symlink(src, dest)
        # Update last_used on any non-upload .orig.tar.gz symlink
        if orig_tar_id:
            # Determine the .orig.tar.gz file name
            for dsc_file in dsc_files.keys():
                if dsc_file.endswith(".orig.tar.gz"):
                    orig_tar_gz = os.path.join(dest_dir, dsc_file)
            # Remove it from the list of packages for later processing by apt-ftparchive
            projectB.query("UPDATE queue_build SET in_queue = 'f', last_used = '%s' WHERE filename = '%s' AND suite = %s" % (now_date, orig_tar_gz, suite_id))
    projectB.query("COMMIT WORK")

    # Finally...
    install_count += 1

################################################################################

def stable_install (summary, short_summary):
    global install_count

    print "Installing to stable."

    # Begin a transaction; if we bomb out anywhere between here and
    # the COMMIT WORK below, the DB won't be changed.
    projectB.query("BEGIN WORK")

    # Add the source to stable (and remove it from proposed-updates)
    for file in files.keys():
        if files[file]["type"] == "dsc":
            package = dsc["source"]
            version = dsc["version"];  # NB: not files[file]["version"], that has no epoch
            q = projectB.query("SELECT id FROM source WHERE source = '%s' AND version = '%s'" % (package, version))
            ql = q.getresult()
            if not ql:
                daklib.utils.fubar("[INTERNAL ERROR] couldn't find '%s' (%s) in source table." % (package, version))
            source_id = ql[0][0]
            suite_id = daklib.database.get_suite_id('proposed-updates')
            projectB.query("DELETE FROM src_associations WHERE suite = '%s' AND source = '%s'" % (suite_id, source_id))
            suite_id = daklib.database.get_suite_id('stable')
            projectB.query("INSERT INTO src_associations (suite, source) VALUES ('%s', '%s')" % (suite_id, source_id))

    # Add the binaries to stable (and remove it/them from proposed-updates)
    for file in files.keys():
        if files[file]["type"] == "deb":
	    binNMU = 0
            package = files[file]["package"]
            version = files[file]["version"]
            architecture = files[file]["architecture"]
            q = projectB.query("SELECT b.id FROM binaries b, architecture a WHERE b.package = '%s' AND b.version = '%s' AND (a.arch_string = '%s' OR a.arch_string = 'all') AND b.architecture = a.id" % (package, version, architecture))
            ql = q.getresult()
            if not ql:
		suite_id = daklib.database.get_suite_id('proposed-updates')
		que = "SELECT b.version FROM binaries b JOIN bin_associations ba ON (b.id = ba.bin) JOIN suite su ON (ba.suite = su.id) WHERE b.package = '%s' AND (ba.suite = '%s')" % (package, suite_id)
		q = projectB.query(que)

		# Reduce the query results to a list of version numbers
		ql = map(lambda x: x[0], q.getresult())
		if not ql:
		    daklib.utils.fubar("[INTERNAL ERROR] couldn't find '%s' (%s for %s architecture) in binaries table." % (package, version, architecture))
		else:
		    for x in ql:
			if re.match(re.compile(r"%s((\.0)?\.)|(\+b)\d+$" % re.escape(version)),x):
			    binNMU = 1
			    break
	    if not binNMU:
		binary_id = ql[0][0]
		suite_id = daklib.database.get_suite_id('proposed-updates')
		projectB.query("DELETE FROM bin_associations WHERE suite = '%s' AND bin = '%s'" % (suite_id, binary_id))
		suite_id = daklib.database.get_suite_id('stable')
		projectB.query("INSERT INTO bin_associations (suite, bin) VALUES ('%s', '%s')" % (suite_id, binary_id))
	    else:
                del files[file]

    projectB.query("COMMIT WORK")

    daklib.utils.move (pkg.changes_file, Cnf["Dir::Morgue"] + '/process-accepted/' + os.path.basename(pkg.changes_file))

    ## Update the Stable ChangeLog file
    new_changelog_filename = Cnf["Dir::Root"] + Cnf["Suite::Stable::ChangeLogBase"] + ".ChangeLog"
    changelog_filename = Cnf["Dir::Root"] + Cnf["Suite::Stable::ChangeLogBase"] + "ChangeLog"
    if os.path.exists(new_changelog_filename):
        os.unlink (new_changelog_filename)

    new_changelog = daklib.utils.open_file(new_changelog_filename, 'w')
    for file in files.keys():
        if files[file]["type"] == "deb":
            new_changelog.write("stable/%s/binary-%s/%s\n" % (files[file]["component"], files[file]["architecture"], file))
        elif daklib.utils.re_issource.match(file):
            new_changelog.write("stable/%s/source/%s\n" % (files[file]["component"], file))
        else:
            new_changelog.write("%s\n" % (file))
    chop_changes = daklib.queue.re_fdnic.sub("\n", changes["changes"])
    new_changelog.write(chop_changes + '\n\n')
    if os.access(changelog_filename, os.R_OK) != 0:
        changelog = daklib.utils.open_file(changelog_filename)
        new_changelog.write(changelog.read())
    new_changelog.close()
    if os.access(changelog_filename, os.R_OK) != 0:
        os.unlink(changelog_filename)
    daklib.utils.move(new_changelog_filename, changelog_filename)

    install_count += 1

    if not Options["No-Mail"] and changes["architecture"].has_key("source"):
        Subst["__SUITE__"] = " into stable"
        Subst["__SUMMARY__"] = summary
        mail_message = daklib.utils.TemplateSubst(Subst,Cnf["Dir::Templates"]+"/process-accepted.installed")
        daklib.utils.send_mail(mail_message)
        Upload.announce(short_summary, 1)

    # Finally remove the .dak file
    dot_dak_file = os.path.join(Cnf["Suite::Proposed-Updates::CopyDotDak"], os.path.basename(Upload.pkg.changes_file[:-8]+".dak"))
    os.unlink(dot_dak_file)

################################################################################

def process_it (changes_file):
    global reject_message

    reject_message = ""

    # Absolutize the filename to avoid the requirement of being in the
    # same directory as the .changes file.
    pkg.changes_file = os.path.abspath(changes_file)

    # And since handling of installs to stable munges with the CWD
    # save and restore it.
    pkg.directory = os.getcwd()

    if installing_to_stable:
        old = Upload.pkg.changes_file
        Upload.pkg.changes_file = os.path.basename(old)
        os.chdir(Cnf["Suite::Proposed-Updates::CopyDotDak"])

    Upload.init_vars()
    Upload.update_vars()
    Upload.update_subst()

    if installing_to_stable:
        Upload.pkg.changes_file = old

    check()
    action()

    # Restore CWD
    os.chdir(pkg.directory)

###############################################################################

def main():
    global projectB, Logger, Urgency_Logger, installing_to_stable

    changes_files = init()

    # -n/--dry-run invalidates some other options which would involve things happening
    if Options["No-Action"]:
        Options["Automatic"] = ""

    # Check that we aren't going to clash with the daily cron job

    if not Options["No-Action"] and os.path.exists("%s/Archive_Maintenance_In_Progress" % (Cnf["Dir::Root"])) and not Options["No-Lock"]:
        daklib.utils.fubar("Archive maintenance in progress.  Try again later.")

    # If running from within proposed-updates; assume an install to stable
    if os.getcwd().find('proposed-updates') != -1:
        installing_to_stable = 1

    # Obtain lock if not in no-action mode and initialize the log
    if not Options["No-Action"]:
        lock_fd = os.open(Cnf["Dinstall::LockFile"], os.O_RDWR | os.O_CREAT)
        try:
            fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError, e:
            if errno.errorcode[e.errno] == 'EACCES' or errno.errorcode[e.errno] == 'EAGAIN':
                daklib.utils.fubar("Couldn't obtain lock; assuming another 'dak process-accepted' is already running.")
            else:
                raise
        Logger = Upload.Logger = daklib.logging.Logger(Cnf, "process-accepted")
        if not installing_to_stable and Cnf.get("Dir::UrgencyLog"):
            Urgency_Logger = Urgency_Log(Cnf)

    # Initialize the substitution template mapping global
    bcc = "X-DAK: dak process-accepted\nX-Katie: this header is obsolete"
    if Cnf.has_key("Dinstall::Bcc"):
        Subst["__BCC__"] = bcc + "\nBcc: %s" % (Cnf["Dinstall::Bcc"])
    else:
        Subst["__BCC__"] = bcc

    # Sort the .changes files so that we process sourceful ones first
    changes_files.sort(daklib.utils.changes_compare)

    # Process the changes files
    for changes_file in changes_files:
        print "\n" + changes_file
        process_it (changes_file)

    if install_count:
        sets = "set"
        if install_count > 1:
            sets = "sets"
        sys.stderr.write("Installed %d package %s, %s.\n" % (install_count, sets, daklib.utils.size_type(int(install_bytes))))
        Logger.log(["total",install_count,install_bytes])

    if not Options["No-Action"]:
        Logger.close()
        if Urgency_Logger:
            Urgency_Logger.close()

###############################################################################

if __name__ == '__main__':
    main()