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
from . import utils

import contextlib
import paramiko
import re

def failover(request):
    instances = None

    failovers = {}
    failovers['primary'] = [failover_object.instance for failover_object in Failover.objects.all()]
    failovers['secondary'] = [failover_object.failover_instance for failover_object in Failover.objects.all()]

    if request.user.is_superuser:
        instances = Instance.objects.all().prefetch_related("userinstance_set").annotate(
            failover_count=Count('instance'),
            secondary_count=Count('failover_instance')
            ).order_by('-failover_count', '-secondary_count', 'name')
    else:
        instances = Instance.objects.filter(
            userinstance__user=request.user
        ).prefetch_related("userinstance_set").annotate(
            failover_count=Count('instance'),
            secondary_count=Count('failover_instance')
            ).order_by('-failover_count', '-secondary_count', 'name')

    if not request.user.is_superuser:
        return HttpResponse("404")

    return render(
        request, "failover.html", {"instances": instances, "failovers": failovers}
    )

def setup(request, pk):
    instance = get_object_or_404(Instance, pk=int(pk))
    status_dict = {0:"No State",
                   1:"Running",
                   2:"Blocked",
                   3:"Suspended",
                   4:"Shutdown",
                   5:"Shut Off",
                   6:"Crashed",
                   7:"Power Suspended",
                   8:"Shutting Down"}
    vm_status = status_dict.get(instance.status)
    computes = (
        Compute.objects.all()
        .order_by("name")
        .exclude(id=instance.compute.id)
    )

    return render(
        request, "setup.html", {"instance": instance, "vmstatus":vm_status, "computes":computes}
    )

def delete(request, pk):
    instance = get_object_or_404(Instance, pk=int(pk))
    failover = Failover.objects.filter(instance=instance).first()

    if failover.failover_method == 1:
        with contextlib.suppress(Exception):
            snap_for_mig = failover.instance.proxy.instance.snapshotLookupByName('snap_for_mig')
            snap_for_mig.delete(0)

    if failover:
        failover.delete()

        instance_to_destroy = failover.failover_instance
        if instance_to_destroy:
            try:
                if instance_to_destroy.status == 1:
                    instance_to_destroy.proxy.force_shutdown()
                elif instance_to_destroy.status == 3:
                    instance_to_destroy.proxy.resume()
                    instance_to_destroy.proxy.force_shutdown()
            except:
                instance_to_destroy.proxy.shutdown()
            finally:
                instance_to_destroy.proxy.delete()
                instance_to_destroy.delete()

    return redirect(request.META.get('HTTP_REFERER', '/'))

def configure(request, pk):
    if request.method == "POST":
        instance = get_object_or_404(Instance, pk=int(pk))
        parameters = {key: value for key, value in request.POST.items() if key != 'csrfmiddlewaretoken'}
        failover_host_id = parameters.get("compute-selection")
        failover_method = parameters.get("failover-selection")
        service_ports = [int(key) for key in parameters.get("service-ports").split(',')]
        bidirectional = "True" == parameters.get("bidirectional-failover-selection")
        migration_conf = utils.prepare_migration_conf(parameters)
        drbd_conf = utils.prepare_drbd_conf(parameters)
        storage_conf = utils.prepare_storage_conf(parameters)
        failover_host = get_object_or_404(Compute, pk=int(failover_host_id))

        failover_object = Failover.objects.create(instance=instance, 
                                                  failover_host=failover_host,
                                                  failover_method=int(failover_method),
                                                  service_ports=service_ports,
                                                  migration_conf=migration_conf,
                                                  drbd_conf=drbd_conf,
                                                  storage_conf=storage_conf,
                                                  bidirectional=bidirectional,
                                                  )
        failover_object.save()
        utils.failover_setup(failover_object)

    return redirect('/failover/')

def drbd_info(request, pk):
    compute_id = request.GET.get('compute')
    instance = get_object_or_404(Instance, pk=int(pk))
    host_compute = instance.compute
    dest_compute = get_object_or_404(Compute, pk=int(compute_id))
    drbd_status = {}
    drbd_status['host_compute'] = utils.check_drbd_service(host_compute)
    drbd_status['dest_compute'] = utils.check_drbd_service(dest_compute)
    return JsonResponse(drbd_status)


