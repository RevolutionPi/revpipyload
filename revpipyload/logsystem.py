# -*- coding: utf-8 -*-
#
# RevPiPyLoad
#
# Webpage: https://revpimodio.org/revpipyplc/
# (c) Sven Sager, License: LGPLv3
#
"""Modul fuer die Verwaltung der Logdateien."""
import os
import proginit
from threading import Event, Lock, Thread
from xmlrpc.client import Binary


class LogReader():

    """Ermoeglicht den Zugriff auf die Logdateien.

    Beinhaltet Funktionen fuer den Abruf der gesamten Logdatei fuer das
    RevPiPyLoad-System und die Logdatei der PLC-Anwendung.

    """

    def __init__(self):
        """Instantiiert LogReader-Klasse."""
        self.fhapp = None
        self.fhapplk = Lock()
        self.fhplc = None
        self.fhplclk = Lock()

    def closeall(self):
        """Fuehrt close auf File Handler durch."""
        if self.fhapp is not None:
            self.fhapp.close()
        if self.fhplc is not None:
            self.fhplc.close()

    def load_applog(self, start, count):
        """Uebertraegt Logdaten des PLC Programms Binaer.

        @param start Startbyte
        @param count Max. Byteanzahl zum uebertragen
        @return Binary() der Logdatei

        """
        if not os.access(proginit.logapp, os.R_OK):
            return Binary(b'\x16')  # 
        elif start > os.path.getsize(proginit.logapp):
            return Binary(b'\x19')  # 
        else:
            with self.fhapplk:
                if self.fhapp is None or self.fhapp.closed:
                    self.fhapp = open(proginit.logapp, "rb")

                self.fhapp.seek(start)
                return Binary(self.fhapp.read(count))

    def load_plclog(self, start, count):
        """Uebertraegt Logdaten des Loaders Binaer.

        @param start Startbyte
        @param count Max. Byteanzahl zum uebertragen
        @return Binary() der Logdatei

        """
        if not os.access(proginit.logplc, os.R_OK):
            return Binary(b'\x16')  # 
        elif start > os.path.getsize(proginit.logplc):
            return Binary(b'\x19')  # 
        else:
            with self.fhplclk:
                if self.fhplc is None or self.fhplc.closed:
                    self.fhplc = open(proginit.logplc, "rb")

                self.fhplc.seek(start)
                return Binary(self.fhplc.read(count))


class PipeLogwriter(Thread):

    """File PIPE fuer das Schreiben des APP Log.

    Spezieller LogFile-Handler fuer die Ausgabe des subprocess fuer das Python
    PLC Programm. Die Ausgabe kann nicht auf einen neuen FileHandler
    umgeschrieben werden. Dadurch waere es nicht moeglich nach einem logrotate
    die neue Datei zu verwenden. Ueber die PIPE wird dies umgangen.

    """

    def __init__(self, logfilename):
        """Instantiiert PipeLogwriter-Klasse.
        @param logfilename Dateiname fuer Logdatei"""
        super().__init__()
        self._exit = Event()
        self._lckfh = Lock()
        self.logfile = logfilename

        # Logdatei öffnen
        self._fh = self._configurefh()

        # Pipes öffnen
        self._fdr, self.fdw = os.pipe()
        proginit.logger.debug("pipe fd read: {} / write: {}".format(
            self._fdr, self.fdw
        ))

    def __del__(self):
        """Close der FileHandler."""
        # FileHandler schließen
        if self._fh is not None:
            self._fh.close()

    def _configurefh(self):
        """Konfiguriert den FileHandler fuer Ausgaben der PLCAPP.
        @return FileHandler-Objekt"""
        proginit.logger.debug("enter PipeLogwriter._configurefh()")

        logfile = None
        dirname = os.path.dirname(self.logfile)

        if os.access(dirname, os.R_OK | os.W_OK):
            logfile = open(self.logfile, "a")
        else:
            raise RuntimeError("can not open logfile {}".format(self.logfile))

        proginit.logger.debug("leave PipeLogwriter._configurefh()")
        return logfile

    def logline(self, message):
        """Schreibt eine Zeile in die Logdatei oder stdout.
        @param message Logzeile zum Schreiben"""
        with self._lckfh:
            self._fh.write("{}\n".format(message))
            self._fh.flush()

    def newlogfile(self):
        """Konfiguriert den FileHandler auf eine neue Logdatei."""
        proginit.logger.debug("enter RevPiPlc.newlogfile()")
        with self._lckfh:
            self._fh.close()
            self._fh = self._configurefh()
        proginit.logger.debug("leave RevPiPlc.newlogfile()")

    def run(self):
        """Prueft auf neue Logzeilen und schreibt diese."""
        proginit.logger.debug("enter PipeLogwriter.run()")

        fhread = os.fdopen(self._fdr)
        while not self._exit.is_set():
            line = fhread.readline()
            self._lckfh.acquire()
            try:
                self._fh.write(line)
                self._fh.flush()
            except:
                proginit.logger.exception("PipeLogwriter in write log line")
            finally:
                self._lckfh.release()
        proginit.logger.debug("leave logreader pipe loop")

        proginit.logger.debug("close all pipes")
        fhread.close()
        os.close(self.fdw)
        proginit.logger.debug("closed all pipes")

        # FileHandler schließen
        if self._fh is not None:
            self._fh.close()

        proginit.logger.debug("leave PipeLogwriter.run()")

    def stop(self):
        """Beendetden Thread und die FileHandler werden geschlossen."""
        proginit.logger.debug("enter PipeLogwriter.stop()")
        self._exit.set()

        self._lckfh.acquire()
        # Letzten Log in Pipe schreiben zum befreien
        try:
            os.write(self.fdw, b"\n")
        except:
            pass
        finally:
            self._lckfh.release()

        proginit.logger.debug("leave PipeLogwriter.stop()")
