#!/usr/bin/env python

import XenAPI
import pprint
import logging
import sys
import getpass
import traceback
from optparse import OptionParser

### Configuration section
#
# If you want an unattended run, uncomment the lines below and put your
# Xen Host credentials there
#USERNAME=''
#PASSWORD=''
LOG_LEVEL=logging.DEBUG
#
### end config

class XenClient:
    def __init__(self, user, password, url):
        self.user = user
        self.password = password
        self.url = url
        try:
            self.xen_session = XenAPI.Session(self.url)
            self.xen_session.xenapi.login_with_password(self.user, self.password)
        except XenAPI.Failure, err:
            error, master_host = err.details[0:2]
            if error == "HOST_IS_SLAVE":
                self.xen_session = XenAPI.Session("https://%s" % master_host)
                self.xen_session.xenapi.login_with_password(self.user, self.password)
            else:
                raise Exception('Cannot connect to Xen')

    def list_vms(self):
        vms = self.xen_session.xenapi.VM.get_all()
        for vm in vms:
            if not self.xen_session.xenapi.VM.get_is_a_template(vm):
                print self.xen_session.xenapi.VM.get_name_label(vm)

    def find_vm(self, name):
        logging.debug("Looking for VM with name '%s'" % name)
        vms = self.xen_session.xenapi.VM.get_by_name_label(name)
        if vms:
            logging.debug("VM found: %s" % vms[0])
            return vms[0]
        else:
            logging.debug("No VMs found with that name")
            return None

    def find_vbd(self, vm):
        logging.debug("Looking for attached VBDs...")
        vbds = self.xen_session.xenapi.VM.get_VBDs(vm)
        if vbds:
            logging.debug("Found VBD: %s" % vbds[0])
            return vbds[0]
        else:
            logging.debug("No attached VBDs found")
            return None

    def find_vdi(self, vbd):
        logging.debug("Looking for associated VDIs...")
        vdi = self.xen_session.xenapi.VBD.get_VDI(vbd)
        if vdi:
            logging.debug("Found VDI: %s" % vdi)
            return vdi
        else:
            logging.debug("No associated VDIs found")
            return None

    def find_sr(self, name):
        logging.debug("Looking for SR with name '%s'" % name)
        sr = self.xen_session.xenapi.SR.get_by_name_label(name)
        if sr:
            logging.debug("Found SR: %s" % name)
            return sr[0]
        else:
            logging.debug("No SRs found with that name")
            return None

    def vdi_copy(self, vdi, target_sr):
        logging.debug("Copying VDI to a new SR. This may take a while...")
        new_vdi = self.xen_session.xenapi.VDI.copy(vdi, target_sr)
        logging.debug("VDI finished copying. New VDI: %s" % new_vdi)
        return new_vdi

    def vbd_create(self, vdi, vm):
        vbd_record = { 'VM': vm,
                       'VDI': vdi,
                       'userdevice': '0',
                       'empty': False,
                       'bootable': True,
                       'mode': 'RW',
                       'type': 'Disk', 
                       'other_config': {},
                       'qos_algorithm_type': '',
                       'qos_algorithm_params': {},
                       }
        logging.debug("Creating a new VBD and attaching it to a VM. VBD record:\n%s" % pprint.pformat(vbd_record))
        vbd = self.xen_session.xenapi.VBD.create(vbd_record)
        logging.debug("New VBD created and attached: %s" % vbd)
        return vbd

    def destroy_vbd(self, vbd):
        logging.debug("Destroying VBD: %s" % vbd)
        self.xen_session.xenapi.VBD.destroy(vbd)

    def destroy_vdi(self, vdi):
        logging.debug("Destroying VDI: %s" % vdi)
        self.xen_session.xenapi.VDI.destroy(vdi)

    def get_powerstate(self, vm):
        return self.xen_session.xenapi.VM.get_power_state(vm)

    def shutdown_vm(self, vm, force=True):
        logging.debug("Shutting down VM: %s" % vm)
        if force:
            self.xen_session.xenapi.VM.hard_shutdown(vm)

    def start_vm(self, vm, force=True):
        logging.debug("Starting up VM: %s" % vm)
        self.xen_session.xenapi.VM.start(vm, False, force)

    def get_vdi_sr(self, vdi):
        return self.xen_session.xenapi.VDI.get_SR(vdi)

    def move_vm(self, vm_name, sr_name):
        try:
            vm_needs_starting = False
            vm = self.find_vm(vm_name)
            dst_sr = self.find_sr(sr_name)
            orig_vbd = self.find_vbd(vm)
            orig_vdi = self.find_vdi(orig_vbd)
            src_sr = self.get_vdi_sr(orig_vdi)
            if dst_sr == src_sr:
                logging.debug("SRs are the same, not moving the VM")
                return
            logging.debug("SRs are different, moving the VM now...")
            vm_state = self.get_powerstate(vm)
            if vm_state == 'Running':
                self.shutdown_vm(vm)
                vm_needs_starting = True
            new_vdi = self.vdi_copy(orig_vdi, dst_sr)
            self.destroy_vbd(orig_vbd)
            self.destroy_vdi(orig_vdi)
            new_vbd = self.vbd_create(new_vdi, vm)
            if vm_needs_starting:
                self.start_vm(vm)
        except XenAPI.Failure:
            logging.critical("Xen error has occured while moving the VM from one SR to another:\n\n%s" % traceback.format_exc())
        except:
            logging.critical("An unexpected error has occured occured:\n\n%s" % traceback.format_exc())


def main():
    logging.basicConfig(format='[%(asctime)s] %(message)s', level=LOG_LEVEL)
    parser = OptionParser()
    parser.add_option('-v', dest='vm', help='Name of the VM to move')
    parser.add_option('-x', dest='xen_host', help='Name of a Xen host (does not need to be the pool master).')
    parser.add_option('-s', dest='sr', help='Destination SR name')
    (options, args) = parser.parse_args()
    if not (options.vm and options.xen_host and options.sr):
        print "ERROR: Missing option"
        parser.print_help()
        sys.exit(-1)

    try:
        username = USERNAME
        password = PASSWORD
    except NameError:
        username = raw_input("Xen host username: ")
        password = getpass.getpass()
    
    x = XenClient(username, password, "https://%s" % options.xen_host)
    x.move_vm(options.vm, options.sr)


if __name__ == '__main__':
    main()

