# -*- coding: utf-8 -*-
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# Copyright 2011 Justin Santa Barbara
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Handles all processes relating to instances (guest vms).

The :py:class:`ComputeManager` class is a :py:class:`nova.manager.Manager` that
handles RPC calls relating to creating instances.  It is responsible for
building a disk image, launching it via the underlying virtualization driver,
responding to calls to check its state, attaching persistent storage, and
terminating it.

"""

import libvirt
from lxml import etree
import json

import nova.context
from nova.i18n import _
import nova.context
from nova.compute.manager import *
import base64


class ComputeManager(nova.compute.manager.ComputeManager):
    """Manages the running instances from creation to destruction."""

    def snapshot_list(self, context, instance):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_list'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshotList = dom.listAllSnapshots();
            result=list()
            for snapshot in snapshotList:
                result.append(self._toDict(snapshot))
            return json.dumps(result,ensure_ascii=False)
        except (exception.InstanceNotFound,exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_list is Failed,instance_id=%s',instance["uuid"]
            LOG.exception(msg, instance=instance)
            raise


    def snapshot_create(self, context, instance, snapshot_name,snapshot_desc=""):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_create'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshot_desc=base64.b64encode(snapshot_desc.encode('utf-8'))
            snapshot_name=base64.b64encode(snapshot_name.encode('utf-8'))
            desc = "<domainsnapshot><name>%s</name><description>%s</description></domainsnapshot>" % (snapshot_name,snapshot_desc);
            snapshot = dom.snapshotCreateXML(desc, 0);
            return json.dumps(self._toDict(snapshot),ensure_ascii=False)
        except (exception.InstanceNotFound,exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_create is Failed,instance_id=%s,snapshot_name=%',(instance["uuid"],snapshot_name)
            LOG.exception(msg, instance=instance)
            raise

    def snapshot_delete(self, context, instance, snapshot_name):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_delete'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotLookupByName(snapshot_name);
            snapshot.delete(0);
        except (exception.InstanceNotFound,exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_delete Failed,instance_id=%s,snapshot_name=%',(instance["uuid"],snapshot_name)
            LOG.exception(msg, instance=instance)
            raise

    def snapshot_revert(self, context, instance, snapshot_name):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_revert'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotLookupByName(snapshot_name);
            dom.revertToSnapshot(snapshot,0)
        except (exception.InstanceNotFound,exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_revert is Failed,instance_id=%s,snapshot_name=%',(instance["uuid"],snapshot_name)
            LOG.exception(msg, instance=instance)
            raise

    def snapshot_current(self, context, instance):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_current'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotCurrent();
            return json.dumps(self._toDict(snapshot))
        except (exception.InstanceNotFound,exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_current is Failed,instance_id=%s',instance["uuid"]
            LOG.exception(msg, instance=instance)
            raise
        except (libvirt.libvirtError):
            return ""

##=========================================================================================================
    def _toDict(self,snapshot):
        dict={}
        dict["name"]=base64.b64decode(snapshot.getName()).decode('utf-8')
        dict["desc"]=self._getDescription(snapshot)
        dict["create_time"]=self._getCreationTime(snapshot)
        try:
            parent=snapshot.getParent();
            dict["parent_name"]=base64.b64decode(parent.getName()).decode('utf-8')
            return dict
        except (libvirt.libvirtError):
            dict["parent_name"]="-1"
            return dict

    def _getCreationTime(self,snapshot):
        xml_desc = snapshot.getXMLDesc()
        domain = etree.fromstring(xml_desc)
        element = domain.find('creationTime')
        localTime = time.localtime(int(element.text))
        createTime= time.strftime("%Y-%m-%d %H:%M:%S", localTime)
        return createTime

    def _getDescription(self,snapshot):
        xml_desc = snapshot.getXMLDesc()
        domain = etree.fromstring(xml_desc)
        element = domain.find('description')
        if element is not None:
            return base64.b64decode(element.text).decode('utf-8')
        else:
            return ""