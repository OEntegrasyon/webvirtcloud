from django.shortcuts import get_object_or_404, render, redirect
from django.http import JsonResponse
from django.http import HttpResponse
from django.utils.translation import gettext_noop as _
from django.contrib import messages
from django.db.models import Count
from logs.views import addlogmsg

from computes.models import Compute
from instances.models import Instance
from failover.models import Failover
from vrtManager.storage import wvmStorages

import libvirt
import paramiko
import re
import threading
import time

def prepare_migration_conf(parameters):
    migration_conf = {}
    migration_conf['migration_interval'] = parameters.get("migration-interval-selection")
    return migration_conf

def prepare_drbd_conf(parameters):
    drbd_conf = {}
    drbd_conf['protocol'] = parameters.get("protocol-selection") 
    drbd_conf['primary_device'] = parameters.get("primary-device")
    drbd_conf['primary_disk'] = parameters.get("primary-disk")
    drbd_conf['primary_metadata'] = parameters.get("primary-metadata-location")
    drbd_conf['secondary_device'] = parameters.get("secondary-device")
    drbd_conf['secondary_disk'] = parameters.get("secondary-disk")
    drbd_conf['secondary_metadata'] = parameters.get("secondary-metadata-location")
    drbd_conf['allow_two_primaries'] = parameters.get("allow-two-primaries")
    drbd_conf['sync_rate'] = parameters.get("sync-rate")
    drbd_conf['al_extents'] = parameters.get("al-extents")
    return drbd_conf

def prepare_storage_conf(parameters):
    storage_conf = {}
    storage_conf['storage_address'] = parameters.get("storage-ip")
    storage_conf['storage_path'] = parameters.get("storage-path")
    return storage_conf

def send_file_to_remote_from_remote(compute_1, compute_2, local_path, remote_path):
    if compute_2.proxy.conn == 2:
        host, username, port = get_ssh_credentials(compute_2)

        command = f"scp -P {port} {local_path} {username}@{host}:{remote_path}"
        execute_command_with_ssh(command, compute_1)


def execute_command_with_ssh(command, compute):
    if compute.proxy.conn == 2:
        host, username, port = get_ssh_credentials(compute)
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(hostname=host, username=username, port=port)
            
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode().strip()
            error_log = stderr.read().decode().strip()
            return output, error_log

        except Exception as e:
            print(f"An error occurred: {e}")
        
        finally:
            ssh.close()
    return None,'SSH connection could not be established'

def get_ssh_credentials(compute):
    host = compute.proxy.host
    username = compute.proxy.login
    port = 22 
    if len(host.split(":")) > 1:
        host_and_port = host.split(":")
        host = host_and_port[0]
        port = host_and_port[1]
    return host, username, port

def check_drbd_service(compute):
    result = ['failed','SSH connection could not be established']
    drbd_info, error_log = execute_command_with_ssh("drbdadm --version", compute)
    if drbd_info:
        match = re.search(r'DRBDADM_VERSION=([\d.]+)', drbd_info)
        if match:
            drbdadm_version = match.group(1)
            result = ['success',drbdadm_version]
    if error_log:
        result = ['failed','Error']
    return result

def failover_setup(failover_object):
    method = failover_object.failover_method
    if method == 1:
        failover_migration_setup(failover_object)
    if method == 2:
        failover_object.update_xml()
        failover_drbd_setup(failover_object)
    if method == 3:
        failover_object.update_xml()
        failover_storage_setup(failover_object)

def failover_migration_setup(failover_object):
    # first time migration
    name = f"migration_process_{failover_object.id}"
    thread = threading.Thread(target=migration_process, name=name, kwargs={'failover_object':failover_object, 'first_time':True}, daemon=True)
    thread.start()
    pass

def failover_drbd_setup(failover_object):
    pass

def failover_storage_setup(failover_object):

    check_storage_pools(failover_object)

    address = failover_object.storage_conf.get('storage_address')
    s_path = failover_object.storage_conf.get('storage_path')
    result, error = execute_command_with_ssh(f"mkdir -p {s_path}", failover_object.failover_host)
    if len(error) == 0:
        result, error = execute_command_with_ssh(f"mount {address}:{s_path} {s_path}", failover_object.failover_host)
        if len(error) == 0:
            failover_object.update_xml()

def check_storage_pools(failover_object):
    primary_compute = failover_object.instance.compute
    secondary_compute = failover_object.failover_host
    storage_path = failover_object.storage_conf.get('storage_path')

    create_pool_if_not_exists(primary_compute, storage_path)
    create_pool_if_not_exists(secondary_compute, storage_path)

def create_pool_if_not_exists(compute, path):
    name, counter = 'failover-storage', 0
    conn = wvmStorages(
            compute.hostname, compute.login, compute.password, compute.type
        )
    
    s_pool = conn.get_pool_by_target(path)
    pool_names = [storage.get('name') for storage in conn.get_storages_info()]
    while name in pool_names:
        name = f'{name}-{counter}'
        counter += 1
    if not s_pool:
        conn.create_storage('dir',name,'',path)
    conn.close()

def migrate_in_interval(failover_object, stop_event):
    interval = int(failover_object.migration_conf.get('migration_interval',60))
    name = f"migration_process_{failover_object.id}"
    while not stop_event.is_set():
        if not any(t.name == name and t.is_alive() for t in threading.enumerate()):
            migration_process(failover_object)
        time.sleep(interval)

def migration_process(failover_object, first_time=False):
    if failover_object.status == "Activated":
        return

    if failover_object.instance.proxy.get_status() != 1:
        failover_object.instance.proxy.start()
        
    ins = failover_object.instance.proxy
    dom = ins.instance
    conn = failover_object.failover_host.connection

    if first_time:
        flags = 577 # VIR_MIGRATE_LIVE | VIR_MIGRATE_NON_SHARED_DISK | VIR_MIGRATE_UNSAFE
        test_object = dom.migrate(conn, flags, None, None, 0)
        ins.create_external_snapshot('snap_for_mig')

    else:
        snap_extension = None
        file_paths = []
        for disk_device in ins.get_disk_devices():
            splitted_name = disk_device.get('image').split('.')
            disk_name = splitted_name[-1] if len(splitted_name) > 0 else None
            if disk_name == 'snap_for_mig' or disk_name == 'temp_snap_for_mig':
                file_paths.append(disk_device.get('path'))

        if len(file_paths) > 0:
            snap_extension = file_paths[0].split('.')[-1]
            if snap_extension == 'snap_for_mig':
                ins.create_external_snapshot('temp_snap_for_mig')
            else:
                ins.create_external_snapshot('snap_for_mig')

        for file_path in file_paths:
            send_file_to_remote_from_remote(failover_object.instance.compute, failover_object.failover_host, file_path, file_path)
            execute_command_with_ssh(f"chown libvirt-qemu:kvm {file_path}", failover_object.failover_host)
        
        ins2 = failover_object.failover_instance.proxy
        dom2 = ins2.instance

        snap_obj_1 = dom.snapshotLookupByName(snap_extension)
        snap_xml = snap_obj_1.getXMLDesc()
        snap_obj_2 = dom2.snapshotCreateXML(snap_xml, 48) # VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY | VIR_DOMAIN_SNAPSHOT_CREATE_REUSE_EXT

        snap_obj_2.delete(0)
        snap_obj_1.delete(0)
        ins.create_external_snapshot(snap_extension)

    failover_object.update_xml()

    if failover_object.instance.proxy.get_status() != 1:
        failover_object.instance.proxy.start()