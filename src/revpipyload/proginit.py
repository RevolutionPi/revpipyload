# -*- coding: utf-8 -*-
"""Main functions of our program."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2018 Sven Sager"
__license__ = "GPLv2"

import logging
import os
import sys
from argparse import ArgumentParser
from configparser import ConfigParser

from . import __version__

conf = ConfigParser()
forked = False
globalconffile = None
logapp = "revpipyloadapp.log"
logplc = "revpipyload.log"
logger = logging.getLogger()
pargs = None
rapcatalog = None
startdir = None


def cleanup():
    """Clean up program."""
    if pargs is not None and pargs.daemon:
        if os.path.exists("/var/run/revpipyload.pid"):
            os.remove("/var/run/revpipyload.pid")

    # Logging beenden
    logging.shutdown()

    # Dateihandler schließen
    if pargs.daemon:
        sys.stdout.close()


def configure():
    """Initialize general program functions."""

    # Command arguments
    parser = ArgumentParser(
        description="RevolutionPi Python Loader"
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s {0}".format(__version__)
    )
    parser.add_argument(
        "-d", "--daemon", action="store_true", dest="daemon",
        help="Run program as a daemon in background"
    )
    parser.add_argument(
        "-c", "--conffile", dest="conffile",
        default="revpipyload.conf",
        help="Application configuration file"
    )
    parser.add_argument(
        "-f", "--logfile", dest="logfile",
        help="Save log entries to this file"
    )
    parser.add_argument(
        "--procimg", dest="procimg",
        default="/dev/piControl0",
        help="Path to process image"
    )
    parser.add_argument(
        "--pictory", dest="configrsc",
        help="piCtory file to use"
    )
    parser.add_argument(
        "--test", action="store_true", dest="test",
        help="Test parameters of config files and print results"
    )
    parser.add_argument(
        "-v", "--verbose", action="count", dest="verbose",
        help="Switch on verbose logging: info -v debug -vv"
    )
    parser.add_argument(
        "--developermode", action="store_true", dest="developermode",
        default=False
    )
    global pargs
    pargs = parser.parse_args()

    # Prüfen ob als Daemon ausgeführt werden soll
    global forked
    pidfile = "/var/run/revpipyload.pid"
    if pargs.daemon and not forked:
        # Prüfen ob daemon schon läuft
        if os.path.exists(pidfile):
            raise SystemError(
                "program already running as daemon. check {0}".format(pidfile)
            )

        # Zum daemon machen
        pid = os.fork()
        if pid > 0:
            with open(pidfile, "w") as f:
                f.write(str(pid))
            sys.exit(0)
        else:
            forked = True

    # piCtory Konfiguration prüfen
    lst_rsc = ["/etc/revpi/config.rsc", "/opt/KUNBUS/config.rsc"]
    if pargs.configrsc is None:
        for rscfile in lst_rsc:
            if os.access(rscfile, os.F_OK | os.R_OK):
                pargs.configrsc = rscfile
                break
    elif not os.access(pargs.configrsc, os.F_OK | os.R_OK):
        pargs.configrsc = None

    if pargs.configrsc is None:
        raise RuntimeError(
            "can not find known pictory configurations at {0}"
            "".format(", ".join(lst_rsc))
        )

    # rap Katalog an bekannten Stellen prüfen und laden
    global rapcatalog
    lst_rap = [
        "/opt/KUNBUS/pictory/resources/data/rap",
        "/var/www/pictory/resources/data/rap"
    ]
    for rapfolder in lst_rap:
        if os.path.isdir(rapfolder):
            rapcatalog = os.listdir(rapfolder)

    # Pfade absolut umschreiben
    global startdir
    if startdir is None:
        startdir = os.path.abspath(".")
    if pargs.conffile is not None and os.path.dirname(pargs.conffile) == "":
        pargs.conffile = os.path.join(startdir, pargs.conffile)
    if pargs.logfile is not None and os.path.dirname(pargs.logfile) == "":
        pargs.logfile = os.path.join(startdir, pargs.logfile)

    global logapp
    global logplc
    if pargs.daemon:
        # Ausgage vor Umhängen schließen
        sys.stdout.close()

        # Ausgaben umhängen in Logfile
        logapp = "/var/log/revpipyloadapp"
        logplc = "/var/log/revpipyload"
        pargs.conffile = "/etc/revpipyload/revpipyload.conf"
        sys.stdout = open(logplc, "a")
        sys.stderr = sys.stdout

    elif pargs.logfile is not None:
        logplc = pargs.logfile

    # Initialize configparser globalconfig
    global globalconffile
    globalconffile = pargs.conffile

    # Alle handler entfernen
    for lhandler in logger.handlers:
        logger.removeHandler(lhandler)

    # Neue Handler bauen
    logformat = logging.Formatter(
        "{asctime} [{levelname:8}] {message}",
        datefmt="%Y-%m-%d %H:%M:%S", style="{"
    )
    lhandler = logging.StreamHandler(sys.stdout)
    lhandler.setFormatter(logformat)
    logger.addHandler(lhandler)

    if pargs.logfile is not None:
        lhandler = logging.FileHandler(filename=pargs.logfile)
        lhandler.setFormatter(logformat)
        logger.addHandler(lhandler)

    # Loglevel auswerten
    if pargs.verbose is None:
        loglevel = logging.WARNING
    elif pargs.verbose == 1:
        loglevel = logging.INFO
    else:
        loglevel = logging.DEBUG
    logger.setLevel(loglevel)
