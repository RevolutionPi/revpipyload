# -*- coding: utf-8 -*-
"""Verwaltet IP Adressen und deren ACLs."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"
__version__ = "0.1.0"

from os import R_OK, W_OK, access
from re import match as rematch


def refullmatch(regex, string):
    """re.fullmatch wegen alter python version aus wheezy nachgebaut.

    @param regex RegEx Statement
    @param string Zeichenfolge gegen die getestet wird
    @return True, wenn komplett passt sonst False

    """
    m = rematch(regex, string)
    return m is not None and m.end() == len(string)


class IpAclManager():
    """Verwaltung fuer IP Adressen und deren ACL Level."""

    def __init__(self, minlevel, maxlevel, acl=None):
        """Init IpAclManager class.

        @param minlevel Smallest access level (min. 0)
        @param maxlevel Biggest access level (max. 9)
        @param acl ACL Liste fuer Berechtigungen als <class 'str'>

        """
        if type(minlevel) != int:
            raise ValueError("parameter minlevel must be <class 'int'>")
        if type(maxlevel) != int:
            raise ValueError("parameter maxlevel must be <class 'int'>")
        if minlevel < 0:
            raise ValueError("minlevel must be 0 or more")
        if maxlevel > 9:
            raise ValueError("maxlevel maximum is 9")
        if minlevel > maxlevel:
            raise ValueError("minlevel is smaller than maxlevel")

        self.__dict_acl = {}
        self.__dict_regex = {}
        self.__dict_knownips = {}
        self.__filename = None
        self.__re_ipacl = "(([\\d\\*]{1,3}\\.){3}[\\d\\*]{1,3},[" \
                          + str(minlevel) + "-" + str(maxlevel) + "] ?)*"

        # Liste erstellen, wenn übergeben
        if acl is not None:
            self.__set_acl(acl)

    def __iter__(self):
        """Gibt einzelne ACLs als <class 'tuple'> aus."""
        for aclip in sorted(self.__dict_acl):
            yield (aclip, self.__dict_acl[aclip])

    def __get_acl(self):
        """Getter fuer den rohen ACL-String.
        return ACLs als <class 'str'>"""
        str_acl = ""
        for aclip in sorted(self.__dict_acl):
            str_acl += "{0},{1} ".format(aclip, self.__dict_acl[aclip])
        return str_acl.strip()

    def __get_filename(self):
        """Getter fuer Dateinamen.
        @return Filename der ACL <class 'str'>"""
        return "" if self.__filename is None else self.__filename

    def __get_regex_acl(self):
        """Gibt formatierten RegEx-String zurueck.
        return RegEx Code als <class 'str'>"""
        return self.__re_ipacl

    def __set_acl(self, value):
        """Uebernimmt neue ACL-Liste fuer die Ausertung der Level.
        @param value Neue ACL-Liste als <class 'str'>"""
        if type(value) != str:
            raise ValueError("parameter acl must be <class 'str'>")

        value = value.strip()
        if not refullmatch(self.__re_ipacl, value):
            raise ValueError("acl format ist not okay - 1.2.3.4,0 5.6.7.8,1")

        # Klassenwerte übernehmen
        self.__dict_acl = {}
        self.__dict_regex = {}
        self.__dict_knownips = {}

        # Liste neu füllen mit regex Strings
        for ip_level in value.split():
            ip, level = ip_level.split(",", 1)
            self.__dict_acl[ip] = int(level)
            self.__dict_regex[ip] = \
                ip.replace(".", "\\.").replace("*", "\\d{1,3}")

    def get_acllevel(self, ipaddress):
        """Prueft IP gegen ACL List und gibt ACL-Wert aus.
        @param ipaddress zum pruefen
        @return <class 'int'> ACL Wert oder -1 wenn nicht gefunden"""
        # Bei bereits aufgelösten IPs direkt ACL auswerten
        if ipaddress in self.__dict_knownips:
            return self.__dict_knownips[ipaddress]

        for aclip in sorted(self.__dict_acl, reverse=True):
            if refullmatch(self.__dict_regex[aclip], ipaddress):
                # IP und Level merken
                self.__dict_knownips[ipaddress] = self.__dict_acl[aclip]

                # Level zurückgeben
                return self.__dict_acl[aclip]

        return -1

    def loadacl(self, str_acl):
        """Laed ACL String und gibt erfolg zurueck.
        @param str_acl ACL als <class 'str'>
        @return True, wenn erfolgreich uebernommen"""
        if not refullmatch(self.__re_ipacl, str_acl):
            return False
        self.__set_acl(str_acl)
        return True

    def loadaclfile(self, filename):
        """Laed ACL Definitionen aus Datei.
        @param filename Dateiname fuer Definitionen
        @return True, wenn Laden erfolgreich war"""
        if type(filename) != str:
            raise ValueError("parameter filename must be <class 'str'>")

        # Zugriffsrecht prüfen
        if not access(filename, R_OK):
            return False

        str_acl = ""
        with open(filename, "r") as fh:
            while True:
                buff = fh.readline()
                if buff == "":
                    break
                buff = buff.split("#")[0].strip()
                if len(buff) > 0:
                    str_acl += buff + " "

        acl_okay = self.loadacl(str_acl.strip())
        if acl_okay:
            # Dateinamen für Schreiben übernehmen
            self.__filename = filename

        return acl_okay

    def writeaclfile(self, filename=None, aclname=None):
        """Schreibt ACL Definitionen in Datei.
        @param filename Dateiname fuer Definitionen
        @return True, wenn Schreiben erfolgreich war"""
        if filename is not None and type(filename) != str:
            raise ValueError("parameter filename must be <class 'str'>")
        if aclname is not None and type(aclname) != str:
            raise ValueError("parameter aclname must be <class 'str'>")

        # Dateinamen prüfen
        if filename is None and self.__filename is not None:
            filename = self.__filename

        # Zugriffsrecht prüfen
        if not access(filename, W_OK):
            return False

        header = "# {0}Access Control List (acl)\n" \
                 "# One entry per Line IPADRESS,LEVEL\n" \
                 "#\n".format("" if aclname is None else aclname + " ")

        with open(filename, "w") as fh:
            fh.write(header)
            for aclip in sorted(self.__dict_acl):
                fh.write("{0},{1}\n".format(aclip, self.__dict_acl[aclip]))

        return True

    acl = property(__get_acl, __set_acl)
    filename = property(__get_filename)
    regex_acl = property(__get_regex_acl)
