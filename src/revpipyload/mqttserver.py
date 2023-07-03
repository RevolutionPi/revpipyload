# -*- coding: utf-8 -*-
"""Stellt die MQTT Uebertragung fuer IoT-Zwecke bereit."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"

from os.path import join
from ssl import CERT_NONE
from threading import Event, Thread

import revpimodio2
from paho.mqtt.client import Client, connack_string

from . import proginit


class MqttServer(Thread):
    """Server fuer die Uebertragung des Prozessabbilds per MQTT."""

    def __init__(
            self, basetopic, sendinterval, broker_address, port=1883,
            tls_set=False, username="", password=None, client_id="",
            send_events=False, write_outputs=False, replace_ios=None):
        """Init MqttServer class.

        @param basetopic Basis-Topic fuer Datenaustausch
        @param sendinterval Prozessabbild alle n Sekunden senden / 0 = aus
        @param broker_address Adresse <class 'str'> des MQTT-Servers
        @param port Portnummer <class 'int'> des MQTT-Servers
        @param tls_set TLS fuer Verbindung zum MQTT-Server verwenden
        @param username Optional Benutzername fuer MQTT-Server
        @param password Optional Password fuer MQTT-Server
        @param client_id MQTT ClientID, wenn leer automatisch random erzeugung
        @param send_events Sendet Werte bei IO Wertaenderung
        @param write_outputs Per MQTT auch Outputs schreiben
        @param replace_ios Replace IOs of RevPiModIO

        """
        if not isinstance(basetopic, str):
            raise ValueError("parameter topic must be <class 'str'>")
        if not (isinstance(sendinterval, int) and sendinterval >= 0):
            raise ValueError("parameter sendinterval must be <class 'int'> and >= 0")
        if not (isinstance(broker_address, str) and broker_address != ""):
            raise ValueError("parameter broker_address must be <class 'str'> and not empty")
        if not (isinstance(port, int) and 0 < port < 65535):
            raise ValueError("parameter sendinterval must be <class 'int'> and 1 - 65535")
        if not isinstance(tls_set, bool):
            raise ValueError("parameter tls_set must be <class 'bool'>")
        if not isinstance(username, str):
            raise ValueError("parameter username must be <class 'str'>")
        if not (password is None or isinstance(password, str)):
            raise ValueError("parameter password must be <class 'str'>")
        if not isinstance(client_id, str):
            raise ValueError("parameter client_id must be <class 'str'>")
        if not isinstance(send_events, bool):
            raise ValueError("parameter send_events must be <class 'bool'>")
        if not isinstance(write_outputs, bool):
            raise ValueError("parameter write_outputs must be <class 'bool'>")
        if not (replace_ios is None or isinstance(replace_ios, str)):
            raise ValueError("parameter replace_ios must be <class 'str'>")

        super().__init__()

        # Klassenvariablen
        self.__exit = False
        self._evt_data = Event()
        self._exported_ios = []
        self._broker_address = broker_address
        self._port = port
        self._reloadmodio = False
        self._replace_ios = replace_ios
        self._rpi = None
        self._send_events = send_events
        self._sendinterval = sendinterval
        self._write_outputs = write_outputs

        # RevPiModIO laden oder mit Exception aussteigen
        self._loadrevpimodio()

        # Topics konfigurieren
        self._basetopic = basetopic.strip("/")
        self._basetopic_len = len(self._basetopic.split("/"))
        self._mqtt_evt_io = join(basetopic, "event/{0}")
        self._mqtt_got_io = join(basetopic, "got/{0}")
        self._mqtt_io = join(basetopic, "io/{0}")
        self._mqtt_ioget = join(basetopic, "get/#")
        self._mqtt_ioset = join(basetopic, "set/#")
        self._mqtt_ioreset = join(basetopic, "reset/#")
        self._mqtt_pictory = join(basetopic, "pictory")
        self._mqtt_senddata = join(basetopic, "get")
        self._mqtt_sendpictory = join(basetopic, "needpictory")

        self._mq = Client(client_id)
        if username != "":
            self._mq.username_pw_set(username, password)
        if tls_set:
            self._mq.tls_set(cert_reqs=CERT_NONE)
            self._mq.tls_insecure_set(True)

        # Handler konfigurieren
        self._mq.on_connect = self._on_connect
        self._mq.on_message = self._on_message

    def _evt_io(self, name, value, requested=False):
        """Sendet Daten aus Events.

        @param name IO-Name
        @param value IO-Value
        @param requested Wenn True, wird 'got' Topic verwendet

        """
        if requested:
            topic = self._mqtt_got_io.format(name)
        else:
            topic = self._mqtt_evt_io.format(name)

        if isinstance(value, bytes):
            value = int.from_bytes(value, "little")
        self._mq.publish(topic, int(value))

    def _loadrevpimodio(self):
        """Instantiiert das RevPiModIO Modul.
        @return None or Exception"""
        self._reloadmodio = False
        self._exported_ios = []

        # RevPiModIO-Modul Instantiieren
        if self._rpi is not None:
            self._rpi.cleanup()

        proginit.logger.debug("create revpimodio2 object for MQTT")
        try:
            # Vollzugriff und Eventüberwachung
            self._rpi = revpimodio2.RevPiModIO(
                autorefresh=self._send_events,
                monitoring=not self._write_outputs,
                configrsc=proginit.pargs.configrsc,
                procimg=proginit.pargs.procimg,
                replace_io_file=self._replace_ios,
                shared_procimg=True,
            )
            self._rpi.debug = -1

            if self._replace_ios:
                proginit.logger.info("loaded replace_ios to MQTT")

        except Exception as e:
            try:
                # Lesend und Eventüberwachung
                self._rpi = revpimodio2.RevPiModIO(
                    autorefresh=self._send_events,
                    monitoring=not self._write_outputs,
                    configrsc=proginit.pargs.configrsc,
                    procimg=proginit.pargs.procimg,
                    shared_procimg=True,
                )
                self._rpi.debug = -1
                proginit.logger.warning("replace_ios_file not loadable for MQTT - using defaults now | {0}".format(e))

            except Exception as e:
                self._rpi = None
                proginit.logger.error("piCtory configuration not loadable for MQTT | {0}".format(e))
                raise e

        # Exportierte IOs laden
        for dev in self._rpi.device:
            for io in dev.get_allios(export=True):
                io.reg_event(self._evt_io)
                self._exported_ios.append(io)

        # CoreIOs prüfen und zu export hinzufügen
        lst_coreio = []
        if self._rpi.core:
            for obj_name in dir(self._rpi.core):
                # Scan all non-private objects of the core device
                if obj_name.find("_") == 0:
                    continue
                obj = getattr(self._rpi.core, obj_name)
                if isinstance(obj, revpimodio2.io.IOBase) and obj.export:
                    lst_coreio.append(obj)

        # IOs exportieren und Events anmelden
        for io in lst_coreio:
            io.reg_event(self._evt_io)
            self._exported_ios.append(io)

        proginit.logger.debug("created revpimodio2 object")

    def _on_connect(self, client, userdata, flags, rc):
        """Verbindung zu MQTT Broker."""
        proginit.logger.debug("enter MqttServer._on_connect()")

        if rc > 0:
            proginit.logger.warning(
                "can not connect to mqtt broker '{0}' - error '{1}' - "
                "will retry".format(self._broker_address, connack_string(rc))
            )
        else:
            # Subscribe piCtory Anforderung
            client.subscribe(self._mqtt_ioget)
            client.subscribe(self._mqtt_senddata)
            client.subscribe(self._mqtt_sendpictory)
            if self._write_outputs:
                client.subscribe(self._mqtt_ioset)
                client.subscribe(self._mqtt_ioreset)

        proginit.logger.debug("leave MqttServer._on_connect()")

    def _on_disconnect(self, client, userdata, rc):
        """Wertet Verbindungsabbruch aus."""
        proginit.logger.debug("enter MqttServer._on_disconnect()")

        if rc != 0:
            proginit.logger.warning("unexpected disconnection from mqtt broker - will try to reconnect")

        proginit.logger.debug("leave MqttServer._on_disconnect()")

    def _on_message(self, client, userdata, msg):
        """Sendet piCtory Konfiguration."""
        proginit.logger.debug("Received topic: {0}".format(msg.topic))

        if msg.topic == self._mqtt_pictory:
            # piCtory Konfiguration senden
            self._send_pictory_conf()

        elif msg.topic == self._mqtt_senddata:
            # Alle zyklischen Daten senden
            self._evt_data.set()

        else:
            # The I/O name may contain forward slashes. Those look nice on the
            # MQTT bus, but make parsing the topic for actions a bit harder since
            # we cannot simply split the topic and know at what index to find the
            # action. To find the action we remove base topic parts to get the
            # action and finally reassemble the I/O name with slashes.
            lst_topic = msg.topic.split("/")[self._basetopic_len:]
            if len(lst_topic) < 2 or lst_topic[0].lower() not in ("get", "set", "reset"):
                proginit.logger.error(
                    "wrong format for topic '{0}', expected '{1}/[get|set|reset]/<ioname>'"
                    "".format(msg.topic, self._basetopic)
                )
                return

            # Aktion und IO auswerten
            io_action = lst_topic[0].lower()
            ioget = io_action == "get"
            ioset = io_action == "set"
            ioreset = io_action == "reset"
            ioname = '/'.join(lst_topic[1:])
            coreio = ioname.find("core.") == 0

            try:
                # IO holen
                if coreio:
                    coreio = ioname.split(".")[-1]
                    io = getattr(self._rpi.core, coreio)
                    if not isinstance(io, revpimodio2.io.IOBase):
                        raise RuntimeError()
                else:
                    io = self._rpi.io[ioname]
                io_needbytes = type(io.value) == bytes
            except Exception:
                proginit.logger.error("can not find io '{0}'".format(ioname))
                return

            # Aktion verarbeiten
            if not io.export:
                proginit.logger.error("io '{0}' is not marked as export in piCtory for MQTT use".format(ioname))

            elif ioget:
                # Werte laden, wenn nicht autorefresh
                if not self._send_events:
                    io._parentdevice.readprocimg()

                # Publish Wert von IO
                self._evt_io(io.name, io.value, requested=True)

            elif ioset and io.type != revpimodio2.OUT:
                proginit.logger.error("can not write to input '{0}'".format(ioname))

            elif ioset:
                # Convert MQTT Payload to valid Output-Value
                value = msg.payload.decode("utf8")

                if value.isdecimal():
                    value = int(value)

                    # Muss eine Byteumwandlung vorgenommen werden?
                    if io_needbytes:
                        try:
                            value = value.to_bytes(io.length, io.byteorder)
                        except OverflowError:
                            proginit.logger.error(
                                "can not convert value '{0}' to fitting bytes for output '{1}'".format(value, ioname))
                            return

                elif value.lower() == "false" and not io_needbytes:
                    value = 0
                elif value.lower() == "true" and not io_needbytes:
                    value = 1
                else:
                    proginit.logger.error("can not convert value '{0}' for output '{1}'".format(value, ioname))
                    return

                # Write Value to RevPi
                try:
                    io.value = value
                    # Write data without autorefresh
                    if not self._send_events:
                        io._parentdevice.writeprocimg()
                except Exception:
                    proginit.logger.error("could not write value '{0}' to output '{1}'".format(value, ioname))

            elif ioreset:
                # Counter zurücksetzen
                if not isinstance(io, revpimodio2.io.IntIOCounter):
                    proginit.logger.warning("io '{0}' is not a counter".format(ioname))
                else:
                    io.reset()

            else:
                # Aktion nicht erkennbar
                proginit.logger.warning("can not find get/set/reset in topic '{0}'".format(msg.topic))

    def _send_pictory_conf(self):
        """Sendet piCtory Konfiguration per MQTT."""
        try:
            fh = open(proginit.pargs.configrsc, "rb")
            self._mq.publish(self._mqtt_pictory, fh.read())
            fh.close()
        except Exception:
            proginit.logger.error("can not read and publish piCtory config '{0}'".format(proginit.pargs.configrsc))

    def newlogfile(self):
        """Konfiguriert die FileHandler auf neue Logdatei."""
        pass

    def reload_revpimodio(self):
        """Fuehrt im naechsten Zyklus zum Reload."""
        proginit.logger.debug("enter MqttServer.reload_revpimodio()")

        self._reloadmodio = True
        self._evt_data.set()

        proginit.logger.debug("leave MqttServer.reload_revpimodio()")

    def run(self):
        """Startet die Uebertragung per MQTT."""
        proginit.logger.debug("enter MqttServer.run()")

        # MQTT verbinden
        proginit.logger.info("connecting to mqtt broker {0}".format(self._broker_address))
        try:
            self._mq.connect(self._broker_address, self._port, keepalive=60)
        except Exception:
            self._on_connect(self._mq, None, None, 3)
            self._mq.connect_async(self._broker_address, self._port, keepalive=60)
        self._mq.loop_start()

        # Eventüberwachung starten
        if self._send_events:
            proginit.logger.debug("start non blocking mainloop of revpimodio")
            self._rpi.mainloop(blocking=False)

        # mainloop
        send_cycledata = self._sendinterval > 0
        while not self.__exit:
            self._evt_data.clear()

            # RevPiModIO neu laden
            if self._reloadmodio:
                proginit.logger.info("reload revpimodio for mqtt")
                self._loadrevpimodio()

                # Eventüberwachung erneut starten
                if self._send_events:
                    proginit.logger.debug("start non blocking mainloop of revpimodio")
                    self._rpi.mainloop(blocking=False)

            if send_cycledata:
                # Werte laden, wenn nicht autorefresh
                if not self._send_events:
                    self._rpi.readprocimg()

                # Exportierte IOs übertragen
                for io in self._exported_ios:
                    value = io.value
                    if isinstance(value, bool):
                        value = int(value)  # Convert False/True to 0/1. Publish function would send the string.
                    self._mq.publish(self._mqtt_io.format(io.name), value)

            self._evt_data.wait(10 if not send_cycledata else self._sendinterval)

        # MQTT trennen
        proginit.logger.info("disconnecting from mqtt broker {0}".format(self._broker_address))
        self._mq.disconnect()

        # RevPiModIO aufräumen
        self._rpi.cleanup()

        proginit.logger.debug("leave MqttServer.run()")

    def stop(self):
        """Stoppt die Uebertragung per MQTT."""
        proginit.logger.debug("enter MqttServer.stop()")
        self.__exit = True
        self._evt_data.set()
        proginit.logger.debug("leave MqttServer.stop()")
