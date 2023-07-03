# -*- coding: utf-8 -*-
"""Stellt Funktionen bereit um das Prozessabbild zu ueberwachen.

Bei ausreichend Rechten koennen Ausgaenge auch gesetzt werden um einen
IO-Check bei Inbetriebname durchzufuehren.

"""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"

import pickle
from xmlrpc.client import Binary

import revpimodio2

from . import proginit


class ProcimgServer:
    """Serverkomponente fuer zusaetzliche XML-RPC Funktionen.

    Diese Klasse registriert zusaetzliche Funktionen an einem besthenden
    XML-RPC-Server. Der Funktionsumfang wird erweitert um zyklisch das
    Prozessabbild zu empfangen und bei ausreichend Rechten Ausgaenge zu
    setzen.

    """

    def __init__(self, xmlserver, replace_ios=None):
        """Instantiiert RevPiCheckServer()-Klasse.
        @param xmlserver XML-RPC Server
        @param replace_ios Replace IOs of RevPiModIO"""
        # Logger übernehmen
        proginit.logger.debug("enter ProcimgServer.__init__()")

        self.rpi = None
        self.replace_ios = replace_ios

        # XML-Server übernehmen
        self.xmlsrv = xmlserver
        self.xmlreadfuncs = {
            "ps_devices": self.devices,
            "ps_inps": lambda: self.ios("inp"),
            "ps_outs": lambda: self.ios("out"),
            "ps_values": self.values,
        }
        self.xmlwritefuncs = {
            "ps_setvalue": self.setvalue,
        }

        # RevPiModIO laden oder mit Exception aussteigen
        self.loadrevpimodio()

        proginit.logger.debug("leave ProcimgServer.__init__()")

    def __del__(self):
        """Clean up RevPiModIO."""
        if self.rpi is not None:
            self.rpi.cleanup()

    def devices(self):
        """Generiert Deviceliste mit Position und Namen.
        @return list() mit Tuple (pos, name)"""
        return [
            (dev.position, dev.name) for dev in self.rpi.device
        ]

    def ios(self, iotype):
        """Generiert ein dict() der Devices und IOs.
        @param iotype IO Typ inp/out
        @return pickled dict()"""
        dict_ios = {}
        for dev in self.rpi.device:
            dict_ios[dev.position] = []

            # IO Typen auswerten
            if iotype == "inp":
                lst_io = dev.get_inputs()
            elif iotype == "out":
                lst_io = dev.get_outputs()
            else:
                lst_io = []

            for io in lst_io:
                dict_ios[dev.position].append([
                    io.name,
                    1 if io._bitlength == 1 else int(io._bitlength / 8),
                    io._slc_address.start + dev.offset,
                    io.bmk,
                    io._bitaddress,
                    io._byteorder,
                    io._signed,
                    getattr(io, "wordorder", "ignored"),
                ])
        return Binary(pickle.dumps(dict_ios))

    def loadrevpimodio(self):
        """Instantiiert das RevPiModIO Modul.
        @return None or Exception"""
        # RevPiModIO-Modul Instantiieren
        if self.rpi is not None:
            self.rpi.cleanup()

        proginit.logger.debug("create revpimodio2 object for ProcimgServer")
        try:
            self.rpi = revpimodio2.RevPiModIO(
                configrsc=proginit.pargs.configrsc,
                procimg=proginit.pargs.procimg,
                replace_io_file=self.replace_ios,
                shared_procimg=True,
            )
            self.rpi.debug = -1

            if self.replace_ios:
                proginit.logger.info("loaded replace_ios to ProcimgServer")

        except Exception as e:
            try:
                self.rpi = revpimodio2.RevPiModIO(
                    configrsc=proginit.pargs.configrsc,
                    procimg=proginit.pargs.procimg,
                    shared_procimg=True,
                )
                self.rpi.debug = -1
                proginit.logger.warning(
                    "replace_ios_file not loadable for ProcimgServer - using "
                    "defaults now | {0}".format(e)
                )
            except Exception as e:
                self.rpi = None
                proginit.logger.error(
                    "piCtory configuration not loadable for ProcimgServer | "
                    "{0}".format(e)
                )
                return e

        proginit.logger.debug("created revpimodio2 object")

    def setvalue(self, device, io, value):
        """Setzt einen Wert auf dem RevPi.

        @param device Device Position oder Name
        @param io IO Name fuer neuen Wert
        @param value Neuer Wert
        @return list() [device, io, status, msg]

        """
        # Binary() in bytes() umwandeln
        if type(value) == Binary:
            value = value.data

        try:
            # Neuen Wert übernehmen
            # fixme: Warum wird hier alles in Bytes umgewandelt?
            if type(value) == bytes or type(value) == bool:
                self.rpi.io[io].set_value(value)
            else:
                self.rpi.io[io].set_value(
                    value.to_bytes(
                        self.rpi.io[io].length,
                        byteorder=self.rpi.io[io]._byteorder,
                        signed=self.rpi.io[io]._signed,
                    )
                )
            self.rpi.writeprocimg()
        except Exception as e:
            return [device, io, False, str(e)]

        return [device, io, True, ""]

    def values(self):
        """Liefert Prozessabbild an Client.
        @return Binary() bytes or None"""
        if self.rpi.readprocimg():
            bytebuff = bytearray()
            for dev in self.rpi.device:
                bytebuff += bytes(dev)
            return Binary(bytes(bytebuff))
        else:
            return None

    def start(self):
        """Registriert XML Funktionen.
        @return True, wenn erfolgreich"""
        proginit.logger.debug("enter ProcimgServer.start()")

        ec = False
        if self.rpi is not None:

            # Registriere Funktionen
            for xmlfunc in self.xmlreadfuncs:
                self.xmlsrv.register_function(
                    1, self.xmlreadfuncs[xmlfunc], xmlfunc
                )
            for xmlfunc in self.xmlwritefuncs:
                self.xmlsrv.register_function(
                    3, self.xmlwritefuncs[xmlfunc], xmlfunc
                )
            ec = True

        proginit.logger.debug("leave ProcimgServer.start()")
        return ec

    def stop(self):
        """Entfernt XML-Funktionen."""
        proginit.logger.debug("enter ProcimgServer.stop()")

        # Entferne Funktionen
        for xmlfunc in self.xmlreadfuncs:
            if xmlfunc in self.xmlsrv.funcs:
                del self.xmlsrv.funcs[xmlfunc]
        for xmlfunc in self.xmlwritefuncs:
            if xmlfunc in self.xmlsrv.funcs:
                del self.xmlsrv.funcs[xmlfunc]

        proginit.logger.debug("leave ProcimgServer.stop()")
