# -*- coding: utf-8 -*-
"""Modul fuer die Verwaltung der PLC-Server Funktionen."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"

import socket
from fcntl import ioctl
from struct import pack, unpack
from threading import Event, Thread
from timeit import default_timer

from . import proginit
from .shared.ipaclmanager import IpAclManager

# Hashvalues
HASH_NULL = b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
HASH_FAIL = b'\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff'
HASH_PICT = HASH_FAIL
HASH_RPIO = HASH_NULL


class RevPiPlcServer(Thread):
    """RevPi PLC-Server.

    Diese Klasste stellt den RevPi PLC-Server zur verfuegung und akzeptiert
    neue Verbindungen. Diese werden dann als RevPiPlcServerDev abgebildet.

    Ueber die angegebenen ACLs koennen Zugriffsbeschraenkungen vergeben werden.

    """

    def __init__(self, ipacl, port=55234, bindip="", watchdog=True):
        """Instantiiert RevPiPlcServer-Klasse.

        @param ipacl AclManager <class 'IpAclManager'>
        @param port Listen Port fuer plc server
        @param bindip IP-Adresse an die der Dienst gebunden wird (leer=alle)
        @param watchdog Trennen, wenn Verarbeitungszeit zu lang

        """
        if not isinstance(ipacl, IpAclManager):
            raise ValueError("parameter ipacl must be <class 'IpAclManager'>")
        if not (isinstance(port, int) and 0 < port <= 65535):
            raise ValueError(
                "parameter port must be <class 'int'> and in range 1 - 65535"
            )
        if not isinstance(bindip, str):
            raise ValueError("parameter bindip must be <class 'str'>")

        super().__init__()
        self.__ipacl = ipacl
        self._bindip = bindip
        self._evt_exit = Event()
        self.exitcode = None
        self._port = port
        self.so = None
        self._th_dev = []
        self._watchdog = watchdog
        self.zeroonerror = False
        self.zeroonexit = False

    def check_connectedacl(self):
        """Prueft bei neuen ACLs bestehende Verbindungen."""
        for dev in self._th_dev:
            ip, port = dev._addr
            level = self.__ipacl.get_acllevel(ip)
            if level < 0:
                # Verbindung killen
                proginit.logger.warning(
                    "client {0} not in acl - disconnect!".format(ip)
                )
                dev.stop()
            elif level != dev._acl:
                # ACL Level anpassen
                proginit.logger.warning(
                    "change acl level from {0} to {1} on existing "
                    "connection {2}".format(level, dev._acl, ip)
                )
                dev._acl = level

    def disconnect_all(self):
        """Close all device connection."""
        # Alle Threads beenden
        for th in self._th_dev:
            th.stop()

    def disconnect_replace_ios(self):
        """Close all device with loaded replace_ios file."""
        # Alle Threads beenden die Replace_IOs emfpangen haben
        for th in self._th_dev:
            if th.got_replace_ios:
                th.stop()

    def newlogfile(self):
        """Konfiguriert die FileHandler auf neue Logdatei."""
        pass

    def run(self):
        """Startet Serverkomponente fuer die Annahme neuer Verbindungen."""
        proginit.logger.debug("enter RevPiPlcServer.run()")

        # Socket öffnen und konfigurieren bis Erfolg oder Ende
        self.so = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.so.settimeout(2)
        sock_bind_err = False
        while not self._evt_exit.is_set():
            try:
                self.so.bind((self._bindip, self._port))
                if sock_bind_err:
                    proginit.logger.warning(
                        "successful bind picontrolserver to socket "
                        "after error"
                    )
            except Exception as e:
                if not sock_bind_err:
                    sock_bind_err = True
                    proginit.logger.warning(
                        "can not bind picontrolserver to socket: {0} "
                        "- retrying".format(e)
                    )
                self._evt_exit.wait(1)
            else:
                self.so.listen(32)
                break

        # Mit Socket arbeiten
        while not self._evt_exit.is_set():
            self.exitcode = -1

            # Verbindung annehmen
            try:
                tup_sock = self.so.accept()
                proginit.logger.info("accepted new connection for revpinetio")
            except socket.timeout:
                continue
            except Exception:
                if not self._evt_exit.is_set():
                    proginit.logger.exception("accept exception")
                continue

            # ACL prüfen
            aclstatus = self.__ipacl.get_acllevel(tup_sock[1][0])
            if aclstatus == -1:
                tup_sock[0].close()
                proginit.logger.warning(
                    "host ip '{0}' does not match revpiacl - disconnect"
                    "".format(tup_sock[1][0])
                )
            else:
                # Thread starten
                th = RevPiPlcServerDev(tup_sock, aclstatus, self._watchdog)
                th.start()
                self._th_dev.append(th)

            # Liste von toten threads befreien
            self._th_dev = [
                th_check for th_check in self._th_dev if th_check.is_alive()
            ]

        # Disconnect all clients and wait some time, because they are daemons
        th_close_err = False
        for th in self._th_dev:  # type: RevPiPlcServerDev
            th.stop()
        for th in self._th_dev:  # type: RevPiPlcServerDev
            th.join(2.0)
            if th.is_alive():
                th_close_err = True
        if th_close_err:
            proginit.logger.warning(
                "piControlServer could not disconnect all clients in timeout"
            )

        # Socket schließen
        self.so.close()
        self.so = None

        self.exitcode = 0

        proginit.logger.debug("leave RevPiPlcServer.run()")

    def stop(self):
        """Beendet Serverausfuehrung."""
        proginit.logger.debug("enter RevPiPlcServer.stop()")

        self._evt_exit.set()
        if self.so is not None:
            try:
                self.so.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass

        proginit.logger.debug("leave RevPiPlcServer.stop()")

    @property
    def watchdog(self):
        return self._watchdog

    @watchdog.setter
    def watchdog(self, value):
        self._watchdog = value
        for th in self._th_dev:  # type: RevPiPlcServerDev
            th.watchdog = value


class RevPiPlcServerDev(Thread):
    """Klasse um eine RevPiModIO Verbindung zu verwalten.

    Diese Klasste stellt die Funktionen zur Verfuegung um Daten ueber das
    Netzwerk mit dem Prozessabbild auszutauschen.
    """

    def __init__(self, devcon, acl, watchdog):
        """Init RevPiPlcServerDev-Class.

        @param devcon Tuple der Verbindung
        @param acl Berechtigungslevel
        @param watchdog Trennen, wenn Verarbeitungszeit zu lang
        """
        super().__init__()
        self.__doerror = False
        self._acl = acl
        self.daemon = True
        self._deadtime = None
        self._devcon, self._addr = devcon
        self._evt_exit = Event()
        self.got_replace_ios = False
        self.watchdog = watchdog

        # Sicherheitsbytes
        self.ey_dict = {}

    def run(self):
        """Verarbeitet Anfragen von Remoteteilnehmer."""
        proginit.logger.debug("enter RevPiPlcServerDev.run()")

        proginit.logger.info(
            "got new connection from host {0} with acl {1}".format(
                self._addr, self._acl
            )
        )

        # Prozessabbild öffnen
        try:
            fh_proc = open(proginit.pargs.procimg, "r+b", 0)
        except Exception:
            self._evt_exit.set()
            proginit.logger.error(
                "can not open process image {0} for {1}"
                "".format(proginit.pargs.procimg, self._addr)
            )
            self._devcon.close()
            self._devcon = None
            return

        buff_size = 2048
        dirty = True
        buff_block = bytearray(buff_size)
        buff_recv = bytearray()
        while not self._evt_exit.is_set():
            # Laufzeitberechnung starten
            ot = default_timer()
            buff_recv.clear()

            # Meldung erhalten
            try:
                recv_len = 16
                while recv_len > 0:
                    count = self._devcon.recv_into(buff_block, recv_len)
                    if count == 0:
                        raise IOError("lost network connection")
                    buff_recv += buff_block[:count]
                    recv_len -= count

                # Unpack ist schneller als Direktzugriff oder Umwandlung
                p_start, cmd, position, length, blob, p_stop = \
                    unpack("=c2sHH8sc", buff_recv)
            except Exception as e:
                proginit.logger.error(e)
                break

            # Wenn Meldung ungültig ist aussteigen
            if p_start != b'\x01' or p_stop != b'\x17':
                proginit.logger.error(
                    "net cmd not valid {0}|{1}|..|{2}"
                    "".format(p_start, cmd, p_stop)
                )
                break

            if cmd == b'DA':
                # Processabbild übertragen
                # b CM ii ii 00000000 b = 16

                fh_proc.seek(position)
                try:
                    self._devcon.sendall(fh_proc.read(length))
                except Exception:
                    proginit.logger.error("error while send read data")
                    break

            elif cmd == b'WD':
                # Ausgänge setzen, wenn acl es erlaubt
                # b CM ii ii c0000000 b = 16

                # Berechtigung prüfen und ggf. trennen
                if self._acl < 1:
                    self._devcon.sendall(b'\x18')
                    break

                # Datenblock schreiben
                buff_recv.clear()
                try:
                    while length > 0:
                        count = self._devcon.recv_into(buff_block, min(length, buff_size))
                        if count == 0:
                            raise IOError("lost network connection")
                        buff_recv += buff_block[:count]
                        length -= count
                except Exception:
                    proginit.logger.error("error while recv data for wd write")
                    break

                fh_proc.seek(position)
                fh_proc.write(buff_recv)

                # Record separator character
                self._devcon.sendall(b'\x1e')

            elif cmd == b'FD':
                # Ausgänge gepuffert setzen, deutlich schneller als WD
                # b CM ii ii 00000000 b = 16

                # Berechtigung prüfen und ggf. trennen
                if self._acl < 1:
                    self._devcon.sendall(b'\x18')
                    break

                buff_recv.clear()
                counter = length
                try:
                    while counter > 0:
                        count = self._devcon.recv_into(buff_block, min(counter, buff_size))
                        if count == 0:
                            raise IOError("lost network connection")
                        buff_recv += buff_block[:count]
                        counter -= count
                except Exception:
                    proginit.logger.error("error while recv data for fd write")
                    break

                # Header: ppllbuff
                index = 0
                while index < length:
                    r_position, r_length = unpack("=HH", buff_recv[index:index + 4])
                    index += 4

                    fh_proc.seek(r_position)
                    fh_proc.write(buff_recv[index:index + r_length])

                    index += r_length

                # Record separator character
                self._devcon.sendall(b'\x1e')

            elif cmd == b'\x06\x16':
                # Just sync
                self._devcon.sendall(b'\x06\x16')

            elif cmd == b'CF':
                # Socket konfigurieren
                # b CM ii xx 00000000 b = 16

                # position = timeoutms
                if 0 < position <= 65535:
                    self._deadtime = position / 1000
                    self._devcon.settimeout(self._deadtime)
                    proginit.logger.debug(
                        "set socket timeout to {0}".format(self._deadtime)
                    )

                    # Record separator character
                    self._devcon.sendall(b'\x1e')
                else:
                    proginit.logger.error("timeout value must be 0 to 65535")
                    self._devcon.sendall(b'\xff')
                    break

            elif cmd == b'EY':
                # Bytes bei Verbindungsabbruch schreiben
                # b CM ii ii x0000000 b = 16

                # Berechtigung prüfen und ggf. trennen
                if self._acl < 1:
                    self._devcon.sendall(b'\x18')
                    break

                control = blob[0:1]

                ok_byte = b'\xff' if self.__doerror else b'\x1e'

                if control == b'\xff':
                    # Alle Dirtybytes löschen
                    self.ey_dict = {}

                    self._devcon.sendall(ok_byte)
                    proginit.logger.info("cleared all dirty bytes")

                elif control == b'\xfe':
                    # Bestimmte Dirtybytes löschen

                    if position in self.ey_dict:
                        del self.ey_dict[position]

                    self._devcon.sendall(ok_byte)
                    proginit.logger.info(
                        "cleared dirty bytes on position {0}"
                        "".format(position)
                    )

                else:
                    # Dirtybytes hinzufügen
                    buff_recv.clear()
                    try:
                        while length > 0:
                            count = self._devcon.recv_into(buff_block, min(length, buff_size))
                            if count == 0:
                                raise IOError("lost network connection")
                            length -= count
                            buff_recv += buff_block[:count]

                    except Exception:
                        proginit.logger.error("error while recv dirty bytes")
                        break

                    self.ey_dict[position] = bytes(buff_recv)

                    self._devcon.sendall(ok_byte)
                    proginit.logger.info(
                        "got dirty bytes to write on error on position {0}"
                        "".format(position)
                    )

            elif cmd == b'PI':
                # piCtory Konfiguration senden
                proginit.logger.debug(
                    "transfair pictory configuration: {0}"
                    "".format(proginit.pargs.configrsc)
                )
                try:
                    with open(proginit.pargs.configrsc, "rb") as fh_pic:
                        # Komplette piCtory Datei lesen
                        buff = fh_pic.read()

                    self._devcon.sendall(pack("=I", len(buff)) + buff)
                except Exception as e:
                    proginit.logger.error(
                        "error on pictory transfair: {0}".format(e)
                    )
                    break

                # Laufzeitberechnung überspringen
                continue

            elif cmd == b'PH':
                # piCtory md5 Hashwert senden (16 Byte)
                proginit.logger.debug(
                    "send pictory hashvalue: {0}".format(HASH_PICT)
                )
                self._devcon.sendall(HASH_PICT)

            elif cmd == b'RP':
                # Replace_IOs Konfiguration senden, wenn hash existiert
                replace_ios = proginit.conf["DEFAULT"].get("replace_ios", "")
                proginit.logger.debug(
                    "transfair replace_io configuration: {0}"
                    "".format(replace_ios)
                )
                if HASH_RPIO != HASH_NULL and replace_ios:
                    try:
                        with open(replace_ios, "rb") as fh:
                            # Komplette replace_io Datei lesen
                            buff = fh.read()

                        self._devcon.sendall(pack("=I", len(buff)) + buff)
                    except Exception as e:
                        proginit.logger.error(
                            "error on replace_io transfair: {0}".format(e)
                        )
                        break

                else:
                    # Nulllänge senden, damit client weiter machen kann
                    self._devcon.sendall(b'\x00\x00\x00\x00')

                # Laufzeitberechnung überspringen
                continue

            elif cmd == b'RH':
                # Replace_IOs md5 Hashwert senden (16 Byte)
                self.got_replace_ios = True

                proginit.logger.debug(
                    "send replace_ios hashvalue: {0}".format(HASH_RPIO)
                )
                self._devcon.sendall(HASH_RPIO)

            elif cmd == b'EX':
                # Sauber Verbindung verlassen
                dirty = False
                self._evt_exit.set()
                continue

            elif cmd == b'IC':
                # Net-IOCTL ausführen
                # b CM xx ii iiii0000 b = 16

                request, = unpack("=I4x", blob)

                buff_recv.clear()
                try:
                    while length > 0:
                        count = self._devcon.recv_into(buff_block, min(length, buff_size))
                        if count == 0:
                            raise IOError("lost network connection")
                        length -= count
                        buff_recv += buff_block[:count]
                except Exception:
                    proginit.logger.error("error on network ioctl call")
                    break

                # Berechtigung prüfen und ggf. trennen
                if self._acl < 1:
                    self._devcon.sendall(b'\x18')
                    break

                try:
                    if proginit.pargs.procimg == "/dev/piControl0":
                        # Läuft auf RevPi
                        ioctl(fh_proc, request, bytes(buff_recv))
                        proginit.logger.debug(
                            "ioctl {0} with {1} successful"
                            "".format(request, bytes(buff_recv))
                        )
                    else:
                        # Simulation
                        # TODO: IOCTL für Dateien implementieren
                        proginit.logger.warning(
                            "ioctl {0} with {1} simulated".format(request, bytes(buff_recv))
                        )
                except Exception as ex:
                    proginit.logger.error(ex)
                    self._devcon.sendall(b'\xff')
                else:
                    self._devcon.sendall(b'\x1e')

            elif proginit.pargs.developermode and cmd == b'DV':
                # Development options
                # b CM ii ii c0000000 b = 16
                if self._acl < 9:
                    # Spezieller ACL-Wert für Entwicklung
                    self._devcon.sendall(b'\x18')
                    break

                c, d = unpack("=cc6x", blob)
                if c == b'a':
                    # CMD a = Switch ACL to 0
                    self._acl = 0
                    proginit.logger.warning("DV: set acl to 0")
                    self._devcon.sendall(b'\x1e')
                elif c == b'b':
                    # CMD b = Aktiviert/Deaktiviert den Fehlermodus
                    if d == b'\x01':
                        self.__doerror = True
                        proginit.logger.warning("DV: set do_error")
                    else:
                        self.__doerror = False
                        proginit.logger.warning("DV: reset do_error")
                    self._devcon.sendall(b'\x1e')

            else:
                # Kein gültiges CMD gefunden, abbruch!
                proginit.logger.error(
                    "found unknown net cmd: {0}".format(cmd)
                )
                break

            # Verarbeitungszeit nicht prüfen
            if self._deadtime is None:
                continue

            comtime = default_timer() - ot
            if comtime > self._deadtime:
                if self.watchdog:
                    proginit.logger.error(
                        "runtime more than {0} ms: {1} - force disconnect"
                        "".format(int(self._deadtime * 1000), comtime)
                    )
                    break
                else:
                    proginit.logger.warning(
                        "runtime more than {0} ms: {1}!"
                        "".format(int(self._deadtime * 1000), comtime)
                    )

        # Dirty verlassen
        if dirty:
            for pos in self.ey_dict:
                fh_proc.seek(pos)
                fh_proc.write(self.ey_dict[pos])

            proginit.logger.error("dirty shutdown of connection")

        fh_proc.close()
        self._devcon.close()
        self._devcon = None

        proginit.logger.info("disconnected from {0}".format(self._addr))
        proginit.logger.debug("leave RevPiPlcServerDev.run()")

    def stop(self):
        """
        Send signal to disconnect from client.

        This will be a dirty disconnect and the thread needs some time to close
        the connection. Call .join() to give the thread some time, it is a
        daemon!
        """
        proginit.logger.debug("enter RevPiPlcServerDev.stop()")

        self._evt_exit.set()
        if self._devcon is not None:
            self._devcon.shutdown(socket.SHUT_RDWR)

        proginit.logger.debug("leave RevPiPlcServerDev.stop()")
