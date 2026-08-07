# -*- coding: utf-8 -*-
"""XML-RPC Server anpassungen fuer Absicherung."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"

import grp
import os
import socket
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

from . import proginit
from .shared.ipaclmanager import IpAclManager


class SaveXMLRPCServer(SimpleXMLRPCServer):
    """Erstellt einen erweiterten XMLRPCServer."""

    def __init__(
            self, addr, logRequests=True, allow_none=False, ipacl=None):
        """Init SaveXMLRPCServer class.
        @param ipacl AclManager <class 'IpAclManager'>"""
        proginit.logger.debug("enter SaveXMLRPCServer.__init__()")

        if ipacl is not None and type(ipacl) != IpAclManager:
            raise ValueError("parameter ipacl must be <class 'IpAclManager'>")

        # Vererbte Klasse instantiieren
        super().__init__(
            addr=addr,
            requestHandler=SaveXMLRPCRequestHandler,
            logRequests=logRequests,
            allow_none=allow_none,
            encoding="utf-8",
            bind_and_activate=False,
        )
        self.timeout = 0.5

        # Klassenvariablen
        if ipacl is None:
            self.aclmgr = IpAclManager(0, 0)
        else:
            self.aclmgr = ipacl
        self.funcacls = {}
        self.requestacl = -1

        proginit.logger.debug("leave SaveXMLRPCServer.__init__()")

    def _dispatch(self, method, params):
        """Prueft ACL Level fuer angeforderte Methode.

        @param method Angeforderte Methode
        @param params Argumente fuer Methode
        @return Dispatched data

        """
        # ACL Level für angeforderte Methode prüfen
        if self.requestacl < self.funcacls.get(method, -1):
            raise RuntimeError("function call not allowed")

        # ACL Mode abfragen (Gibt ACL Level als Parameter)
        if method == "xmlmodus":
            params = (self.requestacl,)

        return super()._dispatch(method, params)

    def register_function(self, acl_level, function, name=None):
        """Override register_function to add acl_level.

        @param acl_level ACL level to call this function
        @param function Function to register
        @param name Alternative name to use

        """
        if type(acl_level) != int:
            raise ValueError("parameter acl_level must be <class 'int'>")

        if name is None:
            name = function.__name__
        self.funcs[name] = function
        self.funcacls[name] = acl_level


class SaveXMLRPCRequestHandler(SimpleXMLRPCRequestHandler):
    """Verwaltet die XML-Requests und prueft Berechtigungen."""

    timeout = 2.0

    def parse_request(self):
        """Berechtigungen pruefen.
        @return True, wenn Parsen erfolgreich war"""
        # Request parsen und ggf. schon abbrechen
        if not super().parse_request():
            return False

        # ACL für IP-Adresse übernehmen
        self.server.requestacl = \
            self.server.aclmgr.get_acllevel(self.client_address[0])

        if self.server.requestacl >= 0:
            return True
        else:
            self.send_error(
                401, "IP '{0}' not allowed".format(self.client_address[0])
            )

        return False


class UnixStreamXMLRPCRequestHandler(SimpleXMLRPCRequestHandler):
    """XML-RPC Request-Handler fuer Unix Domain Sockets."""

    timeout = 2.0

    def setup(self):
        """Initialisiert die Verbindung ohne TCP-spezifische Socket-Optionen."""
        self.connection = self.request
        if self.timeout is not None:
            self.connection.settimeout(self.timeout)
        self.rfile = self.connection.makefile("rb", self.rbufsize)
        self.wfile = self.connection.makefile("wb", self.wbufsize)

    def address_string(self):
        """Liefert einen Namen fuer Logging bei Unix Domain Sockets."""
        return "localhost"


class UnixStreamXMLRPCServer(SimpleXMLRPCServer):
    """XML-RPC Server fuer Unix Domain Sockets."""
    address_family = socket.AF_UNIX

    def __init__(
            self, addr, logRequests=True, allow_none=False, unixgroup="picontrol"):
        """Init UnixStreamXMLRPCServer class."""
        proginit.logger.debug("enter UnixStreamXMLRPCServer.__init__()")

        self.timeout = 0.5
        self.unixgroup = unixgroup

        # Create subdirectories for Unix Domain Socket
        socket_dir = os.path.dirname(addr)
        if socket_dir:
            os.makedirs(socket_dir, exist_ok=True)

        super().__init__(
            addr=addr,
            requestHandler=UnixStreamXMLRPCRequestHandler,
            logRequests=logRequests,
            allow_none=allow_none,
            encoding="utf-8",
            bind_and_activate=False,
        )

        proginit.logger.warning("RevPi Commander 0.12.0 or greater is required to connect via Unix Domain Socket")

        proginit.logger.debug("leave UnixStreamXMLRPCServer.__init__()")

    def server_bind(self):
        """Ueberschreibt server_bind um Berechtigungen zu setzen."""
        super().server_bind()

        # Gruppe setzen
        try:
            gid = grp.getgrnam(self.unixgroup).gr_gid
            # -1 will leave the owner unchanged
            os.chown(self.server_address, -1, gid)
        except (KeyError, PermissionError):
            proginit.logger.warning(
                "can not set group of socket {0} to '{1}'"
                "".format(self.server_address, self.unixgroup)
            )

        # Berechtigungen fuer restliche Benutzer entziehen
        try:
            os.chmod(self.server_address, 0o660)
        except PermissionError:
            proginit.logger.warning(
                "can not set permissions of socket {0}"
                "".format(self.server_address)
            )

    def _dispatch(self, method, params):
        """Prueft ACL Level fuer angeforderte Methode.

        @param method Angeforderte Methode
        @param params Argumente fuer Methode
        @return Dispatched data

        """
        # ACL on a socket is always max level
        if method == "xmlmodus":
            params = (4,)

        return super()._dispatch(method, params)

    def register_function(self, acl_level, function, name=None):
        """Override register_function to ignore acl_level for unix sockets."""
        return super().register_function(function, name)
