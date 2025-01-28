from django.apps import AppConfig
from django.db.models.signals import post_migrate

import threading
import time
import os

def load_failover_objects(sender, **kwargs):
    """
    Load failover objects to memory
    """
    from failover.models import Failover

    FailoverConfig.base_failover_objects = list(Failover.objects.all())

class FailoverConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'failover'
    verbose_name = "Failover"
    base_failover_objects = []
    thread_started = False
    migration_threads = {}

    def ready(self):
        from failover.models import Failover

        try:
            self.base_failover_objects = list(Failover.objects.all())
        except:
            self.base_failover_objects = []

        post_migrate.connect(load_failover_objects, sender=self)
        if os.environ.get("RUN_MAIN") == "true":
            if not self.thread_started:
                self.thread_started = True
                threading.Thread(target=self.check_failover_vms_status, daemon=True).start()

    def check_failover_vms_status(self):
        from failover.models import Failover

        initial_computes = [base_failover_object.instance.compute for base_failover_object in self.base_failover_objects]

        for initial_compute in initial_computes:
            initial_compute.connection.domainEventRegister(self.update_xml, None)

        while True:
            failover_objects = list(Failover.objects.all())
            self.check_migration_threads(failover_objects)
            if self.base_failover_objects != failover_objects:
                self.refresh_failover_objects(self.base_failover_objects, failover_objects)
                self.base_failover_objects = failover_objects
            for failover_object in failover_objects:
                if failover_object.failover_instance.status == 1:
                    continue
                
                if not failover_object.instance.compute.status:
                    failover_object.failover_process()
                    continue
                
                if failover_object.instance.status != 1:
                    failover_object.failover_process()
                    
            time.sleep(5)

    def check_migration_threads(self, failover_objects):
        temp_migration_threads = self.migration_threads.copy()
        for failover_object in failover_objects:
            if failover_object.failover_method == 1:
                temp_migration_threads.pop(failover_object.id, None)
                if failover_object.id not in self.migration_threads.keys():
                    from . import utils
                    stop_event = threading.Event()
                    thread = threading.Thread(target=utils.migrate_in_interval, kwargs={'failover_object': failover_object, 'stop_event':stop_event}, daemon=True)
                    thread.start()
                    self.migration_threads[failover_object.id] = {'thread': thread, 'stop_event':stop_event}

        for failover_id in temp_migration_threads.keys():
            temp_migration_threads[failover_id]['stop_event'].set()
            temp_migration_threads[failover_id]['thread'].join()
            self.migration_threads.pop(failover_id, None)



    def refresh_failover_objects(self, old_objects, new_objects):
        old_computes = [old_object.instance.compute for old_object in old_objects]
        new_computes = [new_object.instance.compute for new_object in new_objects]

        for old_compute in old_computes:
            if old_compute not in new_computes:
                old_compute.connection.domainEventDeregister(self.update_xml)

        for new_compute in new_computes:
            if new_compute not in old_computes:
                new_compute.connection.domainEventRegister(self.update_xml, None)

        for new_object in new_objects:
            if new_object not in old_objects:
                new_object.update_xml()  

    def update_xml(self, conn, dom, event, detail, opaque):
        from failover.models import Failover

        failover_object = Failover.objects.filter(instance__name=dom.name()).first()
        if failover_object:
            failover_object.update_xml()

