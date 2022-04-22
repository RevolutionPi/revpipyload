#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Revolution Pi Python PLC Loader.

Webpage: https://revpimodio.org/revpipyplc/

Stellt das RevPiPyLoad Programm bereit. Dieses Programm lauft als Daemon auf
dem Revolution Pi. Es stellt Funktionen bereit, die es ermoeglichen ein Python
Programm zu starten und fuehrt dessen Ausgaben in eine Logdatei. Die Logdaten
koennen am Pi selber oder ueber eine XML-RPC Schnittstelle ausgelesen werden.

Dieser Daemon prueft ob das Python Programm noch lauft und kann es im Fall
eines Absturzes neu starten.

Ueber diesen Daemon kann die gesamte piCtory Konfiguration exportiert oder
importiert, ein Dump vom Prozessabbild gezogen und das eigene Python
Programm hochgeladen werden.

Es kann von dem Python Programm auch eine Archivdatei herunterladen werden,
welche optional auch die piCtory Konfiguraiton beinhaltet. Damit kann man sehr
schnell auf einem Revolution Pi das Programm inkl. piCtory Konfiguration
austauschen.

Die Zugriffsmoeglichkeiten koennen ueber einen Konfigurationsparameter
begrenzt werden!

"""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2020 Sven Sager"
__license__ = "GPLv3"
__version__ = "0.9.7"

import gzip
import os
import signal
import tarfile
import zipfile
from configparser import ConfigParser
from hashlib import md5
from json import loads as jloads
from shutil import rmtree
from tempfile import mkstemp
from threading import Event
from time import asctime, time
from xmlrpc.client import Binary

import logsystem
import picontrolserver
import plcsystem
import proginit
from helper import get_revpiled_address, pi_control_reset, refullmatch
from shared.ipaclmanager import IpAclManager
from watchdogs import ResetDriverWatchdog
from xrpcserver import SaveXMLRPCServer

min_revpimodio = "2.5.0"


class RevPiPyLoad:
    """Hauptklasse, die alle Funktionen zur Verfuegung stellt.

    Hier wird die gesamte Konfiguraiton eingelesen und der ggf. aktivierte
    XML-RPC-Server gestartet.

    """

    def __init__(self):
        """Instantiiert RevPiPyLoad-Klasse."""
        proginit.logger.debug("enter RevPiPyLoad.__init__()")

        # Klassenattribute
        self._exit = True
        self.evt_loadconfig = Event()
        self.globalconfig = ConfigParser()
        proginit.conf = self.globalconfig
        self.logr = logsystem.LogReader()
        self.xsrv = None
        self.xml_ps = None

        # Dateimerker
        self.pictorymtime = 0
        self.replaceiosmtime = 0
        self.replaceiofail = False
        self.revpi_led_address = -1

        # Berechtigungsmanger
        if proginit.pargs.developermode:
            self.plcslaveacl = IpAclManager(minlevel=0, maxlevel=9)
            self.xmlrpcacl = IpAclManager(minlevel=0, maxlevel=9)
        else:
            self.plcslaveacl = IpAclManager(minlevel=0, maxlevel=1)
            self.xmlrpcacl = IpAclManager(minlevel=0, maxlevel=4)

        # Threads/Prozesse
        self.th_plcmqtt = None
        self.th_plcslave = None
        self.plc = None

        # Konfiguration laden
        self._loadconfig()

        # Signal events
        signal.signal(signal.SIGINT, self._sigexit)
        signal.signal(signal.SIGTERM, self._sigexit)
        signal.signal(signal.SIGHUP, self._sigloadconfig)
        signal.signal(signal.SIGUSR1, self._signewlogfile)

        proginit.logger.debug("leave RevPiPyLoad.__init__()")

    def _check_mustrestart_mqtt(self):
        """Prueft ob sich kritische Werte veraendert haben.
        @return True, wenn Subsystemneustart noetig ist"""
        if self.th_plcmqtt is None:
            return True
        elif "MQTT" not in self.globalconfig:
            return True
        else:
            return (
                self.replace_ios_config !=
                self.globalconfig["DEFAULT"].get("replace_ios", "") or
                self.mqtt !=
                self.globalconfig["MQTT"].getboolean("mqtt", False) or
                self.mqttbasetopic !=
                self.globalconfig["MQTT"].get("basetopic", "") or
                self.mqttsendinterval !=
                self.globalconfig["MQTT"].getint("sendinterval", 30) or
                self.mqttbroker_address !=
                self.globalconfig["MQTT"].get("broker_address", "localhost") or
                self.mqttport !=
                self.globalconfig["MQTT"].getint("port", 1883) or
                self.mqtttls_set !=
                self.globalconfig["MQTT"].getboolean("tls_set", False) or
                self.mqttusername !=
                self.globalconfig["MQTT"].get("username", "") or
                self.mqttpassword !=
                self.globalconfig["MQTT"].get("password", "") or
                self.mqttclient_id !=
                self.globalconfig["MQTT"].get("client_id", "") or
                self.mqttsend_on_event !=
                self.globalconfig["MQTT"].getboolean("send_on_event", False) or
                self.mqttwrite_outputs !=
                self.globalconfig["MQTT"].getboolean("write_outputs", False)
            )

    def _check_mustrestart_plcslave(self):
        """Prueft ob sich kritische Werte veraendert haben.
        @return True, wenn Subsystemneustart noetig ist"""
        if self.th_plcslave is None:
            return True
        elif "PLCSLAVE" not in self.globalconfig:
            return True
        else:
            ip = self.globalconfig["PLCSLAVE"].get("bindip", "127.0.0.1")
            if ip == "*":
                ip = ""
            elif ip == "":
                ip = "127.0.0.1"
            port = self.globalconfig["PLCSLAVE"].getint("port", 55234)

            return (
                self.plcslave !=
                self.globalconfig["PLCSLAVE"].getboolean("plcslave", False) or
                self.plcslavebindip != ip or
                self.plcslaveport != port
            )

    def _check_mustrestart_plcprogram(self):
        """Prueft ob sich kritische Werte veraendert haben.
        @return True, wenn Subsystemneustart noetig ist"""
        if self.plc is None:
            return True
        else:
            return (
                self.plcworkdir !=
                self.globalconfig["DEFAULT"].get("plcworkdir", ".") or
                self.plcprogram !=
                self.globalconfig["DEFAULT"].get("plcprogram", "none.py") or
                self.plcarguments !=
                self.globalconfig["DEFAULT"].get("plcarguments", "") or
                self.plcuid !=
                self.globalconfig["DEFAULT"].getint("plcuid", 65534) or
                self.plcgid !=
                self.globalconfig["DEFAULT"].getint("plcgid", 65534) or
                self.pythonversion !=
                self.globalconfig["DEFAULT"].getint("pythonversion", 3) or
                self.rtlevel !=
                self.globalconfig["DEFAULT"].getint("rtlevel", 0) or (
                    not self.plc.is_alive() and not self.autostart and
                    self.globalconfig["DEFAULT"].getboolean("autostart", False)
                )
            )

    def _loadconfig(self):
        """Load configuration file and setup modul."""
        proginit.logger.debug("enter RevPiPyLoad._loadconfig()")

        # Subsysteme herunterfahren
        self.stop_xmlrpcserver()

        # Konfigurationsdatei laden
        proginit.logger.info(
            "loading config file: {0}".format(proginit.globalconffile)
        )
        self.globalconfig.read(proginit.globalconffile)
        proginit.conf = self.globalconfig

        # Merker für Subsystem-Neustart nach laden, vor setzen
        restart_plcmqtt = self._check_mustrestart_mqtt()
        restart_plcslave = self._check_mustrestart_plcslave()
        restart_plcprogram = self._check_mustrestart_plcprogram()

        # Konfiguration verarbeiten [DEFAULT]
        self.autoreload = self.globalconfig["DEFAULT"].getboolean(
            "autoreload", True)
        self.autoreloaddelay = self.globalconfig["DEFAULT"].getint(
            "autoreloaddelay", 5)
        self.autostart = self.globalconfig["DEFAULT"].getboolean(
            "autostart", False)
        self.plcworkdir = self.globalconfig["DEFAULT"].get(
            "plcworkdir", ".")
        self.plcprogram = self.globalconfig["DEFAULT"].get(
            "plcprogram", "none.py")
        self.plcprogram_stop_timeout = self.globalconfig.getint(
            "DEFAULT", "plcprogram_stop_timeout", fallback=5)
        self.plcprogram_watchdog = self.globalconfig["DEFAULT"].getint(
            "plcprogram_watchdog", 0)
        self.plcarguments = self.globalconfig["DEFAULT"].get(
            "plcarguments", "")
        self.plcworkdir_set_uid = self.globalconfig["DEFAULT"].getboolean(
            "plcworkdir_set_uid", False)
        self.plcuid = self.globalconfig["DEFAULT"].getint(
            "plcuid", 65534)
        self.plcgid = self.globalconfig["DEFAULT"].getint(
            "plcgid", 65534)
        self.pythonversion = self.globalconfig["DEFAULT"].getint(
            "pythonversion", 3)
        self.replace_ios_config = self.globalconfig["DEFAULT"].get(
            "replace_ios", "")
        self.rtlevel = self.globalconfig["DEFAULT"].getint(
            "rtlevel", 0)
        self.reset_driver_action = self.globalconfig["DEFAULT"].getint(
            "reset_driver_action", 2)
        self.zeroonerror = self.globalconfig["DEFAULT"].getboolean(
            "zeroonerror", True)
        self.zeroonexit = self.globalconfig["DEFAULT"].getboolean(
            "zeroonexit", True)

        # Dateiveränderungen prüfen
        file_changed = False
        # Beide Funktionen müssen einmal aufgerufen werden
        if self.check_pictory_changed():
            file_changed = True
        if self.check_replace_ios_changed():
            file_changed = True
        if file_changed:
            restart_plcmqtt = True
            restart_plcslave = True
            restart_plcprogram = True

        # Konfiguration verarbeiten [MQTT]
        self.mqtt = 0
        if "MQTT" in self.globalconfig:
            self.mqtt = \
                self.globalconfig["MQTT"].getboolean("mqtt", False)
            self.mqttbasetopic = \
                self.globalconfig["MQTT"].get("basetopic", "")
            self.mqttsendinterval = \
                self.globalconfig["MQTT"].getint("sendinterval", 30)
            self.mqttbroker_address = \
                self.globalconfig["MQTT"].get("broker_address", "localhost")
            self.mqttport = \
                self.globalconfig["MQTT"].getint("port", 1883)
            self.mqtttls_set = \
                self.globalconfig["MQTT"].getboolean("tls_set", False)
            self.mqttusername = \
                self.globalconfig["MQTT"].get("username", "")
            self.mqttpassword = \
                self.globalconfig["MQTT"].get("password", "")
            self.mqttclient_id = \
                self.globalconfig["MQTT"].get("client_id", "")
            self.mqttsend_on_event = \
                self.globalconfig["MQTT"].getboolean("send_on_event", False)
            self.mqttwrite_outputs = \
                self.globalconfig["MQTT"].getboolean("write_outputs", False)

        # Konfiguration verarbeiten [PLCSLAVE]
        self.plcslave = False
        if "PLCSLAVE" in self.globalconfig:
            self.plcslave = \
                self.globalconfig["PLCSLAVE"].getboolean("plcslave", False)

            # Berechtigungen laden
            if not self.plcslaveacl.loadaclfile(
                    self.globalconfig["PLCSLAVE"].get("aclfile", "")):
                proginit.logger.warning(
                    "can not load plcslave acl - wrong format"
                )
            if not self.plcslave:
                self.stop_plcslave()

            # Bind IP lesen und anpassen
            self.plcslavebindip = \
                self.globalconfig["PLCSLAVE"].get("bindip", "127.0.0.1")
            if self.plcslavebindip == "*":
                self.plcslavebindip = ""
            elif self.plcslavebindip == "":
                self.plcslavebindip = "127.0.0.1"

            self.plcslaveport = \
                self.globalconfig["PLCSLAVE"].getint("port", 55234)
            self.plcwatchdog = self.globalconfig.getboolean(
                "PLCSLAVE", "watchdog", fallback=True
            )

        # Konfiguration verarbeiten [XMLRPC]
        self.xmlrpc = False
        if "XMLRPC" in self.globalconfig:
            self.xmlrpc = \
                self.globalconfig["XMLRPC"].getboolean("xmlrpc", False)

            if not self.xmlrpcacl.loadaclfile(
                    self.globalconfig["XMLRPC"].get("aclfile", "")):
                proginit.logger.warning(
                    "can not load xmlrpc acl - wrong format"
                )

            # Bind IP lesen und anpassen
            self.xmlrpcbindip = \
                self.globalconfig["XMLRPC"].get("bindip", "127.0.0.1")
            if self.xmlrpcbindip == "*":
                self.xmlrpcbindip = ""
            elif self.xmlrpcbindip == "":
                self.xmlrpcbindip = "127.0.0.1"

            self.xmlrpcport = \
                self.globalconfig["XMLRPC"].getint("port", 55123)

        # Workdirectory wechseln
        if not os.access(self.plcworkdir, os.R_OK | os.W_OK | os.X_OK):
            raise ValueError(
                "can not access plcworkdir '{0}'".format(self.plcworkdir)
            )
        os.chdir(self.plcworkdir)

        # Workdirectory owner setzen
        try:
            if self.plcworkdir_set_uid:
                os.chown(self.plcworkdir, self.plcuid, self.plcgid)
            else:
                os.chown(self.plcworkdir, 0, 0)
        except Exception:
            proginit.logger.warning(
                "could not set user id on working directory"
            )

        # MQTT konfigurieren
        if restart_plcmqtt:
            self.stop_plcmqtt()
            self.th_plcmqtt = self._plcmqtt()

            if not self._exit and self.th_plcmqtt is not None:
                proginit.logger.info("restart mqtt publisher after reload")
                self.th_plcmqtt.start()

        # PLC Programm konfigurieren
        if restart_plcprogram:
            self.stop_plcprogram()
            self.plc = self._plcthread()

            if not self._exit and self.plc is not None and self.autostart:
                proginit.logger.info("restart plc program after reload")
                self.plc.start()

        else:
            proginit.logger.info(
                "configure plc program parameters after reload"
            )
            self.plc.autoreload = self.autoreload
            self.plc.autoreloaddelay = self.autoreloaddelay
            self.plc.softdog.timeout = self.plcprogram_watchdog
            self.plc.stop_timeout = self.plcprogram_stop_timeout
            self.plc.zeroonerror = self.zeroonerror
            self.plc.zeroonexit = self.zeroonexit

        # PLC-Slave konfigurieren
        if restart_plcslave:
            self.stop_plcslave()
            self.th_plcslave = self._plcslave()

            if not self._exit and self.th_plcslave is not None:
                proginit.logger.info("restart plc slave after reload")
                self.th_plcslave.start()

        # PLC-Slave ACL und Einstellungen prüfen
        if self.th_plcslave is not None:
            self.th_plcslave.check_connectedacl()
            self.th_plcslave.watchdog = self.plcwatchdog

        # XMLRPC-Server Instantiieren und konfigurieren
        if not self.xmlrpc:
            # Nach Reload und Deaktivierung alte XML Instanz löschen
            self.xsrv = None
        else:
            proginit.logger.debug("create xmlrpc server")
            self.xsrv = SaveXMLRPCServer(
                (self.xmlrpcbindip, self.xmlrpcport),
                logRequests=False,
                allow_none=True,
                ipacl=self.xmlrpcacl
            )
            self.xsrv.register_introspection_functions()
            self.xsrv.register_multicall_functions()

            # Allgemeine Funktionen
            self.xsrv.register_function(0, lambda: __version__, "version")
            self.xsrv.register_function(0, lambda acl: acl, "xmlmodus")

            # XML Modus 1 Nur Logs lesen und PLC Programm neu starten
            self.xsrv.register_function(
                0, self.logr.load_applog, "load_applog")
            self.xsrv.register_function(
                0, self.logr.load_plclog, "load_plclog")
            self.xsrv.register_function(
                0, self.xml_plcexitcode, "plcexitcode")
            self.xsrv.register_function(
                0, self.xml_plcrunning, "plcrunning")
            self.xsrv.register_function(
                0, self.xml_plcstart, "plcstart")
            self.xsrv.register_function(
                0, self.xml_plcstop, "plcstop")
            self.xsrv.register_function(
                0, self.xml_reload, "reload")
            self.xsrv.register_function(
                0, self.xml_mqttrunning, "mqttrunning")
            self.xsrv.register_function(
                0, self.xml_plcslaverunning, "plcslaverunning")

            # Erweiterte Funktionen anmelden
            try:
                import procimgserver
            except Exception:
                self.xml_ps = None
                proginit.logger.warning(
                    "can not load revpimodio2 module. maybe its not installed "
                    "or an old version (required at least {0}). if you "
                    "like to use revpinetio network feature, update/install "
                    "revpimodio2: 'apt-get install python3-revpimodio2'"
                    "".format(min_revpimodio)
                )
            else:
                self.xml_ps = procimgserver.ProcimgServer(
                    self.xsrv,
                    None if not self.replace_ios_config
                    else self.replace_ios_config,
                )

                self.xsrv.register_function(1, self.xml_psstart, "psstart")
                self.xsrv.register_function(1, self.xml_psstop, "psstop")

            # XML Modus 2 Einstellungen lesen und Programm herunterladen
            self.xsrv.register_function(
                2, self.xml_getconfig, "get_config")
            self.xsrv.register_function(
                2, self.xml_getfilelist, "get_filelist")
            self.xsrv.register_function(
                2, self.xml_getpictoryrsc, "get_pictoryrsc")
            self.xsrv.register_function(
                2, self.xml_getprocimg, "get_procimg")
            self.xsrv.register_function(
                2, self.xml_plcdownload, "plcdownload")

            # XML Modus 3 Programm und Konfiguration hochladen
            self.xsrv.register_function(
                3, self.xml_plcupload, "plcupload")
            self.xsrv.register_function(
                3, self.xml_plcuploadclean, "plcuploadclean")
            self.xsrv.register_function(
                3, pi_control_reset, "resetpicontrol")
            self.xsrv.register_function(
                3, self.xml_mqttstart, "mqttstart")
            self.xsrv.register_function(
                3, self.xml_mqttstop, "mqttstop")
            self.xsrv.register_function(
                3, self.xml_plcslavestart, "plcslavestart")
            self.xsrv.register_function(
                3, self.xml_plcslavestop, "plcslavestop")
            self.xsrv.register_function(
                3, self.xml_plcdelete_file, "plcdeletefile")
            self.xsrv.register_function(
                3, self.xml_plcdownload_file, "plcdownload_file")

            # XML Modus 4 Einstellungen ändern
            self.xsrv.register_function(
                4, self.xml_setconfig, "set_config")
            self.xsrv.register_function(
                4, self.xml_setpictoryrsc, "set_pictoryrsc")

            proginit.logger.debug("created xmlrpc server")

            # Neustart bei reload
            if not self._exit:
                proginit.logger.info("bind xmlrpc-server")
                self.xsrv.server_bind()
                self.xsrv.server_activate()

        # Konfiguration abschließen
        self.evt_loadconfig.clear()

        proginit.logger.debug("leave RevPiPyLoad._loadconfig()")

    def _plcmqtt(self):
        """Konfiguriert den MQTT-Thread fuer die Ausfuehrung.
        @return MQTT-Thread Object or None"""
        proginit.logger.debug("enter RevPiPyLoad._plcmqtt()")

        th_plc = None
        if self.mqtt:
            try:
                from mqttserver import MqttServer
            except Exception:
                proginit.logger.warning(
                    "can not load revpimodio2 module. maybe its not installed "
                    "or an old version (required at least {0}). if you "
                    "like to use the mqtt feature, update/install "
                    "revpimodio2: 'apt-get install python3-revpimodio2'"
                    "".format(min_revpimodio)
                )
            else:
                try:
                    th_plc = MqttServer(
                        self.mqttbasetopic,
                        self.mqttsendinterval,
                        self.mqttbroker_address,
                        self.mqttport,
                        self.mqtttls_set,
                        self.mqttusername,
                        self.mqttpassword,
                        self.mqttclient_id,
                        self.mqttsend_on_event,
                        self.mqttwrite_outputs,
                        None if not self.replace_ios_config
                        else self.replace_ios_config,
                    )
                except Exception as e:
                    proginit.logger.error(e)

        proginit.logger.debug("leave RevPiPyLoad._plcmqtt()")
        return th_plc

    def _plcthread(self):
        """Konfiguriert den PLC-Thread fuer die Ausfuehrung.
        @return PLC-Thread Object or None"""
        proginit.logger.debug("enter RevPiPyLoad._plcthread()")

        # Prüfen ob Programm existiert
        if not os.path.exists(os.path.join(self.plcworkdir, self.plcprogram)):
            proginit.logger.error("plc file does not exists {0}".format(
                os.path.join(self.plcworkdir, self.plcprogram)
            ))
            return None

        # Check software watchdog
        if self.revpi_led_address < 0 < self.plcprogram_watchdog:
            proginit.logger.error(
                "can not start plc program, because watchdog is activated "
                "but no address was found in piCtory configuration"
            )
            return None

        proginit.logger.debug("create PLC program watcher")
        th_plc = plcsystem.RevPiPlc(
            os.path.join(self.plcworkdir, self.plcprogram),
            self.plcarguments,
            self.pythonversion
        )
        th_plc.autoreload = self.autoreload
        th_plc.autoreloaddelay = self.autoreloaddelay
        th_plc.gid = self.plcgid
        th_plc.uid = self.plcuid
        th_plc.rtlevel = self.rtlevel
        th_plc.softdog.address = \
            0 if self.revpi_led_address < 0 else self.revpi_led_address
        th_plc.softdog.timeout = self.plcprogram_watchdog
        th_plc.stop_timeout = self.plcprogram_stop_timeout
        th_plc.zeroonerror = self.zeroonerror
        th_plc.zeroonexit = self.zeroonexit

        proginit.logger.debug("leave RevPiPyLoad._plcthread()")
        return th_plc

    def _plcslave(self):
        """Erstellt den PlcSlave-Server Thread.
        @return PLC-Server-Thread Object or None"""
        proginit.logger.debug("enter RevPiPyLoad._plcslave()")
        th_plc = None

        if self.plcslave:
            th_plc = picontrolserver.RevPiSlave(
                self.plcslaveacl, self.plcslaveport, self.plcslavebindip,
                self.plcwatchdog
            )

        proginit.logger.debug("leave RevPiPyLoad._plcslave()")
        return th_plc

    def _sigexit(self, signum, frame):
        """Signal handler to clean and exit program."""
        proginit.logger.debug("enter RevPiPyLoad._sigexit()")
        self.stop()
        proginit.logger.debug("leave RevPiPyLoad._sigexit()")

    def _sigloadconfig(self, signum, frame):
        """Signal handler to load configuration."""
        proginit.logger.debug("enter RevPiPyLoad._sigloadconfig()")
        self.evt_loadconfig.set()
        proginit.logger.debug("leave RevPiPyLoad._sigloadconfig()")

    def _signewlogfile(self, signum, frame):
        """Signal handler to start new logfile."""
        proginit.logger.debug("enter RevPiPyLoad._signewlogfile()")

        # Logger neu konfigurieren
        proginit.configure()
        proginit.logger.warning("start new logfile: {0}".format(asctime()))

        # stdout für revpipyplc
        if self.plc is not None:
            self.plc.newlogfile()

        # Logreader schließen
        self.logr.closeall()

        proginit.logger.debug("leave RevPiPyLoad._signewlogfile()")

    def check_pictory_changed(self):
        """Prueft ob sich die piCtory Datei veraendert hat.
        @return True, wenn veraendert wurde"""
        try:
            mtime = os.path.getmtime(proginit.pargs.configrsc)
        except FileNotFoundError:
            self.pictorymtime = 0
            return False

        if self.pictorymtime == mtime:
            return False
        self.pictorymtime = mtime

        # TODO: Nur "Devices" list vergleich da HASH immer neu wegen timestamp

        try:
            with open(proginit.pargs.configrsc, "rb") as fh:
                rsc_buff = fh.read()
        except Exception:
            return False

        # Check change of RevPiLED address
        self.revpi_led_address = get_revpiled_address(rsc_buff)
        if self.plc is not None and self.plc.is_alive():
            if self.revpi_led_address >= 0:
                self.plc.softdog.address = self.revpi_led_address
            elif self.plcprogram_watchdog > 0:
                # Stop plc program, if watchdog is needed but not found
                proginit.logger.error(
                    "stop plc program, because watchdog is activated but "
                    "no address was found in piCtory configuration"
                )
                self.plc.stop()
                self.plc.join()

        file_hash = md5(rsc_buff).digest()
        if picontrolserver.HASH_PICT == file_hash:
            return False
        picontrolserver.HASH_PICT = file_hash

        return True

    def check_replace_ios_changed(self):
        """Prueft ob sich die replace_ios.conf Datei veraendert hat (oder del).
        @return True, wenn veraendert wurde"""

        # Zugriffsrechte prüfen (pre-check für unten)
        if self.replace_ios_config \
                and not os.access(self.replace_ios_config, os.R_OK):

            if not self.replaceiofail:
                proginit.logger.error(
                    "can not access the replace_ios file '{0}' "
                    "using defaults".format(self.replace_ios_config)
                )
            self.replaceiofail = True
        else:
            self.replaceiofail = False

        if not self.replace_ios_config or self.replaceiofail:
            # Dateipfad leer, prüfen ob es vorher einen gab
            if self.replaceiosmtime > 0 \
                    or picontrolserver.HASH_RPIO != picontrolserver.HASH_NULL:
                self.replaceiosmtime = 0
                picontrolserver.HASH_RPIO = picontrolserver.HASH_NULL
                return True

        else:
            mtime = os.path.getmtime(self.replace_ios_config)
            if self.replaceiosmtime == mtime:
                return False
            self.replaceiosmtime = mtime

            # TODO: Instanz von ConfigParser vergleichen

            with open(self.replace_ios_config, "rb") as fh:
                file_hash = md5(fh.read()).digest()
            if picontrolserver.HASH_RPIO == file_hash:
                return False
            picontrolserver.HASH_RPIO = file_hash

            return True

        return False

    def packapp(self, mode="tar", pictory=False):
        """Erzeugt aus dem PLC-Programm ein TAR/Zip-File.

        @param mode Packart 'tar' oder 'zip'
        @param pictory piCtory Konfiguration mit einpacken
        @return Dateinamen des Archivs

        """
        proginit.logger.debug("enter RevPiPyLoad.packapp()")

        tup_file = mkstemp(suffix="_packed", prefix="plc_")
        filename = tup_file[1]

        if mode == "zip":
            fh_pack = zipfile.ZipFile(filename, mode="w")
            wd = os.walk("./")
            try:
                for tup_dir in wd:
                    if tup_dir[0].find("__pycache__") != -1:
                        continue
                    for file in tup_dir[2]:
                        arcname = os.path.join(
                            os.path.basename(self.plcworkdir),
                            tup_dir[0][2:],
                            file
                        )
                        fh_pack.write(
                            os.path.join(tup_dir[0], file), arcname=arcname
                        )
                if pictory and os.access(proginit.pargs.configrsc, os.R_OK):
                    fh_pack.write(
                        proginit.pargs.configrsc, arcname="config.rsc"
                    )
            except Exception:
                filename = ""
            finally:
                fh_pack.close()

        else:
            fh_pack = tarfile.open(
                name=filename, mode="w:gz", dereference=True)
            try:
                fh_pack.add(".", arcname=os.path.basename(self.plcworkdir))
                if pictory and os.access(proginit.pargs.configrsc, os.R_OK):
                    fh_pack.add(proginit.pargs.configrsc, arcname="config.rsc")
            except Exception:
                filename = ""
            finally:
                fh_pack.close()

        proginit.logger.debug("leave RevPiPyLoad.packapp()")
        return filename

    def start(self):
        """Start revpipyload."""
        proginit.logger.debug("enter RevPiPyLoad.start()")

        proginit.logger.info("starting revpipyload")
        self._exit = False

        if self.xmlrpc and self.xsrv is not None:
            proginit.logger.info("bind xmlrpc-server")
            self.xsrv.server_bind()
            self.xsrv.server_activate()

        # MQTT Uebertragung starten
        if self.th_plcmqtt is not None:
            self.th_plcmqtt.start()

        # Slaveausfuehrung übergeben
        if self.th_plcslave is not None:
            self.th_plcslave.start()

        # PLC Programm automatisch starten
        if self.autostart and self.plc is not None:
            self.plc.start()

        # Watchdog to detect the reset_driver event
        pictory_reset_driver = ResetDriverWatchdog()
        pictory_reset_driver.register_call(self.xml_psstop)

        # mainloop
        while not self._exit:
            # Neue Konfiguration laden
            if self.evt_loadconfig.is_set():
                proginit.logger.info("got reqeust to reload config")
                self._loadconfig()

            file_changed = False
            reset_driver_detected = pictory_reset_driver.triggered

            # Dateiveränderungen prüfen mit beiden Funktionen!
            if (reset_driver_detected or
                    pictory_reset_driver.not_implemented) and \
                    self.check_pictory_changed():
                file_changed = True

                # Alle Verbindungen von ProcImgServer trennen
                if self.plcslave and self.th_plcslave is not None:
                    self.th_plcslave.disconnect_all()

                proginit.logger.warning("piCtory configuration was changed")

            if self.check_replace_ios_changed():
                if not file_changed:
                    # Verbindungen von ProcImgServer trennen mit replace_ios
                    if self.plcslave and self.th_plcslave is not None:
                        self.th_plcslave.disconnect_replace_ios()

                file_changed = True
                proginit.logger.warning("replace ios file was changed")

            if file_changed:
                # Auf Dateiveränderung reagieren

                # MQTT Publisher neu laden
                if self.mqtt and self.th_plcmqtt is not None:
                    self.th_plcmqtt.reload_revpimodio()

                # XML Prozessabbildserver neu laden
                if self.xml_ps is not None:
                    self.xml_psstop()
                    self.xml_ps.loadrevpimodio()
                    # Kein psstart um Reload im Client zu erzeugen

            # Restart plc program after piCtory change
            if not pictory_reset_driver.not_implemented and \
                    self.plc is not None and self.plc.is_alive() and (
                    self.reset_driver_action == 2 and reset_driver_detected or
                    self.reset_driver_action == 1 and file_changed):
                # Plc program is running and we have to restart it
                proginit.logger.warning(
                    "restart plc program after 'reset driver' was requested"
                )
                self.stop_plcprogram()
                self.plc = self._plcthread()
                self.plc.start()

            # MQTT Publisher Thread prüfen
            if self.mqtt and self.th_plcmqtt is not None \
                    and not self.th_plcmqtt.is_alive():
                proginit.logger.warning(
                    "restart mqtt publisher after thread was not running"
                )
                self.th_plcmqtt = self._plcmqtt()
                if self.th_plcmqtt is not None:
                    self.th_plcmqtt.start()

            # PLC Server Thread prüfen
            if self.plcslave and self.th_plcslave is not None \
                    and not self.th_plcslave.is_alive():
                if not file_changed:
                    proginit.logger.warning(
                        "restart plc slave after thread was not running"
                    )
                self.th_plcslave = self._plcslave()
                if self.th_plcslave is not None:
                    self.th_plcslave.start()

            if self.xmlrpc and self.xsrv is not None:
                # Work multiple xml calls in same thread until timeout
                xml_start = time()
                while time() - xml_start < 1.0:
                    self.xsrv.handle_request()
            else:
                self.evt_loadconfig.wait(1.0)

        proginit.logger.info("stopping revpipyload")

        # Alle Sub-Systeme beenden
        self.stop_plcprogram()
        self.stop_plcmqtt()
        self.stop_plcslave()
        self.stop_xmlrpcserver()

        # Logreader schließen
        self.logr.closeall()

        proginit.logger.debug("leave RevPiPyLoad.start()")

    def stop(self):
        """Stop revpipyload."""
        proginit.logger.debug("enter RevPiPyLoad.stop()")
        self._exit = True
        proginit.logger.debug("leave RevPiPyLoad.stop()")

    def stop_plcmqtt(self):
        """Beendet MQTT Sender."""
        proginit.logger.debug("enter RevPiPyLoad.stop_plcmqtt()")

        if self.th_plcmqtt is not None and self.th_plcmqtt.is_alive():
            proginit.logger.info("stopping mqtt thread")
            self.th_plcmqtt.stop()
            self.th_plcmqtt.join()
            proginit.logger.debug("mqtt thread successfully closed")

        proginit.logger.debug("leave RevPiPyLoad.stop_plcmqtt()")

    def stop_plcprogram(self):
        """Beendet PLC Programm."""
        proginit.logger.debug("enter RevPiPyLoad.stop_plcprogram()")

        if self.plc is not None and self.plc.is_alive():
            proginit.logger.info("stopping revpiplc thread")
            self.plc.stop()
            self.plc.join(5.0)
            if self.plc.is_alive():
                proginit.logger.warning("revpiplc thread not closed")
            else:
                proginit.logger.debug("revpiplc thread successfully closed")

        proginit.logger.debug("leave RevPiPyLoad.stop_plcprogram()")

    def stop_plcslave(self):
        """Beendet PLC Slave."""
        proginit.logger.debug("enter RevPiPyLoad.stop_plcslave()")

        if self.th_plcslave is not None and self.th_plcslave.is_alive():
            proginit.logger.info("stopping revpi slave thread")
            self.th_plcslave.stop()
            self.th_plcslave.join()
            proginit.logger.debug("revpi slave thread successfully closed")

        proginit.logger.debug("leave RevPiPyLoad.stop_plcslave()")

    def stop_xmlrpcserver(self):
        """Beendet XML-RPC."""
        proginit.logger.debug("enter RevPiPyLoad.stop_xmlrpcserver()")

        if self.xsrv is not None:
            proginit.logger.info("close xmlrpc-server")
            self.xsrv.server_close()

        proginit.logger.debug("leave RevPiPyLoad.stop_xmlrpcserver()")

    def xml_getconfig(self):
        """Uebertraegt die RevPiPyLoad Konfiguration.
        @return dict() der Konfiguration"""
        proginit.logger.debug("xmlrpc call getconfig")
        dc = {}

        # DEFAULT Sektion
        dc["autoreload"] = int(self.autoreload)
        dc["autoreloaddelay"] = self.autoreloaddelay
        dc["autostart"] = int(self.autostart)
        dc["plcworkdir"] = self.plcworkdir
        dc["plcworkdir_set_uid"] = int(self.plcworkdir_set_uid)
        dc["plcprogram"] = self.plcprogram
        dc["plcprogram_stop_timeout"] = self.plcprogram_stop_timeout
        dc["plcprogram_watchdog"] = self.plcprogram_watchdog
        dc["plcarguments"] = self.plcarguments
        dc["plcuid"] = self.plcuid
        dc["plcgid"] = self.plcgid
        dc["pythonversion"] = self.pythonversion
        dc["replace_ios"] = self.replace_ios_config.replace(
            self.plcworkdir + "/", "")
        dc["reset_driver_action"] = self.reset_driver_action
        dc["rtlevel"] = self.rtlevel
        dc["zeroonerror"] = int(self.zeroonerror)
        dc["zeroonexit"] = int(self.zeroonexit)

        # MQTT Sektion
        dc["mqtt"] = int(self.mqtt)
        dc["mqttbasetopic"] = self.mqttbasetopic
        dc["mqttsendinterval"] = self.mqttsendinterval
        dc["mqttbroker_address"] = self.mqttbroker_address
        dc["mqttport"] = self.mqttport
        dc["mqtttls_set"] = int(self.mqtttls_set)
        dc["mqttusername"] = self.mqttusername
        dc["mqttpassword"] = self.mqttpassword
        dc["mqttclient_id"] = self.mqttclient_id
        dc["mqttsend_on_event"] = int(self.mqttsend_on_event)
        dc["mqttwrite_outputs"] = int(self.mqttwrite_outputs)

        # PLCSLAVE Sektion
        dc["plcslave"] = int(self.plcslave)
        dc["plcslaveacl"] = self.plcslaveacl.acl
        dc["plcslavebindip"] = self.plcslavebindip
        dc["plcslaveport"] = self.plcslaveport
        dc["plcslavewatchdog"] = int(self.plcwatchdog)

        # XMLRPC Sektion
        dc["xmlrpc"] = int(self.xmlrpc)
        dc["xmlrpcacl"] = self.xmlrpcacl.acl
        dc["xmlrpcbindip"] = self.xmlrpcbindip

        return dc

    def xml_getfilelist(self):
        """Uebertraegt die Dateiliste vom plcworkdir.
        @return list() mit Dateinamen"""
        proginit.logger.debug("xmlrpc call getfilelist")
        lst_file = []
        wd = os.walk("./")
        for tup_dir in wd:
            if tup_dir[0].find("__pycache__") != -1:
                continue
            for file in tup_dir[2]:
                lst_file.append(os.path.join(tup_dir[0], file)[2:])
        return lst_file

    def xml_getpictoryrsc(self):
        """Gibt die config.rsc Datei von piCotry zurueck.
        @return xmlrpc.client.Binary()"""
        proginit.logger.debug("xmlrpc call getpictoryrsc")
        with open(proginit.pargs.configrsc, "rb") as fh:
            buff = fh.read()
        return Binary(buff)

    def xml_getprocimg(self):
        """Gibt die Rohdaten aus piControl0 zurueck.
        @return xmlrpc.client.Binary()"""
        proginit.logger.debug("xmlrpc call getprocimg")
        with open(proginit.pargs.procimg, "rb") as fh:
            buff = fh.read()
        return Binary(buff)

    def xml_mqttrunning(self):
        """Prueft ob MQTT Uebertragung noch lauft.
        @return True, wenn MQTT Uebertragung noch lauft"""
        proginit.logger.debug("xmlrpc call mqttrunning")
        return False if self.th_plcmqtt is None \
            else self.th_plcmqtt.is_alive()

    def xml_mqttstart(self):
        """Startet die MQTT Uebertragung.

        @return Statuscode:
            0: erfolgreich gestartet
            -1: Nicht aktiv in Konfiguration
            -2: Laeuft bereits

        """
        if self.th_plcmqtt is not None and self.th_plcmqtt.is_alive():
            return -2
        else:
            self.th_plcmqtt = self._plcmqtt()
            if self.th_plcmqtt is None:
                return -1
            else:
                self.th_plcmqtt.start()
                return 0

    def xml_mqttstop(self):
        """Stoppt  die MQTT Uebertragung.
        @return True, wenn stop erfolgreich"""
        if self.th_plcmqtt is not None:
            self.stop_plcmqtt()
            self.th_plcmqtt = None
            return True
        else:
            return False

    def xml_plcdelete_file(self, file_name: str):
        """
        Delete a single file in work directory.

        :param file_name: File with full path relative to work directory
        :return: True on success
        """
        file_name = os.path.join(self.plcworkdir, file_name)
        if os.path.exists(file_name):
            os.remove(file_name)
            dirname = os.path.dirname(file_name)
            if dirname != self.plcworkdir:
                try:
                    # Try to remove directory, which will work if it is empty
                    os.rmdir(dirname)
                except Exception:
                    pass
            return True
        return False

    def xml_plcdownload(self, mode="tar", pictory=False):
        """Uebertraegt ein Archiv vom plcworkdir.

        @param mode Archivart 'tar' 'zip'
        @param pictory piCtory Konfiguraiton mit einpacken
        @return Binary() mit Archivdatei

        """
        proginit.logger.debug("xmlrpc call plcdownload")

        # TODO: Daten einzeln übertragen

        file = self.packapp(mode, pictory)
        if os.path.exists(file):
            with open(file, "rb") as fh:
                xmldata = Binary(fh.read())
            os.remove(file)
            return xmldata
        return Binary()

    def xml_plcdownload_file(self, file_name: str):
        """
        Download a single file from work directory.

        :param file_name: File with full path relative to work directory
        :return: Binary object in gzip format
        """
        file_name = os.path.join(self.plcworkdir, file_name)
        if os.path.exists(file_name):
            with open(file_name, "rb") as fh:
                xmldata = Binary(gzip.compress(fh.read()))
            return xmldata
        return Binary()

    def xml_plcexitcode(self):
        """Gibt den aktuellen exitcode vom PLC Programm zurueck.

        @return int() exitcode oder:
            -1 laeuft noch
            -2 Datei nicht gefunden
            -3 Lief nie
            -9 Killed by watchdog or os

        """
        if self.plc is None:
            return -2
        elif self.plc.exitcode is None and self.plc.is_alive():
            return -1
        else:
            return -3 if self.plc.exitcode is None else self.plc.exitcode

    def xml_plcrunning(self):
        """Prueft ob das PLC Programm noch lauft.
        @return True, wenn das PLC Programm noch lauft"""
        proginit.logger.debug("xmlrpc call plcrunning")
        return False if self.plc is None else self.plc.is_alive()

    def xml_plcstart(self):
        """Startet das PLC Programm.

        @return int() Status:
            -0 Erfolgreich
            -1 Programm lauft noch
            -2 Datei nicht gefunden

        """
        proginit.logger.debug("xmlrpc call plcstart")
        if self.plc is not None and self.plc.is_alive():
            return -1
        else:
            self.plc = self._plcthread()
            if self.plc is None:
                return -2
            else:
                self.plc.start()
                return 0

    def xml_plcstop(self):
        """Stoppt das PLC Programm.

        @return int() Exitcode vom PLC Programm
            -0 Erfolgreich
            -1 PLC Programm lief nicht

        """
        proginit.logger.debug("xmlrpc call plcstop")
        if self.plc is not None and self.plc.is_alive():
            self.stop_plcprogram()
            return self.plc.exitcode
        else:
            return -1

    def xml_plcupload(self, filedata, filename):
        """Empfaengt Dateien fuer das PLC Programm einzeln.

        @param filedata GZIP Binary data der Datei
        @param filename Name inkl. Unterverzeichnis der Datei
        @return Ture, wenn Datei erfolgreich gespeichert wurde

        """
        proginit.logger.debug("xmlrpc call plcupload")

        if filedata is None or filename is None:
            return False

        # Windowszeichen prüfen
        filename = filename.replace("\\", "/")

        # Build absolut path, join will return last element, if absolute
        dirname = os.path.join(self.plcworkdir, os.path.dirname(filename))
        if os.path.abspath(dirname).find(self.plcworkdir) != 0:
            proginit.logger.warning(
                "file path is not in plc working directory"
            )
            return False

        set_uid = self.plcuid if self.plcworkdir_set_uid else 0
        set_gid = self.plcgid if self.plcworkdir_set_uid else 0

        # Set permissions only to newly created directories
        if not os.path.exists(dirname):
            lst_subdir = dirname.replace(self.plcworkdir + "/", "").split("/")
            for i in range(len(lst_subdir)):
                dir_part = os.path.join(self.plcworkdir, *lst_subdir[:i + 1])
                if os.path.exists(dir_part):
                    # Do not change owner of existing directorys
                    continue

                os.makedirs(dirname)
                os.chown(dir_part, set_uid, set_gid)

        # Datei erzeugen
        try:
            with open(filename, "wb") as fh:
                fh.write(gzip.decompress(filedata.data))
            os.chown(filename, set_uid, set_gid)
            return True
        except Exception:
            return False

    def xml_plcuploadclean(self):
        """Loescht das gesamte plcworkdir Verzeichnis.
        @return True, wenn erfolgreich"""
        proginit.logger.debug("xmlrpc call plcuploadclean")
        try:
            rmtree(".", ignore_errors=True)
        except Exception:
            return False
        return True

    def xml_reload(self):
        """Startet RevPiPyLoad neu und verwendet neue Konfiguraiton."""
        proginit.logger.debug("xmlrpc call reload")
        self.evt_loadconfig.set()

    def xml_setconfig(self, dc, loadnow=False):
        """Empfaengt die RevPiPyLoad Konfiguration.
        @return True, wenn erfolgreich angewendet"""
        proginit.logger.debug("xmlrpc call setconfig")
        keys = {
            "DEFAULT": {
                "autoreload": "[01]",
                "autoreloaddelay": "[0-9]+",
                "autostart": "[01]",
                "plcprogram": ".+",
                "plcprogram_stop_timeout": "[0-9]+",
                "plcprogram_watchdog": "[0-9]+",
                "plcarguments": ".*",
                "plcworkdir_set_uid": "[01]",
                # "plcuid": "[0-9]{,5}",
                # "plcgid": "[0-9]{,5}",
                "pythonversion": "[23]",
                "replace_ios": ".*",
                "reset_driver_action": "[0-2]",
                "rtlevel": "[0-1]",
                "zeroonerror": "[01]",
                "zeroonexit": "[01]",
            },
            "MQTT": {
                "mqtt": "[01]",
                "mqttbasetopic": ".*",
                "mqttsendinterval": "[0-9]+",
                "mqttbroker_address": ".+",
                "mqttport": "[0-9]+",
                "mqtttls_set": "[01]",
                "mqttusername": ".*",
                "mqttpassword": ".*",
                "mqttclient_id": ".*",
                "mqttsend_on_event": "[01]",
                "mqttwrite_outputs": "[01]",
            },
            "PLCSLAVE": {
                "plcslave": "[01]",
                "plcslaveacl": self.plcslaveacl.regex_acl,
                # "plcslavebindip": "^((([\\d]{1,3}\\.){3}[\\d]{1,3})|\\*)+$",
                "plcslaveport": "[0-9]{,5}",
                "plcslavewatchdog": "[01]",
            },
            "XMLRPC": {
                "xmlrpc": "[01]",
                "xmlrpcacl": self.xmlrpcacl.regex_acl,
                # "xmlrpcbindip": "^((([\\d]{1,3}\\.){3}[\\d]{1,3})|\\*)+$",
                # "xmlslaveport": "[0-9]{,5}",
            }
        }

        # Adjust values
        if dc.get("replace_ios", "") and dc["replace_ios"].find("/") == -1:
            dc["replace_ios"] = os.path.join(
                self.plcworkdir, dc["replace_ios"])

        # Werte übernehmen, die eine Definition in key haben (andere nicht)
        for sektion in keys:
            suffix = sektion.lower()
            for key in keys[sektion]:
                if key in dc:
                    localkey = key.replace(suffix, "")
                    if not refullmatch(keys[sektion][key], str(dc[key])):
                        proginit.logger.error(
                            "got wrong setting '{0}' with value '{1}'".format(
                                key, dc[key]
                            )
                        )
                        return False
                    if localkey != "acl":
                        self.globalconfig.set(
                            sektion,
                            key if localkey == "" else localkey,
                            str(dc[key])
                        )

        # conf-Datei schreiben
        with open(proginit.globalconffile, "w") as fh:
            self.globalconfig.write(fh)
        proginit.conf = self.globalconfig
        proginit.logger.info(
            "got new config and wrote it to {0}"
            "".format(proginit.globalconffile)
        )

        # ACLs sofort übernehmen und schreiben
        str_acl = dc.get("plcslaveacl", None)
        if str_acl is not None and self.plcslaveacl.acl != str_acl:
            self.plcslaveacl.acl = str_acl
            if not self.plcslaveacl.writeaclfile(aclname="PLC-SLAVE"):
                proginit.logger.error(
                    "can not write acl file '{0}' for PLC-SLAVE"
                    "".format(self.plcslaveacl.filename)
                )
                return False
            else:
                proginit.logger.info(
                    "wrote new acl file '{0}' for PLC-SLAVE"
                    "".format(self.plcslaveacl.filename)
                )
        str_acl = dc.get("xmlrpcacl", None)
        if str_acl is not None and self.xmlrpcacl.acl != str_acl:
            self.xmlrpcacl.acl = str_acl
            if not self.xmlrpcacl.writeaclfile(aclname="XML-RPC"):
                proginit.logger.error(
                    "can not write acl file '{0}' for XML-RPC"
                    "".format(self.xmlrpcacl.filename)
                )
                return False
            else:
                proginit.logger.info(
                    "wrote new acl file '{0}' for XML-RPC"
                    "".format(self.xmlrpcacl.filename)
                )

        # RevPiPyLoad neu konfigurieren
        self.evt_loadconfig.set()

        return True

    def xml_setpictoryrsc(self, filebytes, reset=False):
        """Schreibt die config.rsc Datei von piCotry.

        @param filebytes xmlrpc.client.Binary()-Objekt
        @param reset Reset piControl Device
        @return Statuscode:
            -0 Alles erfolgreich
            -1 Kann JSON-Datei nicht laden
            -2 piCtory Elemente in JSON-Datei nicht gefunden
            -3 Konnte Konfiguraiton nicht schreiben
            -4 Module in Konfiguration enthalten, die es nicht gibt
            -5 Kein RAP Katalog zur Ueberpruefung gefunden
            Positive Zahl ist exitcode von pi_control_reset

        """
        proginit.logger.debug("xmlrpc call setpictoryrsc")

        # Datei als JSON laden
        try:
            jconfigrsc = jloads(filebytes.data.decode())
        except Exception:
            return -1

        # Elemente prüfen
        lst_check = ["Devices", "Summary", "App"]
        for chk in lst_check:
            if chk not in jconfigrsc:
                return -2

        # Prüfen ob Modulkatalog vorhanden ist
        if proginit.rapcatalog is None:
            return -5
        else:

            # piCtory Device in Katalog suchen
            for picdev in jconfigrsc["Devices"]:
                found = False
                picdev = picdev["id"][7:-4]
                for rapdev in proginit.rapcatalog:
                    if rapdev.find(picdev) >= 0:
                        found = True

                # Device im Katalog nicht gefunden
                if not found:
                    return -4

        try:
            with open(proginit.pargs.configrsc, "wb") as fh:
                fh.write(filebytes.data)
        except Exception:
            return -3
        else:
            if reset:
                return pi_control_reset()
            else:
                return 0

    def xml_psstart(self):
        """Startet den Prozessabbildserver.
        @return True, wenn start erfolgreich"""
        if self.xml_ps is not None:
            return self.xml_ps.start()
        else:
            return False

    def xml_psstop(self):
        """Stoppt den Prozessabbildserver.
        @return True, wenn stop erfolgreich"""
        if self.xml_ps is not None:
            self.xml_ps.stop()
            self.xml_ps.loadrevpimodio()
            return True
        else:
            return False

    def xml_plcslaverunning(self):
        """Prueft ob PLC-Slave noch lauft.
        @return True, wenn PLC-Slave noch lauft"""
        proginit.logger.debug("xmlrpc call plcslaverunning")
        return False if self.th_plcslave is None \
            else self.th_plcslave.is_alive()

    def xml_plcslavestart(self):
        """Startet den PLC Slave Server.

        @return Statuscode:
            0: erfolgreich gestartet
            -1: Nicht aktiv in Konfiguration
            -2: Laeuft bereits

        """
        if self.th_plcslave is not None and self.th_plcslave.is_alive():
            return -2
        else:
            self.th_plcslave = self._plcslave()
            if self.th_plcslave is None:
                return -1
            else:
                self.th_plcslave.start()
                return 0

    def xml_plcslavestop(self):
        """Stoppt den PLC Slave Server.
        @return True, wenn stop erfolgreich"""
        if self.th_plcslave is not None:
            self.stop_plcslave()
            self.th_plcslave = None
            return True
        else:
            return False


if __name__ == "__main__":
    # Programmeinstellungen konfigurieren
    proginit.configure()

    if proginit.pargs.test:
        from testsystem import TestSystem

        root = TestSystem()
    else:
        root = RevPiPyLoad()

    # Programm starten
    root.start()

    # Aufräumen
    proginit.cleanup()
