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
DEFAULT_ROLE='vm-power-admin'
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

    def get_all_users(self):
        users = self.xen_session.xenapi.subject.get_all_records()
        return users

    def get_role_names(self, roles_list):
        role_names = []
        for role in roles_list:
            role_names.append(self.xen_session.xenapi.role.get_name_label(role))
        return role_names

    def create_user(self, user_record, roles):
        logging.debug("Creating new user (%s) with these roles: %s" % (user_record['other_config']['subject-name'], roles))
        logging.debug("New user record:\n%s" % pprint.pformat(user_record))
        user_ref = self.xen_session.xenapi.subject.create(user_record)
        logging.debug("New user created with reference: %s" % user_ref)
        for role_name in roles:
            role_ref = self.xen_session.xenapi.role.get_by_name_label(role_name)[0]
            logging.debug("Found matching role: %s (%s). Adding to it..." % (role_ref, role_name))
            self.xen_session.xenapi.subject.add_to_roles(user_ref, role_ref)

    def get_available_roles(self):
        #available_roles = self.get_role_names(self.xen_session.xenapi.role.get_all())
        available_roles = []
        for role_ref in self.xen_session.xenapi.role.get_all():
            if len(self.xen_session.xenapi.role.get_subroles(role_ref)):
                available_roles.append(role_ref)
        return self.get_role_names(available_roles)

    def add_user(self, username, roles):
        logging.debug("Searching for user %s in the external directory service..." % username)
        try:
            subj_id = self.xen_session.xenapi.auth.get_subject_identifier(username)
        except XenAPI.Failure:
            logging.debug("Cannot find the specified user")
            return False
        logging.debug("Found one with subject ID: %s. Retrieving user details..." % subj_id)
        user_record = self.xen_session.xenapi.auth.get_subject_information_from_identifier(subj_id)
        logging.debug("User record:\n\n%s" % pprint.pformat(user_record))
        existing_sids = [r['subject_identifier'] for r in self.get_all_users().values()]
        logging.debug("Existing SIDs: %s" % existing_sids)
        if subj_id in existing_sids:
            logging.debug("User already exists")
            return False
        self.create_user({'other_config': user_record, 'subject_identifier': subj_id}, roles)
        return True

    def delete_user(self, username):
        logging.debug("Searching for user %s in the external directory service..." % username)
        try:
            subj_id = self.xen_session.xenapi.auth.get_subject_identifier(username)
        except XenAPI.Failure:
            logging.debug("Cannot find the specified user in the external directory")
            return False
        logging.debug("Found one with subject ID: %s" % subj_id)
        deleted = False
        for subj_ref, record in self.xen_session.xenapi.subject.get_all_records().iteritems():
            if subj_id == record['subject_identifier']:
                self.xen_session.xenapi.subject.destroy(subj_ref)
                deleted = True
        if deleted:
            logging.debug("Successfully deleted the user")
        else:
            logging.debug("Could not delete the user - perhaps it was not on the Xen host")
        return deleted



def clone_xen_users(src_x, dst_x, operation='copy'):
    logging.debug("Clonning all users from %s to %s. Clone operation: %s" % (src_x, dst_x, operation))
    users = src_x.get_all_users()
    dst_sids = [r['subject_identifier'] for r in dst_x.get_all_users().values()]
    logging.debug("SIDs on the target: %s" % dst_sids)
    for user_ref, user_record in users.iteritems():
        logging.debug("Cloning: %s..." % user_record['other_config']['subject-name'])
        if user_record['subject_identifier'] in dst_sids:
            logging.debug("User already exists on the destination Xen host. Skipping.")
            continue
        roles = src_x.get_role_names(user_record['roles'])
        # the following two need to be removed as being specific to the source host
        del user_record['uuid']
        user_record['roles'] = []
        dst_x.create_user(user_record, roles)


def main():
    logging.basicConfig(format='[%(asctime)s] %(message)s', level=LOG_LEVEL)
    parser = OptionParser()
    parser.add_option('-x', dest='xen_host', help='Name of a reference Xen host')
    parser.add_option('-d', dest='dst_xen_host', help='Name of a target Xen host (or a comma separated list of multiple hosts)')
    parser.add_option('-u', dest='username', help='Username of the user you wish to add')
    parser.add_option('-r', dest='roles', help="List of roles a new user should be assigned to. Defaults to: %s if empty." % DEFAULT_ROLE)
    parser.add_option('-m', dest='minimal', action='store_true', default=False, help='Minimal output')
    (options, args) = parser.parse_args()
    if not (options.xen_host and args):
        print "ERROR: Missing an option or a command"
        parser.print_help()
        sys.exit(-1)

    try:
        username = USERNAME
        password = PASSWORD
    except NameError:
        username = raw_input("Xen host username: ")
        password = getpass.getpass()
    
    x = XenClient(username, password, "https://%s" % options.xen_host)

    # operation:
    #   copy  - copy users from src to dst, leave existing users on dst if not present in src
    #   clone - copy users from dst to src, remove users from dst that are not present on src (TODO)
    #   merge - merge both user bases (effectivelly copy src to dst, then copy dst to src)
    if  args[0] == 'list':
        users = x.get_all_users()
        for user_ref, user_record in users.iteritems():
            if options.minimal:
                print "%s (%s)" % tuple(user_record['other_config'][key] for key in ['subject-displayname', 'subject-name'])
            else:
                print '-' * 79
                print "%s" % pprint.pformat(user_record)
    elif args[0] in ['copy', 'merge']:
        if not options.dst_xen_host:
            print "ERROR: Need to specify a destination Xen host"
            sys.exit(-1)
        xen_hosts = [h.strip() for h in options.dst_xen_host.split(',')]
        for dst_host in xen_hosts:
            dst_x = XenClient(username, password, "https://%s" % dst_host)
            clone_xen_users(x, dst_x)
            if args[0] == 'merge':
                clone_xen_users(dst_x, x)
    elif args[0] == 'roles':
        print "Available roles:"
        for role in x.get_available_roles():
            print "    %s" % role,
            if role == DEFAULT_ROLE:
                print " [default]"
            else:
                print
    elif args[0] == 'add':
        if not options.username:
            print "ERROR: You must specify a username"
            sys.exit(-1)
        roles_list = [DEFAULT_ROLE]
        if options.roles:
            roles_list = [r.strip() for r in options.roles.split(',')]
        x.add_user(options.username, roles_list)
    elif args[0] == 'remove':
        if not options.username:
            print "ERROR: You must specify a username"
            sys.exit(-1)
        x.delete_user(options.username)
    else:
        print "ERROR: Unknown command"


if __name__ == '__main__':
    main()

