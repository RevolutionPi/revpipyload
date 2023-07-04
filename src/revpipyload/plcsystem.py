# -*- coding: utf-8 -*-
"""Modul fuer die Verwaltung der PLC Funktionen."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"

import os
import shlex
import subprocess
from pwd import getpwuid
from sys import stdout as sysstdout
from threading import Event, Thread
from time import asctime, sleep

from . import proginit
from .helper import _setuprt, _zeroprocimg
from .logsystem import PipeLogwriter
from .watchdogs import SoftwareWatchdog


class RevPiPlc(Thread):
    """Verwaltet das PLC Python Programm.

    Dieser Thread startet das PLC Python Programm und ueberwacht es. Sollte es
    abstuerzen kann es automatisch neu gestartet werden. Die Ausgaben des
    Programms werden in eine Logdatei umgeleitet, damit der Entwickler sein
    Programm analysieren und debuggen kann.

    """

    def __init__(self, program, arguments, pversion):
        """Instantiiert RevPiPlc-Klasse."""
        super().__init__()

        self._arguments = arguments
        self._autoreloaddelay = 5 * 2
        self._delaycounter = 5 * 2
        self._evt_exit = Event()
        self._plw = self._configureplw()
        self._program = program
        self._procplc = None
        self._pversion = pversion
        self._stop_timeout_steps = 10
        self.autoreload = False
        self.exitcode = None
        self.gid = 65534
        self.uid = 65534
        self.rtlevel = 0
        self.zeroonerror = False
        self.zeroonexit = False

        # Software watchdog
        self.softdog = SoftwareWatchdog(0, 0)

    def __exec_rtlevel(self):
        """RealTime Scheduler nutzen nach 5 Sekunden Programmvorlauf."""
        if self.rtlevel > 0 and not self._evt_exit.wait(5) and self._procplc.poll() is None:
            _setuprt(self._procplc.pid, self._evt_exit)

    def __get_autoreloaddelay(self):
        """Getter fuer autoreloaddelay.
        @return Delayzeit in Sekunden <class 'int'>"""
        return int(self._autoreloaddelay / 2)

    def __set_autoreloaddelay(self, value):
        """Setter fuer autoreloaddelay."""
        if type(value) != int:
            raise RuntimeError("parameter value must be <class 'int'>")
        self._autoreloaddelay = value * 2
        self._delaycounter = value * 2

    def _configureplw(self):
        """Konfiguriert den PipeLogwriter fuer Ausgaben der PLCAPP.
        @return PipeLogwriter()"""
        proginit.logger.debug("enter RevPiPlc._configureplw()")

        logfile = None
        if proginit.pargs.daemon:
            if os.access(os.path.dirname(proginit.logapp), os.R_OK | os.W_OK):
                logfile = proginit.logapp
        elif proginit.pargs.logfile is not None:
            logfile = proginit.pargs.logfile

        if logfile is not None:
            logfile = PipeLogwriter(logfile)

        proginit.logger.debug("leave RevPiPlc._configureplw()")
        return logfile

    def _setuppopen(self):
        """Setzt UID und GID fuer das PLC Programm."""
        proginit.logger.debug("enter RevPiPlc._setuppopen()")

        proginit.logger.info("set uid {0} and gid {1} for plc program".format(self.uid, self.gid))

        # Set user last to hold root right to do the group things
        try:
            name = getpwuid(self.uid).pw_name
            os.initgroups(name, self.gid)
        except Exception:
            proginit.logger.warning("could not initialize the group access list with all groups")
        os.setgid(self.gid)
        os.setuid(self.uid)

        proginit.logger.debug("leave RevPiPlc._setuppopen()")

    def _spopen(self, lst_proc):
        """Startet das PLC Programm.
        @param lst_proc Prozessliste
        @return subprocess"""
        proginit.logger.debug("enter RevPiPlc._spopen({0})".format(lst_proc))

        sp = subprocess.Popen(
            lst_proc,
            preexec_fn=self._setuppopen,
            cwd=os.path.dirname(self._program),
            bufsize=0,
            stdout=sysstdout if self._plw is None else self._plw.fdw,
            stderr=subprocess.STDOUT
        )
        proginit.logger.debug("leave RevPiPlc._spopen()")

        # Pass process to watchdog
        self.softdog.kill_process = sp
        self.softdog.reset()

        return sp

    def newlogfile(self):
        """Konfiguriert die FileHandler auf neue Logdatei."""
        proginit.logger.debug("enter RevPiPlc.newlogfile()")

        if self._plw is not None:
            self._plw.newlogfile()
            self._plw.logline("-" * 55)
            self._plw.logline("start new logfile: {0}".format(asctime()))

        proginit.logger.debug("leave RevPiPlc.newlogfile()")

    def run(self):
        """Fuehrt PLC-Programm aus und ueberwacht es."""
        proginit.logger.debug("enter RevPiPlc.run()")

        # LogWriter starten und Logausgaben schreiben
        if self._plw is not None:
            self._plw.logline("-" * 55)
            self._plw.logline("plc: {0} started: {1}".format(os.path.basename(self._program), asctime()))
            self._plw.start()

        # Befehlstliste aufbauen
        lst_proc = shlex.split("/usr/bin/env {0} -u {1} {2}".format(
            "python2" if self._pversion == 2 else "python3",
            self._program,
            self._arguments
        ))

        # Prozess erstellen
        proginit.logger.info("start plc program {0}".format(self._program))
        self._procplc = self._spopen(lst_proc)
        self.__exec_rtlevel()

        # Überwachung starten
        while not self._evt_exit.is_set():

            # Auswerten
            self.exitcode = self._procplc.poll()

            if self.exitcode is not None:
                # Do nothing, if delay for restart is running
                if self._delaycounter == self._autoreloaddelay:
                    self.softdog.stop()

                    if self.exitcode == 0:
                        # PLC Python Programm sauber beendet
                        proginit.logger.info("plc program did a clean exit")
                        if self.zeroonexit:
                            _zeroprocimg()
                            proginit.logger.info("set piControl0 to ZERO after PLC program returns clean exitcode")
                    else:
                        # PLC Python Programm abgestürzt
                        proginit.logger.error("plc program crashed - exitcode: {0}".format(self.exitcode))
                        if self.zeroonerror:
                            _zeroprocimg()
                            proginit.logger.warning("set piControl0 to ZERO after PLC program error")

                if not self._evt_exit.is_set() and self.autoreload:
                    self._delaycounter -= 1
                    if self._delaycounter < 0:
                        self._delaycounter = self._autoreloaddelay

                        # Prozess neu starten
                        self._procplc = self._spopen(lst_proc)
                        if self.exitcode == 0:
                            proginit.logger.warning("restart plc program after clean exit")
                        else:
                            proginit.logger.warning("restart plc program after crash")
                        self.__exec_rtlevel()
                else:
                    break

            self._evt_exit.wait(0.5)

        if self._plw is not None:
            self._plw.logline("-" * 55)
            self._plw.logline("plc: {0} stopped: {1}".format(os.path.basename(self._program), asctime()))

        proginit.logger.debug("leave RevPiPlc.run()")

    def stop(self):
        """Beendet PLC-Programm."""
        proginit.logger.debug("enter RevPiPlc.stop()")

        proginit.logger.info("stop revpiplc thread")
        self._evt_exit.set()
        self.softdog.stop()

        # Prüfen ob es einen subprocess gibt
        if self._procplc is None:
            if self._plw is not None:
                self._plw.stop()
                self._plw.join()
                proginit.logger.debug("log pipes successfully closed")

            proginit.logger.debug("leave RevPiPlc.stop()")
            return

        # Prozess beenden
        count = 0
        proginit.logger.info("term plc program {0}".format(self._program))
        try:
            self._procplc.terminate()
        except ProcessLookupError:
            proginit.logger.error("plc program was terminated unexpectedly")
            proginit.logger.debug("leave RevPiPlc.stop()")
            return

        while self._procplc.poll() is None and count < self._stop_timeout_steps:
            count += 1
            proginit.logger.debug("wait term plc program {0} seconds".format(count * 0.5))
            sleep(0.5)
        if self._procplc.poll() is None:
            proginit.logger.warning("can not term plc program {0}".format(self._program))
            self._procplc.kill()
            proginit.logger.warning("killed plc program")

        # Exitcode auswerten
        self.exitcode = self._procplc.poll()
        if self.zeroonexit and self.exitcode == 0 or self.zeroonerror and self.exitcode != 0:
            _zeroprocimg()

        if self.exitcode == 0:
            proginit.logger.info("stopped plc program")
        elif self.exitcode == -15:
            proginit.logger.warning("terminated plc program without own exit code")
        elif self.exitcode is not None and self.exitcode > 0:
            proginit.logger.warning("stopped plc program with exit code {0}".format(self.exitcode))

        if self._plw is not None:
            self._plw.stop()
            self._plw.join()
            proginit.logger.debug("log pipes successfully closed")

        proginit.logger.debug("leave RevPiPlc.stop()")

    autoreloaddelay = property(__get_autoreloaddelay, __set_autoreloaddelay)

    @property
    def stop_timeout(self) -> int:
        """Get program stop timeout in seconds."""
        return self._stop_timeout_steps // 2

    @stop_timeout.setter
    def stop_timeout(self, value: int):
        """Set program stop timeout in seconds."""
        if type(value) != int:
            raise TypeError("Value of stop_timeout must be <class 'int'>")
        self._stop_timeout_steps = value * 2
