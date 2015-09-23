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
from nova.sihuatech.orm import *
from nova.sihuatech.model import Snapshot
from webob.response import Response
import nova.context
from nova.i18n import _
import nova.context
from nova.compute.manager import *
from nova import utils
import os

class ComputeManager(nova.compute.manager.ComputeManager):
    """Manages the running instances from creation to destruction."""

    def snapshot_list(self, context, instance):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_list'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshotList = dom.listAllSnapshots();
            result = list()
            for snapshot in snapshotList:
                result.append(self._toDict(snapshot))
            return json.dumps(result, ensure_ascii=False)
        except Exception:
            msg = 'snapshot_list is Failed,instance_id=%s', instance["uuid"]
            LOG.exception(msg, instance=instance)
            return Response(status=500)


    def snapshot_create(self, context, instance, snapshot_name, snapshot_desc=u""):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_create'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            if not snapshot_desc:
                snapshot_desc=u""
            parent_name = ""
            try:
                current_snapshot = dom.snapshotCurrent()
                parent_name = current_snapshot.getName()
            except Exception:
                pass

            # 记录到数据库
            parent_name_utf8=base64.b64decode(parent_name).decode('utf-8');
            self._save(instance, snapshot_name, snapshot_desc, parent_name_utf8)
            # 中文必须为utf8编码
            snapshot_name_utf8 = base64.b64encode(snapshot_name.encode('utf-8'))
            snapshot_desc_utf8 = base64.b64encode(snapshot_desc.encode('utf-8'))
            
            desc = "<domainsnapshot><name>%s</name><description>%s</description></domainsnapshot>" % (snapshot_name_utf8, snapshot_desc_utf8);
            
            #生成快照xml
            snapshot_xml_file="/tmp/"+uuid.uuid4().hex + '.xml'
            try:
                with open(snapshot_xml_file,'wb') as f:
                    f.write(desc)
                virsh_cmd = ('/usr/bin/virsh', 'snapshot-create', instance['name'],"--xmlfile", snapshot_xml_file)
                utils.execute(*virsh_cmd)
            finally:
                if os.path.exists(snapshot_xml_file):
                    os.remove(snapshot_xml_file)
            
            snapshot = dom.snapshotLookupByName(snapshot_name_utf8)

            # 快照创建完成后更改状态
            self._update(instance, snapshot_name, {'state':1})
            str = json.dumps(self._toDict(snapshot), ensure_ascii=False)
            return str
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_create is Failed,instance_id=%s,snapshot_name=%', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            return Response(status=500)

    def snapshot_delete(self, context, instance, snapshot_name):
        context = context.elevated()
        session = Session()
        try:
            LOG.audit(_('snapshot_delete'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            snapshot_name_utf8 = base64.b64encode(snapshot_name.encode('utf-8')) #不确定snapshot_name的编码,此处转换有可能出错
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotLookupByName(snapshot_name_utf8);
            snapshot.delete(0);
            self._update(instance, snapshot_name, {'deleted':1})
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_delete Failed,instance_id=%s,snapshot_name=%', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            return Response(status=500)
        finally:
            if session:
                session.close()

    def snapshot_revert(self, context, instance, snapshot_name):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_revert'), context=context, instance=instance)
            snapshot_name_utf8 = base64.b64encode(snapshot_name.encode('utf-8'))
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotLookupByName(snapshot_name_utf8);
            dom.revertToSnapshot(snapshot, 0)
            return Response(status=200)
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_revert is Failed,instance_id=%s,snapshot_name=%', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            return Response(status=500)

    def snapshot_current(self, context, instance):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_current'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotCurrent();
            return json.dumps(self._toDict(snapshot), ensure_ascii=False)
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_current is Failed,instance_id=%s', instance["uuid"]
            LOG.exception(msg, instance=instance)
            return Response(status=500)
        except (libvirt.libvirtError):
            return ""

##=========================================================================================================
    def _toDict(self, snapshot):
        dict = {}
        dict["name"] = self._getName(snapshot)
        dict["desc"] = self._getDescription(snapshot)
        dict["create_time"] = self._getCreationTime(snapshot)
        try:
            parent = snapshot.getParent();
            dict["parent_name"] = self._getName(parent)
            return dict
        except (libvirt.libvirtError):
            dict["parent_name"] = "-1"
            return dict

    def _getCreationTime(self, snapshot):
        xml_desc = snapshot.getXMLDesc()
        domain = etree.fromstring(xml_desc)
        element = domain.find('creationTime')
        localTime = time.localtime(int(element.text))
        createTime = time.strftime("%Y-%m-%d %H:%M:%S", localTime)
        return createTime

    def _getDescription(self, snapshot):
        xml_desc = snapshot.getXMLDesc()
        domain = etree.fromstring(xml_desc)
        element = domain.find('description')
        if element is not None:
            try:
                return base64.b64decode(element.text).decode('utf-8')
            except Exception:
                if isinstance(element.text, unicode):
                    return element.text
                return element.text.decode('utf-8')
        else:
            return ""
        
    def _getName(self, snapshot):
        snapshot_name = snapshot.getName();
        try:
            snapshot_name_utf8 = base64.b64decode(snapshot_name).decode('utf-8')
            return snapshot_name_utf8
        except Exception:
            if isinstance(snapshot_name, unicode):
                return snapshot_name
            return snapshot_name.decode('utf-8')
    
    def _save(self, instance, snapshot_name, snapshot_desc, parent_name):
        session = Session()
        try:
            snapshot_record = Snapshot(name=snapshot_name,desc=snapshot_desc,parent=parent_name,instance_uuid=instance["uuid"])
            session.add(snapshot_record)
            session.commit()
        except Exception:
            pass
        finally:
            if session:
                session.close()
        
    def _update(self, instance, snapshot_name, args):
        session = Session()
        try:
            snapshot_record = session.query(Snapshot).filter(Snapshot.deleted == 0,Snapshot.instance_uuid == instance["uuid"],Snapshot.name == snapshot_name).first()
            if snapshot_record:
                if args.has_key("deleted"):
                    snapshot_record.deleted = args["deleted"]
                    snapshot_record.delete_at = timeutils.utcnow()
                if args.has_key("state"):
                    snapshot_record.state = args["state"]
                snapshot_record.update_at = timeutils.utcnow()
                session.add(snapshot_record)
            session.commit()
        except Exception:
            pass
        finally:
            if session:
                session.close()

