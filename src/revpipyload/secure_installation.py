#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Secure your installation of RevPiPyLoad.

Exit codes:
    1: Runtime error
    2: Program did no changes on files
    4: No root permissions
    8: Write error to acl files
"""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"


def main() -> int:
    """Secure installation script to use on Revolution Pi."""
    from configparser import ConfigParser
    from os import R_OK, access, getuid, system
    from re import match
    from sys import stderr, stdout

    from .shared.ipaclmanager import IpAclManager

    CONFIG_FILE = "/etc/revpipyload/revpipyload.conf"

    if not access(CONFIG_FILE, R_OK):
        raise PermissionError("Can not access {0}".format(CONFIG_FILE))

    conf = ConfigParser()
    conf.read(CONFIG_FILE)

    aclxmlrpc = conf.get("XMLRPC", "aclfile")
    if not access(aclxmlrpc, R_OK):
        raise PermissionError("Can not access {0}".format(aclxmlrpc))

    # Load config values
    xmlrpc = conf.getboolean("XMLRPC", "xmlrpc", fallback=False)
    xmlrpcbindip = conf.get("XMLRPC", "bindip", fallback="127.0.0.1")

    # Prepare variables
    xmlrpcacl = IpAclManager(minlevel=0, maxlevel=4)
    xmlrpcacl.loadaclfile(aclxmlrpc)
    xmlrpc_only_localhost = xmlrpcbindip.find("127.") == 0 or xmlrpcbindip == ""

    # ----- Print summary of actual configuration
    stdout.write("""
    This will secure your installation of RevPiPyLoad.
    
    We found the following configuration files:
        RevPiPyLoad:   {revpipyload}
        XML-RPC ACL:   {aclxmlrpc}
    
    Access with RevPiPyControl is {xmlrpc}activated{source}
    """.format(
        revpipyload=CONFIG_FILE,
        aclxmlrpc=aclxmlrpc,
        xmlrpc="" if xmlrpc else "NOT ",
        source="" if not xmlrpc
        else " from this computer only (localhost)." if xmlrpc_only_localhost
        else " from ACL listed remote computers!"
    ))

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
    # region #      REGION: Shared functions
    def save_xmlrpcacls():
        """Save new acl's to file."""
        if not xmlrpcacl.writeaclfile(aclname="XML-RPC"):
            stderr.write("Error while writing ACL file!\n")
            return 8

    def print_xmlrpcacls():
        """Print a list with all acl."""
        if xmlrpcacl.acl:
            stdout.write("\nThis is the actual ACL file ({0}):".format(aclxmlrpc))
            counter = 0
            for acl in xmlrpcacl.acl.split():
                stdout.write("\n" if counter % 2 == 0 else "     |     ")
                ip, level = acl.split(",")
                stdout.write("{0:15} - Level: {1:2}".format(ip, level))
                counter += 1
            stdout.write("\n")
        else:
            stderr.write(
                "\nWARNING: NO IP addresses defined in ACL!\n         You will "
                "not be able to connect with RevPiPyControl at this moment!\n"
            )

    # endregion # # # # #

    try:
        if not xmlrpc_only_localhost:
            cmd = input("\nDo you want to check ACL listed computers? (y/N) ").lower()
            if cmd == "y":
                print_xmlrpcacls()

        if getuid() != 0:
            stderr.write("\nYou need root permissions to change values (sudo).\n")
            return 4

        cmd = input("\nDo you want to allow connections from remote hosts? (y/N) ").lower()
        if cmd == "y":
            conf.set("XMLRPC", "xmlrpc", "1")
            conf.set("XMLRPC", "bindip", "*")

            cmd = input("Reset the ACL file to allow all private networks? (y/N) ").lower()
            if cmd == "y":
                xmlrpcacl.acl = "127.*.*.*,4 " \
                                "169.254.*.*,4 " \
                                "10.*.*.*,4 " \
                                "172.16.*.*,4 172.17.*.*,4 172.18.*.*,4 172.19.*.*,4 " \
                                "172.20.*.*,4 172.21.*.*,4 172.22.*.*,4 172.23.*.*,4 " \
                                "172.24.*.*,4 172.25.*.*,4 172.26.*.*,4 172.27.*.*,4 " \
                                "172.28.*.*,4 172.29.*.*,4 172.30.*.*,4 172.31.*.*,4 " \
                                "192.168.*.*,4"
                save_xmlrpcacls()

            else:
                cmd = input("Reset the ACL file by enter individual ip addresses to grant access? (y/N) ").lower()
                if cmd == "y":
                    # Always set local host
                    lst_ip = ["127.*.*.*,4 "]
                    while True:
                        cmd = input("Enter single IPv4 address | Press RETURN to complete: ")
                        if not cmd:
                            xmlrpcacl.acl = " ".join(lst_ip)
                            save_xmlrpcacls()
                            break
                        elif match(r"([\d*]{1,3}\.){3}[\d*]{1,3}", cmd):
                            ip_level = "{0},4".format(cmd)
                            if ip_level not in lst_ip:
                                lst_ip.append(ip_level)
                        else:
                            stderr.write("Wrong format (0.0.0.0)\n")

        else:
            cmd = input("Do you want to allow connections from localhost ONLY? (y/N) ").lower()
            if cmd == "y":
                conf.set("XMLRPC", "xmlrpc", "1")
                conf.set("XMLRPC", "bindip", "127.0.0.1")

                cmd = input("Reset the ACL file to allow localhost connections only? (y/N) ").lower()
                if cmd == "y":
                    xmlrpcacl.acl = "127.*.*.*,4 "
                    save_xmlrpcacls()

            else:
                cmd = input(
                    "\nWARNING: This will disable the possibility to connect with "
                    "RevPiPyControl!\n         Are you sure? (y/N) "
                ).lower()
                if cmd == "y":
                    conf.set("XMLRPC", "xmlrpc", "0")
                    conf.set("XMLRPC", "bindip", "127.0.0.1")
                    xmlrpcacl.acl = ""
                    save_xmlrpcacls()
                else:
                    stdout.write("\nWe did no changes!\n")
                    return 2

        # Write configuration
        with open(CONFIG_FILE, "w") as fh:
            conf.write(fh)

        print_xmlrpcacls()

    except KeyboardInterrupt:
        stdout.write("\n\nWe did no changes!\n")
        return 2

    if system("/etc/init.d/revpipyload status > /dev/null") == 0:
        try:
            cmd = input("\nDo you want to apply the new settings now? (Y/n) ").lower()
            if cmd in ("", "y"):
                system("/etc/init.d/revpipyload reload")
            else:
                stderr.write(
                    "\nYou have to activate the new settings for RevPiPyLoad!\n"
                    "    sudo /etc/init.d/revpipyload reload\n"
                )
        except KeyboardInterrupt:
            pass

    return 0


if __name__ == '__main__':
    import sys

    sys.exit(main())
