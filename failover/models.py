from django.db import models
from computes.models import Compute
from instances.models import Instance
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from xml.etree import ElementTree

class Failover(models.Model):
    instance = models.ForeignKey(Instance, on_delete=models.CASCADE, related_name='instance')
    failover_host = models.ForeignKey(Compute, on_delete=models.CASCADE, related_name='failover_host')
    failover_instance = models.ForeignKey(Instance, on_delete=models.CASCADE, null=True, related_name='failover_instance')
    failover_method = models.IntegerField(_("failover_method"))
    service_ports = models.JSONField(_("service_ports"), null=True, default=list)
    migration_conf = models.JSONField(_("migration_conf"), null=True)
    drbd_conf = models.JSONField(_("drbd_conf"), null=True)
    storage_conf = models.JSONField(_("storage_conf"), null=True)
    bidirectional = models.BooleanField(_("bidirectional"), default=False)
    status = models.CharField(_("status"), default="Ready", max_length=100)
    created = models.DateTimeField(_("created"), auto_now_add=True)

    def update_xml(self):
        vm_xml = self.instance.proxy._XMLDesc(0)
        
        root = ElementTree.fromstring(vm_xml)
        name = root.find('name').text
        uuid = root.find('uuid').text

        if self.failover_instance:
            self.failover_instance.proxy._defineXML(vm_xml)
            return

        existing_vm = Instance.objects.filter(name=self.instance.name, compute=self.failover_host).first()
        if existing_vm:
            existing_vm.proxy._defineXML(vm_xml)
            self.failover_instance = existing_vm
            self.save()
            return

        conn = self.failover_host.connection
        new_domain = conn.defineXML(vm_xml)
        
        create_instance = Instance(compute_id=self.failover_host.id, name=name, uuid=uuid)
        create_instance.save()
        self.failover_instance = create_instance
        self.save()

    def failover_process(self):
        if self.failover_host.status == 1:
            self.stonith()
        else:
            return {'status':'Failed', 'message':'Failover host is not running'}
        
        self.status = "Activated"

        if self.failover_instance == 3:
            self.failover_instance.proxy.resume()
        elif self.failover_instance != 1:
            self.failover_instance.proxy.start()

        if self.bidirectional:
            self.flip_failover()

    def stonith(self):
        if self.instance.status == 3:
            try:
                self.instance.proxy.resume()
                self.instance.proxy.force_shutdown()
            except:
                self.instance.proxy.shutdown()
            finally:
                return
        while self.instance.status != 5:
            self.instance.proxy.force_shutdown()

    def flip_failover(self):
        self.instance, self.failover_instance, self.failover_host = self.failover_instance, self.instance, self.instance.compute
        self.drbd_conf['primary_device'], self.drbd_conf['secondary_device'] = self.drbd_conf['secondary_device'], self.drbd_conf['primary_device']
        self.drbd_conf['primary_disk'], self.drbd_conf['secondary_disk'] = self.drbd_conf['secondary_disk'], self.drbd_conf['primary_disk']
        self.drbd_conf['primary_metadata'], self.drbd_conf['secondary_metadata'] = self.drbd_conf['secondary_metadata'], self.drbd_conf['primary_metadata']
        self.status = "Ready"
        self.save()