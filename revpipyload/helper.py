# -*- coding: utf-8 -*-
"""Helperfunktionen fuer das gesamte RevPiPyLoad-System."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2020 Sven Sager"
__license__ = "GPLv3"

import os
from json import loads
from re import match as rematch
from subprocess import Popen, PIPE

import proginit


def _setuprt(pid, evt_exit):
    """Konfiguriert Programm fuer den RT-Scheduler.
    @param pid PID, der angehoben werden soll
    @return None"""
    if proginit.logger is not None:
        proginit.logger.debug("enter _setuprt()")

    dict_change = {
        "ksoftirqd/0,ksoftirqd/1,ksoftirqd/2,ksoftirqd/3": 10,
        "ktimersoftd/0,ktimersoftd/1,ktimersoftd/2,ktimersoftd/3": 20
    }

    for ps_change in dict_change:
        # pid und prio ermitteln
        kpidps = Popen([
            "/bin/ps", "-o", "pid=,rtprio=", "-C", ps_change
        ], bufsize=1, stdout=PIPE)

        # Timeout nachbilden da in Python 3.2 nicht vorhanden
        count = 10
        while kpidps.poll() is None:
            count -= 1
            if count == 0:
                kpidps.kill()
                if proginit.logger is not None:
                    proginit.logger.error(
                        "ps timeout to get rt prio info - no rt active"
                    )
                return None

            evt_exit.wait(0.5)
            if evt_exit.is_set():
                return None

        try:
            kpiddat = kpidps.communicate()[0]
            lst_kpids = kpiddat.split()
        except Exception:
            kpidps.kill()
            if proginit.logger is not None:
                proginit.logger.error(
                    "can not get pid and prio - no rt active"
                )
            return None

        while len(lst_kpids) > 0:
            # Elemente paarweise übernehmen
            kpid = lst_kpids.pop(0)
            kprio = lst_kpids.pop(0)

            # Daten prüfen
            if not kpid.isdigit():
                if proginit.logger is not None:
                    proginit.logger.error(
                        "pid={0} and prio={1} are not valid - no rt active"
                        "".format(kpid, kprio)
                    )
                return None
            kpid = int(kpid)

            # RTPrio ermitteln
            if kprio.isdigit():
                kprio = int(kprio)
            else:
                kprio = 0

            if kprio < 10:
                # Profile anpassen
                ec = os.system("/usr/bin/env chrt -fp {0} {1}".format(
                    dict_change[ps_change], kpid
                ))
                if ec != 0:
                    if proginit.logger is not None:
                        proginit.logger.error(
                            "could not adjust scheduler - no rt active"
                        )
                    return None

    # SCHED_RR für pid setzen
    if proginit.logger is not None:
        proginit.logger.info("set scheduler profile of pid {0}".format(pid))

    ec = os.system("/usr/bin/env chrt -p 1 {0}".format(pid))
    if ec != 0 and proginit.logger is not None:
        proginit.logger.error(
            "could not set scheduler profile of pid {0}"
            "".format(pid)
        )

    if proginit.logger is not None:
        proginit.logger.debug("leave _setuprt()")


def _zeroprocimg():
    """Setzt Prozessabbild auf NULL."""
    procimg = "/dev/piControl0" if proginit.pargs is None \
        else proginit.pargs.procimg

    if os.access(procimg, os.W_OK):
        with open(procimg, "w+b", 0) as f:
            f.write(bytes(4096))
    else:
        if proginit.logger is not None:
            proginit.logger.error(
                "zeroprocimg can not write to piControl device"
            )


def get_revpiled_address(configrsc_bytes):
    """
    Find byte address of revpiled output.

    :return: Address or -1 on error
    """
    try:
        rsc = loads(configrsc_bytes.decode())  # type: dict
    except Exception:
        return -1

    # Check the result does match
    if not type(rsc) == dict:
        return -1

    byte_address = -1
    for device in rsc.get("Devices", ()):  # type: dict
        if device.get("type", "") == "BASE":
            try:
                byte_address = device["offset"] + int(device["out"]["0"][3])
                proginit.logger.debug(
                    "found revpi_led_address on {0} byte".format(byte_address)
                )
            except Exception:
                pass
            break

    return byte_address


def refullmatch(regex, string):
    """re.fullmatch wegen alter python version aus wheezy nachgebaut.

    @param regex RegEx Statement
    @param string Zeichenfolge gegen die getestet wird
    @return True, wenn komplett passt sonst False

    """
    m = rematch(regex, string)
    return m is not None and m.end() == len(string)
